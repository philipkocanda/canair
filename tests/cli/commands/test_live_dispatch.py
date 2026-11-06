"""Transport guards, the ``--unsafe`` gate and the stop-signal wiring of the shared live runtime.

The handlers are typed against the :class:`~canair.transport.protocol.Terminal` contract, so
WiCAN-only ones must narrow explicitly. The interactive REPL sets the ECU header via raw ``ATSH``
sends, which are no-ops on the raw transport (RawTerminal uses ``set_header()``), so a UDS request
there would fire with no header set: the handler refuses it with a clear error rather than a
broken prompt.
"""

import argparse

import pytest

from canair.cli.live import LiveOperation
from tests.support.live_args import live_args


class TestInteractiveTransportGuard:
    @pytest.mark.asyncio
    async def test_interactive_refused_on_non_elm_terminal(self, capsys):
        from canair.cli.live.handlers.elm_only import handle_interactive

        class NotWiCAN:  # not a WiCANTerminal -> raw path
            pass

        with pytest.raises(SystemExit):
            await handle_interactive(live_args(), NotWiCAN(), {}, "1.2.3.4")
        assert "wican-ws" in capsys.readouterr().err


_OPERATION = LiveOperation(lambda *a, **k: None)


class _FakeLock:
    def __init__(self):
        self.released = False

    def acquire(self, force=False, fail=None):
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

        from canair.cli.live import runtime as _live
        from canair.core.stop_signals import STOP_SIGNALS

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        captured = {}

        async def fake_main(args, operation):
            captured["handlers"] = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)

        before = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
        rc = _live.run_live(argparse.Namespace(force=False), _OPERATION)
        assert rc == 0
        for sig in STOP_SIGNALS:
            with pytest.raises(KeyboardInterrupt):
                captured["handlers"][sig](sig, None)
            # The previous handler is restored on the way out.
            assert signal.getsignal(sig) == before[sig]

    def test_keyboardinterrupt_exits_cleanly(self, monkeypatch, capsys):
        from canair.cli.live import runtime as _live

        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: _FakeLock())

        async def fake_main(args, operation):
            raise KeyboardInterrupt

        monkeypatch.setattr(_live, "async_main", fake_main)
        rc = _live.run_live(argparse.Namespace(force=False), _OPERATION)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Interrupted" in captured.err
        assert captured.out == ""

    def test_a_dead_terminal_does_not_turn_a_clean_stop_into_a_crash(self, monkeypatch, capsys):
        """After a SIGHUP the tty is gone, so the closing print can fail (EIO)."""
        import builtins

        from canair.cli.live import runtime as _live

        lock = _FakeLock()
        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: lock)

        async def fake_main(args, operation):
            raise KeyboardInterrupt

        monkeypatch.setattr(_live, "async_main", fake_main)
        monkeypatch.setattr(
            builtins, "print", lambda *a, **kw: (_ for _ in ()).throw(OSError(5, "I/O error"))
        )

        assert _live.run_live(argparse.Namespace(force=False), _OPERATION) == 0
        assert lock.released

    def test_runs_a_lock_watchdog_for_the_session(self, monkeypatch):
        """An orphaned session must notice when another run asks for the device."""
        from canair.cli.live import runtime as _live

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

        async def fake_main(args, operation):
            events.append("session")
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)
        assert _live.run_live(argparse.Namespace(force=False), _OPERATION) == 0
        # Watching spans the whole session, and stops before the lock is released
        # (so a normal teardown never looks like a takeover).
        assert events == ["built", "start", "session", "stop"]
        assert lock.released


class TestUnsafeNeedsTheConfigKey:
    """``--unsafe`` is refused before the device is touched until the user config enables it."""

    def _run(self, monkeypatch, tmp_path, *, unsafe: bool, config: str | None):
        from canair.cli.live import runtime as _live

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        if config is not None:
            path = tmp_path / "canair" / "config.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(config)
        monkeypatch.setattr(_live, "_require_car", lambda args: None)
        lock = _FakeLock()
        locked: list[bool] = []
        lock.acquire = lambda force=False, fail=None: locked.append(True)
        monkeypatch.setattr(_live, "WiCANLock", lambda *a, **k: lock)

        async def fake_main(args, operation):
            return 0

        monkeypatch.setattr(_live, "async_main", fake_main)
        args = argparse.Namespace(force=False, unsafe=unsafe)
        return _live.run_live(args, _OPERATION), locked

    def test_refused_without_the_key(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            self._run(monkeypatch, tmp_path, unsafe=True, config=None)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "--unsafe is disabled" in err
        assert "canair config set safety.allow_unsafe true" in err

    def test_refused_before_the_device_lock(self, monkeypatch, tmp_path):
        from canair.cli.live import runtime as _live

        monkeypatch.setattr(
            _live, "WiCANLock", lambda *a, **k: pytest.fail("must not take the device lock")
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with pytest.raises(SystemExit):
            _live.run_live(argparse.Namespace(force=False, unsafe=True), _OPERATION)

    def test_refused_when_the_key_is_false(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit):
            self._run(monkeypatch, tmp_path, unsafe=True, config="safety:\n  allow_unsafe: false\n")

    def test_runs_with_the_key(self, monkeypatch, tmp_path):
        rc, locked = self._run(
            monkeypatch, tmp_path, unsafe=True, config="safety:\n  allow_unsafe: true\n"
        )
        assert rc == 0 and locked

    def test_without_unsafe_the_key_does_not_matter(self, monkeypatch, tmp_path):
        rc, locked = self._run(monkeypatch, tmp_path, unsafe=False, config=None)
        assert rc == 0 and locked
