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
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import isotp

from canair.uds.addressing import EcuAddress

if TYPE_CHECKING:
    import can


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
