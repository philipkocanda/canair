"""Audit PID definitions for decoding gaps (unmapped bytes, partial bitfields).

For every ECU/PID in ecus/, this cross-references the signal expressions
against the *longest* captured payload for that PID and reports:

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
"""

import argparse
import json
import re
import sys
from typing import NotRequired, TypedDict

from canlib import capture_io
from canlib.byteindex import extract_bit_indices, extract_byte_indices, mapped_offsets
from canlib.byteindex import mappable_data_indices as _mappable_data_indices
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
    capture: NotRequired[dict[str, str]]
    data_bytes: NotRequired[int]
    payload_len: NotRequired[int]
    unmapped: NotRequired[list[int]]
    unverified_mapped: NotRequired[list[int]]
    incomplete_bitfields: NotRequired[list[BitfieldGap]]


# ANSI colors
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def load_longest_payloads() -> dict[tuple[str, str], dict]:
    """Return {(ECU_UPPER, PID_UPPER): {payload, date, label, file}} for the
    longest captured payload seen per PID (most complete response).

    Capture ``ecu`` fields store the ECU CAN response address (e.g. ``0x7EC``)
    and are resolved to the canonical short name for the ``(ECU, PID)`` key.
    """
    from canlib.ecus import build_rx_index, ecu_name_from_ref
    from canlib.profile import active

    captures_dir = active().captures_dir

    try:
        rx_index = build_rx_index()
    except Exception:
        rx_index = {}

    best: dict[tuple[str, str], dict] = {}
    capture_io.ensure_migrated(captures_dir)
    for fpath in capture_io.iter_capture_files(captures_dir):
        data = capture_io.load_capture_file(fpath) or {}
        for session in data.get("sessions", []):
            for cap in session.get("captures", []):
                payload = cap.get("payload")
                if not payload:
                    continue
                payload = payload.replace(" ", "")
                ecu_name = ecu_name_from_ref(capture_io.capture_rx(cap), rx_index)
                key = (ecu_name.upper(), str(cap.get("pid", "")).upper())
                prev = best.get(key)
                if prev is None or len(payload) > len(prev["payload"]):
                    best[key] = {
                        "payload": payload,
                        "date": str(session.get("date", "")),
                        "label": session.get("label", ""),
                        "file": fpath.name,
                    }
    return best


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
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    ecu_index = build_ecu_index(load_pids())
    payloads = load_longest_payloads()

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
                entry["capture"] = {"date": cap["date"], "file": cap["file"]}
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
        f"\n{_BOLD}PID coverage audit{_RESET} — {n_gaps} PID(s) with gaps, "
        f"{n_nocap} without captures\n"
    )

    notation = resolve_notation(args.notation)

    for e in results:
        sub_bytes = subfunction_bytes_for_pid(e["pid"])
        # The frame layout depends on the payload's length, so the non-WiCAN
        # notations need it to name the right byte (see ByteRef.from_wican).
        payload_len = e.get("payload_len")
        header = (
            f"  {_BOLD}{_CYAN}{e['ecu']} {e['pid']}{_RESET} "
            f"{_DIM}({e['params']}p, {e['verified']} verified){_RESET}"
        )
        if e.get("no_capture"):
            print(f"{header}  {_YELLOW}NO CAPTURE{_RESET}")
            continue
        print(f"{header}  {_DIM}{e['data_bytes']} data bytes, {e['capture']['date']}{_RESET}")
        if e["unmapped"]:
            byts = ",".join(
                relabel_signal(f"B{i}", notation, sub_bytes=sub_bytes, payload_len=payload_len)
                for i in e["unmapped"]
            )
            print(f"      {_YELLOW}UNMAPPED{_RESET} {byts}")
        if e.get("unverified_mapped"):
            byts = ",".join(
                relabel_signal(f"B{i}", notation, sub_bytes=sub_bytes, payload_len=payload_len)
                for i in e["unverified_mapped"]
            )
            print(f"      {_YELLOW}UNVERIFIED{_RESET} {byts}")
        for bf in e["incomplete_bitfields"]:
            have = ",".join(map(str, bf["have"]))
            miss = ",".join(map(str, bf["missing"]))
            byte_label = relabel_signal(
                f"B{bf['byte']}", notation, sub_bytes=sub_bytes, payload_len=payload_len
            )
            whole = f" {_DIM}(also read whole){_RESET}" if bf.get("also_whole") else ""
            print(f"      {_RED}BITS{_RESET} {byte_label} have{{{have}}} missing{{{miss}}}{whole}")
    print()
    return 0
