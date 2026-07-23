"""Audit PID definitions for decoding gaps (unmapped bytes, partial bitfields).

For every ECU/PID in ecus/, this cross-references the parameter expressions
against the *longest* captured payload for that PID and reports:

  - UNMAPPED  data bytes present in the payload that no expression reads
  - BITS      bytes read only bit-by-bit (Bn:k) with some bits still undecoded
  - NO CAPTURE PIDs that have parameters defined but no payload captured yet

Byte indices are WiCAN Bnn (the flat CAN-frame index used by expressions,
including PCI bytes). PCI, SID, and subfunction/DID-echo bytes are excluded
from the "mappable data" set, so they never show up as UNMAPPED.

Examples:
  canair coverage                 # audit every ECU/PID
  canair coverage IGPM            # only the IGPM ECU
  canair coverage IGPM 22BC03     # a single ECU/PID
  canair coverage --bitfields     # only incomplete-bitfield findings
  canair coverage --unmapped      # only unmapped-byte findings
  canair coverage --no-capture    # only PIDs missing captures
  canair coverage --all           # include fully-mapped PIDs too
  canair coverage --json          # machine-readable output
"""

import argparse
import json
import re
import sys
from typing import NotRequired, TypedDict

import yaml

from canlib.byteindex import extract_byte_indices, payload_to_wican_frame
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.commands._hints import pid_completer as _pid_completer
from canlib.pids import build_ecu_index, load_pids

NAME = "coverage"


class BitfieldGap(TypedDict):
    """One byte read only bit-by-bit with some bits still undecoded."""

    byte: int
    have: list[int]
    missing: list[int]


class PidAnalysis(TypedDict):
    """Coverage findings for one PID's parameters against a payload."""

    data_bytes: int
    unmapped: list[int]
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
    unmapped: NotRequired[list[int]]
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
    for fpath in sorted(captures_dir.glob("*.yaml")):
        if fpath.name.startswith(("SCHEMA", "_")):
            continue
        data = yaml.safe_load(fpath.read_text()) or {}
        for session in data.get("sessions", []):
            for cap in session.get("captures", []):
                payload = cap.get("payload")
                if not payload:
                    continue
                payload = payload.replace(" ", "")
                ecu_name = ecu_name_from_ref(cap.get("ecu", ""), rx_index)
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
    """WiCAN indices that carry real data (not PCI, SID, or subfunction bytes).

    A WiCAN byte is mappable when it has a payload index (not a PCI byte) and
    that index is past the UDS header (payload index 0 = SID, 1..sfb = sub/DID).
    """
    payload_bytes = [int(payload_hex[i : i + 2], 16) for i in range(0, len(payload_hex), 2)]
    frame = payload_to_wican_frame(payload_bytes)
    return [
        wican_idx for wican_idx, (_val, pidx) in enumerate(frame) if pidx is not None and pidx > sfb
    ]


_BIT_RE = re.compile(r"(?<!\[)B(\d+):(\d+)(?!\d)")


def bit_references(expr: str) -> dict[int, set[int]]:
    """Map WiCAN byte index -> set of bit positions read via ``Bn:k``."""
    out: dict[int, set[int]] = {}
    for m in _BIT_RE.finditer(expr):
        out.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    return out


def references_full_byte(expr: str, idx: int) -> bool:
    """True if ``expr`` reads byte ``idx`` as a whole byte (Bn/Sn, not Bn:k)."""
    return re.search(rf"(?<!\[)[BS]0*{idx}(?![0-9:])", expr) is not None


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
        for b, bits in bit_references(expr).items():
            all_bits.setdefault(b, set()).update(bits)

    unmapped = [i for i in data_idx if i not in covered]

    incomplete: list[BitfieldGap] = []
    for b, bits in sorted(all_bits.items()):
        if b not in data_idx:
            continue
        full = any(references_full_byte(p.get("expression", ""), b) for p in parameters.values())
        if not full and len(bits) < 8:
            missing = [x for x in range(8) if x not in bits]
            incomplete.append({"byte": b, "have": sorted(bits), "missing": missing})

    return {
        "data_bytes": len(data_idx),
        "unmapped": unmapped,
        "incomplete_bitfields": incomplete,
    }


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
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
        "--bitfields", action="store_true", help="Only report incomplete-bitfield findings"
    )
    parser.add_argument(
        "--no-capture", action="store_true", help="Only report PIDs with params but no capture"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
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
            return not (args.unmapped or args.bitfields)
        if args.no_capture:
            return False
        has_unmapped = bool(e.get("unmapped"))
        has_bits = bool(e.get("incomplete_bitfields"))
        if args.unmapped:
            return has_unmapped
        if args.bitfields:
            return has_bits
        if args.all:
            return True
        return has_unmapped or has_bits

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

    for e in results:
        header = (
            f"  {_BOLD}{_CYAN}{e['ecu']} {e['pid']}{_RESET} "
            f"{_DIM}({e['params']}p, {e['verified']} verified){_RESET}"
        )
        if e.get("no_capture"):
            print(f"{header}  {_YELLOW}NO CAPTURE{_RESET}")
            continue
        print(f"{header}  {_DIM}{e['data_bytes']} data bytes, {e['capture']['date']}{_RESET}")
        if e["unmapped"]:
            byts = ",".join(f"B{i}" for i in e["unmapped"])
            print(f"      {_YELLOW}UNMAPPED{_RESET} {byts}")
        for bf in e["incomplete_bitfields"]:
            have = ",".join(map(str, bf["have"]))
            miss = ",".join(map(str, bf["missing"]))
            print(f"      {_RED}BITS{_RESET} B{bf['byte']} have{{{have}}} missing{{{miss}}}")
    print()
    return 0
