"""Shared ISO-TP stack factory for the raw-CAN clients.

Both raw clients (:class:`~canair.transport.raw.terminal.RawTerminal` and
:class:`~canair.transport.raw.uds_client.RawUdsClient`) build one ``can-isotp`` stack
per ECU over a shared python-can bus + ``Notifier``. Centralising the
construction here keeps the two clients identical and gives a single home for
the one make-specific twist can-isotp can't express on its own: a **flow-control
arbitration-id override** for functional-TX / physical-RX ECUs.

For a functional-broadcast request (``0x18DB33F1``) with a physical response
(``0x18DAF1xx``), ISO-TP flow control must be addressed to the ECU's *physical*
request id — but can-isotp emits flow control on the TX id (the functional
broadcast), which is wrong. :func:`build_isotp_stack` returns a thin
``NotifierBasedCanStack`` subclass that rewrites the flow-control frame's
arbitration id when an ``fc_id`` is given. See gap G-J in
``plans/2026-07-28-multi-vehicle-support.md``.

:class:`EcuStacks` holds what a client keeps per ECU name: the stack, the request id and the
lock that allows one exchange on that ECU at a time. The client hands it the factory (it owns
the bus, the notifier and the ISO-TP parameters). An ECU that joins after the client was built
(one the monitor's picker adds, a session step's target that is not polled, an actuator's ECU)
gets its stack from :meth:`EcuStacks.ensure` on first use, from any thread.

:class:`ReceptionFaults` tells a reply that broke off after its first frame from one that
never started, and :func:`hint_transport_choice` says once when the link is too slow for
client-side ISO-TP.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import isotp

from canair.core.eventlog import log_event
from canair.transport.errors import BrokenReplyError
from canair.transport.link_latency import LinkLatency
from canair.uds.addressing import EcuAddress

if TYPE_CHECKING:
    import can

#: Measured round trip above which ``slcan-tcp`` is the wrong transport for this link. Adaptive
#: budgets stop a slow link from aborting a read, but cannot stop it costing a round trip per
#: flow-control frame.
TRANSPORT_HINT_RTT_S = 0.25

#: The can-isotp errors that mean a reply began (a first frame came) and then broke off.
_BROKEN_RECEPTION: tuple[type[isotp.errors.IsoTpError], ...] = (
    isotp.errors.ConsecutiveFrameTimeoutError,
    isotp.errors.WrongSequenceNumberError,
    isotp.errors.ReceptionInterruptedWithSingleFrameError,
    isotp.errors.ReceptionInterruptedWithFirstFrameError,
)


class ReceptionFaults:
    """Per stack, whether the last reply that never arrived had started: a broken reception.

    can-isotp reports a reply that stops after its first frame only to its error handler, and
    then drops the partial message. Without this, a reply that broke off looks the same as an
    ECU that never answered. On ``slcan-tcp`` canair sends the flow-control frame itself, over
    the network, so a late one shows up here.
    """

    def __init__(self) -> None:
        self._last: dict[int, str] = {}

    def watch(self, stack: Any, key: int) -> None:
        """Note ``stack``'s broken receptions under ``key`` (its public ``error_handler``)."""

        def note(error: Exception) -> None:
            if isinstance(error, _BROKEN_RECEPTION):
                self._last[key] = type(error).__name__

        stack.error_handler = note

    def clear(self, key: int) -> None:
        """Forget ``key``'s earlier faults: call it before each request."""
        self._last.pop(key, None)

    def take(self, stack: Any, key: int) -> str | None:
        """Why the reply on ``stack`` did not arrive, when it had started; else ``None``."""
        name = self._last.pop(key, None)
        if name is not None:
            return f"a first frame came, then {name}"
        if stack.is_rx_active():
            return "a first frame came, and the reply was incomplete at the deadline"
        return None

    def lost(self, stack: Any, key: int, ecu: str) -> tuple[TimeoutError, bool]:
        """The error for a reply ``ecu`` did not deliver by its deadline, and whether it is owed.

        A reply that never started may still come, and so may one still arriving. One that
        broke off will not: the ECU gave up on it.
        """
        in_flight = stack.is_rx_active()
        broken = self.take(stack, key)
        if broken is None:
            return TimeoutError(f"no UDS response from {ecu}"), True
        return BrokenReplyError(f"truncated ISO-TP: {broken}"), in_flight


