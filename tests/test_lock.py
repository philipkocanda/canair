"""Tests for canlib.lock — the WiCAN connection mutex + --force heads-up."""

import os
import subprocess

import pytest

from canlib.lock import WiCANLock, _pid_alive


class TestPidAlive:
    def test_self_is_alive(self):
        assert _pid_alive(os.getpid())

    def test_absent_pid_is_dead(self):
        # A PID that (essentially) never exists.
        assert not _pid_alive(2_000_000_000)


class TestForceHeadsUp:
    def test_warns_when_stolen_from_a_live_holder(self, tmp_path, capsys):
        lf = tmp_path / "lock"
        # Record a live PID in the lock file but hold no flock, so --force
        # acquires immediately and can warn the holder is still running.
        proc = subprocess.Popen(["sleep", "30"])
        try:
            lf.write_text(str(proc.pid))
            lock = WiCANLock(lock_file=lf)
            lock.acquire(force=True)
            lock.release()
            err = capsys.readouterr().err
            assert f"stole the lock from PID {proc.pid}" in err
            assert "pkill -f canair" in err
        finally:
            proc.terminate()
            proc.wait()

    def test_no_warning_when_previous_holder_is_dead(self, tmp_path, capsys):
        lf = tmp_path / "lock"
        lf.write_text("2000000000")  # a dead PID
        WiCANLock(lock_file=lf).acquire(force=True)
        assert "stole the lock" not in capsys.readouterr().err

    def test_no_warning_when_holder_is_self(self, tmp_path, capsys):
        lf = tmp_path / "lock"
        lf.write_text(str(os.getpid()))
        WiCANLock(lock_file=lf).acquire(force=True)
        assert "stole the lock" not in capsys.readouterr().err

    def test_no_warning_when_file_empty(self, tmp_path, capsys):
        lf = tmp_path / "lock"
        WiCANLock(lock_file=lf).acquire(force=True)  # fresh file, no prior holder
        assert "stole the lock" not in capsys.readouterr().err


class TestContention:
    def test_non_force_reports_holder_pid_and_exits(self, tmp_path, capsys):
        lf = tmp_path / "lock"
        holder = WiCANLock(lock_file=lf)
        holder.acquire()  # holds the flock + records our PID
        try:
            contender = WiCANLock(lock_file=lf)
            with pytest.raises(SystemExit):
                contender.acquire(force=False)
            err = capsys.readouterr().err
            assert f"held by PID {os.getpid()}" in err
            assert "--force" in err
        finally:
            holder.release()
