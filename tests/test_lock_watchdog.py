"""Tests for canlib.lock_watchdog — a session standing down when the mutex is taken.

An orphaned session (terminal gone, no hangup ever delivered) otherwise holds the
device's single connection indefinitely. Since canair never kills another
process, the *holder* has to notice and leave, which is this watchdog's whole job.
"""

import os
import signal
import time

from canlib.lock import WiCANLock
from canlib.lock_watchdog import LockWatchdog, _default_on_lost


class TestCheckOnce:
    def test_silent_while_the_mutex_is_ours(self, tmp_path):
        lock = WiCANLock(lock_file=tmp_path / "lock", steal_file=tmp_path / "steal")
        lock.try_acquire()
        lost: list[str] = []
        try:
            watchdog = LockWatchdog(lock, on_lost=lost.append)
            assert watchdog.check_once() is None
            assert not lost and not watchdog.fired
        finally:
            lock.release()

    def test_fires_on_a_steal_request_aimed_at_us(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        lost: list[str] = []
        try:
            sf.write_text(f"target={os.getpid()} by={os.getppid()} at={time.time()}")
            watchdog = LockWatchdog(lock, on_lost=lost.append)
            reason = watchdog.check_once()
            assert reason is not None and lost == [reason]
            assert watchdog.fired
        finally:
            lock.release()

    def test_fires_only_once(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        lost: list[str] = []
        try:
            sf.write_text(f"target={os.getpid()} by={os.getppid()} at={time.time()}")
            watchdog = LockWatchdog(lock, on_lost=lost.append)
            watchdog.check_once()
            watchdog.check_once()
            assert len(lost) == 1
        finally:
            lock.release()

    def test_a_failing_probe_never_breaks_the_session(self, tmp_path, monkeypatch):
        lock = WiCANLock(lock_file=tmp_path / "lock", steal_file=tmp_path / "steal")
        lock.try_acquire()
        lost: list[str] = []
        try:
            monkeypatch.setattr(
                lock, "still_ours", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            )
            watchdog = LockWatchdog(lock, on_lost=lost.append)
            assert watchdog.check_once() is None
            assert not lost, "on_lost must not fire when the probe itself failed"
        finally:
            lock.release()


class TestThread:
    def test_polls_in_the_background_and_stops(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        lost: list[str] = []
        try:
            watchdog = LockWatchdog(lock, interval=0.05, on_lost=lost.append)
            watchdog.start()
            # Another process asks for the connection.
            sf.write_text(f"target={os.getpid()} by={os.getppid()} at={time.time()}")
            deadline = time.monotonic() + 3.0
            while not lost and time.monotonic() < deadline:
                time.sleep(0.05)
            watchdog.stop()
            assert lost, "watchdog thread did not notice the steal request"
        finally:
            lock.release()

    def test_normal_teardown_is_not_mistaken_for_a_takeover(self, tmp_path):
        """stop() before release(), so relinquishing the lock never self-signals."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        lost: list[str] = []
        watchdog = LockWatchdog(lock, interval=0.05, on_lost=lost.append)
        watchdog.start()
        time.sleep(0.15)
        watchdog.stop()
        lock.release()
        time.sleep(0.15)
        assert not lost


class TestDefaultAction:
    def test_signals_ourselves_with_sigterm(self, monkeypatch):
        """The default action reuses each command's existing graceful-stop path."""
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        _default_on_lost("because")
        assert sent == [(os.getpid(), signal.SIGTERM)]

    def test_a_dead_terminal_does_not_stop_us_standing_down(self, monkeypatch, capsys):
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))

        import builtins

        real_print = builtins.print

        def _explode(*a, **kw):
            if kw.get("file") is not None:
                raise OSError(5, "Input/output error")
            real_print(*a, **kw)

        monkeypatch.setattr(builtins, "print", _explode)
        _default_on_lost("because")
        assert sent == [(os.getpid(), signal.SIGTERM)]
