"""Contract tests for the shared FakeTerminal fixture (tests/support/fakes.py).

Guards the fake's documented surface so it doesn't silently drift from the real
terminals it stands in for (WiCANTerminal / RawTerminal).
"""

import asyncio

import pytest

from tests.support.fakes import NO_DATA, FakeTerminal, nrc, ok


class TestResponseBuilders:
    def test_ok_positive_shape(self):
        r = ok("62 B0 04")
        assert r == {"ok": True, "bytes": b"\x62\xb0\x04", "hex": "62B004", "raw": "62 B0 04"}

    def test_nrc_shape(self):
        assert nrc(0x31) == {"ok": False, "nrc": 0x31, "raw": "7F31"}
        assert nrc(0x11, "serviceNotSupported") == {
            "ok": False,
            "nrc": 0x11,
            "nrc_desc": "serviceNotSupported",
            "raw": "7F11",
        }


class TestSurface:
    @pytest.mark.asyncio
    async def test_send_uds_scripted_and_default(self):
        t = FakeTerminal({"2101": ok("6101AB")})
        assert (await t.send_uds("2101"))["hex"] == "6101AB"
        assert await t.send_uds("2199") == NO_DATA
        assert t.sent == ["2101", "2199"]

    @pytest.mark.asyncio
    async def test_custom_default(self):
        t = FakeTerminal(default=nrc(0x31))
        assert (await t.send_uds("300000"))["nrc"] == 0x31

    @pytest.mark.asyncio
    async def test_set_header_recorded(self):
        t = FakeTerminal()
        await t.set_header(0x7E4)
        assert t.headers == [0x7E4]
        assert t.header == 0x7E4
        assert ("set_header", 0x7E4) in t.calls

    @pytest.mark.asyncio
    async def test_key_by_header(self):
        t = FakeTerminal({(0x770, "1003"): ok("5003")}, key_by_header=True)
        await t.set_header(0x770)
        assert (await t.send_uds("1003"))["hex"] == "5003"

    @pytest.mark.asyncio
    async def test_uds_kwargs_captured(self):
        t = FakeTerminal({"22BC03": ok("62BC0300")})
        await t.send_uds("22BC03", expected_sid=0x62, expected_echo=b"\xbc\x03")
        assert t.uds_kwargs[0]["expected_sid"] == 0x62
        assert t.uds_kwargs[0]["expected_echo"] == b"\xbc\x03"

    @pytest.mark.asyncio
    async def test_flaky_recover(self):
        t = FakeTerminal(flaky_recover={"2101": "6101AB"})
        assert (await t.send_uds("2101")) == NO_DATA  # first sight misses
        assert (await t.send_uds("2101"))["hex"] == "6101AB"  # recovers after

    @pytest.mark.asyncio
    async def test_enter_extended_session_mode_contract(self):
        # The dual-transport contract: mode= keyword recorded per call.
        t = FakeTerminal()
        assert await t.enter_extended_session(wake=True, mode="81") == (True, None)
        assert t.sessions == [(True, "81")]

    @pytest.mark.asyncio
    async def test_session_result_override(self):
        t = FakeTerminal(session_result=(None, None))
        assert await t.enter_extended_session() == (None, None)

    @pytest.mark.asyncio
    async def test_send_command_reply(self):
        t = FakeTerminal(send_command_reply="7E 00")
        assert await t.send_command("3E00") == "7E 00"
        assert ("send_command", "3E00") in t.calls


class TestSerialize:
    """``serialize`` lets a test show which header a request really went to."""

    @staticmethod
    def _keepalive(t: FakeTerminal):
        async def run():
            async with t.transaction():
                await t.set_header(0x7A5)
                await t.send_uds("3E00")

        return run

    @pytest.mark.asyncio
    async def test_an_intruder_re_points_an_unguarded_request(self):
        t = FakeTerminal(serialize=True)
        t.intrude(self._keepalive(t))
        await t.set_header(0x770)
        await t.send_uds("22BC03")
        await asyncio.gather(*t.intruder_tasks)
        assert (0x7A5, "22BC03") in t.addressed

    @pytest.mark.asyncio
    async def test_a_transaction_keeps_the_intruder_out(self):
        t = FakeTerminal(serialize=True)
        t.intrude(self._keepalive(t))
        async with t.transaction():
            await t.set_header(0x770)
            await t.send_uds("22BC03")
        await asyncio.gather(*t.intruder_tasks)
        assert t.addressed == [(0x770, "22BC03"), (0x7A5, "3E00")]
