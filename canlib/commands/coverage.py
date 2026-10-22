"""Audit PID definitions for decoding gaps (unmapped bytes, partial bitfields).

For every ECU/PID in ecus/, this cross-references the signal expressions
against the payload length of that PID's newest captured session and reports:

  - UNMAPPED  data bytes present in the payload that no expression reads
  - UNVERIFIED data bytes mapped only by an unverified signal (needs confirming)
  - BITS      bytes read bit-wise (Bn:k, or a type: bitmask map) with some bits
              still undecoded. A byte some signal *also* reads whole is still
              reported, flagged "(also read whole)" — a raw-byte read does not
              account for the byte's individual bits.
  - NO CAPTURE PIDs that have signals defined but no payload captured yet

Byte indices are WiCAN Bnn (the flat CAN-frame index used by expressions,
including PCI bytes). PCI, SID, and subfunction/DID-echo bytes are excluded
from the "mappable data" set, so they never show up as UNMAPPED.

Examples:
  canair coverage                 # audit every ECU/PID
  canair coverage IGPM            # only the IGPM ECU
  canair coverage IGPM 22BC03     # a single ECU/PID
  canair coverage --bitfields     # only incomplete-bitfield findings
  canair coverage --unmapped      # only unmapped-byte findings
  canair coverage --unverified    # only bytes mapped by an unverified param
  canair coverage --no-capture    # only PIDs missing captures
  canair coverage --all           # include fully-mapped PIDs too
  canair coverage --json          # machine-readable output
  canair coverage --until 2026-08-01  # lengths as of that date
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import NotRequired, TypedDict

from canlib import ansi
from canlib.byteindex import extract_bit_indices, extract_byte_indices, mapped_offsets
from canlib.byteindex import mappable_data_indices as _mappable_data_indices
from canlib.capture_dates import add_scope_args, resolve_scope_bounds
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.commands._hints import pid_completer as _pid_completer
from canlib.notation import (
    add_notation_arg,
    relabel_signal,
    resolve_notation,
    subfunction_bytes_for_pid,
)
from canlib.pids import build_ecu_index, load_pids

NAME = "coverage"
ALIASES = ["cov"]


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


class PidAnalysis(TypedDict):
    """Coverage findings for one PID's parameters against a payload."""

    data_bytes: int
    # Reassembled payload length in bytes. Carried so the render layer can index
    # WiCAN offsets against the *actual* frame layout — a single-frame (<=7B)
    # response has one PCI byte, a multi-frame response two.
    payload_len: int
    unmapped: list[int]
    unverified_mapped: list[int]
    incomplete_bitfields: list[BitfieldGap]


class CoverageEntry(TypedDict):
    """A per-PID coverage audit result (fields depend on capture availability)."""

    ecu: str
    pid: str
    params: int
    verified: int
    no_capture: NotRequired[bool]
    capture: NotRequired[dict[str, str | bool]]
    data_bytes: NotRequired[int]
    payload_len: NotRequired[int]
    unmapped: NotRequired[list[int]]
    unverified_mapped: NotRequired[list[int]]
    incomplete_bitfields: NotRequired[list[BitfieldGap]]


def is_frame_aligned(length: int) -> bool:
    """True if ``length`` is exactly a whole number of CAN frames (``6 + 7k`` bytes).

    A multi-frame payload of this length is indistinguishable from a shorter one whose
    final frame was padded out to 8 bytes.
    """
    return length > 7 and (length - 6) % 7 == 0


# Bytes ECUs commonly pad an ISO-TP frame with. Old captures kept that filler, so a
# payload ending in anything else cannot have a padded tail.
_FILL_BYTES = frozenset({"00", "55", "AA", "CC", "FF"})


def may_be_padded(payload_hex: str, recorded_version: str | None) -> bool:
    """True if ``payload_hex``'s length may include trailing frame padding.

    Only captures from before sessions recorded a ``version`` stored the final frame
    padded, and only a frame-aligned length ending in a fill byte can hide padding.
    """
    n = len(payload_hex) // 2
    return not recorded_version and is_frame_aligned(n) and payload_hex[-2:].upper() in _FILL_BYTES


def session_length(lengths: Counter) -> int:
    """The payload length one session reports: its most common, longer on a tie."""
    return max(lengths, key=lambda n: (lengths[n], n))


