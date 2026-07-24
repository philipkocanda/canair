"""Byte index conversion between WiCAN, ISO-TP, Torque, and OBDb notations.

Protocol stack: CAN → ISO-TP → UDS

**Which space is canonical:** ISO-TP (the reassembled UDS payload, no framing) is
the natural byte space — it's what every transport returns and what SavvyCAN /
ImHex show. The **WiCAN index is a firmware-specific *view*** (ISO-TP with the
ISO-TP PCI bytes re-inserted) that only exists because WiCAN AutoPID expressions
address that interleaved buffer. Keep analysis reasoning in ISO-TP where you can;
convert to WiCAN only at the edges that must feed the firmware — evaluating a
stored expression (:func:`payload_to_wican_bytes`) and persisting/promoting one.

- **WiCAN index**: Index into CAN frame data including PCI bytes.
  PCI bytes sit at positions 0 (single-frame), 0-1 (multi-frame first frame),
  and 8, 16, 24, ... (consecutive frames).
- **ISO-TP index**: Pure ISO-TP payload index (no PCI bytes).
- **Torque index**: UDS data payload, skipping SID + subfunction byte(s).
  Torque 1: 1-byte subfunction (e.g. ``21 01``), data starts at ISO-TP offset 2.
  Torque 2: 2-byte subfunction (e.g. ``22 C0 0B``), data starts at ISO-TP offset 3.
- **bix (bit index)**: Torque byte index x 8. Used by Torque app and OBDb.
- **Torque letter**: A=byte 0, B=byte 1, ..., Z=byte 25, AA=byte 26, AB=byte 27, ...

See ``docs/concepts/byte-indexing.md`` (primer) and
``docs/concepts/wican-byte-index.md`` (firmware-grounded reference) for the full
conversion tables.
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

    Only 1- and 2-letter notation is supported (index 0–701), matching
    :func:`letter_to_torque_idx`.
    """
    if idx < 0:
        raise ValueError(f"Torque byte index out of range: {idx}")
    if idx < 26:
        return chr(ord("A") + idx)
    if idx < 702:
        first = chr(ord("A") + (idx // 26) - 1)
        second = chr(ord("A") + (idx % 26))
        return first + second
    raise ValueError(f"Torque byte index out of range for 2-letter notation: {idx}")


def torque_label(torque_idx: int | None) -> str | None:
    """Display label for a Torque byte index: the letter, or the number past ``ZZ``.

    :func:`torque_idx_to_letter` models only Torque's 1–2 letter notation
    (``A``..``ZZ``, index 0–701) and *raises* beyond it. This presentation-layer
    wrapper falls back to the raw Torque byte-index string for larger indices
    rather than crash — letters past ``ZZ`` aren't a notation Torque/OBDb sheets
    use anyway. Returns ``None`` for a ``None`` index (a PCI/header byte).
    """
    if torque_idx is None:
        return None
    try:
        return torque_idx_to_letter(torque_idx)
    except ValueError:
        return str(torque_idx)


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


def extract_bit_indices(expression: str) -> set[tuple[int, int]]:
    """Extract ``(byte_offset, bit)`` pairs a WiCAN expression reads bit-wise.

    Only explicit bit accessors ``Bn:k`` / ``Sn:k`` are returned (e.g. ``B10:5``
    → ``(10, 5)``). Whole-byte and multi-byte reads produce no bit pairs — use
    :func:`extract_byte_indices` for byte-level coverage.
    """
    bits: set[tuple[int, int]] = set()
    for m in re.finditer(r"(?<!\[)[BS](\d+):(\d+)(?!\d)", expression):
        bits.add((int(m.group(1)), int(m.group(2))))
    return bits


def mapped_bits(
    parameters: dict, *, include_unverified: bool = True
) -> dict[tuple[int, int], tuple[str, bool]]:
    """Which ``(offset, bit)`` positions a PID's params cover, and at what confidence.

    Returns ``{(offset, bit): (param_name, verified)}`` for params that read an
    individual bit (``Bn:k``). First writer wins, but a verified mapping upgrades
    an unverified one for the same bit — mirroring :func:`mapped_offsets`.
    """
    mapped: dict[tuple[int, int], tuple[str, bool]] = {}
    for name, pdef in parameters.items():
        expr = pdef.get("expression") or ""
        if not expr:
            continue
        verified = bool(pdef.get("verified", False))
        if not verified and not include_unverified:
            continue
        for pos in extract_bit_indices(expr):
            prev = mapped.get(pos)
            if prev is None or (verified and not prev[1]):
                mapped[pos] = (name, verified)
    return mapped


def mapped_offsets(
    parameters: dict, *, include_unverified: bool = True
) -> dict[int, tuple[str, bool]]:
    """Which WiCAN byte offsets a PID's parameters cover, and at what confidence.

    Returns ``{offset: (param_name, verified)}`` where ``verified`` reflects the
    covering param's ``verified`` flag. The first param encountered for an offset
    wins, but a *verified* mapping is preferred over an unverified one for the
    same offset (so a byte confirmed by any param reads as confirmed).

    With ``include_unverified=False``, bytes covered only by unverified params
    are treated as unmapped and omitted — the caller sees them as still-open work.
    """
    mapped: dict[int, tuple[str, bool]] = {}
    for name, pdef in parameters.items():
        expr = pdef.get("expression") or ""
        if not expr:
            continue
        verified = bool(pdef.get("verified", False))
        if not verified and not include_unverified:
            continue
        for off in extract_byte_indices(expr):
            prev = mapped.get(off)
            # First writer wins, but a verified mapping upgrades an unverified one.
            if prev is None or (verified and not prev[1]):
                mapped[off] = (name, verified)
    return mapped


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


def elm_to_wican_idx(elm_idx: int, payload_len: int) -> int:
    """Inverse of :func:`wican_to_elm_idx`: ELM payload index → WiCAN byte index.

    Maps a byte position in the raw payload hex back to its WiCAN frame index
    (which skips PCI framing bytes at 0/1, 8, 16, 24, ...).
    """
    if payload_len <= 7:
        # Single frame: [PCI] [d d d d d d d]
        return elm_idx + 1
    return isotp_to_wican(elm_idx)


# ---------------------------------------------------------------------------
# UDS payload → WiCAN frame reconstruction
# ---------------------------------------------------------------------------


def payload_to_wican_frame(payload_bytes: list[int]) -> list[tuple[int, int | None]]:
    """Convert raw UDS payload bytes to a WiCAN frame with PCI bytes inserted.

    Returns a list of ``(byte_value, payload_index_or_None)`` tuples;
    ``payload_index`` is None for PCI bytes.
    """
    n = len(payload_bytes)
    if n <= 7:
        # Single Frame: PCI = 0x0n where n = length
        frame: list[tuple[int, int | None]] = [(n, None)]  # SF PCI
        for i, b in enumerate(payload_bytes):
            frame.append((b, i))
        return frame
    # First Frame: PCI = 10 nn (2 bytes)
    frame = [(0x10 | ((n >> 8) & 0x0F), None), (n & 0xFF, None)]
    pi = 0  # payload index
    # First frame carries 6 data bytes
    for _ in range(min(6, n)):
        frame.append((payload_bytes[pi], pi))
        pi += 1
    # Consecutive frames: PCI = 2x, carry 7 data bytes each
    seq = 1
    while pi < n:
        frame.append((0x20 | (seq & 0x0F), None))  # CF PCI
        seq += 1
        for _ in range(min(7, n - pi)):
            frame.append((payload_bytes[pi], pi))
            pi += 1
    return frame


# Backwards-compatible alias (was defined in bix.py).
_payload_to_wican_frame = payload_to_wican_frame


class NotAFrameError(ValueError):
    """Raised when bytes given as an already-framed CAN payload aren't one.

    The ISO-TP frame type lives in the first byte's high nibble: ``0`` Single
    Frame, ``1`` First Frame, ``2`` Consecutive Frame, ``3`` Flow Control. A
    Flow-Control frame is never a response payload, and an empty input has no
    frame type — both are rejected so ``bix --annotate --raw`` fails loudly
    rather than mislabelling garbage.
    """


def framed_to_wican_frame(framed_bytes: list[int]) -> list[tuple[int, int | None]]:
    """Index an ALREADY-FRAMED CAN payload (ISO-TP PCI bytes present).

    The inverse companion to :func:`payload_to_wican_frame`: instead of *inserting*
    PCI bytes into a PCI-stripped UDS payload, this walks a payload that already
    carries its framing (e.g. a raw response copied straight off the bus, PCI and
    all) and marks which bytes are PCI. Returns the same
    ``[(byte_value, isotp_index_or_None)]`` shape, so every downstream column
    (ISO-TP / Torque / bix / Role) works identically for both input kinds.

    The frame layout is read from the ISO-TP PCI itself (first-byte high nibble),
    so it is exact — no length guessing:

      - ``0x0N`` Single Frame → 1 PCI byte at index 0, data follows.
      - ``0x1N NN`` First Frame → 2 PCI bytes, 6 data bytes, then one CF PCI byte
        (``0x2x``) at the start of every following 8-byte block.
      - ``0x2N`` Consecutive Frame (a lone CF) → 1 PCI byte, then data.

    Raises :class:`NotAFrameError` for an empty input or a Flow-Control (``0x3N``)
    first byte.
    """
    if not framed_bytes:
        raise NotAFrameError("empty input is not a CAN frame")
    ftype = (framed_bytes[0] >> 4) & 0x0F
    frame: list[tuple[int, int | None]] = []
    pi = 0  # running ISO-TP payload index

    if ftype == 0x0:  # Single Frame: [PCI] [data...]
        frame.append((framed_bytes[0], None))
        for b in framed_bytes[1:]:
            frame.append((b, pi))
            pi += 1
        return frame

    if ftype == 0x2:  # lone Consecutive Frame: [PCI] [data...]
        frame.append((framed_bytes[0], None))
        for b in framed_bytes[1:]:
            frame.append((b, pi))
            pi += 1
        return frame

    if ftype == 0x1:  # First Frame: [PCI PCI] [6 data] then CF PCI every 8 bytes
        for w, b in enumerate(framed_bytes):
            in_frame_pos = w % 8
            is_pci = (w < 2) if w < 8 else (in_frame_pos == 0)
            if is_pci:
                frame.append((b, None))
            else:
                frame.append((b, pi))
                pi += 1
        return frame

    raise NotAFrameError(
        f"first byte 0x{framed_bytes[0]:02X} is a Flow-Control/unknown ISO-TP "
        "frame type, not a response payload"
    )


def payload_to_wican_bytes(payload_hex: str) -> bytes:
    """Raw UDS payload hex → WiCAN frame bytes (PCI inserted).

    The single canonical hex→frame converter used by ``decode``, ``align``, and
    the cross-signal analysis engine, so byte indexing is identical everywhere.
    """
    payload_hex = payload_hex.replace(" ", "")
    payload_bytes = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
    return bytes(b for b, _ in payload_to_wican_frame(payload_bytes))


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
                "torque_letter": torque_label(torque),
                "bix": torque_to_bix(torque) if torque is not None else None,
            }
        )
    return rows
