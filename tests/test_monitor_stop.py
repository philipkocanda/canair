"""Stop-signal handling in the live monitor.

The monitor is the command most likely to be left running with nobody watching
(that's the point of it), so it is the one that must stop cleanly however it is
asked: Ctrl-C, a `kill`, the lock watchdog standing down, or the controlling
terminal disappearing (SIGHUP after an SSH drop). Whatever the trigger, the
``--save`` journal must be reconciled and the device connection released.
"""

from __future__ import annotations

import asyncio
import signal

import pytest

from canlib.modes import monitor as monitor_mod
from canlib.stop_signals import STOP_SIGNALS


class _FakeController:
    """Minimal stand-in for MonitorController's poll-loop surface."""

    def __init__(self, *, raise_on_poll: signal.Signals | None = None):
        self.interval = 0.01
        self.disconnected = False
        self.reconnect = None
        self.session_steps = None
        self.cycle = 0
        self.polls = 0
        self.interrupted = 0
        self._raise_on_poll = raise_on_poll

    async def poll_once(self):
        self.polls += 1
        if self._raise_on_poll is not None:
            # Stand in for the signal arriving mid-cycle.
            signal.raise_signal(self._raise_on_poll)

    def interrupt(self):
        self.interrupted += 1


class TestNoninteractiveLoop:
    @pytest.mark.parametrize("sig", STOP_SIGNALS)
    def test_stops_on_each_stop_signal(self, sig):
        controller = _FakeController(raise_on_poll=sig)
        asyncio.run(asyncio.wait_for(monitor_mod._monitor_noninteractive(controller), timeout=5.0))
        assert controller.polls == 1
        # The in-flight poll is aborted too, so a raw session doesn't wait out
        # its per-ECU timeouts before exiting.
        assert controller.interrupted == 1

    def test_restores_previous_handlers(self):
        before = {sig: signal.getsignal(sig) for sig in (*STOP_SIGNALS, signal.SIGINT)}
        controller = _FakeController(raise_on_poll=signal.SIGHUP)
        asyncio.run(monitor_mod._monitor_noninteractive(controller))
        for sig, prev in before.items():
            assert signal.getsignal(sig) == prev

    def test_notices_survive_a_dead_terminal(self, monkeypatch):
        """Rendering to a hung-up tty must not crash the shutdown path."""
        monkeypatch.setattr(
            monitor_mod._console,
            "print",
            lambda *a, **kw: (_ for _ in ()).throw(OSError(5, "I/O error")),
        )
        controller = _FakeController(raise_on_poll=signal.SIGTERM)
        controller.disconnected = False
        asyncio.run(monitor_mod._monitor_noninteractive(controller))


class _FakeJournal:
    def __init__(self, path):
        self.path = path
        self.reconciled = False
        self.meta: dict = {}

    def update_meta(self, **kwargs):
        self.meta.update(kwargs)

    def reconcile(self):
        self.reconciled = True


class _FakeRecorder:
    def drain_deferred_saves(self):
        return ["Saved 1 capture(s) to /tmp/somewhere.json"]

    def _backfill_states(self):
        return []

    def segment_quality(self):
        return None


class _TeardownController:
    def __init__(self, *args, **kwargs):
        self.recorder = _FakeRecorder()
        self.journal = None
        self.disconnected = False
        self.raw = False
        self.closed = False
        self._state_explicit = False
        self.captures_dir = None
        self.session_label = ""
        self.session_states: list[str] = []
        self.session_notes = ""
        self.transport_type = None
        self.reconnect = None
        self.session_steps = None
        self.cycle = 0

    def diag_recorder(self):
        return None

    def query_label(self):
        return "fake query"

    async def setup(self, session_steps):
        pass

    def render(self):
        return "final values"

    async def close(self):
        self.closed = True


class TestTeardown:
    def test_journal_is_reconciled_even_if_output_fails(self, monkeypatch, tmp_path):
        """A SIGHUP leaves stdout broken; the recorded data must still be written."""
        import builtins

        import canlib.profile
        import canlib.states

        journal = _FakeJournal(tmp_path / "journal.jsonl")
        controller_box: dict[str, _TeardownController] = {}

        def _make_controller(*args, **kwargs):
            controller_box["c"] = _TeardownController()
            return controller_box["c"]

        monkeypatch.setattr(monitor_mod, "MonitorController", _make_controller)
        monkeypatch.setattr(monitor_mod, "_open_journal", lambda *a, **k: journal)
        monkeypatch.setattr(
            canlib.profile, "active", lambda: type("P", (), {"captures_dir": tmp_path})()
        )
        monkeypatch.setattr(canlib.states, "parse_states", lambda v: [])

        async def _noop(controller):
            # The terminal goes away mid-session: every write fails from here on.
            monkeypatch.setattr(builtins, "print", _broken_print)
            monkeypatch.setattr(
                monitor_mod._console,
                "print",
                lambda *a, **kw: (_ for _ in ()).throw(OSError(5, "I/O error")),
            )

        monkeypatch.setattr(monitor_mod, "_monitor_noninteractive", _noop)
        monkeypatch.setattr(monitor_mod.sys.stdout, "isatty", lambda: False)

        def _broken_print(*args, **kwargs):
            raise OSError(5, "Input/output error")

        asyncio.run(
            monitor_mod.mode_monitor(
                object(),
                [{"type": "query", "ecu": "BMS", "pids": ["2101"]}],
                {"ecus": {}},
                False,
                save=True,
            )
        )

        assert journal.reconciled, "a failed print must not cost us the --save data"
        assert controller_box["c"].closed, "the device connection must still be released"


class TestTuiStop:
    """The TUI must exit through its own quit path, not a KeyboardInterrupt
    unwinding through Textual's internals (which leaves the terminal wrecked)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("sig", STOP_SIGNALS)
    async def test_stop_signal_quits_the_app(self, monkeypatch, sig):
        from canlib.modes import _monitor_tui

        class _FakeApp:
            def __init__(self, controller):
                self.controller = controller
                self._stopping = False
                self._exited = asyncio.Event()

            async def run_async(self):
                await self._exited.wait()

            def exit(self):
                self._exited.set()

        built: dict[str, _FakeApp] = {}

        def _build(controller):
            built["app"] = _FakeApp(controller)
            return built["app"]

        monkeypatch.setattr(_monitor_tui, "MonitorApp", _build)

        task = asyncio.create_task(_monitor_tui.run_monitor_app(object()))
        await asyncio.sleep(0.05)
        signal.raise_signal(sig)
        await asyncio.wait_for(task, timeout=5.0)

        # _stopping also aborts an in-flight reconnect, so --wait can't hold us.
        assert built["app"]._stopping
