"""Tests for canair.transport.session_manager — SessionManager with mock terminal."""

import asyncio
import time

import pytest

from canair.transport.session_manager import SessionManager
from tests.support.fakes import FakeTerminal, nrc
from tests.support.fakes import ok as _ok


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
        assert result.ok is True
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
        assert result.ok is False
        assert result.nrc == 0x12
        assert sm.has_session(0x770)  # Still tracked

    @pytest.mark.asyncio
    async def test_open_session_error_still_tracked(self):
        t = MockTerminal(uds_responses={(0x770, "1003"): {"ok": False, "error": "NO DATA"}})
        sm = SessionManager(t)
        result = await sm.open_session(0x770)
        assert result.ok is False
        assert result.error == "NO DATA"
        assert sm.has_session(0x770)


# --- session_mode / force-switching ---


class TestSessionMode:
    @pytest.mark.asyncio
    async def test_session_mode_tracked_after_open(self):
        t = MockTerminal()
        sm = SessionManager(t)
        assert sm.session_mode(0x770) is None
        await sm.open_session(0x770, mode="81")
        assert sm.session_mode(0x770) == 0x81
        assert ("send_uds", "1081") in t.calls

    @pytest.mark.asyncio
    async def test_open_session_already_active_mode_switch_needs_force(self):
        """Without force, a second open_session on an already-open session is a
        pure refresh (no resend) even when a *different* mode is requested —
        so the tracked mode does NOT change; this is the bug the picker's
        force=True path exists to bypass."""
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.open_session(0x770, mode="03")
        await sm.open_session(0x770, mode="81")  # no wake, no force -> refresh-only
        assert sm.session_mode(0x770) == 0x03
        assert [c for c in t.calls if c[0] == "send_uds"] == [("send_uds", "1003")]

    @pytest.mark.asyncio
    async def test_open_session_force_resends_and_updates_mode(self):
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.open_session(0x770, mode="03")
        result = await sm.open_session(0x770, mode="81", force=True)
        assert result.ok is True
        assert sm.session_mode(0x770) == 0x81
        assert [c for c in t.calls if c[0] == "send_uds"] == [
            ("send_uds", "1003"),
            ("send_uds", "1081"),
        ]

    @pytest.mark.asyncio
    async def test_close_session_clears_mode(self):
        t = MockTerminal()
        sm = SessionManager(t)
        await sm.open_session(0x770, mode="81")
        assert sm.session_mode(0x770) == 0x81
        await sm.close_session(0x770)
        assert sm.session_mode(0x770) is None


# --- keepalive ---


class TestKeepalive:
    @pytest.mark.asyncio
    async def test_send_keepalive(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x770, at=time.monotonic() - 10)  # stale
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
        assert time.monotonic() - sm.table.refreshed_at(0x770) < 1

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
        sm.table.track(0x770, at=time.monotonic() - 10)
        await sm.send_keepalive(0x770)
        assert time.monotonic() - sm.table.refreshed_at(0x770) < 1

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
        sm.table.track(0x770, at=time.monotonic())  # fresh
        sm.table.track(0x7A5, at=time.monotonic() - 10)  # stale
        await sm.keepalive_stale(threshold=1.5)
        # Only 7A5 should have been refreshed
        header_calls = [c[1] for c in t.calls if c[0] == "set_header"]
        assert 0x7A5 in header_calls
        assert 0x770 not in header_calls

    @pytest.mark.asyncio
    async def test_keepalive_all_refreshes_everything(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x770, at=time.monotonic())
        sm.table.track(0x7A5, at=time.monotonic())
        await sm.keepalive_all()
        header_calls = [c[1] for c in t.calls if c[0] == "set_header"]
        assert 0x770 in header_calls
        assert 0x7A5 in header_calls


class TestMarkActive:
    def test_mark_active_refreshes_open_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x7E2, at=time.monotonic() - 10)  # would be stale
        sm.mark_active(0x7E2)
        assert time.monotonic() - sm.table.refreshed_at(0x7E2) < 1

    def test_mark_active_noop_without_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.mark_active(0x7E2)  # no tracked session
        assert not sm.has_session(0x7E2)

    @pytest.mark.asyncio
    async def test_active_poll_suppresses_redundant_keepalive(self):
        # An ECU we just polled successfully must not get a 3E00 on the next
        # keepalive sweep (the read already reset its S3 timer).
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x7E2, at=time.monotonic() - 10)  # stale before the poll
        sm.mark_active(0x7E2)  # simulate a successful read
        await sm.keepalive_stale(threshold=1.5)
        assert ("send_uds", "3E00") not in t.calls


# --- background keepalive ---


class TestBackgroundKeepalive:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x770, at=time.monotonic() - 10)
        task = sm.start_background_keepalive(interval=0.05)
        assert not task.done()
        await asyncio.sleep(0.15)  # Let it tick a few times
        await sm.stop_background_keepalive()
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
        await sm.stop_background_keepalive()


