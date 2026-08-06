"""Self-abort watchdog for the WiCAN connection mutex.

An orphaned session (a monitor whose terminal vanished, a run whose SSH link
dropped without a hangup ever being delivered) keeps both the connection lock and
the device's single connection. Nothing external can reliably clear it, and
canair never signals another process — so the session watches the mutex itself.

This thread polls :meth:`canlib.lock.WiCANLock.still_ours` and, the moment the
mutex is no longer ours (another run asked for it with ``--force``, or the lock
file was taken over/replaced), stops this process the same way Ctrl-C would: by
raising SIGTERM on ourselves, which every live command maps onto its graceful
shutdown path (``--save`` journal reconciled, sessions closed, connection freed).

It runs as a daemon thread so it can act even while the main thread is blocked in
a socket read — the signal interrupts the syscall and the handler runs.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from collections.abc import Callable

from .lock import WiCANLock

#: How often the mutex is re-checked. Fast enough that a ``--force`` contender
#: (which waits ~10s) sees the connection freed well within its window.
POLL_INTERVAL_S = 2.0


def _default_on_lost(reason: str) -> None:
    """Report the takeover and start this process's graceful shutdown."""
    try:
        print(
            f"\n  Stopping: {reason}.\n"
            "  Releasing the device connection (any --save data is written on the way out).",
            file=sys.stderr,
            flush=True,
        )
    except OSError:
        # The terminal may be long gone — losing the notice must not stop us
        # from standing down.
        pass
    os.kill(os.getpid(), signal.SIGTERM)


class LockWatchdog:
    """Stand down when the connection mutex is taken from us.

    Fires :attr:`on_lost` at most once. ``on_lost`` is injectable so tests can
    observe the decision without signalling the test runner.
    """

    def __init__(
        self,
        lock: WiCANLock,
        *,
        interval: float = POLL_INTERVAL_S,
        on_lost: Callable[[str], None] | None = None,
    ) -> None:
        self._lock = lock
        self._interval = interval
        self._on_lost = on_lost or _default_on_lost
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired = False

    @property
    def fired(self) -> bool:
        """True once the mutex was lost and :attr:`on_lost` was invoked."""
        return self._fired

    def check_once(self) -> str | None:
        """Poll the mutex; fire ``on_lost`` and return the reason if it's gone."""
        if self._fired:
            return None
        try:
            reason = self._lock.still_ours()
        except Exception:
            # A watchdog must never be the thing that breaks a session.
            return None
        if reason is None:
            return None
        self._fired = True
        self._on_lost(reason)
        return reason

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="canair-lock-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling. Called before releasing the lock, so a normal teardown
        (which does relinquish the mutex) never looks like a takeover."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._interval + 1.0)

    def __enter__(self) -> LockWatchdog:
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self.check_once() is not None:
                return
