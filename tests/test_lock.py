"""Tests for canlib.lock — the WiCAN connection mutex + its cooperative steal.

The mutex exists because the device serves a single connection. A *dead* holder
is free (the kernel drops its flock), so the interesting behaviour is a *live*
one: ``--force`` must ask it to leave and then either get the connection or fail
with an actionable message — never block forever, never kill anything.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from canlib.lock import (
    StealRequest,
    WiCANLock,
    _pid_alive,
    describe_acquire_failure,
    holder_info,
    lock_state,
    read_steal,
    write_steal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A stand-in for an orphaned canair session: takes the lock, then either honours
# the steal request via the watchdog (cooperative) or ignores everything (wedged,
# as an older canair without a watchdog would).
_HOLDER_SCRIPT = """
import sys, signal, time
sys.path.insert(0, {root!r})
from pathlib import Path
from canlib.lock import WiCANLock
from canlib.lock_watchdog import LockWatchdog

lock_file, steal_file, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
lock = WiCANLock(lock_file=lock_file, steal_file=steal_file)
lock.acquire()

if mode == "wedged":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(60)
else:
    def _stop(_s, _f):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    watchdog = LockWatchdog(lock, interval=0.1)
    try:
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
def holder(tmp_path):
    """Spawn a live lock holder; yields a launcher and cleans the process up."""
    procs: list[subprocess.Popen] = []
    script = tmp_path / "holder.py"
    script.write_text(_HOLDER_SCRIPT.format(root=str(REPO_ROOT)))

    def _start(lock_file: Path, steal_file: Path, mode: str = "cooperative"):
        proc = subprocess.Popen(
            [sys.executable, str(script), str(lock_file), str(steal_file), mode],
            stdout=subprocess.PIPE,
            text=True,
        )
        procs.append(proc)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"  # lock is held before we return
        return proc

    yield _start

    for proc in procs:
        proc.kill()
        proc.wait(timeout=5)


class TestPidAlive:
    def test_self_is_alive(self):
        assert _pid_alive(os.getpid())

    def test_absent_pid_is_dead(self):
        # A PID that (essentially) never exists.
        assert not _pid_alive(2_000_000_000)


class TestHolderInfo:
    def test_describes_a_live_process(self):
        info = holder_info(os.getpid())
        assert info.alive and info.pid == os.getpid()
        # Command line + age come from `ps`; both should resolve for ourselves.
        assert info.cmdline
        assert info.age_s is not None
        assert f"PID {os.getpid()}" in info.describe()

    def test_dead_process_is_reported_as_gone(self):
        info = holder_info(2_000_000_000)
        assert not info.alive
        assert "no longer running" in info.describe()

    def test_foreignness_needs_positive_evidence(self):
        """An unreadable command line is *unknown*, not proof of innocence/guilt."""
        from canlib.lock import HolderInfo

        assert HolderInfo(pid=1, alive=True, cmdline="uv run canair monitor").looks_foreign is False
        assert HolderInfo(pid=1, alive=True, cmdline="sleep 30").looks_foreign is True
        assert HolderInfo(pid=1, alive=True, cmdline=None).looks_foreign is False


class TestContention:
    def test_non_force_refuses_and_names_the_holder(self, tmp_path, holder):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        proc = holder(lf, sf)

        result = WiCANLock(lock_file=lf, steal_file=sf).try_acquire(force=False)

        assert not result.ok
        assert result.outcome == "contended"
        assert result.holder is not None and result.holder.pid == proc.pid
        message = describe_acquire_failure(result)
        assert f"PID {proc.pid}" in message
        assert "--force" in message

    def test_non_force_does_not_request_a_steal(self, tmp_path, holder):
        """A refusal must leave the holder alone — only --force asks it to leave."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        holder(lf, sf)

        WiCANLock(lock_file=lf, steal_file=sf).try_acquire(force=False)

        assert not sf.exists()

    def test_free_lock_acquires_immediately(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        result = lock.try_acquire(force=False)
        try:
            assert result.ok and result.outcome == "free"
            assert lock.held
        finally:
            lock.release()

    def test_dead_holders_pid_does_not_block(self, tmp_path):
        """A killed session leaves its PID behind but no flock — that's free."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lf.write_text("2000000000")
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        try:
            assert lock.try_acquire(force=False).ok
        finally:
            lock.release()


