"""Coverage findings for one PID's signals against a representative payload.

Pure functions over ``(parameters, payload_hex, sfb)`` — no capture loading, no
rendering — so every finding category is unit-testable on a literal payload.
"""

from typing import NotRequired, TypedDict

from canlib.uds.byteindex import (
    declared_bits,
    extract_byte_indices,
    mapped_offsets,
    payload_index_to_wican,
    payload_to_wican_frame,
    references_full_byte,
)
from canlib.uds.byteindex import mappable_data_indices as _mappable_data_indices
from canlib.uds.support_map import MapDecl, ResolvedMap, resolve


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
    # With a support map: bits the map does not mark, so neither defined nor open work.
    not_signals: NotRequired[list[int]]


class PastEnd(TypedDict):
    """A signal whose expression reads WiCAN bytes the payload does not reach."""

    signal: str
    bytes: list[int]


class OutsideMap(TypedDict):
    """A defined signal reading a position the PID's support map does not mark."""

    signal: str
    byte: int
    bit: int | None
    # True for a bit directly above a mark, which is read as the rest of a multi-bit field.
    continuation: bool


class SupportMapFinding(TypedDict):
    """What a declared support map did to a PID's findings."""

    unit: str
    mask: str
    # WiCAN indices of the map's own bytes, and of covered bytes that carry no signal.
    map_bytes: list[int]
    not_signal_bytes: list[int]


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
    support_map: NotRequired[SupportMapFinding]
    outside_map: NotRequired[list[OutsideMap]]
    # A declared map the representative payload is too short to contain.
    support_map_past_end: NotRequired[bool]


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


def wican_frame_len(payload_hex: str) -> int:
    """How many WiCAN byte indices ``payload_hex`` occupies, PCI bytes included."""
    payload = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
    return len(payload_to_wican_frame(payload))


def analyze_pid(
    parameters: dict, payload_hex: str, sfb: int, support: MapDecl | None = None
) -> PidAnalysis:
    """Return coverage findings for one PID's parameters against a payload.

    A signal reading a byte at or beyond the payload's end is reported in
    ``past_end`` rather than dropped: it either outgrew a short reply (a single
    truncated read, a ``variable_length:`` PID's shorter variant) or has the wrong
    offset, and both deserve a look.

    ``support`` is the PID's declared support map. With one, its own bytes and the bytes it
    marks as carrying no signal leave ``unmapped``, ``BITS`` only counts marked bits, and
    defined signals reading unmarked positions are reported in ``outside_map``. Without one
    the result is exactly what it always was.
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

    out: PidAnalysis = {
        "data_bytes": len(data_idx),
        "payload_len": len(payload_hex) // 2,
        "frame_len": frame_len,
        "unmapped": unmapped,
        "unverified_mapped": unverified_mapped,
        "incomplete_bitfields": incomplete,
        "past_end": past_end,
    }
    if support is not None:
        payload = bytes.fromhex(payload_hex)
        resolved = resolve(support, payload, 1 + sfb)
        if resolved is None:
            out["support_map_past_end"] = True
        else:
            _apply_support_map(out, resolved, parameters, payload)
    return out


def _apply_support_map(out: PidAnalysis, rm: ResolvedMap, parameters: dict, payload: bytes) -> None:
    """Fold a resolved support map into ``out``: drop non-signals, find reads outside it."""
    to_wican = payload_index_to_wican(list(payload))
    to_index = {w: i for i, w in to_wican.items()}
    map_bytes = sorted(to_wican[i] for i in rm.map_indices)
    not_signal = sorted(to_wican[i] for i in rm.covered if not rm.is_signal_byte(i))
    skip = {*map_bytes, *not_signal}
    out["unmapped"] = [b for b in out["unmapped"] if b not in skip]

    gaps: list[BitfieldGap] = []
    for gap in out["incomplete_bitfields"]:
        index = to_index.get(gap["byte"])
        if index is None or index not in rm.covered:
            gaps.append(gap)
            continue
        marks, have = rm.signal_bits(index), set(gap["have"])
        if marks - have:
            gaps.append(
                {
                    "byte": gap["byte"],
                    "have": gap["have"],
                    "missing": sorted(marks - have),
                    "also_whole": gap["also_whole"],
                    "not_signals": sorted(set(range(8)) - marks - have),
                }
            )
    out["incomplete_bitfields"] = gaps

    outside: list[OutsideMap] = []
    for name, pdef in parameters.items():
        expr = pdef.get("expression") or ""
        for byte, bits in declared_bits(pdef).items():
            index = to_index.get(byte)
            if index is None:
                continue
            bad, cont = rm.outside(index, bits)
            outside += [
                {"signal": str(name), "byte": byte, "bit": k, "continuation": k in cont}
                for k in sorted(bad | cont)
            ]
        for byte in sorted(extract_byte_indices(expr)):
            index = to_index.get(byte)
            if (
                index in rm.covered
                and references_full_byte(expr, byte)
                and not rm.is_signal_byte(index)
            ):
                outside.append(
                    {"signal": str(name), "byte": byte, "bit": None, "continuation": False}
                )
    out["outside_map"] = outside
    out["support_map"] = {
        "unit": rm.decl.unit,
        "mask": rm.mask_hex,
        "map_bytes": map_bytes,
        "not_signal_bytes": not_signal,
    }
