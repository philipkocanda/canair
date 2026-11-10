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

from canair.core.oserrors import connect_error_detail

_cached_types: tuple[type[BaseException], ...] | None = None


class AdapterRefusedError(ConnectionError):
    """The adapter is reachable but refused a command a request cannot go without.

    An ELM327 that answers ``?`` to a header keeps the previous one, so sending the
    request anyway would reach the wrong ECU. A :class:`ConnectionError` so every
    live path already stops (or re-homes) cleanly on it; the message names the
    command and why it matters, since the link itself is fine.
    """


class TransportUnsupported(Exception):
    """A capability the selected transport does not have (not a link failure).

    Raised when a feature needs a transport that this one is not, e.g. injecting a
    raw broadcast CAN frame, which needs ``slcan-tcp`` (an ELM327 adapter cannot).
    The message names the working transport, so the caller reports it cleanly
    rather than as a dropped-link error.
    """


# Why an ELM327 refuses a raw broadcast frame (its send_frame raises this).
CANNOT_INJECT_FRAME = (
    "injecting a raw broadcast CAN frame needs the slcan-tcp transport; an ELM327 adapter "
    "(wican-ws, elm327-tcp) speaks request/response only"
)


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
    if isinstance(exc, AdapterRefusedError):
        # The link works; the remedy is the adapter or the profile, not the network.
        lines = [f"{transport_label} adapter{where} refused a command — {exc}."]
    else:
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
