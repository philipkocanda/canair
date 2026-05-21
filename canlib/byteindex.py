"""Byte index conversion between WiCAN, ISO-TP, Torque, and OBDb notations.

Protocol stack: CAN → ISO-TP → UDS

- **WiCAN index**: Index into CAN frame data including PCI bytes.
  PCI bytes sit at positions 0 (single-frame), 0-1 (multi-frame first frame),
  and 8, 16, 24, ... (consecutive frames).
- **ISO-TP index**: Pure ISO-TP payload index (no PCI bytes).
- **Torque index**: UDS data payload, skipping SID + subfunction byte(s).
  Torque 1: 1-byte subfunction (e.g. ``21 01``), data starts at ISO-TP offset 2.
  Torque 2: 2-byte subfunction (e.g. ``22 C0 0B``), data starts at ISO-TP offset 3.
- **bix (bit index)**: Torque byte index x 8. Used by Torque app and OBDb.
- **Torque letter**: A=byte 0, B=byte 1, ..., Z=byte 25, AA=byte 26, AB=byte 27, ...

See ``docs/wican-iso-tp-index-conversion.md`` for the full reference table.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Core conversions (all multi-frame, which is the common case for 21xx/22xx)
# ---------------------------------------------------------------------------


def wican_to_isotp(wican_idx: int) -> int | None:
    """Convert WiCAN byte index to ISO-TP payload index.

    Returns None if the index points to a PCI byte.

    Layout (multi-frame):
      Frame 0 (FF): [PCI_hi PCI_lo] [d d d d d d]  → 6 data bytes
      Frame N (CF): [PCI]           [d d d d d d d] → 7 data bytes
    """
    frame = wican_idx // 8
    pos = wican_idx % 8
    if frame == 0:
        if pos < 2:
            return None  # FF PCI bytes
        return pos - 2
    else:
        if pos == 0:
            return None  # CF PCI byte
        return 6 + (frame - 1) * 7 + (pos - 1)


def isotp_to_wican(isotp_idx: int) -> int:
    """Convert ISO-TP payload index to WiCAN byte index."""
    if isotp_idx < 6:
        # First frame: data starts at position 2
        return isotp_idx + 2
    else:
        # Consecutive frames: 7 data bytes per frame
        remaining = isotp_idx - 6
        cf_frame = remaining // 7  # 0-based CF number
        pos_in_cf = remaining % 7
        frame = cf_frame + 1
        return frame * 8 + 1 + pos_in_cf


def isotp_to_torque(isotp_idx: int, subfunction_bytes: int = 1) -> int | None:
    """Convert ISO-TP index to Torque data byte index.

    Args:
        isotp_idx: ISO-TP payload index.
        subfunction_bytes: 1 for ``21xx`` PIDs, 2 for ``22xxxx`` DIDs.

    Returns None if the index points to the SID or subfunction bytes.
    """
    # UDS header = 1 (SID) + subfunction_bytes
    header = 1 + subfunction_bytes
    if isotp_idx < header:
        return None
    return isotp_idx - header


def torque_to_isotp(torque_idx: int, subfunction_bytes: int = 1) -> int:
    """Convert Torque data byte index to ISO-TP index."""
    return torque_idx + 1 + subfunction_bytes


def torque_to_bix(torque_idx: int) -> int:
    """Convert Torque byte index to bit index (bix)."""
    return torque_idx * 8


def bix_to_torque(bix: int) -> int:
    """Convert bit index (bix) to Torque byte index."""
    return bix // 8


# ---------------------------------------------------------------------------
# Compound conversions
# ---------------------------------------------------------------------------


def wican_to_torque(wican_idx: int, subfunction_bytes: int = 1) -> int | None:
    """Convert WiCAN index directly to Torque byte index.

    Returns None if the index points to PCI, SID, or subfunction bytes.
    """
    isotp = wican_to_isotp(wican_idx)
    if isotp is None:
        return None
    return isotp_to_torque(isotp, subfunction_bytes)


def torque_to_wican(torque_idx: int, subfunction_bytes: int = 1) -> int:
    """Convert Torque byte index to WiCAN index."""
    isotp = torque_to_isotp(torque_idx, subfunction_bytes)
    return isotp_to_wican(isotp)


def wican_to_bix(wican_idx: int, subfunction_bytes: int = 1) -> int | None:
    """Convert WiCAN index to Torque bit index (bix)."""
    t = wican_to_torque(wican_idx, subfunction_bytes)
    if t is None:
        return None
    return torque_to_bix(t)


def bix_to_wican(bix: int, subfunction_bytes: int = 1) -> int:
    """Convert Torque bit index (bix) to WiCAN index."""
    return torque_to_wican(bix_to_torque(bix), subfunction_bytes)


# ---------------------------------------------------------------------------
# Torque letter notation
# ---------------------------------------------------------------------------


def torque_idx_to_letter(idx: int) -> str:
    """Convert Torque byte index to letter notation.

    0→A, 1→B, ..., 25→Z, 26→AA, 27→AB, ..., 51→AZ, 52→BA, ...
    """
    if idx < 26:
        return chr(ord("A") + idx)
    first = chr(ord("A") + (idx // 26) - 1)
    second = chr(ord("A") + (idx % 26))
    return first + second


def letter_to_torque_idx(letter: str) -> int:
    """Convert Torque letter notation to byte index.

    A→0, B→1, ..., Z→25, AA→26, AB→27, ...
    """
    letter = letter.upper()
    if len(letter) == 1:
        return ord(letter) - ord("A")
    if len(letter) == 2:
        return (ord(letter[0]) - ord("A") + 1) * 26 + (ord(letter[1]) - ord("A"))
    raise ValueError(f"Invalid Torque letter notation: {letter!r}")


# ---------------------------------------------------------------------------
# WiCAN expression byte index extraction (moved from formatting.py)
# ---------------------------------------------------------------------------


def extract_byte_indices(expression: str) -> set[int]:
    """Extract all WiCAN byte indices referenced in a WiCAN expression.

    Patterns: ``B03``, ``S03``, ``B03:0`` (bit), ``[B04:B05]`` (range),
    ``[S04:S05]`` (signed range).
    """
    indices: set[int] = set()
    # Multi-byte ranges: [B04:B05] or [S04:S05]
    for m in re.finditer(r"\[([BS])(\d+):([BS])(\d+)\]", expression):
        lo, hi = int(m.group(2)), int(m.group(4))
        indices.update(range(lo, hi + 1))
    # Single byte: B03, S03, B03:0 (bit access)
    for m in re.finditer(r"(?<!\[)([BS])(\d+)(?::\d+)?(?!\d)", expression):
        indices.add(int(m.group(2)))
    return indices


def wican_to_elm_idx(wican_idx: int, payload_len: int) -> int | None:
    """Map a WiCAN AutoPID byte index to an ELM payload byte index.

    This is the original function from formatting.py, kept for backward
    compatibility. For new code, prefer the specific conversion functions.

    Returns None if the index points to a PCI byte.
    """
    if payload_len <= 7:
        # Single frame: [PCI] [d d d d d d d]
        if wican_idx == 0:
            return None
        return wican_idx - 1
    else:
        return wican_to_isotp(wican_idx)


# ---------------------------------------------------------------------------
# Bulk conversion / table generation
# ---------------------------------------------------------------------------


def conversion_table(
    max_wican: int = 71,
    subfunction_bytes: int = 1,
) -> list[dict]:
    """Generate the full conversion table as a list of dicts.

    Each entry has keys: wican, isotp, torque, torque_letter, bix.
    Values are None where the index maps to a protocol header byte.
    """
    rows = []
    for w in range(max_wican + 1):
        isotp = wican_to_isotp(w)
        torque = wican_to_torque(w, subfunction_bytes)
        rows.append(
            {
                "wican": w,
                "isotp": isotp,
                "torque": torque,
                "torque_letter": torque_idx_to_letter(torque) if torque is not None else None,
                "bix": torque_to_bix(torque) if torque is not None else None,
            }
        )
    return rows