class TestCooperativeSteal:
    def test_force_gets_the_connection_from_a_live_holder(self, tmp_path, holder):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        proc = holder(lf, sf)

        lock = WiCANLock(lock_file=lf, steal_file=sf)
        result = lock.try_acquire(force=True, wait=10.0)
        try:
            assert result.ok, describe_acquire_failure(result)
            assert result.outcome == "stolen"
            assert result.waited_s < 10.0
            # The holder stood down of its own accord (no signal was sent).
            assert proc.wait(timeout=5) == 0
            # The served request is cleaned up, so it can't affect a later run.
            assert not sf.exists()
            assert lock.still_ours() is None
        finally:
            lock.release()

    def test_force_gives_up_on_a_wedged_holder_instead_of_hanging(self, tmp_path, holder):
        """Regression: --force used to flock(LOCK_EX) and block forever here."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        proc = holder(lf, sf, mode="wedged")

        lock = WiCANLock(lock_file=lf, steal_file=sf)
        started = time.monotonic()
        result = lock.try_acquire(force=True, wait=1.0)
        elapsed = time.monotonic() - started

        assert not result.ok and result.outcome == "timeout"
        assert elapsed < 10.0  # bounded by `wait`, not by the holder
        assert proc.poll() is None  # still running: canair never kills it
        message = describe_acquire_failure(result)
        assert f"kill {proc.pid}" in message
        assert "--recover" in message
        # A request that went unanswered is withdrawn rather than left as a trap.
        assert not sf.exists()


class TestStillOurs:
    def test_ours_while_we_hold_it(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            assert lock.still_ours() is None
        finally:
            lock.release()

    def test_unheld_lock_reports_nothing_to_lose(self, tmp_path):
        lock = WiCANLock(lock_file=tmp_path / "lock", steal_file=tmp_path / "steal")
        assert lock.still_ours() is None

    def test_detects_a_steal_request_aimed_at_us(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            sf.write_text(f"target={os.getpid()} by={os.getppid()} at={time.time()}")
            reason = lock.still_ours()
            assert reason is not None and "--force" in reason
        finally:
            lock.release()

    def test_ignores_a_request_aimed_at_another_pid(self, tmp_path):
        """A stale request must never abort an unrelated later session."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            sf.write_text(f"target=2000000000 by={os.getppid()} at={time.time()}")
            assert lock.still_ours() is None
        finally:
            lock.release()

    def test_ignores_a_request_from_a_dead_requester(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            sf.write_text(f"target={os.getpid()} by=2000000000 at={time.time()}")
            assert lock.still_ours() is None
        finally:
            lock.release()

    def test_ignores_an_expired_request(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            sf.write_text(f"target={os.getpid()} by={os.getppid()} at={time.time() - 3600}")
            assert lock.still_ours() is None
        finally:
            lock.release()

    def test_detects_a_foreign_holder_pid(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            lf.write_text("2000000000")
            reason = lock.still_ours()
            assert reason is not None and "taken over" in reason
        finally:
            lock.release()

    def test_detects_a_removed_lock_file(self, tmp_path):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            lf.unlink()
            reason = lock.still_ours()
            assert reason is not None and "removed" in reason
        finally:
            lock.release()

    def test_detects_a_replaced_lock_file(self, tmp_path):
        """Another process unlinking + recreating the file bypasses our flock."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        try:
            lf.unlink()
            lf.write_text(str(os.getpid()))  # same PID, different inode
            reason = lock.still_ours()
            assert reason is not None and "replaced" in reason
        finally:
            lock.release()


class TestStealRequestFile:
    def test_round_trip(self, tmp_path):
        sf = tmp_path / "steal"
        assert write_steal(4242, sf)
        request = read_steal(sf)
        assert request is not None
        assert request.target == 4242 and request.by == os.getpid()

    def test_malformed_request_is_ignored(self, tmp_path):
        sf = tmp_path / "steal"
        sf.write_text("garbage without fields")
        assert read_steal(sf) is None

    def test_missing_file_is_no_request(self, tmp_path):
        assert read_steal(tmp_path / "absent") is None

    def test_expiry(self):
        assert StealRequest(target=1, by=2, at=time.time()).expired is False
        assert StealRequest(target=1, by=2, at=time.time() - 3600).expired is True


class TestLockState:
    def test_free_when_no_lock_file(self, tmp_path):
        state = lock_state(tmp_path / "absent", tmp_path / "steal")
        assert not state.held and state.pid is None
        assert state.as_dict()["held"] is False

    def test_free_when_holder_is_gone(self, tmp_path):
        lf = tmp_path / "lock"
        lf.write_text("2000000000")
        assert not lock_state(lf, tmp_path / "steal").held

    def test_reports_a_live_holder(self, tmp_path, holder):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        proc = holder(lf, sf)

        state = lock_state(lf, sf)

        assert state.held
        assert state.pid == proc.pid
        assert not state.ours
        assert state.holder is not None and state.holder.alive
        assert state.as_dict()["pid"] == proc.pid

    def test_probing_does_not_take_the_lock(self, tmp_path):
        """`canair status` must be side-effect-free — including on the mutex."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lock = WiCANLock(lock_file=lf, steal_file=sf)
        lock.try_acquire()
        lock.release()

        assert not lock_state(lf, sf).held
        # And a fresh acquire still works afterwards.
        again = WiCANLock(lock_file=lf, steal_file=sf)
        assert again.try_acquire().ok
        again.release()

    def test_surfaces_a_pending_request(self, tmp_path, holder):
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        holder(lf, sf, mode="wedged")
        write_steal(1234, sf)

        state = lock_state(lf, sf)

        assert state.steal is not None and state.steal.target == 1234
        assert state.as_dict()["steal_pending"]["target"] == 1234


class TestForeignOwnedLockFile:
    def test_unwritable_lock_file_reports_instead_of_crashing(self, tmp_path, monkeypatch):
        """A lock file owned by another user used to raise PermissionError."""
        lf, sf = tmp_path / "lock", tmp_path / "steal"
        lf.write_text("4242")

        real_open = os.open

        def _deny(path, flags, *args):
            if str(path) == str(lf) and flags & os.O_RDWR:
                raise PermissionError(13, "Permission denied")
            return real_open(path, flags, *args)

        monkeypatch.setattr(os, "open", _deny)
        result = WiCANLock(lock_file=lf, steal_file=sf).try_acquire(force=True)

        assert not result.ok and result.outcome == "permission"
        assert "owned by" in describe_acquire_failure(result)