def representative_payloads(entries: Iterable[Mapping]) -> dict[tuple[str, str], dict]:
    """Return {(ECU_UPPER, PID_UPPER): {payload, date, label, file, ...}} for the
    payload that sets each PID's length.

    ``entries`` are flattened capture rows (:func:`canlib.capture_store.load_all_captures`)
    in chronological order. The length comes from the PID's **newest session** (most
    common length within it). The longest-ever payload is the wrong choice: before
    2026-07-28 the final CAN frame of a multi-frame response was stored padded to 8
    bytes, and that padding is invisible in the stored hex (the ISO-TP length is gone),
    so the padded length always won and its filler was reported as undecoded data.
    Taking the newest session also ignores one-off strays from an older session.

    ``may_include_padding`` (see :func:`may_be_padded`) flags a length that may still be
    padded because no newer session has read the PID.
    """
    sessions: dict[tuple, dict[tuple[str, str], Counter]] = {}
    meta: dict[tuple, Mapping] = {}
    sample: dict[tuple, dict[int, str]] = {}
    for e in entries:
        payload = (e.get("payload") or "").replace(" ", "")
        # An NRC (7F…) carries no data bytes to audit.
        if not payload or payload.upper().startswith("7F"):
            continue
        skey = (e.get("_path") or e.get("file"), e.get("_session_idx"))
        pkey = (str(e.get("ecu", "")).upper(), str(e.get("pid", "")).upper())
        n = len(payload) // 2
        sessions.setdefault(skey, {}).setdefault(pkey, Counter())[n] += 1
        meta.setdefault(skey, e)
        sample.setdefault((skey, pkey), {}).setdefault(n, payload)

    best: dict[tuple[str, str], dict] = {}
    for skey, pids in sessions.items():
        m = meta[skey]
        date = str(m.get("date") or "")
        for pkey, counts in pids.items():
            prev = best.get(pkey)
            if prev is not None and date < prev["date"]:
                continue
            n = session_length(counts)
            best[pkey] = {
                "payload": sample[(skey, pkey)][n],
                "date": date,
                "label": m.get("session_label", ""),
                "file": m.get("file", ""),
                "may_include_padding": may_be_padded(
                    sample[(skey, pkey)][n], m.get("session_version")
                ),
            }
    return best


def load_representative_payloads(
    since=None, until=None, state=None, label=None
) -> dict[tuple[str, str], dict]:
    """:func:`representative_payloads` over the active profile's captures, in scope."""
    from canlib.capture_dates import filter_by_date_range, filter_by_text
    from canlib.capture_store import load_all_captures

    entries = filter_by_date_range(load_all_captures(), since, until)
    return representative_payloads(filter_by_text(entries, state=state, label=label))


