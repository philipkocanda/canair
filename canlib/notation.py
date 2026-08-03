"""Typed byte-reference value object + switchable display notation.

The **canonical internal byte space is ISO-TP** — the reassembled UDS payload
with no transport framing, exactly what every transport returns. A :class:`ByteRef`
names a position in that space; WiCAN / Torque / bix are *views* computed on
demand for display (:meth:`ByteRef.render`), and the WiCAN expression (the
firmware format) is produced only at the edge (:meth:`ByteRef.to_wican_expression`).

This is the de-conflation layer for the byte-notation work (see
``plans/2026-07-24-byte-notation-phase2-isotp-canonical.md``): WiCAN's
PCI-interleaved indexing is a firmware artifact, not the tool's native unit.
:class:`ByteSpace` reserves ``RAW_CAN`` so a future passively-sniffed CAN frame
(no ISO-TP layer) can be referenced by the same type without the WiCAN/PCI
concept applying.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .byteindex import (
    isotp_to_torque,
    isotp_to_wican,
    torque_label,
    torque_to_bix,
    wican_to_isotp,
)
from .expression import signed_range_is_exact


class ByteSpace(StrEnum):
    """The space a :class:`ByteRef` offset is measured in.

    ``ISOTP`` is the canonical space (reassembled UDS payload, no PCI). ``RAW_CAN``
    is reserved for future raw-frame analysis (passive broadcast frames), where a
    byte is an offset into the CAN frame data itself and the WiCAN/Torque/bix
    (UDS-derived) views do not apply.
    """

    ISOTP = "isotp"
    RAW_CAN = "raw-can"


class ByteNotation(StrEnum):
    """How to render a byte reference for display.

    ``WICAN`` is the default (and the promotable/firmware form); the others are
    for reading and for cross-referencing external sheets. Note there is no
    separate "raw CAN" *notation*: for a diagnostic (ISO-TP) payload the raw CAN
    frame bytes are exactly the WiCAN buffer, so ``WICAN`` already is that view.
    """

    WICAN = "wican"
    ISOTP = "isotp"
    TORQUE = "torque"
    BIX = "bix"


def subfunction_bytes_for_pid(pid: str) -> int:
    """Torque/bix subfunction width for a PID: 2 for ``22xxxx`` DIDs, else 1.

    Torque/OBDb count from the first UDS *data* byte, after the SID + subfunction,
    so the mapping shifts by one for service ``0x22`` (2-byte DID) vs ``0x21``.
    """
    return 2 if pid.upper().startswith("22") else 1


@dataclass(frozen=True)
class ByteRef:
    """A position in a decoded payload, canonical in ISO-TP space.

    ``offset`` is an index within ``space`` (an ISO-TP payload index by default);
    ``bit`` is ``None`` for a whole byte or ``0``-``7`` for a single bit;
    ``width``/``signed``/``little`` describe a multi-byte interpretation. WiCAN,
    Torque and bix are derived views — never stored — so the two spaces can never
    be silently conflated (mixing is a construction choice, not an int mix-up).
    """

    offset: int
    bit: int | None = None
    width: int = 1
    signed: bool = False
    little: bool = False
    space: ByteSpace = ByteSpace.ISOTP

    # -- constructors ------------------------------------------------------
    @classmethod
    def from_isotp(
        cls,
        offset: int,
        *,
        bit: int | None = None,
        width: int = 1,
        signed: bool = False,
        little: bool = False,
    ) -> ByteRef:
        """Build a ref from an ISO-TP payload index (the canonical constructor)."""
        return cls(offset, bit=bit, width=width, signed=signed, little=little)

    @classmethod
    def from_wican(
        cls,
        wican_offset: int,
        *,
        bit: int | None = None,
        width: int = 1,
        signed: bool = False,
        little: bool = False,
    ) -> ByteRef:
        """Build a ref from a WiCAN frame index (e.g. from a stored expression).

        Raises :class:`ValueError` if ``wican_offset`` is a PCI (framing) byte,
        which has no ISO-TP position and can never be a data reference.
        """
        iso = wican_to_isotp(wican_offset)
        if iso is None:
            raise ValueError(f"WiCAN B{wican_offset} is a PCI byte, not a data byte")
        return cls(iso, bit=bit, width=width, signed=signed, little=little)

    @classmethod
    def from_raw_can(
        cls,
        offset: int,
        *,
        bit: int | None = None,
        width: int = 1,
        signed: bool = False,
        little: bool = False,
    ) -> ByteRef:
        """Build a ref into a raw CAN frame's data (no ISO-TP/UDS layer).

        Reserved for the future raw-frame domain; such a ref has no WiCAN/Torque
        views and no firmware expression.
        """
        return cls(
            offset, bit=bit, width=width, signed=signed, little=little, space=ByteSpace.RAW_CAN
        )

    # -- derived views -----------------------------------------------------
    @property
    def wican_offset(self) -> int:
        """The WiCAN frame index of this byte (ISO-TP-space refs only)."""
        if self.space is not ByteSpace.ISOTP:
            raise ValueError(f"{self.space.value} ref has no WiCAN offset")
        return isotp_to_wican(self.offset)

    def to_wican_expression(self) -> str | None:
        """The equivalent WiCAN expression (the firmware/promotable form).

        Single bytes/bits and big-endian *contiguous* multi-byte reads map to the
        familiar ``Bn`` / ``Sn`` / ``Bn:k`` / ``[Bn:Bm]`` forms. A multi-byte read
        whose bytes straddle a PCI byte in the WiCAN frame — contiguous in ISO-TP
        but not in WiCAN — is emitted as an explicit shift-composition (the
        capability the old PCI-skipping analysis could not express). A *signed*
        read that can't use the ``[Sn:Sm]`` range form (little-endian,
        PCI-straddling, or a width the range form would sign-extend from the wrong
        bit — see :func:`canlib.expression.signed_range_is_exact`) is emitted as an
        arithmetic composition with the most-significant byte signed (e.g.
        ``B9 + S10*256``) — ``<<``/``|`` would mishandle the negative high byte.
        Non-ISO-TP refs return ``None``.
        """
        if self.space is not ByteSpace.ISOTP:
            return None
        woffs = [isotp_to_wican(self.offset + k) for k in range(self.width)]
        if self.bit is not None:
            return f"B{woffs[0]}:{self.bit}"
        char = "S" if self.signed else "B"
        if self.width == 1:
            return f"{char}{woffs[0]}"
        contiguous = all(woffs[i + 1] - woffs[i] == 1 for i in range(len(woffs) - 1))
        exact_range = not self.signed or signed_range_is_exact(self.width)
        if not self.little and contiguous and exact_range:
            return f"[{char}{woffs[0]}:{char}{woffs[-1]}]"
        if self.signed:
            # Signed, non-range (little-endian or PCI-straddling): compose
            # arithmetically with the MSB signed. ``+``/``*`` not ``<<``/``|``,
            # which mishandle the negative high byte.
            msb_idx = self.width - 1 if self.little else 0
            terms: list[str] = []
            for k, w in enumerate(woffs):
                shift = 8 * k if self.little else 8 * (self.width - 1 - k)
                mult = 1 << shift
                c = "S" if k == msb_idx else "B"
                terms.append(f"{c}{w}" if mult == 1 else f"{c}{w}*{mult}")
            return " + ".join(terms)
        # Unsigned shift-composition (handles PCI-straddling and little-endian).
        # Big-endian: first byte is most significant; little-endian: least.
        terms = []
        for k, w in enumerate(woffs):
            shift = 8 * k if self.little else 8 * (self.width - 1 - k)
            terms.append(f"B{w}" if shift == 0 else f"(B{w} << {shift})")
        return " | ".join(terms)

    # -- display -----------------------------------------------------------
    def render(self, notation: ByteNotation, *, sub_bytes: int = 1) -> str:
        """Render this byte reference as a short label in ``notation``.

        ``sub_bytes`` (1 for ``21xx`` PIDs, 2 for ``22xxxx`` DIDs) is only needed
        for the Torque/bix views. A bit is shown as a ``.k`` suffix (a byte range
        uses ``:`` so the two never collide); WiCAN keeps its native ``Bn:k``.
        """
        if self.space is ByteSpace.RAW_CAN:
            base = (
                f"r{self.offset}"
                if self.width == 1
                else f"r{self.offset}:{self.offset + self.width - 1}"
            )
            return base if self.bit is None else f"{base}.{self.bit}"

        if notation is ByteNotation.WICAN:
            expr = self.to_wican_expression()
            return expr if expr is not None else f"B{self.wican_offset}"

        if notation is ByteNotation.ISOTP:
            base = (
                f"i{self.offset}"
                if self.width == 1
                else f"i{self.offset}:{self.offset + self.width - 1}"
            )
            return base if self.bit is None else f"{base}.{self.bit}"

        if notation is ByteNotation.TORQUE:
            first = torque_label(isotp_to_torque(self.offset, sub_bytes))
            if first is None:
                return "—"  # SID/subfunction byte has no Torque index
            if self.width > 1:
                last = torque_label(isotp_to_torque(self.offset + self.width - 1, sub_bytes))
                if last is not None:
                    first = f"{first}:{last}"
            return first if self.bit is None else f"{first}.{self.bit}"

        if notation is ByteNotation.BIX:
            torque = isotp_to_torque(self.offset, sub_bytes)
            if torque is None:
                return "—"
            return str(torque_to_bix(torque) + (self.bit or 0))

        raise ValueError(f"unknown notation: {notation!r}")


# Matches an analysis signal label ending in a WiCAN byte token — an optional
# "ECU:PID:" prefix then "Bn" or "Bn:k". Anything else (a named parameter) is
# left untouched by :func:`relabel_signal`.
_WICAN_BYTE_LABEL = re.compile(r"^(?P<prefix>.*:)?B(?P<off>\d+)(?::(?P<bit>\d+))?$")


def relabel_signal(label: str, notation: ByteNotation, *, sub_bytes: int | None = None) -> str:
    """Re-render a raw-byte analysis label (``…:Bn`` / ``…:Bn:k``) in ``notation``.

    Whole-signal labels for *named parameters* (``ECU:PID:SOC_BMS``) and anything
    not ending in a WiCAN byte token pass through unchanged. WiCAN is a no-op fast
    path, so the default keeps every existing label byte-for-byte identical.

    ``sub_bytes`` (needed only for Torque/bix) is auto-derived from an
    ``ECU:PID:`` prefix when present (``22xxxx`` DID → 2, else 1), so callers can
    relabel with just ``(label, notation)``.
    """
    if notation is ByteNotation.WICAN:
        return label
    m = _WICAN_BYTE_LABEL.match(label)
    if m is None:
        return label
    prefix = m.group("prefix") or ""
    if sub_bytes is None:
        parts = prefix.rstrip(":").split(":")
        sub_bytes = subfunction_bytes_for_pid(parts[-1]) if len(parts) >= 2 and parts[-1] else 1
    bit = int(m.group("bit")) if m.group("bit") is not None else None
    try:
        ref = ByteRef.from_wican(int(m.group("off")), bit=bit)
    except ValueError:
        return label
    return prefix + ref.render(notation, sub_bytes=sub_bytes)


def add_notation_arg(parser) -> None:
    """Register the shared ``--notation`` flag on an analysis command's parser.

    Default is ``None`` so :func:`resolve_notation` can fall back to the
    ``display.byte_notation`` config key (then ``wican``).
    """
    parser.add_argument(
        "--notation",
        choices=[n.value for n in ByteNotation],
        default=None,
        metavar="NAME",
        help="byte-index notation for output labels: wican (default), isotp, "
        "torque, bix. Overrides the display.byte_notation config key.",
    )


def resolve_notation(value: str | None) -> ByteNotation:
    """Effective notation: explicit ``--notation`` > ``display.byte_notation`` > wican."""
    if value:
        return ByteNotation(value)
    from .config import get_config_key

    configured = get_config_key("display.byte_notation")
    if configured:
        try:
            return ByteNotation(str(configured))
        except ValueError:
            pass
    return ByteNotation.WICAN
