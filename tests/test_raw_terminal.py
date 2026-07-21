"""Tests for RawTerminal (WiCANTerminal-compatible raw-CAN adapter), fake ISO-TP."""

import pytest

from canlib.transport import raw_terminal
from canlib.transport import slcan_tcp as slcan_mod


class FakeBus:
    def __init__(self, *a, **k):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


class FakeNotifier:
    def __init__(self, *a, **k):
        pass

    def add_listener(self, *a, **k):
        pass

    def stop(self):
        pass


class FakeStack:
    def __init__(self, txid, table):
        self.txid = txid
        self.table = table
        self._resp = None

    def start(self):
        pass

    def stop(self):
        pass

    def available(self):
        return False

    def send(self, data, *a, **k):
        self._resp = self.table.get((self.txid, bytes(data)))

    def recv(self, block=False, timeout=None):
        r, self._resp = self._resp, None
        return bytearray(r) if r is not None else None


@pytest.fixture
def make_terminal(monkeypatch):
    def build(table):
        monkeypatch.setattr(slcan_mod, "SlcanTcpBus", FakeBus)
        monkeypatch.setattr(raw_terminal.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(
            raw_terminal.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: FakeStack(address._txid, table),
        )
        monkeypatch.setattr(raw_terminal.time, "sleep", lambda *_a: None)  # skip settle
        return raw_terminal.RawTerminal("h", 3333, 500000, timeout=0.3)

    return build


class TestRawTerminalSendUds:
    @pytest.mark.asyncio
    async def test_positive_response(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE")})
        await t.set_header(0x770)
        r = await t.send_uds("22BC03")
        assert r["ok"] is True
        assert r["hex"] == "62BC03FDEE"
        assert r["bytes"] == bytes.fromhex("62BC03FDEE")
        await t.close()

    @pytest.mark.asyncio
    async def test_did_echo_validation(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("22BC03")): bytes.fromhex("62BC03FDEE")})
        await t.set_header(0x770)
        r = await t.send_uds("22BC03", expected_sid=0x22, expected_did=0xBC03)
        assert r["ok"] is True
        await t.close()

    @pytest.mark.asyncio
    async def test_negative_response_nrc(self, make_terminal):
        t = make_terminal({(0x7A0, bytes.fromhex("22B004")): bytes.fromhex("7F2213")})
        await t.set_header(0x7A0)
        r = await t.send_uds("22B004")
        assert r["ok"] is False
        assert r["nrc"] == 0x13
        await t.close()

    @pytest.mark.asyncio
    async def test_timeout_no_data(self, make_terminal):
        t = make_terminal({})  # nothing in table -> recv returns None
        await t.set_header(0x770)
        r = await t.send_uds("22BC03")
        assert r["ok"] is False
        assert "NO DATA" in r["error"]
        await t.close()

    @pytest.mark.asyncio
    async def test_set_header_switches_ecu(self, make_terminal):
        t = make_terminal(
            {
                (0x770, bytes.fromhex("2101")): bytes.fromhex("6101AA"),
                (0x7E4, bytes.fromhex("2101")): bytes.fromhex("6101BB"),
            }
        )
        await t.set_header(0x770)
        assert (await t.send_uds("2101"))["hex"] == "6101AA"
        await t.set_header(0x7E4)
        assert (await t.send_uds("2101"))["hex"] == "6101BB"
        await t.close()


class TestRawTerminalSendCommand:
    @pytest.mark.asyncio
    async def test_at_command_is_noop_ok(self, make_terminal):
        t = make_terminal({})
        await t.set_header(0x770)
        assert await t.send_command("ATSH770") == "OK"
        await t.close()

    @pytest.mark.asyncio
    async def test_uds_command_sends(self, make_terminal):
        t = make_terminal({(0x770, bytes.fromhex("3E00")): bytes.fromhex("7E00")})
        await t.set_header(0x770)
        assert await t.send_command("3E00") == "7E00"
        await t.close()


class TestRawTerminalSafety:
    @pytest.mark.asyncio
    async def test_blocked_service_raises_without_unsafe(self, make_terminal):
        t = make_terminal({})
        await t.set_header(0x770)
        with pytest.raises(ValueError):
            await t.send_uds("2E1234AA")  # 0x2E WriteDataByIdentifier is blocked
        await t.close()

    @pytest.mark.asyncio
    async def test_blocked_service_allowed_with_unsafe(self, make_terminal, monkeypatch):
        t = make_terminal({(0x770, bytes.fromhex("2E1234AA")): bytes.fromhex("6E1234")})
        t.unsafe = True
        await t.set_header(0x770)
        r = await t.send_uds("2E1234AA")
        assert r["ok"] is True
        await t.close()