def subfunction_bytes(pid_code: str) -> int:
    """Number of subfunction/DID bytes after the service byte.

    ``2101`` -> 1 (service 21, sub 01); ``22BC03`` -> 2 (service 22, DID BC03).
    """
    return max(0, (len(pid_code) - 2) // 2)


def mappable_data_indices(payload_hex: str, sfb: int) -> list[int]:
    """Deprecated alias — the primitive now lives in :mod:`canlib.byteindex`.

    Kept so existing importers of this name keep working; new code should import
    it from ``canlib.byteindex`` directly.
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


def analyze_pid(parameters: dict, payload_hex: str, sfb: int) -> PidAnalysis:
    """Return coverage findings for one PID's parameters against a payload."""
    data_idx = mappable_data_indices(payload_hex, sfb)
    covered: set[int] = set()
    all_bits: dict[int, set[int]] = {}
    for pdef in parameters.values():
        expr = pdef.get("expression", "")
        if not expr:
            continue
        covered |= extract_byte_indices(expr)
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
        "unmapped": unmapped,
        "unverified_mapped": unverified_mapped,
        "incomplete_bitfields": incomplete,
    }


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Audit PID definitions for decoding gaps",
        description="Audit PID definitions for decoding gaps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "ecu", nargs="?", help="Filter to one ECU (e.g. IGPM)"
    ).completer = _ecu_completer
    parser.add_argument(
        "pid", nargs="?", help="Filter to one PID (e.g. 22BC03)"
    ).completer = _pid_completer
    parser.add_argument("--all", action="store_true", help="Include fully-mapped PIDs (no gaps)")
    parser.add_argument(
        "--unmapped", action="store_true", help="Only report unmapped-byte findings"
    )
    parser.add_argument(
        "--unverified",
        action="store_true",
        help="Only report bytes mapped by an unverified signal (needs confirming)",
    )
    parser.add_argument(
        "--bitfields",
        action="store_true",
        help="Only report bytes with undecoded bits (partial bitfields). A byte "
        "some signal also reads whole is still reported, flagged '(also read whole)'",
    )
    parser.add_argument(
        "--no-capture", action="store_true", help="Only report PIDs with params but no capture"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    add_notation_arg(parser)
    add_scope_args(parser)
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    ecu_index = build_ecu_index(load_pids())
    payloads = load_representative_payloads(
        since, until, state=getattr(args, "state", None), label=getattr(args, "label", None)
    )

    # Accept an ECU-registry alias (e.g. LDC for OBC) or any case, matching
    # `canair captures`/`decode`. Canonicalises to the ecus/ key before filtering.
    from canlib.ecus import canonical_ecu_name_safe

    ecu_filter = canonical_ecu_name_safe(args.ecu).upper() if args.ecu else None
    pid_filter = args.pid.upper() if args.pid else None

    results: list[CoverageEntry] = []
    for ecu in sorted(ecu_index):
        if ecu_filter and ecu != ecu_filter:
            continue
        for pid in sorted(ecu_index[ecu]["pids"]):
            if pid_filter and pid != pid_filter:
                continue
            parameters = ecu_index[ecu]["pids"][pid]["parameters"]
            if not parameters:
                continue
            cap = payloads.get((ecu, pid))
            entry: CoverageEntry = {
                "ecu": ecu,
                "pid": pid,
                "params": len(parameters),
                "verified": sum(1 for p in parameters.values() if p.get("verified")),
            }
            if cap is None:
                entry["no_capture"] = True
            else:
                entry["capture"] = {
                    "date": cap["date"],
                    "file": cap["file"],
                    "may_include_padding": cap.get("may_include_padding", False),
                }
                entry.update(analyze_pid(parameters, cap["payload"], subfunction_bytes(pid)))
            results.append(entry)

    # Apply category filters
    def keep(e):
        if e.get("no_capture"):
            return not (args.unmapped or args.unverified or args.bitfields)
        if args.no_capture:
            return False
        has_unmapped = bool(e.get("unmapped"))
        has_unverified = bool(e.get("unverified_mapped"))
        has_bits = bool(e.get("incomplete_bitfields"))
        if args.unmapped:
            return has_unmapped
        if args.unverified:
            return has_unverified
        if args.bitfields:
            return has_bits
        if args.all:
            return True
        return has_unmapped or has_unverified or has_bits

    results = [e for e in results if keep(e)]

    if args.json:
        json.dump(results, sys.stdout, indent=2, default=str)
        print()
        return 0

    if not results:
        print("No matching findings.")
        return 0

    n_nocap = sum(1 for e in results if e.get("no_capture"))
    n_gaps = len(results) - n_nocap
    print(
        f"\n{ansi.BOLD}PID coverage audit{ansi.RESET} — {n_gaps} PID(s) with gaps, "
        f"{n_nocap} without captures\n"
    )

    notation = resolve_notation(args.notation)

    for e in results:
        sub_bytes = subfunction_bytes_for_pid(e["pid"])
        # The frame layout depends on the payload's length, so the non-WiCAN
        # notations need it to name the right byte (see ByteRef.from_wican).
        payload_len = e.get("payload_len")
        header = (
            f"  {ansi.BOLD}{ansi.CYAN}{e['ecu']} {e['pid']}{ansi.RESET} "
            f"{ansi.DIM}({e['params']}p, {e['verified']} verified){ansi.RESET}"
        )
        if e.get("no_capture"):
            print(f"{header}  {ansi.YELLOW}NO CAPTURE{ansi.RESET}")
            continue
        padding = (
            ", may include trailing frame padding" if e["capture"]["may_include_padding"] else ""
        )
        print(
            f"{header}  {ansi.DIM}{e['data_bytes']} data bytes, "
            f"{e['capture']['date']}{padding}{ansi.RESET}"
        )
        if e["unmapped"]:
            byts = ",".join(
                relabel_signal(f"B{i}", notation, sub_bytes=sub_bytes, payload_len=payload_len)
                for i in e["unmapped"]
            )
            print(f"      {ansi.YELLOW}UNMAPPED{ansi.RESET} {byts}")
        if e.get("unverified_mapped"):
            byts = ",".join(
                relabel_signal(f"B{i}", notation, sub_bytes=sub_bytes, payload_len=payload_len)
                for i in e["unverified_mapped"]
            )
            print(f"      {ansi.YELLOW}UNVERIFIED{ansi.RESET} {byts}")
        for bf in e["incomplete_bitfields"]:
            have = ",".join(map(str, bf["have"]))
            miss = ",".join(map(str, bf["missing"]))
            byte_label = relabel_signal(
                f"B{bf['byte']}", notation, sub_bytes=sub_bytes, payload_len=payload_len
            )
            whole = f" {ansi.DIM}(also read whole){ansi.RESET}" if bf.get("also_whole") else ""
            print(
                f"      {ansi.RED}BITS{ansi.RESET} {byte_label} have{{{have}}} missing{{{miss}}}{whole}"
            )
    print()
    return 0
