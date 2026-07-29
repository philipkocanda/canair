"""Centralized transport-error classification for the live (bus-talking) paths.

Both transports — the raw ``slcan-tcp`` socket and the ``wican-ws`` WebSocket —
fail in the same *kinds* of ways: the host goes silent (timeout), refuses the
port, drops mid-session (peer close / reset / broken pipe), or the network route
disappears. This module is the single place that

  1. names the set of exceptions that count as a *transport/IO* failure — as
     opposed to a genuine bug, which we must let propagate rather than mask; and
  2. turns one into a clean, actionable, human message (+ the recovery hint when
     a ``--save`` journal is in flight).

It is used both at **connection setup** and **during a session** (monitor / scan
/ query), so a dropped device is always handled gracefully — never a bare
traceback — and a partially-recorded ``--save`` session is never silently lost.
"""

from __future__ import annotations

import errno
import socket

# errno values that mean "the peer/link dropped the connection mid-transfer".
_DROP_ERRNOS = frozenset({errno.ECONNRESET, errno.EPIPE, errno.ECONNABORTED})

_cached_types: tuple[type[BaseException], ...] | None = None


def _errno_of(err: BaseException) -> int | None:
    return getattr(err, "errno", None)


def connect_error_detail(err: BaseException) -> str:
    """A short, human phrase for a failed connection — the *actual* OS/link reason.

    Turns a raw socket/OS exception into a one-liner ("connection timed out",
    "connection refused …", "connection dropped by the device …") so an alert can
    say *what happened*, not just "didn't respond". Distinguishing these matters:
    they point at different fixes (silent host vs wrong port/mode vs VPN/routing).
    """
    en = _errno_of(err)
    if isinstance(err, socket.gaierror):
        return f"name resolution failed ({err})"
    if isinstance(err, ConnectionRefusedError) or en == errno.ECONNREFUSED:
        return "connection refused (the host is up, but nothing is listening on that port)"
    if isinstance(err, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)) or (
        en in _DROP_ERRNOS
    ):
        return "connection dropped by the device (reset/closed mid-transfer)"
    if isinstance(err, TimeoutError) or en in (errno.ETIMEDOUT, errno.EAGAIN):
        return "connection timed out (no response from the host/port)"
    if en == errno.EHOSTUNREACH:
        return "no route to host (routing/VPN problem?)"
    if en == errno.ENETUNREACH:
        return "network is unreachable (routing/VPN problem?)"
    return str(err) or err.__class__.__name__


def transport_error_types() -> tuple[type[BaseException], ...]:
    """Exception classes that count as a recoverable transport/IO failure (cached).

    Deliberately *not* a blanket ``Exception`` — a bug in a mode handler should
    still surface as a traceback. This is the closed set of "the bus/link failed"
    errors both transports can raise: the ``OSError`` family (sockets), python-can
    ``CanError`` (raised e.g. when the SLCAN peer closes), and the ``websockets``
    exceptions (WS closed / invalid URI). ``can``/``websockets`` are hard deps but
    imported lazily so importing this module stays cheap on non-live paths.
    """
    global _cached_types
    if _cached_types is None:
        types: list[type[BaseException]] = [OSError]  # covers ConnectionError, TimeoutError
        try:
            import can

            types.append(can.CanError)
        except Exception:
            pass
        try:
            import websockets

            types.append(websockets.exceptions.WebSocketException)
        except Exception:
            pass
        _cached_types = tuple(types)
    return _cached_types


def is_transport_error(exc: BaseException) -> bool:
    """True when ``exc`` is a transport/IO failure (see :func:`transport_error_types`)."""
    return isinstance(exc, transport_error_types())


def _reason(exc: BaseException) -> str:
    """The 'what happened' clause for any transport error (OSError or otherwise)."""
    if isinstance(exc, OSError):
        return connect_error_detail(exc)
    # websockets / can and friends: their own message is already descriptive
    # (e.g. "SLCAN TCP connection closed by peer", "no close frame received").
    return str(exc) or exc.__class__.__name__


def describe_transport_error(
    exc: BaseException,
    *,
    host: str | None,
    transport_label: str,
    saving: bool = False,
) -> str:
    """A clean, actionable message for a transport failure (connect *or* session).

    ``transport_label`` names the link in the user's terms (e.g. "SLCAN" /
    "WebSocket"). ``saving`` appends the ``--recover`` hint so a user whose
    ``--save`` session dropped knows their data is safe in the write-ahead
    journal, not lost.
    """
    where = f" to {host}" if host else ""
    lines = [
        f"{transport_label} transport error{where} — {_reason(exc)}.",
        "  • the device likely dropped off — asleep, rebooted, out of range, "
        "moved network, or the bus was disconnected",
        "  • a device that's been up a long time can wedge its socket; a reboot "
        "(power-cycle) often clears it",
    ]
    if host:
        lines.append(f"  • diagnose:  canair status --wican {host}")
    if saving:
        lines.append(
            "  • any --save data is safe — sessions stream to a write-ahead journal; "
            "if it wasn't already auto-saved above, recover it with:  "
            "canair captures uds --recover"
        )
    return "\n".join(lines)
