"""Tests for ``canair lock`` — seeing and clearing a stuck device connection.

The command exists for the case that used to force a reboot: a session whose
terminal vanished still holds the device's single connection, and the only way to
clear it was to hunt down its PID by hand.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from canlib.commands import lock as lock_cmd
from canlib.lock import WiCANLock

REPO_ROOT = Path(__file__).resolve().parents[1]

_HOLDER_SCRIPT = """
import sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from canlib.lock import WiCANLock
from canlib.lock_watchdog import LockWatchdog
from canlib.stop_signals import graceful_stop

lock = WiCANLock(lock_file=Path(sys.argv[1]), steal_file=Path(sys.argv[2]))
lock.acquire()

def _stop(_s, _f):
    raise KeyboardInterrupt

watchdog = LockWatchdog(lock, interval=0.1)
try:
    with graceful_stop(_stop):
        watchdog.start()
        print("ready", flush=True)
        time.sleep(60)
except KeyboardInterrupt:
    pass
finally:
    watchdog.stop()
    lock.release()
"""


@pytest.fixture
def holder(tmp_path, monkeypatch):
    """A live holder of a temp lock, with the command pointed at that lock."""
    lock_file, steal_file = tmp_path / "lock", tmp_path / "steal"
    # Named so the process's command line honestly mentions canair — `canair lock
    # kill` refuses to signal a holder it can see is *not* a canair session.
    script = tmp_path / "canair_session.py"
    script.write_text(_HOLDER_SCRIPT.format(root=str(REPO_ROOT)))
    procs: list[subprocess.Popen] = []

    import canlib.lock as lock_mod

    monkeypatch.setattr(lock_mod, "LOCK_FILE", lock_file)
    monkeypatch.setattr(lock_mod, "STEAL_FILE", steal_file)

    def _start():
        proc = subprocess.Popen(
            [sys.executable, str(script), str(lock_file), str(steal_file)],
            stdout=subprocess.PIPE,
            text=True,
        )
        procs.append(proc)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
        return proc

    yield _start

    for proc in procs:
        proc.kill()
        proc.wait(timeout=5)


def _args(**kwargs) -> argparse.Namespace:
    base = {"lock_action": None, "json": False, "wait": 5.0, "yes": True, "hard": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


class TestShow:
    def test_free_lock_exits_zero(self, tmp_path, monkeypatch, capsys):
        import canlib.lock as lock_mod

        monkeypatch.setattr(lock_mod, "LOCK_FILE", tmp_path / "absent")
        monkeypatch.setattr(lock_mod, "STEAL_FILE", tmp_path / "steal")
        assert lock_cmd.run(_args()) == 0
        assert "free" in capsys.readouterr().out

    def test_held_lock_names_the_holder_and_exits_one(self, holder, capsys):
        proc = holder()
        assert lock_cmd.run(_args()) == 1
        out = capsys.readouterr().out
        assert f"PID {proc.pid}" in out
        assert "canair lock steal" in out  # tells the user how to clear it

    def test_json_output(self, holder, capsys):
        import json

        proc = holder()
        lock_cmd.run(_args(json=True))
        payload = json.loads(capsys.readouterr().out)
        assert payload["held"] is True
        assert payload["pid"] == proc.pid


class TestSteal:
    def test_asks_the_holder_to_release_and_leaves_the_lock_free(self, holder, capsys):
        proc = holder()

        assert lock_cmd.run(_args(lock_action="steal")) == 0

        assert proc.wait(timeout=5) == 0  # it stood down on its own
        assert "released" in capsys.readouterr().out
        # Freed, not held by us: the next command must be able to take it.
        from canlib.lock import lock_state

        assert not lock_state().held

    def test_free_lock_is_a_no_op(self, tmp_path, monkeypatch, capsys):
        import canlib.lock as lock_mod

        monkeypatch.setattr(lock_mod, "LOCK_FILE", tmp_path / "absent")
        monkeypatch.setattr(lock_mod, "STEAL_FILE", tmp_path / "steal")
        assert lock_cmd.run(_args(lock_action="steal")) == 0
        assert "already free" in capsys.readouterr().out

    def test_declining_the_prompt_leaves_the_holder_alone(self, holder, monkeypatch, capsys):
        proc = holder()
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        assert lock_cmd.run(_args(lock_action="steal", yes=False)) == 1

        assert proc.poll() is None
        assert "Aborted" in capsys.readouterr().out


class TestKill:
    def test_signals_a_canair_holder(self, holder, capsys):
        proc = holder()

        assert lock_cmd.run(_args(lock_action="kill")) == 0

        assert proc.wait(timeout=5) == 0
        out = capsys.readouterr().out
        assert "SIGTERM" in out
        assert "--recover" in out  # its --save data is not lost

    def test_refuses_a_pid_that_does_not_look_like_canair(self, tmp_path, monkeypatch, capsys):
        """PID reuse: the recorded PID may now belong to something unrelated."""
        import canlib.lock as lock_mod

        lock_file, steal_file = tmp_path / "lock", tmp_path / "steal"
        monkeypatch.setattr(lock_mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(lock_mod, "STEAL_FILE", steal_file)

        sleeper = subprocess.Popen(["sleep", "30"])
        held = WiCANLock(lock_file=lock_file, steal_file=steal_file)
        held.try_acquire()
        lock_file.write_text(str(sleeper.pid))  # as if a recycled PID were recorded
        try:
            assert lock_cmd.run(_args(lock_action="kill")) == 1
            out = capsys.readouterr().out
            assert "does not look like a canair session" in out
            assert f"kill {sleeper.pid}" in out  # the manual escape hatch
            assert sleeper.poll() is None
        finally:
            held.release()
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_refuses_to_signal_itself(self, tmp_path, monkeypatch, capsys):
        import canlib.lock as lock_mod

        lock_file, steal_file = tmp_path / "lock", tmp_path / "steal"
        monkeypatch.setattr(lock_mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(lock_mod, "STEAL_FILE", steal_file)
        held = WiCANLock(lock_file=lock_file, steal_file=steal_file)
        held.try_acquire()
        try:
            assert lock_cmd.run(_args(lock_action="kill")) == 1
            assert "refusing to signal myself" in capsys.readouterr().out.lower()
        finally:
            held.release()

    def test_free_lock_is_a_no_op(self, tmp_path, monkeypatch, capsys):
        import canlib.lock as lock_mod

        monkeypatch.setattr(lock_mod, "LOCK_FILE", tmp_path / "absent")
        monkeypatch.setattr(lock_mod, "STEAL_FILE", tmp_path / "steal")
        assert lock_cmd.run(_args(lock_action="kill")) == 0
        assert "nothing to signal" in capsys.readouterr().out

    def test_hard_uses_sigkill(self, holder, monkeypatch, capsys):
        proc = holder()
        sent: list[tuple[int, int]] = []
        real_kill = os.kill

        def _spy(pid, sig):
            sent.append((pid, sig))
            real_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _spy)
        assert lock_cmd.run(_args(lock_action="kill", hard=True)) == 0
        assert (proc.pid, signal.SIGKILL) in sent


class TestStatusIntegration:
    def test_status_reports_the_lock(self, holder, monkeypatch):
        from canlib.commands import status

        proc = holder()
        monkeypatch.setattr(status, "_tcp_open", lambda *a, **k: False)
        info = status._gather(argparse.Namespace(transport=None, wican="10.0.2.86", json=True))
        assert info["lock"]["held"] is True
        assert info["lock"]["pid"] == proc.pid

    def test_status_lock_probe_does_not_hold_it(self, tmp_path, monkeypatch):
        """`canair status` is documented side-effect-free — including the mutex."""
        import canlib.lock as lock_mod
        from canlib.commands import status

        lock_file, steal_file = tmp_path / "lock", tmp_path / "steal"
        monkeypatch.setattr(lock_mod, "LOCK_FILE", lock_file)
        monkeypatch.setattr(lock_mod, "STEAL_FILE", steal_file)
        monkeypatch.setattr(status, "_tcp_open", lambda *a, **k: False)

        status._gather(argparse.Namespace(transport=None, wican="10.0.2.86", json=True))

        fresh = WiCANLock(lock_file=lock_file, steal_file=steal_file)
        assert fresh.try_acquire().ok
        fresh.release()


class TestForceEndToEnd:
    def test_a_live_session_releases_for_force_within_the_window(self, holder):
        """The whole point: --force gets the connection instead of hanging."""
        proc = holder()
        import canlib.lock as lock_mod

        contender = WiCANLock(lock_file=lock_mod.LOCK_FILE, steal_file=lock_mod.STEAL_FILE)
        started = time.monotonic()
        result = contender.try_acquire(force=True, wait=10.0)
        try:
            assert result.ok
            assert time.monotonic() - started < 10.0
            assert proc.wait(timeout=5) == 0
        finally:
            contender.release()
