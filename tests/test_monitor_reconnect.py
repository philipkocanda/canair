"""Tests for mid-session reconnect / auto-failover and the ``--wait`` primitive.

Device-free: fake transport candidates + a fake reachability probe + a minimal
fake controller exercise the reconnect logic without any live WiCAN.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from canlib.modes.monitor_reconnect import (
    MonitorReconnector,
    ReconnectPolicy,
    reconnect_policy,
)
from canlib.transport import fallback
from canlib.transport.config import TransportConfig


def _raw(host: str) -> TransportConfig:
    return TransportConfig(type="slcan-tcp", host=host, port=3333, bitrate=500000)


def _ws(host: str) -> TransportConfig:
    return TransportConfig(type="wican-ws", host=host, port=None, bitrate=500000)


# ---------------------------------------------------------------------------
# wait_for_reachable
# ---------------------------------------------------------------------------


class TestWaitForReachable:
    def test_returns_first_reachable_now(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda host, port, timeout: host == "up")
        cand = fallback.wait_for_reachable(
            [_raw("down"), _raw("up")], connect_timeout=0.01, poll_interval=0.01
        )
        assert cand is not None and cand.host == "up"

    def test_waits_until_device_appears(self, monkeypatch):
        calls = {"n": 0}

        def probe(host, port, timeout):
            calls["n"] += 1
            return calls["n"] >= 3  # unreachable for the first two rounds

        monkeypatch.setattr("canlib.wican_mode._tcp_open", probe)
        notes: list[str] = []
        cand = fallback.wait_for_reachable(
            [_raw("dev")],
            connect_timeout=0.001,
            poll_interval=0.01,
            notice=notes.append,
        )
        assert cand is not None and cand.host == "dev"
        assert calls["n"] >= 3
        # A single "waiting…" notice is emitted while unreachable.
        assert notes and "waiting" in notes[0].lower()

    def test_deadline_gives_up(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: False)
        start = time.monotonic()
        cand = fallback.wait_for_reachable(
            [_raw("dev")],
            connect_timeout=0.001,
            poll_interval=0.02,
            deadline=time.monotonic() + 0.05,
        )
        assert cand is None
        assert time.monotonic() - start < 1.0  # bounded, didn't hang

    def test_stop_aborts(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: False)
        cand = fallback.wait_for_reachable(
            [_raw("dev")],
            connect_timeout=0.001,
            poll_interval=0.02,
            stop=lambda: True,  # already asked to stop
        )
        assert cand is None


# ---------------------------------------------------------------------------
# ReconnectPolicy / reconnect_policy
# ---------------------------------------------------------------------------


class TestReconnectPolicy:
    def test_forever_has_no_deadline(self):
        p = ReconnectPolicy(forever=True, max_wait=6.0, connect_timeout=2.0)
        assert p.deadline_from(100.0) is None

    def test_bounded_has_deadline(self):
        p = ReconnectPolicy(forever=False, max_wait=6.0, connect_timeout=2.0)
        assert p.deadline_from(100.0) == 106.0

    def test_from_args_wait_is_forever(self, monkeypatch):
        monkeypatch.setattr(
            "canlib.config.fallback_settings", lambda: (True, 2.0, None), raising=True
        )
        monkeypatch.setattr("canlib.config.reconnect_max_wait", lambda: 9.0, raising=True)

        class Args:
            wait = True

        p = reconnect_policy(Args())
        assert p.forever is True
        assert p.max_wait == 9.0
        assert p.connect_timeout == 2.0

    def test_from_args_default_is_bounded(self, monkeypatch):
        monkeypatch.setattr(
            "canlib.config.fallback_settings", lambda: (True, 2.0, None), raising=True
        )
        monkeypatch.setattr("canlib.config.reconnect_max_wait", lambda: 6.0, raising=True)

        class Args:
            wait = False

        assert reconnect_policy(Args()).forever is False


# ---------------------------------------------------------------------------
# MonitorReconnector
# ---------------------------------------------------------------------------


class FakeController:
    """Minimal controller stand-in exercising the reconnector's contract."""

    def __init__(self) -> None:
        self.disconnected = True
        self.closed = 0
        self.rebound: list[object] = []
        self.setup_calls: list[object] = []

    async def close_client(self) -> None:
        self.closed += 1

    def rebind(self, client) -> None:
        self.rebound.append(client)
        self.disconnected = False

    async def setup(self, session_steps) -> None:
        self.setup_calls.append(session_steps)


