"""Graceful-stop signal handling for live sessions.

A live canair session owns the device's single connection, so *however* it is
asked to stop it must unwind properly: reconcile any ``--save`` journal, close
open UDS sessions, release the connection and the mutex. Three signals mean
"stop" and are handled identically:

- ``SIGINT``  — Ctrl-C (already raises ``KeyboardInterrupt``).
- ``SIGTERM`` — ``kill`` / ``pkill``, and the lock watchdog's self-abort.
- ``SIGHUP``  — the controlling terminal went away (SSH disconnect, closed
  window). Its default disposition kills the process outright, which would skip
  the journal reconcile and leave the device connection to time out.

Handlers are installed for the duration of a scope and restored afterwards, so a
mode that wants its own handling (the monitor) can override and hand back.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable, Iterator
from types import FrameType
from typing import Any

#: What ``signal.signal`` accepts (and ``signal.getsignal`` hands back).
Handler = Callable[[int, FrameType | None], Any] | int | None

#: Signals that mean "stop this session gracefully". SIGINT is deliberately
#: excluded: Python already raises KeyboardInterrupt for it, and Textual binds
#: Ctrl-C itself.
STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGHUP)


def _restore(previous: dict[signal.Signals, Handler]) -> None:
    for sig, prev in previous.items():
        with contextlib.suppress(TypeError, ValueError, OSError):
            signal.signal(sig, prev)


@contextlib.contextmanager
def graceful_stop(handler: Callable[[int, FrameType | None], Any]) -> Iterator[None]:
    """Route :data:`STOP_SIGNALS` to ``handler`` for the duration of the scope.

    Previous handlers are restored on exit (including on exception), so nesting
    scopes — a command-level handler with a mode-level one inside it — behaves
    predictably.
    """
    previous: dict[signal.Signals, Handler] = {}
    try:
        for sig in STOP_SIGNALS:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        yield
    finally:
        _restore(previous)


@contextlib.contextmanager
def graceful_stop_async(callback: Callable[[], object]) -> Iterator[None]:
    """Route :data:`STOP_SIGNALS` to ``callback`` on the running event loop.

    Used where a plain signal handler would raise inside an event loop's
    internals (the Textual monitor): the callback is scheduled on the loop, so
    the app can shut itself down cleanly. Falls back to a plain handler where the
    loop cannot install one.
    """
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    previous: dict[signal.Signals, Handler] = {}
    try:
        for sig in STOP_SIGNALS:
            previous[sig] = signal.getsignal(sig)
            try:
                loop.add_signal_handler(sig, callback)
            except (NotImplementedError, RuntimeError, ValueError):
                signal.signal(sig, lambda _s, _f: callback())
            else:
                installed.append(sig)
        yield
    finally:
        for sig in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        _restore(previous)
