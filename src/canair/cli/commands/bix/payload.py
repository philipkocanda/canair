"""Reading a hex payload and warning when it contradicts the chosen mode."""

from __future__ import annotations

import sys

from canair.render import messages
from canair.uds.services import NEGATIVE_RESPONSE_SID, RESPONSE_SID_OFFSET


def parse_hex_payload(raw: str) -> list[int]:
    """Parse a hex string (with or without spaces) into a list of byte values."""
    cleaned = raw.replace(" ", "").strip()
    if not cleaned:
        messages.fail("--annotate requires a non-empty hex payload", code=2)
    if len(cleaned) % 2 != 0:
        messages.fail(f"odd number of hex characters in '{raw}'", code=2)
    payload = []
    for i in range(0, len(cleaned), 2):
        token = cleaned[i : i + 2]
        try:
            payload.append(int(token, 16))
        except ValueError:
            messages.fail(f"invalid hex byte '{token}' in '{raw}'", code=2)
    return payload


def looks_like_pci_first_byte(b: int) -> bool:
    """True if ``b`` looks like an ISO-TP PCI byte (SF/FF/CF/FC frame type).

    The high nibble encodes the frame type (0/1/2/3), so a PCI first byte is
    ``0x00``–``0x3F``. UDS positive-response SIDs are request SID + 0x40, landing
    in ``0x50``–``0x7F`` (0x7F = negative response) — a disjoint range. So a
    first byte < 0x40 is reliably a framing byte, not a UDS response SID.
    """
    return b < RESPONSE_SID_OFFSET


def looks_like_uds_sid(b: int) -> bool:
    """True if ``b`` looks like a UDS response SID (positive 0x40–0x7E, or 0x7F).

    Disjoint from :func:`looks_like_pci_first_byte` — used to warn when a
    PCI-stripped UDS payload is passed to ``--raw`` (which expects framing).
    """
    return RESPONSE_SID_OFFSET <= b <= NEGATIVE_RESPONSE_SID


def emit_warning(headline: str, *detail_lines: str):
    """Print a warning to stderr, set apart by a blank line from the table that follows."""
    messages.warning("\n".join((headline.rstrip("."), *detail_lines)))
    print(file=sys.stderr)


def warn_payload_kind_mismatch(first_byte: int, raw_frame: bool):
    """Warn (reliably) when the input's first byte contradicts the chosen mode.

    Ranges are disjoint (see the two predicates), so this never false-positives on
    a well-formed input: a raw frame always starts < 0x40, a UDS response payload
    always starts 0x40–0x7F. It's a warning, not a hard error — an odd fragment
    shouldn't be blocked outright — but it names the likely fix.
    """
    if not raw_frame and looks_like_pci_first_byte(first_byte):
        emit_warning(
            f"first byte 0x{first_byte:02X} looks like an ISO-TP PCI byte, not a UDS response SID.",
            "--annotate expects the reassembled UDS payload (SID-first, PCI stripped).",
            "If this is a raw CAN frame straight off the bus, pass `--raw`.",
        )
    elif raw_frame and looks_like_uds_sid(first_byte):
        emit_warning(
            f"first byte 0x{first_byte:02X} looks like a UDS response SID, not an ISO-TP PCI byte.",
            "--raw expects an already-framed CAN payload (PCI present).",
            "If this is a PCI-stripped UDS payload, drop `--raw`.",
        )