# --- close ---


class TestCloseSession:
    @pytest.mark.asyncio
    async def test_close_session(self):
        t = MockTerminal()
        sm = SessionManager(t)
        sm.table.track(0x770, at=time.monotonic())
        await sm.close_session(0x770)
        assert not sm.has_session(0x770)
        # Validated like every other request, and in one transaction with its header.
        assert ("send_uds", "1001") in t.calls
        assert t.uds_kwargs[-1]["expected_sid"] == 0x10
        assert t.calls.index(("transaction_enter", None)) < t.calls.index(("set_header", 0x770))

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
        sm.table.track(0x770, at=time.monotonic())
        sm.table.track(0x7A5, at=time.monotonic())
        sm.start_background_keepalive(interval=1.0)
        await sm.close_all()
        assert len(sm.active_sessions) == 0
        assert sm._bg_task is None

    @pytest.mark.asyncio
    async def test_active_sessions_property(self):
        t = MockTerminal()
        sm = SessionManager(t)
        assert sm.active_sessions == []
        sm.table.track(0x770, at=time.monotonic())
        sm.table.track(0x7A0, at=time.monotonic())
        assert set(sm.active_sessions) == {0x770, 0x7A0}


# --- atomicity: a background keepalive never re-points an open, a wake or a close ---


def _keepalive_intruder(t, sm, tx_id=0x7A5):
    async def run():
        await sm.send_keepalive(tx_id)

    return run


class TestAtomic:
    @pytest.mark.asyncio
    async def test_open_session_cannot_be_re_pointed(self):
        t = FakeTerminal(default=_ok("5003"), serialize=True)
        sm = SessionManager(t)
        t.intrude(_keepalive_intruder(t, sm))
        await sm.open_session(0x770)
        await asyncio.gather(*t.intruder_tasks)
        assert (0x770, "1003") in t.addressed
        assert (0x7A5, "1003") not in t.addressed

    @pytest.mark.asyncio
    async def test_a_wake_burst_cannot_be_re_pointed(self):
        from canair.ecus.wake import WakePlan

        t = FakeTerminal(default=_ok("5003"), send_command_reply="NO DATA", serialize=True)
        sm = SessionManager(t)
        t.intrude(_keepalive_intruder(t, sm))
        plan = WakePlan("rapid_read", "22B003", attempts=3, interval_ms=0, session_mode="03")
        await sm.open_session(0x770, wake=True, wake_plan=plan)
        await asyncio.gather(*t.intruder_tasks)
        burst = [h for h, req in t.addressed if req in ("22B003", "1003")]
        assert burst == [0x770] * 4

    @pytest.mark.asyncio
    async def test_close_session_cannot_be_re_pointed(self):
        t = FakeTerminal(default=_ok("5001"), serialize=True)
        sm = SessionManager(t)
        sm.track(0x770)
        t.intrude(_keepalive_intruder(t, sm))
        await sm.close_session(0x770)
        await asyncio.gather(*t.intruder_tasks)
        assert (0x770, "1001") in t.addressed


class TestModeTracking:
    @pytest.mark.asyncio
    async def test_a_refused_session_records_no_mode(self):
        t = MockTerminal({(0x770, "1081"): {"ok": False, "nrc": 0x12, "nrc_desc": "SFNS"}})
        sm = SessionManager(t)
        assert (await sm.open_session(0x770, mode="81")).ok is False
        assert sm.has_session(0x770)
        assert sm.session_mode(0x770) is None

    @pytest.mark.asyncio
    async def test_a_refused_switch_keeps_the_mode_the_ecu_is_in(self):
        t = MockTerminal({(0x770, "1081"): {"ok": False, "nrc": 0x12, "nrc_desc": "SFNS"}})
        sm = SessionManager(t)
        await sm.open_session(0x770, mode="03")
        await sm.open_session(0x770, mode="81", force=True)
        assert sm.session_mode(0x770) == 0x03

    def test_track_registers_a_session_opened_elsewhere(self):
        sm = SessionManager(MockTerminal())
        sm.track(0x7A5, "03")
        assert sm.has_session(0x7A5) and sm.session_mode(0x7A5) == 0x03


class TestLinkError:
    @pytest.mark.asyncio
    async def test_a_dead_link_ends_the_background_keepalive_and_is_kept(self):
        class Dead(FakeTerminal):
            async def send_uds(self, *a, **kw):
                raise ConnectionError("pipe resync failed")

        sm = SessionManager(Dead())
        sm.table.track(0x770, at=0.0)
        task = sm.start_background_keepalive(interval=0.01)
        await asyncio.wait_for(asyncio.wait({task}), 1.0)
        assert task.exception() is None
        assert isinstance(sm.link_error, ConnectionError)
        # The next foreground request surfaces it, which is where the monitor reconnects.
        with pytest.raises(ConnectionError):
            await sm.keepalive_stale()

    @pytest.mark.asyncio
    async def test_stop_waits_until_the_task_has_ended(self):
        sm = SessionManager(MockTerminal())
        task = sm.start_background_keepalive(interval=1.0)
        await sm.stop_background_keepalive()
        assert task.done()
        await sm.stop_background_keepalive()  # a second stop is harmless


