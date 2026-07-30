"""Tests for canlib.wake (per-ECU wake ritual resolver) + SessionManager wiring."""

import pytest

from canlib.session_manager import SessionManager
from canlib.wake import (
    DEFAULT_ATTEMPTS,
    DEFAULT_INTERVAL_MS,
    DEFAULT_PRIME,
    WakePlan,
    resolve_wake,
)
from tests._fakes import FakeTerminal
from tests._fakes import ok as _ok

# ── resolve_wake ─────────────────────────────────────────────────────────────


class TestResolveWake:
    def test_none_when_no_block(self):
        assert resolve_wake({"tx_id": 0x7A5}) is None
        assert resolve_wake({}) is None
        assert resolve_wake(None) is None

    def test_non_mapping_wake_ignored(self):
        assert resolve_wake({"wake": "rapid_read"}) is None

    def test_full_block(self):
        plan = resolve_wake(
            {
                "wake": {
                    "method": "rapid_read",
                    "prime_pid": "22B003",
                    "attempts": 8,
                    "interval_ms": 80,
                    "sleep_timer_ms": 2000,
                    "session_mode": "03",
                }
            }
        )
        assert isinstance(plan, WakePlan)
        assert plan.method == "rapid_read"
        assert plan.prime == "22B003"
        assert plan.attempts == 8
        assert plan.interval_ms == 80
        assert plan.sleep_timer_ms == 2000
        assert plan.session_mode == "03"
        assert plan.interval_s == pytest.approx(0.08)

    def test_defaults_filled(self):
        plan = resolve_wake({"wake": {"method": "rapid_read"}})
        assert plan is not None
        assert plan.prime == DEFAULT_PRIME
        assert plan.attempts == DEFAULT_ATTEMPTS
        assert plan.interval_ms == DEFAULT_INTERVAL_MS
        assert plan.sleep_timer_ms is None

    def test_bare_did_not_auto_prefixed(self):
        """A 4-hex value is a valid standalone request (1001/3E00), so we do NOT
        guess a service-22 prefix — the author writes the service byte."""
        plan = resolve_wake({"wake": {"method": "rapid_read", "prime_pid": "3E00"}})
        assert plan is not None
        assert plan.prime == "3E00"

    def test_prime_normalized(self):
        plan = resolve_wake({"wake": {"method": "rapid_read", "prime_pid": "22 b0 03"}})
        assert plan is not None
        assert plan.prime == "22B003"

    def test_tolerant_of_bad_ints(self):
        """A malformed numeric field falls back to the default, not a crash."""
        plan = resolve_wake(
            {"wake": {"method": "rapid_read", "attempts": "lots", "interval_ms": None}}
        )
        assert plan is not None
        assert plan.attempts == DEFAULT_ATTEMPTS
        assert plan.interval_ms == DEFAULT_INTERVAL_MS

    def test_attempts_floored_at_one(self):
        plan = resolve_wake({"wake": {"method": "rapid_read", "attempts": 0}})
        assert plan is not None
        assert plan.attempts == 1


# ── SessionManager.rapid_read_wake ───────────────────────────────────────────


class TestRapidReadWake:
    @pytest.mark.asyncio
    async def test_fires_prime_and_stops_when_awake(self):
        # First prime answers positively -> stop after one attempt.
        t = FakeTerminal(send_command_reply="62 B0 03 00")
        sm = SessionManager(t)
        plan = WakePlan("rapid_read", "22B003", attempts=8, interval_ms=0, session_mode="03")
        awake = await sm.rapid_read_wake(0x7A5, plan)
        assert awake is True
        primes = [c for c in t.calls if c == ("send_command", "22B003")]
        assert len(primes) == 1  # stopped on first success
        assert ("set_header", 0x7A5) in t.calls

    @pytest.mark.asyncio
    async def test_all_attempts_when_no_data(self):
        t = FakeTerminal(send_command_reply="NO DATA")
        sm = SessionManager(t)
        plan = WakePlan("rapid_read", "22B003", attempts=4, interval_ms=0, session_mode="03")
        awake = await sm.rapid_read_wake(0x7A5, plan)
        assert awake is False
        primes = [c for c in t.calls if c == ("send_command", "22B003")]
        assert len(primes) == 4  # exhausted all attempts

    @pytest.mark.asyncio
    async def test_open_session_uses_plan_rapid_fire(self):
        """open_session(wake=True, wake_plan=...) fires the rapid-fire prime loop
        instead of a single 1001."""
        t = FakeTerminal(
            responses={"1003": _ok("5003")},
            send_command_reply="62 B0 03 00",
        )
        sm = SessionManager(t)
        plan = WakePlan("rapid_read", "22B003", attempts=8, interval_ms=0, session_mode="03")
        await sm.open_session(0x7A5, wake=True, wake_plan=plan)
        # The prime (send_command) fired; no single 1001 send_uds wake.
        assert ("send_command", "22B003") in t.calls
        assert ("send_uds", "1001") not in t.calls
        assert ("send_uds", "1003") in t.calls

    @pytest.mark.asyncio
    async def test_plan_session_mode_overrides_default(self):
        t = FakeTerminal(send_command_reply="62 B0 03 00")
        sm = SessionManager(t)
        plan = WakePlan("rapid_read", "22B003", attempts=2, interval_ms=0, session_mode="81")
        await sm.open_session(0x7A5, wake=True, wake_plan=plan)
        assert ("send_uds", "1081") in t.calls
