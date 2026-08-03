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