def _reconnector(candidates, connect, *, forever=False, max_wait=0.2):
    policy = ReconnectPolicy(
        forever=forever, max_wait=max_wait, connect_timeout=0.001, poll_interval=0.01
    )
    return MonitorReconnector(candidates, connect, policy)


def _run_bounded(reconnector, *args, timeout=5.0, **kwargs):
    """Run a reconnector, failing loudly if it never returns.

    A bounded reconnect that ignores its deadline loops forever, which would
    otherwise *hang* the suite (and CI) instead of failing. Wrapping it in a
    timeout converts that regression into a clean, fast assertion failure.
    """

    async def drive():
        try:
            return await asyncio.wait_for(reconnector(*args, **kwargs), timeout=timeout)
        except TimeoutError:
            raise AssertionError(
                f"reconnector did not return within {timeout}s — its retry budget "
                "is not being honoured (unbounded retry loop)"
            ) from None

    return asyncio.run(drive())


class TestMonitorReconnector:
    def test_resumes_on_reachable(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)

        async def connect(cand):
            return f"client@{cand.host}"

        ctl = FakeController()
        r = _reconnector([_raw("dev")], connect)
        ok = asyncio.run(r(ctl, [{"type": "session"}]))
        assert ok is True
        assert ctl.closed == 1  # dead socket freed first
        assert ctl.rebound == ["client@dev"]
        assert ctl.setup_calls == [[{"type": "session"}]]  # session steps replayed
        assert ctl.disconnected is False

    def test_bounded_gives_up_when_unreachable(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: False)

        async def connect(cand):  # never called
            raise AssertionError("should not connect")

        ctl = FakeController()
        r = _reconnector([_raw("dev")], connect, forever=False, max_wait=0.05)
        ok = asyncio.run(r(ctl, None))
        assert ok is False
        assert ctl.rebound == []

    def test_no_candidates_gives_up(self):
        async def connect(cand):
            raise AssertionError

        ok = asyncio.run(_reconnector([], connect)(FakeController(), None))
        assert ok is False

    def test_retries_when_connect_fails_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)
        attempts = {"n": 0}

        async def connect(cand):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("boom")  # a transport error the first time
            return "client"

        ctl = FakeController()
        # forever so the deadline never expires between the two attempts
        r = _reconnector([_raw("dev")], connect, forever=True)
        ok = asyncio.run(r(ctl, None))
        assert ok is True
        assert attempts["n"] == 2
        assert ctl.rebound == ["client"]

    def test_stop_during_wait_gives_up(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: False)

        async def connect(cand):
            raise AssertionError

        ctl = FakeController()
        r = _reconnector([_raw("dev")], connect, forever=True)
        ok = asyncio.run(r(ctl, None, stop=lambda: True))
        assert ok is False

    # -- retry budget when the probe answers but the connect doesn't ---------
    # Regression: `wait_for_reachable` returns a candidate as soon as its probe
    # port answers and only consults the deadline when *nothing* is reachable. So
    # a device whose probe port is up but whose data port refuses (a WiCAN that
    # rebooted into auto_pid: port 80 answers, the SLCAN port doesn't) sent the
    # retry loop spinning forever with no sleep — ignoring
    # `transport.reconnect_max_wait` entirely and issuing tens of thousands of
    # connects per second. The pre-existing retry test uses forever=True, which
    # cannot observe a deadline, so nothing covered this.

    def test_bounded_gives_up_when_probe_answers_but_connect_always_fails(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)
        attempts = {"n": 0}

        async def connect(cand):
            attempts["n"] += 1
            raise ConnectionError("data port refused")

        ctl = FakeController()
        r = _reconnector([_raw("dev")], connect, forever=False, max_wait=0.2)
        ok = _run_bounded(r, ctl, None)
        assert ok is False, "a bounded reconnect must give up once max_wait is spent"
        assert ctl.rebound == []
        # Paced by poll_interval (0.01s here), not a hot loop. The unfixed code
        # managed tens of thousands of attempts in this window.
        assert attempts["n"] < 200, f"hot-spinning: {attempts['n']} connect attempts"

    def test_bounded_reconnect_respects_max_wait_duration(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)

        async def connect(cand):
            raise ConnectionError("nope")

        r = _reconnector([_raw("dev")], connect, forever=False, max_wait=0.3)
        started = time.monotonic()
        ok = _run_bounded(r, FakeController(), None)
        elapsed = time.monotonic() - started
        assert ok is False
        # Bounded by max_wait (+ scheduling slack), and it must actually return.
        assert elapsed < 3.0, f"took {elapsed:.2f}s for a 0.3s budget"

    def test_zero_budget_attempts_once_then_gives_up(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)
        attempts = {"n": 0}

        async def connect(cand):
            attempts["n"] += 1
            raise ConnectionError("nope")

        r = _reconnector([_raw("dev")], connect, forever=False, max_wait=0.0)
        assert _run_bounded(r, FakeController(), None) is False
        assert attempts["n"] == 1

    def test_forever_keeps_retrying_a_failing_connect_but_paces_itself(self, monkeypatch):
        """`--wait` must retry indefinitely — without busy-looping."""
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)
        attempts = {"n": 0}

        async def connect(cand):
            attempts["n"] += 1
            raise ConnectionError("nope")

        async def drive():
            r = _reconnector([_raw("dev")], connect, forever=True)
            task = asyncio.create_task(r(FakeController(), None))
            await asyncio.sleep(0.2)
            still_going = not task.done()
            task.cancel()
            return still_going

        assert asyncio.run(drive()) is True, "--wait must not give up"
        assert attempts["n"] < 200, f"hot-spinning: {attempts['n']} attempts in 0.2s"

    def test_stop_is_honoured_during_the_retry_backoff(self, monkeypatch):
        monkeypatch.setattr("canlib.wican_mode._tcp_open", lambda *a: True)
        stopped = {"v": False}

        async def connect(cand):
            stopped["v"] = True  # stop right after the first failed attempt
            raise ConnectionError("nope")

        # A long poll_interval: only a slice-wise, stop-aware backoff returns fast.
        policy = ReconnectPolicy(
            forever=True, max_wait=99.0, connect_timeout=0.001, poll_interval=5.0
        )
        r = MonitorReconnector([_raw("dev")], connect, policy)
        started = time.monotonic()
        ok = asyncio.run(r(FakeController(), None, stop=lambda: stopped["v"]))
        assert ok is False
        assert time.monotonic() - started < 2.0, "backoff ignored the stop flag"


