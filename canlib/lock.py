"""WiCAN connection mutex using flock(2).

Uses an exclusive advisory lock on a lock file so only one canreq.py
process holds a WebSocket connection to the WiCAN at a time.

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


class WiCANLock:
    """Exclusive advisory lock on LOCK_FILE via flock(2).

    The lock file stores the PID of the holder so a useful error message
    can be shown when contended.
    """

    def __init__(self, lock_file: Path = LOCK_FILE):
        self._path = lock_file
        self._fd: int | None = None

    def acquire(self, force: bool = False):
        """Acquire the lock. Exits with an error message if contended and not forcing.

        Args:
            force: If True, steal the lock even if another process holds it.
        """
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)

        if force:
            # Unconditional exclusive lock (blocking — steals from any holder)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        else:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Read holder PID from the file for the error message
                try:
                    holder = os.read(self._fd, 32).decode().strip()
                    holder_info = f" (held by PID {holder})" if holder else ""
                except Exception:
                    holder_info = ""
                os.close(self._fd)
                self._fd = None
                print(
                    f"ERROR: Another canreq.py is already connected to the WiCAN{holder_info}.\n"
                    f"  Only one WebSocket connection is allowed at a time.\n"
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
