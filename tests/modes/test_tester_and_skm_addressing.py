"""Tests for the modes that address the ELM327 directly: TesterPresent and SKM wake.

Both used to write ``ATSH``/``ATFCSH`` themselves, behind the addressing cache's
back; they now go through ``set_header``, so a receive filter pinned for an earlier
ECU is restored and the cache stays coherent.
"""

import asyncio
import contextlib

import pytest

from canlib.modes import skm_wakeup
from canlib.modes.tester import mode_tester_present, parse_target


class RecordingTerminal:
    """The slice of the ELM327 terminal these modes use, recording every call."""

    elm_timeout_cmd = "ATST96"

    def __init__(self, replies: dict[str, str] | None = None, stop_after: int | None = None):
        self.calls: list[tuple] = []
        self._replies = replies or {}
        self._stop_after = stop_after
        self._sent = 0

    @contextlib.asynccontextmanager
    async def transaction(self):
        self.calls.append(("transaction",))
        yield

    async def set_header(self, tx_id: int) -> None:
        self.calls.append(("set_header", tx_id))

    async def send_command(self, cmd: str, timeout: float | None = None) -> str:
        self._sent += 1
        if self._stop_after is not None and self._sent > self._stop_after:
            raise asyncio.CancelledError
        self.calls.append(("send_command", cmd))
        return self._replies.get(cmd, "OK")

    async def drain(self) -> None:
        self.calls.append(("drain",))

    async def recv_frame(self, timeout: float) -> str | None:
        return None


class TestParseTarget:
    @pytest.mark.parametrize(
        ("text", "tx_id"),
        [(None, 0x7DF), ("", 0x7DF), ("7e0", 0x7E0), ("0x7E0", 0x7E0), ("18DA10F1", 0x18DA10F1)],
    )
    def test_valid(self, text, tx_id):
        assert parse_target(text) == tx_id

    @pytest.mark.parametrize("text", ["ZZ", "7E0X", "-1", "0x", "1FFFFFFFF"])
    def test_invalid(self, text):
        assert parse_target(text) is None


class TestTesterPresent:
    @pytest.mark.asyncio
    async def test_broadcast_goes_through_set_header(self, capsys):
        term = RecordingTerminal({"3E00": "7E00"}, stop_after=2)
        await mode_tester_present(term, None, 0, verbose=False)
        assert ("set_header", 0x7DF) in term.calls
        assert not any(c[0] == "send_command" and c[1].startswith("AT") for c in term.calls)
        assert "Stopped after" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_target_header_and_request_share_a_transaction(self):
        term = RecordingTerminal(stop_after=1)
        await mode_tester_present(term, "0x7e0", 0, verbose=True)
        assert term.calls[:3] == [
            ("transaction",),
            ("set_header", 0x7E0),
            ("send_command", "3E00"),
        ]

    @pytest.mark.asyncio
    async def test_a_bad_target_is_a_clean_error(self, capsys):
        term = RecordingTerminal()
        await mode_tester_present(term, "ZZ", 0, verbose=False)
        assert term.calls == []
        assert "Invalid target 'ZZ'" in capsys.readouterr().out


class TestSkmWakeAddressing:
    @pytest.mark.asyncio
    async def test_wake_and_read_addresses_through_set_header(self):
        term = RecordingTerminal({"1003": "5003", "22BC03": "62BC03AABB"})
        result = await skm_wakeup.wake_and_read(term, "770", "22BC03")
        assert result == {"ok": True, "response": "62BC03AABB"}
        assert term.calls[:2] == [("transaction",), ("set_header", 0x770)]
        assert [c[1] for c in term.calls if c[0] == "send_command"] == ["1001", "1003", "22BC03"]

    @pytest.mark.asyncio
    async def test_wake_and_read_reports_a_failed_session(self):
        term = RecordingTerminal({"1003": "7F1022"})
        result = await skm_wakeup.wake_and_read(term, "7A0", "22B003")
        assert result["ok"] is False
        assert ("set_header", 0x7A0) in term.calls

    @pytest.mark.asyncio
    async def test_mode_skm_wakeup_never_writes_a_header_itself(self, monkeypatch):
        monkeypatch.setattr(skm_wakeup.asyncio, "sleep", _no_sleep)
        term = RecordingTerminal({"1001": "5001", "1003": "5003", "2FB10803" + "0A0A05": "6FB108"})
        await skm_wakeup.mode_skm_wakeup(term, "acc", verbose=False)
        headers = [c[1] for c in term.calls if c[0] == "set_header"]
        assert headers == [0x7A5, 0x770, 0x7A0]
        sent = [c[1] for c in term.calls if c[0] == "send_command"]
        assert not any(cmd.startswith(("ATSH", "ATFCSH", "ATCRA")) for cmd in sent)


async def _no_sleep(_seconds: float) -> None:
    return None
