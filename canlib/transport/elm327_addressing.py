"""Which header, receive filter and flow control an ELM327 must be told, per ECU.

An ELM327 (or the WiCAN Pro's STN2120) is configured by state, not per request: the
request header (``ATSH``), the CAN receive filter (``ATCRA``) and flow control
(``ATFCSH``/``ATFCSD``/``ATFCSM``) all persist until changed. This module decides what
that state must be for one ECU and which commands move the adapter there, so the
terminal keeps only the I/O. It is pure: no channel, no clock, no event loop.

Left alone, the adapter derives two of those ids itself. *Auto receive* picks the
filter "based on the current header bytes", and flow-control mode 0 sends flow control
to an id derived from the ECU's First Frame. Both follow the ISO 15765-4 convention
(``0x7E0`` → ``0x7E8``, ``0x18DA{ta}{sa}`` → ``0x18DA{sa}{ta}``). An ECU that follows
it is **conventional** and is left to the adapter, so a Hyundai/Kia profile sends
exactly what it always has. Any other ECU is **explicit**, with both ids pinned:

- a non-``+0x08`` response offset (the Leaf's ``0x79B`` → ``0x7BB``), where the
  adapter would filter the reply out and derive flow control to ``0x7B3``, so the ECU
  stalls after its First Frame;
- a functional-request ECU (Renault/Outlander ``0x18DB33F1`` → ``0x18DAF1DB``), which
  several ECUs may answer, so only the filter selects ours.

``ATFCSH`` is only honoured in flow-control mode 1, which in turn needs the header and
data set first (the datasheet's "Altering Flow Control Messages"). An explicit ECU
switches mode 1 on once; it then stays on, which is still correct for a conventional
ECU, because ``ATFCSH`` always follows the header. See
``plans/2026-10-20-elm327-ecu-addressing.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from ..addressing import (
    DEFAULT_RX_OFFSET,
    FUNCTIONAL_29BIT_ID,
    AddressingMode,
    EcuAddress,
    fixed_29bit_rx,
    is_extended,
)

# 11-bit OBD-II functional (broadcast) request id. 29-bit: FUNCTIONAL_29BIT_ID.
FUNCTIONAL_11BIT_ID: Final = 0x7DF

# Flow-control modes (`ATFCSM`): 0 = the adapter derives id and data; 1 = the user's
# `ATFCSH` + `ATFCSD`.
FC_MODE_ADAPTER: Final = 0
FC_MODE_USER: Final = 1

# The ISO 15765-4 FlowControl frame the adapter sends in mode 0: ClearToSend,
# BlockSize 0, STmin 0. It is also what the isotp defaults produce, so a profile
# without an `isotp:` block gets byte-for-byte the upstream corpus's ATFCSD300000.
DEFAULT_FC_DATA: Final = "300000"


class _Unknown:
    """The adapter's value is not known: the next plan must state it explicitly."""

    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN: Final = _Unknown()
# The adapter's own auto receive filter (`ATCRA` with no argument, `ATAR`, a reset).
AUTO: Final = "auto"

# Modes the ELM327 path cannot address yet (no ATCEA extension byte). Left exactly as
# before rather than half-addressed: several such ECUs share one request id (BMW 0x6F1),
# so a filter keyed by that id would be the wrong one for all but one of them.
_UNADDRESSABLE_MODES: Final = frozenset(
    {AddressingMode.NORMAL_EXTENDED_11BIT, AddressingMode.EXTENDED_29BIT}
)


@dataclass(frozen=True)
class ElmTarget:
    """The adapter state one ECU needs.

    ``rx_filter``: an id pins the filter there; :data:`AUTO` restores the adapter's own
    filter (a broadcast, which several ECUs answer); None keeps the session's baseline
    — auto, unless the profile ``init`` set one. ``fc_mode`` None means either mode
    works (``ATFCSH`` already names the right id in mode 1). ``extended`` selects the
    8-digit id form.
    """

    header: int
    fc_header: int
    rx_filter: int | str | None = None
    fc_mode: int | None = None
    extended: bool = False
    # A conventional ECU's own reply id: a filter already pinned there is left alone.
    rx_id: int | None = None

    @property
    def explicit(self) -> bool:
        """Whether this ECU needs its own receive filter (and so flow-control mode 1)."""
        return isinstance(self.rx_filter, int)


def elm_id(can_id: int, extended: bool | None = None) -> str:
    """Format a CAN id the way ELM327 header/filter commands take it (3 or 8 digits).

    The width follows the addressing mode when known, else the value.
    """
    if extended is None:
        extended = can_id > 0x7FF
    return f"{can_id:08X}" if extended else f"{can_id:03X}"


def _in_range(can_id: int, extended: bool) -> bool:
    return 0 <= can_id <= (0x1FFFFFFF if extended else 0x7FF)