# ---------------------------------------------------------------------------
# MonitorController.rebind / close_client
# ---------------------------------------------------------------------------


class TestControllerRebind:
    def _controller(self, *, raw: bool):
        from canlib.modes.monitor import MonitorController

        steps = [{"type": "query", "ecu": "BMS", "pids": ["2101"]}]
        if raw:
            return MonitorController(None, steps, {"ecus": {}}, verbose=False, raw_client=object())
        return MonitorController(object(), steps, {"ecus": {}}, verbose=False)

    def test_rebind_raw_swaps_client_and_poller(self):
        c = self._controller(raw=True)
        c.disconnected = True
        old_poller = c.raw_poller
        new_client = object()
        c.rebind(new_client)
        assert c.raw_client is new_client
        assert c.raw_poller is not old_poller  # rebuilt for the new client
        assert c.disconnected is False

    def test_rebind_elm_swaps_terminal_and_session_manager(self):
        c = self._controller(raw=False)
        c.disconnected = True
        old_sm = c.sm
        new_terminal = object()
        c.rebind(new_terminal)
        assert c.terminal is new_terminal
        assert c.sm is not old_sm
        assert c.sm.terminal is new_terminal
        assert c.disconnected is False

    def test_close_client_raw_is_best_effort(self):
        class Boom:
            def close(self):
                raise RuntimeError("already dead")

        c = self._controller(raw=True)
        c.raw_client = Boom()
        asyncio.run(c.close_client())  # must not raise

    def test_close_client_elm_is_best_effort(self):
        class Boom:
            async def close(self):
                raise RuntimeError("already dead")

        c = self._controller(raw=False)
        c.terminal = Boom()
        asyncio.run(c.close_client())  # must not raise


# ---------------------------------------------------------------------------
# all-stale escalation (a live-but-useless pipe)
# ---------------------------------------------------------------------------


