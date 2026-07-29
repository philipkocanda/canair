"""WiCAN connection mutex using flock(2).

Uses an exclusive advisory lock on a lock file so only one canair
process talks to the WiCAN at a time (over either transport — the WebSocket
ELM327 terminal or the SLCAN TCP socket).

The lock is automatically released when the process exits (clean or crash),
so stale locks are never a problem. The --force flag steals the lock
unconditionally (useful after a killed session).

Usage:
    lock = WiCANLock()
    lock.acquire(force=args.force)   # exits with error message if contended
    try:
        ...
    finally:
        lock.release()

Or as a context manager:
    with WiCANLock(force=args.force):
        ...
"""

import fcntl
import os
import sys
from pathlib import Path

LOCK_FILE = Path("/tmp/wican-connection.lock")


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still alive for our purposes.
        return True
    return True


class WiCANLock:
    """Exclusive advisory lock on LOCK_FILE via flock(2).

    The lock file stores the PID of the holder so a useful error message
    can be shown when contended.
    """

    def __init__(self, lock_file: Path = LOCK_FILE):
        self._path = lock_file
        self._fd: int | None = None

    def _read_holder(self) -> int | None:
        """Return the PID recorded in the lock file, or None if empty/unreadable."""
        if self._fd is None:
            return None
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            data = os.read(self._fd, 32).decode().strip()
            return int(data) if data else None
        except (ValueError, OSError):
            return None

    def acquire(self, force: bool = False):
        """Acquire the lock. Exits with an error message if contended and not forcing.

        Args:
            force: If True, steal the lock even if another process holds it.
        """
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)

        # Who holds it right now (if anyone)? Read before we take/overwrite the
        # lock so --force can warn when it steals from a process still alive.
        prev_holder = self._read_holder()

        if force:
            # Unconditional exclusive lock (blocking — steals from any holder)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
            # An orphaned/stuck canair process keeps the device's single
            # connection open even after --force steals *this* lock, so a fresh
            # connection can then time out. Warn (and name the PID) so the user
            # can clear it without a device reboot.
            if prev_holder is not None and prev_holder != os.getpid() and _pid_alive(prev_holder):
                print(
                    f"WARNING: --force stole the lock from PID {prev_holder}, which is still "
                    f"running.\n"
                    f"  If that's an orphaned/stuck canair session it still holds the device's\n"
                    f"  single connection — a new connection can then time out. Kill it if so:\n"
                    f"    kill {prev_holder}   (or: pkill -f canair)",
                    file=sys.stderr,
                )
        else:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Report the holder PID (read above) for the error message.
                holder_info = f" (held by PID {prev_holder})" if prev_holder else ""
                os.close(self._fd)
                self._fd = None
                print(
                    f"ERROR: Another canair session is already using the WiCAN{holder_info}.\n"
                    f"  Only one canair session can talk to the device at a time.\n"
                    f"  Use --force to steal the lock if the previous session was killed.",
                    file=sys.stderr,
                )
                sys.exit(1)

        # Write our PID so contenders can show it
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, str(os.getpid()).encode())

    def release(self):
        """Release the lock."""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()