def is_functional_id(can_id: int) -> bool:
    """True for the OBD-II functional broadcast request ids."""
    return can_id in (FUNCTIONAL_11BIT_ID, FUNCTIONAL_29BIT_ID)


def is_conventional(addr: EcuAddress) -> bool:
    """Whether the adapter's own receive filter and flow-control id are right for ``addr``.

    The ISO 15765-4 pairs: 11-bit ``tx + 0x08``, and the normal-fixed 29-bit byte swap.
    The 11-bit case is proven on the WiCAN Pro (every bundled Ioniq ECU); the 29-bit
    case follows the same convention but no 29-bit car has been read over an ELM327
    transport yet. A flow-control override always makes an ECU explicit.
    """
    if addr.fc_id is not None and addr.fc_id != addr.tx_id:
        return False
    if addr.mode == AddressingMode.NORMAL_FIXED_29BIT:
        return addr.rx_id == fixed_29bit_rx(addr.tx_id)
    if addr.mode == AddressingMode.NORMAL_11BIT:
        return addr.rx_id == addr.tx_id + DEFAULT_RX_OFFSET
    return False


def target_for(addr: EcuAddress) -> ElmTarget:
    """The adapter state ``addr`` needs: the adapter's defaults, or both ids pinned.

    Left on the defaults: a conventional ECU; a broadcast request id with no ``fc_id``
    (an OBD-II ``0x7DF`` ECU, answered by whichever ECUs reply); a mode the ELM327 path
    cannot address; and a response id the mode cannot carry (a ``tx + offset`` past
    ``0x7FF`` during a discovery sweep).
    """
    extended = is_extended(addr.mode)
    if is_functional_id(addr.tx_id) and addr.fc_id is None:
        return functional_target(addr.tx_id)
    fc_header = addr.fc_id if addr.fc_id is not None else addr.tx_id
    if (
        is_conventional(addr)
        or addr.mode in _UNADDRESSABLE_MODES
        or not _in_range(addr.rx_id, extended)
    ):
        return ElmTarget(
            header=addr.tx_id,
            fc_header=fc_header,
            extended=extended,
            rx_id=addr.rx_id if is_conventional(addr) else None,
        )
    return ElmTarget(
        header=addr.tx_id,
        fc_header=fc_header,
        rx_filter=addr.rx_id,
        fc_mode=FC_MODE_USER,
        extended=extended,
    )


def pinned_target(addr: EcuAddress) -> ElmTarget:
    """``addr``'s target with a conventional ECU's own reply id pinned as well.

    For an AutoPID profile in which any ECU is pinned: the firmware runs each PID's
    init verbatim and never resets anything between PIDs, so once one ECU's filter is
    set every other PID must set its own. Broadcast and unaddressable ECUs keep their
    target (a broadcast restores the auto filter).
    """
    target = target_for(addr)
    if target.rx_id is None:
        return target
    return ElmTarget(
        target.header,
        target.fc_header,
        rx_filter=target.rx_id,
        fc_mode=FC_MODE_USER,
        extended=target.extended,
    )


def functional_target(tx_id: int) -> ElmTarget:
    """A broadcast request with no single responder: every ECU's reply must get through.

    The filter goes back to auto and flow control to mode 0, so each responder gets
    flow control on its own physical id.
    """
    return ElmTarget(
        header=tx_id,
        fc_header=tx_id,
        rx_filter=AUTO,
        fc_mode=FC_MODE_ADAPTER,
        extended=tx_id > 0x7FF,
    )


def flow_control_data(isotp_config: Mapping[str, Any] | None) -> str:
    """``ATFCSD`` data from the profile's ``isotp:`` block: ``30 {blocksize} {stmin}``.

    One tuning knob for both transports, since the raw path's ISO-TP stack sends the
    same FlowControl frame from the same two fields.
    """
    from .isotp_params import build_isotp_params

    params = build_isotp_params(dict(isotp_config) if isotp_config else None)

    def _byte(value: object) -> int:
        n = value if isinstance(value, int) and not isinstance(value, bool) else 0
        return max(0, min(0xFF, n))

    return f"30{_byte(params['blocksize']):02X}{_byte(params['stmin']):02X}"


def _hex(value: str) -> int | None:
    try:
        return int(value, 16)
    except ValueError:
        return None


