"""Skip a long-running step with a keypress, without leaving the line-oriented output.

``canair dtc --all`` reads snapshots as a final step that may take a while on a slow link,
and the user can skip the rest of it. :func:`skip_listener` puts a TTY's stdin in cbreak
mode (keeping Ctrl-C a signal) and watches it from a daemon thread, so the step only asks
:attr:`SkipListener.pressed` between requests. Off a TTY nothing is read and ``pressed``
stays False: there the flag that leaves the step out is the way to skip it.
"""

from __future__ import annotations

import os
import select
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: Keys that skip: ``s`` and escape (which backs out one level everywhere in canair).
SKIP_KEYS = frozenset({"s", "S", "\x1b"})

_POLL_S = 0.1


class SkipListener:
    """Whether a skip key has been pressed since the listener started."""

    def __init__(self) -> None:
        self._pressed = threading.Event()
        self.active = False

    @property
    def pressed(self) -> bool:
        return self._pressed.is_set()

    def press(self) -> None:
        self._pressed.set()


@contextmanager
def skip_listener(enabled: bool = True) -> Iterator[SkipListener]:
    """Listen for a skip key on stdin while the block runs, restoring the terminal after.

    With ``enabled`` False, or when stdin is not a TTY, the listener is inert. ``active``
    says whether it is listening, so a caller only advertises the key when it works.
    """
    listener = SkipListener()
    fd = _tty_fd() if enabled else None
    if fd is None:
        yield listener
        return

    import termios
    import tty

    old = termios.tcgetattr(fd)
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            ready, _, _ = select.select([fd], [], [], _POLL_S)
            if not ready:
                continue
            key = os.read(fd, 16).decode("utf-8", errors="ignore")
            if key[:1] in SKIP_KEYS:
                listener.press()
                return

    tty.setcbreak(fd)
    listener.active = True
    thread = threading.Thread(target=watch, name="skip-listener", daemon=True)
    thread.start()
    try:
        yield listener
    finally:
        stop.set()
        thread.join(timeout=_POLL_S * 5)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _tty_fd() -> int | None:
    try:
        if not sys.stdin.isatty():
            return None
        return sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return None