def hint_transport_choice(link: LinkLatency) -> None:
    """Say once, on a slow link, that the transport is the limiting factor.

    canair runs ISO-TP itself on ``slcan-tcp``, so every flow-control frame of a multi-frame
    response crosses the network; ``wican-ws`` keeps it on the dongle. Only the user can act on
    this (a connection choice, not a tunable), so it is stated once per session rather than per
    failed read, where it would read as a symptom instead of a cause.
    """
    rtt = link.rtt
    if rtt is None or rtt < TRANSPORT_HINT_RTT_S:
        return
    log_event(
        "config",
        f"link round trip measured at {rtt * 1000.0:.0f}ms; on slcan-tcp every "
        "flow-control frame of a multi-frame response crosses it. "
        "`--transport wican-ws` moves ISO-TP onto the dongle (WiCAN Pro only).",
        level=logging.INFO,
        transport="slcan-tcp",
    )


class _FcAddressStack(isotp.NotifierBasedCanStack):
    """A stack that emits ISO-TP flow-control frames to a fixed arbitration id.

    Decouples the flow-control address from the request (TX) id, needed for
    functional-TX / physical-RX ECUs (see the module docstring). Everything else
    — request framing, reassembly — is unchanged.
    """

    def __init__(self, *args, fc_id: int, **kwargs) -> None:
        self._fc_id = fc_id
        # A 29-bit fc id needs the extended-frame flag; 11-bit does not.
        self._fc_extended = fc_id > 0x7FF
        super().__init__(*args, **kwargs)

    def _make_flow_control(self, *args, **kwargs) -> isotp.protocol.CanMessage:
        # Reuse the base implementation (correct FC payload/prefix) then redirect
        # the frame to the ECU's physical address. Overrides the can-isotp internal
        # _make_flow_control (whose name/return object the fc-override test guards
        # against a library rename); returns the same isotp.protocol.CanMessage.
        msg = super()._make_flow_control(*args, **kwargs)
        msg.arbitration_id = self._fc_id
        msg.is_extended_id = self._fc_extended
        return msg


def build_isotp_stack(
    bus: can.BusABC,
    notifier: can.Notifier,
    address: isotp.Address,
    params: dict[str, int | bool],
    *,
    fc_id: int | None = None,
) -> isotp.NotifierBasedCanStack:
    """Build a ``NotifierBasedCanStack`` for one ECU (with optional FC override).

    When ``fc_id`` is set, flow-control frames are addressed there instead of the
    TX id (functional-TX ECUs). The stack is returned **not** started — the caller
    owns the lifecycle, matching the existing raw-client code.
    """
    if fc_id is not None:
        return _FcAddressStack(bus, notifier, address=address, params=params, fc_id=fc_id)
    return isotp.NotifierBasedCanStack(bus, notifier, address=address, params=params)


class EcuStacks:
    """``name -> stack``, ``name -> lock`` and ``name -> request id`` for one raw client."""

    def __init__(self, build: Callable[[EcuAddress], Any], settle: Callable[[], None]) -> None:
        self._build = build
        self._settle = settle
        self.stacks: dict[str, Any] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.tx_ids: dict[str, int] = {}
        self._build_lock = threading.Lock()

    def add(self, name: str, address: EcuAddress) -> None:
        """Build and start ``name``'s stack (the caller settles it)."""
        stack = self._build(address)
        stack.start()
        self.tx_ids[name] = address.tx_id
        self.locks[name] = threading.Lock()
        # Last: a name in `stacks` means its lock and its id are there too.
        self.stacks[name] = stack

    def ensure(self, name: str, address: EcuAddress) -> None:
        """Give ``name`` a settled stack if it has none (thread-safe; blocks while it settles)."""
        if name in self.stacks:
            return
        with self._build_lock:
            if name in self.stacks:
                return
            self.add(name, address)
            self._settle()

    def require(self, name: str) -> None:
        """Raise ``LookupError`` when ``name`` has no stack."""
        if name not in self.stacks:
            raise LookupError(f"no ISO-TP stack for {name}: call ensure_ecu() first")