class AdapterState:
    """canair's view of the adapter's addressing state, kept coherent command by command.

    Every command the terminal sends is passed to :meth:`observe`, including ones a
    mode writes directly, so nothing can move the adapter behind the cache's back. A
    reply containing ``?`` means the adapter rejected the command: the affected value
    becomes unknown rather than assumed, and that exact command is not planned again
    until a reset, so a clone that lacks it costs one round trip, not one per read. A
    new instance starts as after a reset.

    The **baseline** filter is the one the profile ``init`` left in place (auto unless
    it sent ``ATCRA``), recorded by :meth:`mark_baseline`. A conventional ECU restores
    it, so an init that filters to a single ECU (the ELM327-Emulator fixture's
    ``ATCRA7E8``) keeps working.
    """

    def __init__(self) -> None:
        self.header: int | None = None
        self.fc_header: int | None = None
        self.rx_filter: int | str | _Unknown = AUTO
        self.fc_mode: int | _Unknown = FC_MODE_ADAPTER
        self.fc_data: str | None | _Unknown = None
        self.baseline_filter: int | str = AUTO
        self._rejected: set[str] = set()

    def mark_baseline(self) -> None:
        """Record the current filter as the one conventional ECUs restore."""
        self.baseline_filter = self.rx_filter if not isinstance(self.rx_filter, _Unknown) else AUTO

    def assume_defaults(self) -> None:
        """The state after a reset: no header, auto receive, flow-control mode 0."""
        self.header = None
        self.fc_header = None
        self.rx_filter = AUTO
        self.fc_mode = FC_MODE_ADAPTER
        self.fc_data = None
        self._rejected.clear()

    def forget(self) -> None:
        """Nothing is known (a fresh connection): the next plan restates everything."""
        self.header = None
        self.fc_header = None
        self.rx_filter = UNKNOWN
        self.fc_mode = UNKNOWN
        self.fc_data = UNKNOWN
        self._rejected.clear()

    def observe(self, cmd: str, reply: str = "") -> None:
        """Apply the effect of ``cmd`` (answered with ``reply``) to the cached state."""
        cu = cmd.upper().replace(" ", "")
        rejected = "?" in reply
        if rejected:
            self._rejected.add(cu)
        if cu in ("ATZ", "ATWS", "ATD"):
            if rejected:
                self.forget()
            else:
                self.assume_defaults()
        elif cu.startswith("ATFCSH"):
            self.fc_header = None if rejected else _hex(cu[6:])
        elif cu.startswith("ATFCSD"):
            self.fc_data = UNKNOWN if rejected else cu[6:]
        elif cu.startswith("ATFCSM"):
            mode = cu[6:]
            self.fc_mode = UNKNOWN if rejected or mode not in ("0", "1", "2") else int(mode)
        elif cu.startswith("ATSH"):
            self.header = None if rejected else _hex(cu[4:])
        elif cu == "ATCRA" or cu == "ATAR":
            self.rx_filter = UNKNOWN if rejected else AUTO
        elif cu.startswith("ATCRA"):
            value = None if rejected else _hex(cu[5:])
            self.rx_filter = UNKNOWN if value is None else value
        elif cu.startswith(("ATCF", "ATCM")) and not cu.startswith("ATCFC"):
            # A raw mask/filter replaces whatever ATCRA set. (ATCFC is flow control
            # on/off, not a filter.)
            self.rx_filter = UNKNOWN

    def _filter_command(self, wanted: int | str) -> str | None:
        """The command that sets the filter to ``wanted`` (an id or AUTO), if it differs."""
        if self.rx_filter == wanted:
            return None
        return "ATCRA" if wanted == AUTO else f"ATCRA{elm_id(int(wanted))}"

    def plan(self, target: ElmTarget, fc_data: str = DEFAULT_FC_DATA) -> list[str]:
        """The commands that move the adapter to ``target``, in the order they must go.

        Flow-control header and data precede ``ATFCSM1``, which the adapter otherwise
        rejects. Nothing is sent for a value already in place.
        """
        width = target.extended
        cmds: list[str] = []
        if self.header != target.header:
            cmds.append(f"ATSH{elm_id(target.header, width)}")
        if self.fc_header != target.fc_header:
            cmds.append(f"ATFCSH{elm_id(target.fc_header, width)}")
        if target.fc_mode == FC_MODE_USER:
            if self.fc_data != fc_data:
                cmds.append(f"ATFCSD{fc_data}")
            if self.fc_mode != FC_MODE_USER:
                cmds.append("ATFCSM1")
        elif target.fc_mode == FC_MODE_ADAPTER and self.fc_mode != FC_MODE_ADAPTER:
            cmds.append("ATFCSM0")
        if isinstance(target.rx_filter, int):
            if self.rx_filter != target.rx_filter:
                cmds.append(f"ATCRA{elm_id(target.rx_filter, width)}")
        elif target.rx_filter == AUTO:
            restore = self._filter_command(AUTO)
            if restore:
                cmds.append(restore)
        elif self.rx_filter != target.rx_id:
            restore = self._filter_command(self.baseline_filter)
            if restore:
                cmds.append(restore)
        return [cmd for cmd in cmds if cmd not in self._rejected]