class TestReporting:
    @pytest.mark.asyncio
    async def test_a_refusal_is_one_warning(self, capsys):
        t = MockTerminal({(0x770, "1081"): {"ok": False, "nrc": 0x12, "nrc_desc": "SFNS"}})
        outcome = await SessionManager(t).open_session(0x770, mode="81")
        err = capsys.readouterr().err
        assert "warning: 0x770 refused session 10 81: NRC 0x12 (SFNS)" in err
        assert outcome.problem() == "0x770 refused session 10 81: NRC 0x12 (SFNS)"

    @pytest.mark.asyncio
    async def test_report_false_leaves_it_to_the_caller(self, capsys):
        t = MockTerminal({(0x770, "1003"): {"ok": False, "error": "NO DATA"}})
        outcome = await SessionManager(t).open_session(0x770, report=False)
        assert capsys.readouterr().err == ""
        assert outcome.problem() == "no answer from 0x770 to session 10 03 (NO DATA)"


class TestOneRitual:
    """The ritual every command shares (plans/2026-11-05-one-session-owner.md, phase 4)."""

    @pytest.mark.asyncio
    async def test_a_default_wake_runs_with_the_short_ecu_wait_and_asks_twice(self, monkeypatch):
        slept: list[float] = []

        async def no_sleep(s):
            slept.append(s)

        t = FakeTerminal({"1003": _ok("5003")}, session_ok=False)  # 10 01 draws NO DATA
        sm = SessionManager(t)
        monkeypatch.setattr("canair.transport.session_manager.asyncio.sleep", no_sleep)
        await sm.open_session(0x7A5, wake=True)
        calls = [c for c in t.calls if c[0] in ("short_ecu_wait", "send_uds")]
        assert calls == [
            ("short_ecu_wait", None),
            ("send_uds", "1001"),
            ("send_uds", "1001"),
            ("send_uds", "1003"),
        ]
        assert t.uds_kwargs[0] == {
            "timeout": 3.0,
            "expected_sid": 0x10,
            "expected_echo": None,
            "retries": 0,
        }
        assert slept == []  # no fixed pause after the wake

    @pytest.mark.asyncio
    async def test_a_wake_ritual_runs_with_the_short_ecu_wait(self):
        from canair.ecus.wake import WakePlan

        t = FakeTerminal(send_command_reply="62 B0 03 00")
        plan = WakePlan("rapid_read", "22B003", attempts=4, interval_ms=0, session_mode="03")
        await SessionManager(t).open_session(0x7A5, wake=True, wake_plan=plan)
        names = [c[0] for c in t.calls]
        assert names.index("short_ecu_wait") < names.index("send_command")

    @pytest.mark.asyncio
    async def test_a_session_request_with_no_answer_is_asked_once_more(self):
        t = FakeTerminal(session_ok=False)
        outcome = await SessionManager(t).open_session(0x770, report=False)
        assert t.sent == ["1003", "1003"]
        assert outcome.ok is False

    @pytest.mark.asyncio
    async def test_a_refusal_is_not_asked_again(self):
        t = FakeTerminal({"1003": nrc(0x12, "SFNS")})
        await SessionManager(t).open_session(0x770, report=False)
        assert t.sent == ["1003"]

    @pytest.mark.asyncio
    async def test_held_opens_keeps_alive_and_lets_go(self):
        t = FakeTerminal()
        sm = SessionManager(t)
        async with sm.held(0x7E4, mode="81") as outcome:
            assert outcome.ok and sm.keepalive_running
            assert sm.session_mode(0x7E4) == 0x81
        assert not sm.keepalive_running
        assert not sm.has_session(0x7E4)
        assert t.sent == ["1081"]  # left to time out: no 10 01

    @pytest.mark.asyncio
    async def test_held_with_close_returns_to_the_default_session(self):
        t = FakeTerminal()
        async with SessionManager(t).held(0x7E4, close=True):
            pass
        assert t.sent == ["1003", "1001"]

    @pytest.mark.asyncio
    async def test_no_keepalive_while_the_ecu_keeps_answering(self):
        t = FakeTerminal({"3E00": _ok("7E00")})
        sm = SessionManager(t)
        sm.table.track(0x770, at=time.monotonic() - 10)  # stale by its own clock
        t.diag.answered[0x770] = time.monotonic()  # but a scan probe was just answered
        await sm.keepalive_stale()
        assert "3E00" not in t.sent
        t.diag.answered[0x770] = time.monotonic() - 10
        sm.table.track(0x770, at=time.monotonic() - 10)
        await sm.keepalive_stale()
        assert t.sent == ["3E00"]
