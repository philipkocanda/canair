"""Tests for canlib.session_manager — SessionManager with mock terminal."""

import asyncio
import time
import pytest

from canlib.session_manager import SessionManager


class MockTerminal:
    """Minimal WiCANTerminal mock that records calls."""

    def __init__(self, uds_responses=None):
        self.calls = []  # [(method, args), ...]
        self._current_header = None
        self._uds_responses = uds_responses or {}

    async def set_header(self, tx_id: int):
        self.calls.append(("set_header", tx_id))
        self._current_header = tx_id

    async def send_uds(self, cmd: str, timeout: float = 3.0) -> dict:
        self.calls.append(("send_uds", cmd))
        key = (self._current_header, cmd)
        if key in self._uds_responses:
            return self._uds_responses[key]
        # Default: positive response
        return {"ok": True, "hex": "5003", "bytes": b"\x50\x03", "raw": "50 03"}

    async def send_command(self, cmd: str, timeout: float = 3.0) -> str:
        self.calls.append(("send_command", cmd))
        return "7E 00"


# --- open_session ---

class TestOpenSession:
    @pytest.mark.asyncio
    async def test_open_session_success(self):
        t = MockTerminal()
        sm = SessionManager(t)
        result = await sm.open_session(0x770)
        assert result is True
        assert sm.has_session(0x770)
        assert 0x770 in sm.active_sessions
        # Should have set header then sent 1003
        assert ("set_header", 0x770) in t.calls
        assert ("send_uds", "1003") in t.calls

    @pytest.mark.asyncio
    async def test_open_session_with_wake(self):
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.open_session(0x7A5, wake=True)
        # Should send 1001 before 1003
        uds_calls = [c for c in t.calls if c[0] == "send_uds"]
        assert uds_calls[0] == ("send_uds", "1001")
        assert uds_calls[1] == ("send_uds", "1003")

    @pytest.mark.asyncio
    async def test_open_session_nrc_still_tracked(self):
        """Session is tracked even if ECU responds with NRC (best-effort)."""
        t = MockTerminal(uds_responses={
            (0x770, "1003"): {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}
        })
        sm = SessionManager(t)
        result = await sm.open_session(0x770)
        assert result is False
        assert sm.has_session(0x770)  # Still tracked

    @pytest.mark.asyncio
    async def test_open_session_error_still_tracked(self):
        t = MockTerminal(uds_responses={
            (0x770, "1003"): {"ok": False, "error": "NO DATA"}
        })
        sm = SessionManager(t)
        result = await sm.open_session(0x770)
        assert result is False
        assert sm.has_session(0x770)


# --- keepalive ---

class TestKeepalive:
    @pytest.mark.asyncio
    async def test_send_keepalive(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic() - 10  # stale
        await sm.send_keepalive(0x770)
        assert ("set_header", 0x770) in t.calls
        assert ("send_command", "3E00") in t.calls
        # Timestamp should be updated
        assert time.monotonic() - sm._sessions[0x770] < 1

    @pytest.mark.asyncio
    async def test_keepalive_stale_only_refreshes_old(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic()          # fresh
        sm._sessions[0x7A5] = time.monotonic() - 10     # stale
        await sm.keepalive_stale(threshold=1.5)
        # Only 7A5 should have been refreshed
        header_calls = [c[1] for c in t.calls if c[0] == "set_header"]
        assert 0x7A5 in header_calls
        assert 0x770 not in header_calls

    @pytest.mark.asyncio
    async def test_keepalive_all_refreshes_everything(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic()
        sm._sessions[0x7A5] = time.monotonic()
        await sm.keepalive_all()
        header_calls = [c[1] for c in t.calls if c[0] == "set_header"]
        assert 0x770 in header_calls
        assert 0x7A5 in header_calls


# --- background keepalive ---

class TestBackgroundKeepalive:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic() - 10
        task = sm.start_background_keepalive(interval=0.05)
        assert not task.done()
        await asyncio.sleep(0.15)  # Let it tick a few times
        sm.stop_background_keepalive()
        assert sm._bg_task is None
        # Should have sent at least one keepalive
        assert any(c == ("send_command", "3E00") for c in t.calls)

    @pytest.mark.asyncio
    async def test_double_start_cancels_old(self):
        t = MockTerminal()
        sm = SessionManager(t)
        task1 = sm.start_background_keepalive(interval=1.0)
        task2 = sm.start_background_keepalive(interval=1.0)
        await asyncio.sleep(0)  # Let cancellation propagate
        assert task1.done() or task1.cancelled()
        sm.stop_background_keepalive()


# --- close ---

class TestCloseSession:
    @pytest.mark.asyncio
    async def test_close_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic()
        await sm.close_session(0x770)
        assert not sm.has_session(0x770)
        assert ("send_command", "1001") in t.calls

    @pytest.mark.asyncio
    async def test_close_nonexistent_is_noop(self):
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.close_session(0x770)  # not tracked
        assert len(t.calls) == 0

    @pytest.mark.asyncio
    async def test_close_all(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic()
        sm._sessions[0x7A5] = time.monotonic()
        sm.start_background_keepalive(interval=1.0)
        await sm.close_all()
        assert len(sm.active_sessions) == 0
        assert sm._bg_task is None

    @pytest.mark.asyncio
    async def test_active_sessions_property(self):
        t = MockTerminal()
        sm = SessionManager(t)
        assert sm.active_sessions == []
        sm._sessions[0x770] = time.monotonic()
        sm._sessions[0x7A0] = time.monotonic()
        assert set(sm.active_sessions) == {0x770, 0x7A0}
