"""Audit PID definitions for decoding gaps (unmapped bytes, partial bitfields).

For every ECU/PID in ecus/, this cross-references the signal expressions
against the payload length of that PID's newest captured session (its longest
reply for a variable_length: PID) and reports:

  - UNMAPPED  data bytes present in the payload that no expression reads
  - UNVERIFIED data bytes mapped only by an unverified signal (needs confirming)
  - BITS      bytes read bit-wise (Bn:k, or a type: bitmask map) with some bits
              still undecoded. A byte some signal *also* reads whole is still
              reported, flagged "(also read whole)" — a raw-byte read does not
              account for the byte's individual bits.
  - PAST END  signals reading bytes beyond the end of that payload (a short or
              truncated reply, or a wrong offset)
  - NO CAPTURE PIDs that have signals defined but no positive reply captured
              (in scope, when a scope flag is set)
  - NEGATIVE RESPONSE ONLY  PIDs that only ever answered with an NRC

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
import sys
from typing import NotRequired, TypedDict

from canlib.capture_dates import active_scope_flags, add_scope_args, resolve_scope_bounds
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.commands._hints import pid_completer as _pid_completer
from canlib.pids import build_ecu_index, load_pids
from canlib.uds.notation import add_notation_arg

from .analysis import (
    BitfieldGap,
    PastEnd,
    PidAnalysis,
    analyze_pid,
    declared_bits,
    mappable_data_indices,
    references_full_byte,
    subfunction_bytes,
)
from .payloads import (
    CoverageCaptures,
    NegativeOnly,
    PidKey,
    is_frame_aligned,
    load_coverage_captures,
    load_representative_payloads,
    may_be_padded,
    representative_payloads,
    select_captures,
    session_length,
)
from .render import render_text

__all__ = [
    "ALIASES",
    "NAME",
    "BitfieldGap",
    "CoverageCaptures",
    "CoverageEntry",
    "NegativeOnly",
    "PastEnd",
    "PidAnalysis",
    "add_parser",
    "analyze_pid",
    "audit",
    "declared_bits",
    "is_frame_aligned",
    "keep",
    "load_coverage_captures",
    "load_representative_payloads",
    "mappable_data_indices",
    "may_be_padded",
    "references_full_byte",
    "representative_payloads",
    "run",
    "select_captures",
    "session_length",
    "subfunction_bytes",
    "variable_length_pids",
]

NAME = "coverage"
ALIASES = ["cov"]


class CoverageEntry(TypedDict):
    """A per-PID coverage audit result (fields depend on capture availability).

    ``scope`` (the active scope flags) is set on a ``no_capture`` or
    ``negative_response_only`` entry, whose absence of data is then relative to
    that scope rather than to the whole capture history.
    """

    ecu: str
    pid: str
    params: int
    verified: int
    no_capture: NotRequired[bool]
    negative_response_only: NotRequired[bool]
    negative_response: NotRequired[NegativeOnly]
    scope: NotRequired[list[str]]
    capture: NotRequired[dict[str, str | bool]]
    data_bytes: NotRequired[int]
    payload_len: NotRequired[int]
    frame_len: NotRequired[int]
    unmapped: NotRequired[list[int]]
    unverified_mapped: NotRequired[list[int]]
    incomplete_bitfields: NotRequired[list[BitfieldGap]]
    past_end: NotRequired[list[PastEnd]]


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


def variable_length_pids(pids_data: dict) -> frozenset[PidKey]:
    """``(ECU, PID)`` keys (upper-cased, like the ECU index) flagged ``variable_length:``."""
    return frozenset(
        (str(ecu).upper(), str(pid).upper())
        for ecu, ecu_def in (pids_data.get("ecus") or {}).items()
        for pid, pid_def in ((ecu_def or {}).get("pids") or {}).items()
        if (pid_def or {}).get("variable_length")
    )


def audit(
    ecu_index: dict,
    captures: CoverageCaptures,
    ecu_filter: str | None = None,
    pid_filter: str | None = None,
    scope: list[str] | None = None,
) -> list[CoverageEntry]:
    """One :class:`CoverageEntry` per defined PID with signals, before category filters."""
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
            entry: CoverageEntry = {
                "ecu": ecu,
                "pid": pid,
                "params": len(parameters),
                "verified": sum(1 for p in parameters.values() if p.get("verified")),
            }
            cap = captures.payloads.get((ecu, pid))
            if cap is None:
                nrc = captures.negative_only.get((ecu, pid))
                if nrc is None:
                    entry["no_capture"] = True
                else:
                    entry["negative_response_only"] = True
                    entry["negative_response"] = nrc
                if scope:
                    entry["scope"] = list(scope)
            else:
                entry["capture"] = {
                    "date": cap["date"],
                    "file": cap["file"],
                    "may_include_padding": cap.get("may_include_padding", False),
                }
                entry.update(analyze_pid(parameters, cap["payload"], subfunction_bytes(pid)))
            results.append(entry)
    return results


def keep(e: CoverageEntry, args) -> bool:
    """Whether ``e`` survives the category flags (``--unmapped``/``--bitfields``/…)."""
    if e.get("no_capture") or e.get("negative_response_only"):
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
    return has_unmapped or has_unverified or has_bits or bool(e.get("past_end"))


def run(args) -> int:
    since, until, err = resolve_scope_bounds(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    from canlib.quirks import HK_F1XX_MINUS_ONE, has_quirk

    pids_data = load_pids()
    ecu_index = build_ecu_index(pids_data)
    captures = load_coverage_captures(
        since,
        until,
        state=getattr(args, "state", None),
        label=getattr(args, "label", None),
        variable_length=variable_length_pids(pids_data),
        hk_f1xx_offset=has_quirk(pids_data, HK_F1XX_MINUS_ONE),
    )

    # Accept an ECU-registry alias (e.g. LDC for OBC) or any case, matching
    # `canair captures`/`decode`. Canonicalises to the ecus/ key before filtering.
    from canlib.ecus import canonical_ecu_name_safe

    ecu_filter = canonical_ecu_name_safe(args.ecu).upper() if args.ecu else None
    pid_filter = args.pid.upper() if args.pid else None
    scope = active_scope_flags(args)

    results = audit(ecu_index, captures, ecu_filter, pid_filter, scope)
    results = [e for e in results if keep(e, args)]

    if args.json:
        json.dump(results, sys.stdout, indent=2, default=str)
        print()
        return 0
    render_text(results, args.notation, scoped=bool(scope))
    return 0
