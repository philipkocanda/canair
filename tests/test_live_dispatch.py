"""Transport guards in the shared live dispatcher (canlib/commands/_live.py).

``dispatch_mode`` is typed against the :class:`~canlib.transport.protocol.Terminal`
contract, so WiCAN-only modes must narrow explicitly. The interactive REPL sets
the ECU header via raw ``ATSH`` sends, which are no-ops on the raw transport
(RawTerminal uses ``set_header()``), so a UDS request there would fire with no
header set — dispatch refuses it with a clear error rather than a broken prompt,
mirroring the skm-wake guard.
"""

import argparse

import pytest


class TestInteractiveTransportGuard:
    @pytest.mark.asyncio
    async def test_interactive_refused_on_non_elm_terminal(self, capsys):
        from canlib.commands._live import CANAIR_DEFAULTS
        from canlib.modes.dispatch import dispatch_mode

        class NotWiCAN:  # not a WiCANTerminal -> raw path; no mode selector set
            pass

        # No mode selector => the else (interactive) branch.
        args = argparse.Namespace(**CANAIR_DEFAULTS)
        with pytest.raises(SystemExit):
            await dispatch_mode(args, NotWiCAN(), {}, "1.2.3.4")
        assert "wican-ws" in capsys.readouterr().err


class _FakeLock:
    def __init__(self):
        self.released = False

    def acquire(self, force=False):
        pass

    def release(self):
        self.released = True

    def still_ours(self):
        return None


class TestRunLiveStopSignals:
    def test_maps_stop_signals_to_graceful_and_restores(self, monkeypatch):
        """SIGTERM (a `kill` / the lock watchdog) *and* SIGHUP (the terminal went
        away) must unwind like Ctrl-C, so the --save journal is reconciled and the
        device connection released."""
        import signal

        from canlib.commands import _live
        from canlib.stop_signals import STOP_SIGNALS

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        captured = {}

        async def fake_main(args):
            captured["handlers"] = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)

        before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
        rc = _live.run_live(argparse.Namespace(force=False))
        assert rc == 0
        for sig in STOP_SIGNALS:
            with pytest.raises(KeyboardInterrupt):
                captured["handlers"][sig](sig, None)
            # The previous handler is restored on the way out.
            assert signal.getsignal(sig) == before[sig]

    def test_keyboardinterrupt_exits_cleanly(self, monkeypatch, capsys):
        from canlib.commands import _live

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        async def fake_main(args):
            raise KeyboardInterrupt

        monkeypatch.setattr(_live, "async_main", fake_main)
        rc = _live.run_live(argparse.Namespace(force=False))
        assert rc == 0
        assert "Interrupted" in capsys.readouterr().out

    def test_a_dead_terminal_does_not_turn_a_clean_stop_into_a_crash(self, monkeypatch, capsys):
        """After a SIGHUP the tty is gone, so the closing print can fail (EIO)."""
        import builtins

        from canlib.commands import _live

        lock = _FakeLock()
        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: lock)

        async def fake_main(args):
            raise KeyboardInterrupt

        monkeypatch.setattr(_live, "async_main", fake_main)
        monkeypatch.setattr(
            builtins, "print", lambda *a, **kw: (_ for _ in ()).throw(OSError(5, "I/O error"))
        )

        assert _live.run_live(argparse.Namespace(force=False)) == 0
        assert lock.released

    def test_runs_a_lock_watchdog_for_the_session(self, monkeypatch):
        """An orphaned session must notice when another run asks for the device."""
        from canlib.commands import _live

        lock = _FakeLock()
        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: lock)

        events: list[str] = []

        class _SpyWatchdog:
            def __init__(self, watched, **kwargs):
                events.append("built")
                assert watched is lock

            def start(self):
                events.append("start")

            def stop(self):
                events.append("stop")

        monkeypatch.setattr(_live, "LockWatchdog", _SpyWatchdog)

        async def fake_main(args):
            events.append("session")
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)
        assert _live.run_live(argparse.Namespace(force=False)) == 0
        # Watching spans the whole session, and stops before the lock is released
        # (so a normal teardown never looks like a takeover).
        assert events == ["built", "start", "session", "stop"]
        assert lock.released
