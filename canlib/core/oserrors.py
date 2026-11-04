"""A short human phrase for an OS-level connection failure.

Stdlib only: the WiCAN device checks and the transports both turn a raw socket exception into
"connection refused" or "timed out" for the user.
"""

from __future__ import annotations

import errno
import socket

# errno values that mean "the peer/link dropped the connection mid-transfer".
_DROP_ERRNOS = frozenset({errno.ECONNRESET, errno.EPIPE, errno.ECONNABORTED})


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