class TestStaleEscalation:
    """A desynced pipe raises nothing, so only response *content* can trigger a
    reconnect. Without this, `monitor` sits forever showing carried-forward
    values on a connection that will never answer again — the reported bug."""

    def _controller(self):
        from canlib.modes.monitor import MonitorController

        steps = [{"type": "query", "ecu": "BMS", "pids": ["2101"]}]
        c = MonitorController(object(), steps, {"ecus": {}}, verbose=False)
        c.last_queries = [{"ecu": "BMS"}]  # a cycle that did ask for something
        return c

    @staticmethod
    def _cycle(c, entry):
        """One poll cycle whose only PID resolved to ``entry``."""
        c._cycle_answered = False
        c._displayify(("BMS", "2101"), entry)
        c._check_liveness()

    def test_all_stale_cycles_escalate_at_the_threshold(self, monkeypatch):
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 3)
        c = self._controller()
        c._last_good[("BMS", "2101")] = {"raw_hex": "6101AA"}  # so rows carry forward
        for expected in (1, 2):
            self._cycle(c, {"error": "Echo mismatch"})
            assert c.dead_cycles == expected
            assert c.disconnected is False
        self._cycle(c, {"error": "Echo mismatch"})
        assert c.disconnected is True
        assert c.dead_cycles == 0  # armed afresh for the post-reconnect run

    def test_one_good_response_resets_the_count(self, monkeypatch):
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 3)
        c = self._controller()
        self._cycle(c, {"error": "timeout"})
        self._cycle(c, {"error": "timeout"})
        assert c.dead_cycles == 2
        self._cycle(c, {"raw_hex": "6101AA"})
        assert c.dead_cycles == 0
        assert c.disconnected is False

    def test_an_nrc_counts_as_alive(self, monkeypatch):
        # A negative response proves the request reached the ECU and its reply
        # came back in the right slot: the pipe is healthy and refusing, which is
        # not something reconnecting would fix.
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 2)
        c = self._controller()
        self._cycle(c, {"error": "NRC 0x31 requestOutOfRange"})
        self._cycle(c, {"error": "NRC 0x31 requestOutOfRange"})
        self._cycle(c, {"error": "NRC 0x31 requestOutOfRange"})
        assert c.dead_cycles == 0
        assert c.disconnected is False

    def test_zero_disables_escalation(self, monkeypatch):
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 0)
        c = self._controller()
        for _ in range(20):
            self._cycle(c, {"error": "timeout"})
        assert c.disconnected is False
        assert c.dead_cycles == 20  # still observed, just not acted on

    def test_an_idle_cycle_is_not_a_dead_cycle(self, monkeypatch):
        # Every PID filtered out (or a paused/empty plan) means nothing was asked,
        # so nothing failing to answer is not evidence of a broken link.
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 1)
        c = self._controller()
        c.last_queries = []
        c._cycle_answered = False
        c._check_liveness()
        assert c.dead_cycles == 0
        assert c.disconnected is False


# ---------------------------------------------------------------------------
# --wait initial connect (async_main)
# ---------------------------------------------------------------------------


class TestWaitInitialConnect:
    def test_async_main_waits_then_proceeds(self, monkeypatch):
        """--wait blocks on wait_for_reachable until a device appears, then runs."""
        from canlib.commands import _live

        cand = _raw("dev")
        monkeypatch.setattr("canlib.transport.resolve_transport_candidates", lambda args: [cand])
        monkeypatch.setattr("canlib.config.fallback_settings", lambda: (True, 2.0, None))

        waited = {"n": 0}

        def fake_wait(candidates, **kw):
            waited["n"] += 1
            assert kw["deadline"] is None  # forever
            return candidates[0]

        monkeypatch.setattr("canlib.transport.wait_for_reachable", fake_wait)
        # select_reachable_transport must NOT be used on the --wait path.
        monkeypatch.setattr(
            "canlib.transport.select_reachable_transport",
            lambda *a, **k: pytest.fail("should not select on --wait"),
        )

        captured = {}

        async def fake_run_raw(args, transport, pids_data):
            captured["host"] = transport.host
            return 0

        monkeypatch.setattr("canlib.modes.raw_ops.run_raw", fake_run_raw)
        monkeypatch.setattr(_live, "load_pids", lambda: {"ecus": {}})

        class Args:
            wait = True
            unsafe = False
            verbose = False
            reboot = False
            # mode selectors dispatch_mode/log_command peek at:
            param = ecu = raw = None
            scan = skm_wakeup = identity = discover = False
            dtc = iocontrol = routines = None
            routines_scan = iocontrol_scan = sessions_scan = None
            session = False

        rc = asyncio.run(_live.async_main(Args()))
        assert rc == 0
        assert waited["n"] == 1
        assert captured["host"] == "dev"
