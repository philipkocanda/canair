"""Coverage findings for one PID's signals against a representative payload.

Pure functions over ``(parameters, payload_hex, sfb)`` — no capture loading, no
rendering — so every finding category is unit-testable on a literal payload.
"""

import re
from typing import TypedDict

from canlib.uds.byteindex import (
    extract_bit_indices,
    extract_byte_indices,
    mapped_offsets,
    payload_to_wican_frame,
)
from canlib.uds.byteindex import mappable_data_indices as _mappable_data_indices


class BitfieldGap(TypedDict):
    """One byte read bit-by-bit with some bits still undecoded.

    ``also_whole`` marks a byte that some param *additionally* reads whole
    (``Bn``/``Sn``). That does not account for the byte's bits, so the gap is
    still reported — but it is flagged, because on a byte that is genuinely a
    discrete code rather than independent flags the gap may be intentional.
    """

    byte: int
    have: list[int]
    missing: list[int]
    also_whole: bool


class PastEnd(TypedDict):
    """A signal whose expression reads WiCAN bytes the payload does not reach."""

    signal: str
    bytes: list[int]


class PidAnalysis(TypedDict):
    """Coverage findings for one PID's parameters against a payload."""

    data_bytes: int
    # Reassembled payload length in bytes. Carried so the render layer can index
    # WiCAN offsets against the *actual* frame layout — a single-frame (<=7B)
    # response has one PCI byte, a multi-frame response two.
    payload_len: int
    # One past the last WiCAN index the payload occupies (PCI bytes included).
    frame_len: int
    unmapped: list[int]
    unverified_mapped: list[int]
    incomplete_bitfields: list[BitfieldGap]
    past_end: list[PastEnd]


def subfunction_bytes(pid_code: str) -> int:
    """Number of subfunction/DID bytes after the service byte.

    ``2101`` -> 1 (service 21, sub 01); ``22BC03`` -> 2 (service 22, DID BC03).
    """
    return max(0, (len(pid_code) - 2) // 2)


def mappable_data_indices(payload_hex: str, sfb: int) -> list[int]:
    """Deprecated alias — the primitive now lives in :mod:`canlib.uds.byteindex`.

    Kept so existing importers of this name keep working; new code should import
    it from ``canlib.uds.byteindex`` directly.
    """
    return _mappable_data_indices(payload_hex, sfb)


def references_full_byte(expr: str, idx: int) -> bool:
    """True if ``expr`` reads byte ``idx`` as a whole byte (Bn/Sn, not Bn:k)."""
    return re.search(rf"(?<!\[)[BS]0*{idx}(?![0-9:])", expr) is not None


def declared_bits(pdef: dict) -> dict[int, set[int]]:
    """Map WiCAN byte index -> bit positions a param accounts for.

    Two models declare a bit: the ``Bn:k``/``Sn:k`` accessor (one param per bit)
    and a ``type: bitmask`` param whose ``bits:`` map labels positions of the
    whole byte it reads. Both are real decodings, so both count as coverage —
    otherwise converting a half-labelled byte to a bitmask would hide the rest
    of the gap.
    """
    expr = pdef.get("expression") or ""
    if not expr:
        return {}
    out: dict[int, set[int]] = {}
    for byte, bit in extract_bit_indices(expr):
        out.setdefault(byte, set()).add(bit)
    if pdef.get("type") == "bitmask":
        labelled = {int(k) for k in (pdef.get("bits") or {})}
        if labelled:
            for byte in extract_byte_indices(expr):
                out.setdefault(byte, set()).update(labelled)
    return out


def wican_frame_len(payload_hex: str) -> int:
    """How many WiCAN byte indices ``payload_hex`` occupies, PCI bytes included."""
    payload = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
    return len(payload_to_wican_frame(payload))


def analyze_pid(parameters: dict, payload_hex: str, sfb: int) -> PidAnalysis:
    """Return coverage findings for one PID's parameters against a payload.

    A signal reading a byte at or beyond the payload's end is reported in
    ``past_end`` rather than dropped: it either outgrew a short reply (a single
    truncated read, a ``variable_length:`` PID's shorter variant) or has the wrong
    offset, and both deserve a look.
    """
    data_idx = mappable_data_indices(payload_hex, sfb)
    frame_len = wican_frame_len(payload_hex)
    covered: set[int] = set()
    all_bits: dict[int, set[int]] = {}
    past_end: list[PastEnd] = []
    for name, pdef in parameters.items():
        expr = pdef.get("expression", "")
        if not expr:
            continue
        refs = extract_byte_indices(expr)
        covered |= refs
        beyond = sorted(i for i in refs if i >= frame_len)
        if beyond:
            past_end.append({"signal": str(name), "bytes": beyond})
        for b, bits in declared_bits(pdef).items():
            all_bits.setdefault(b, set()).update(bits)

    unmapped = [i for i in data_idx if i not in covered]

    # Bytes covered only by unverified params — mapped, but still needing confirmation.
    verified_covered = set(mapped_offsets(parameters, include_unverified=False))
    unverified_mapped = [i for i in data_idx if i in covered and i not in verified_covered]

    incomplete: list[BitfieldGap] = []
    for b, bits in sorted(all_bits.items()):
        if b not in data_idx or len(bits) >= 8:
            continue
        # A whole-byte read does NOT excuse the gap: `DEBUG_*_FLAGS`-style raw
        # bytes coexist with per-bit params by convention, and suppressing here
        # hid genuinely undecoded bits on the profile's most-captured bitfields.
        # Flag it instead and let the reader judge.
        also_whole = any(
            references_full_byte(p.get("expression", ""), b) for p in parameters.values()
        )
        incomplete.append(
            {
                "byte": b,
                "have": sorted(bits),
                "missing": [x for x in range(8) if x not in bits],
                "also_whole": also_whole,
            }
        )

    return {
        "data_bytes": len(data_idx),
        "payload_len": len(payload_hex) // 2,
        "frame_len": frame_len,
        "unmapped": unmapped,
        "unverified_mapped": unverified_mapped,
        "incomplete_bitfields": incomplete,
        "past_end": past_end,
    }
