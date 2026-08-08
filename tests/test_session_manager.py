"""Tests for canlib.session_manager — SessionManager with mock terminal."""

import asyncio
import time

import pytest

from canlib.session_manager import SessionManager
from tests._fakes import FakeTerminal
from tests._fakes import ok as _ok


def MockTerminal(uds_responses=None):
    """SessionManager's terminal double: responses keyed by ``(header, cmd)``,
    positive ``50 03`` default, and ``send_command`` returning ``"7E 00"``."""
    return FakeTerminal(
        uds_responses,
        default=_ok("5003"),
        send_command_reply="7E 00",
        key_by_header=True,
    )


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
    async def test_open_session_already_active_skips_resend(self):
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.open_session(0x770)  # first entry sends 10 03
        await sm.open_session(0x770)  # already active -> must NOT resend
        assert [c for c in t.calls if c == ("send_uds", "1003")] == [("send_uds", "1003")]
        assert sm.has_session(0x770)

    @pytest.mark.asyncio
    async def test_open_session_nrc_still_tracked(self):
        """Session is tracked even if ECU responds with NRC (best-effort)."""
        t = MockTerminal(
            uds_responses={
                (0x770, "1003"): {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}
            }
        )
        sm = SessionManager(t)
        result = await sm.open_session(0x770)
        assert result is False
        assert sm.has_session(0x770)  # Still tracked

    @pytest.mark.asyncio
    async def test_open_session_error_still_tracked(self):
        t = MockTerminal(uds_responses={(0x770, "1003"): {"ok": False, "error": "NO DATA"}})
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
        # Sent as a *validated* UDS request, not a fire-and-forget send_command:
        # an unchecked keepalive silently absorbs a desynced reply.
        assert ("send_uds", "3E00") in t.calls
        assert t.uds_kwargs[-1]["expected_sid"] == 0x3E
        # Header + request must be one transaction so a concurrent poll can't
        # re-point the header between them.
        assert t.calls.index(("transaction_enter", None)) < t.calls.index(("set_header", 0x770))
        # Timestamp should be updated
        assert time.monotonic() - sm._sessions[0x770] < 1

    @pytest.mark.asyncio
    async def test_keepalive_refreshes_timestamp_even_when_the_reply_is_stale(self):
        """A mismatched reply must not make keepalive_stale re-send every cycle.

        The *request* went out, so the ECU's S3 timer was reset regardless of what
        came back; the timestamp only rate-limits re-sends. Gating it on success
        would hammer a non-answering ECU with a 3E00 on every poll.
        """
        t = MockTerminal()
        t.default = {"ok": False, "error": "Echo mismatch", "error_kind": "stale"}
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic() - 10
        await sm.send_keepalive(0x770)
        assert time.monotonic() - sm._sessions[0x770] < 1

    @pytest.mark.asyncio
    async def test_keepalive_propagates_a_failed_resync(self):
        """An unrecoverable pipe must escalate, not be swallowed as a hiccup."""

        class Wedged(FakeTerminal):
            async def send_uds(self, *a, **kw):
                raise ConnectionError("ELM327 pipe resync failed")

        sm = SessionManager(Wedged())
        with pytest.raises(ConnectionError):
            await sm.send_keepalive(0x770)

    @pytest.mark.asyncio
    async def test_keepalive_stale_only_refreshes_old(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x770] = time.monotonic()  # fresh
        sm._sessions[0x7A5] = time.monotonic() - 10  # stale
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


class TestMarkActive:
    def test_mark_active_refreshes_open_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x7E2] = time.monotonic() - 10  # would be stale
        sm.mark_active(0x7E2)
        assert time.monotonic() - sm._sessions[0x7E2] < 1

    def test_mark_active_noop_without_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.mark_active(0x7E2)  # no tracked session
        assert 0x7E2 not in sm._sessions

    @pytest.mark.asyncio
    async def test_active_poll_suppresses_redundant_keepalive(self):
        # An ECU we just polled successfully must not get a 3E00 on the next
        # keepalive sweep (the read already reset its S3 timer).
        t = MockTerminal()
        sm = SessionManager(t)
        sm._sessions[0x7E2] = time.monotonic() - 10  # stale before the poll
        sm.mark_active(0x7E2)  # simulate a successful read
        await sm.keepalive_stale(threshold=1.5)
        assert ("send_command", "3E00") not in t.calls


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
        assert any(c == ("send_uds", "3E00") for c in t.calls)

    @pytest.mark.asyncio
    async def test_double_start_cancels_old(self):
        t = MockTerminal()
        sm = SessionManager(t)
        task1 = sm.start_background_keepalive(interval=1.0)
        _task2 = sm.start_background_keepalive(interval=1.0)
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
