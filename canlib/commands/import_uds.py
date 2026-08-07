#!/usr/bin/env python3
"""``canair import uds`` — record externally-provided UDS payloads (domain A).

The device-free counterpart to the ``--save`` path: adds a capture you already
have (e.g. pasted from a forum/GitHub issue, or read on another tool) into the
active profile's ``captures/`` — through the same machinery as a live save, so
it's indistinguishable from a device-recorded one and immediately queryable by
``decode``/``captures``/``coverage``.

Each capture is given as ``ECU:PID=PAYLOAD`` (the ECU short name or a hex TX id,
the PID/DID, and the reassembled UDS response payload — SID-first, ISO-TP PCI
stripped). One command call records one session; pass several pairs to group
them. Payloads are validated as hex and cross-checked against the PID's expected
SID/DID echo (a mismatch warns but does not block — you may know better).

This is the UDS (domain-A, request/response) half of ``canair import``; the
raw broadcast-frame (domain-B) half is ``import can`` / ``import dbc``.

Examples:
  # One verified odometer reading with context
  canair import uds CLU:22B002=62B002E0000000FFB7008D08000000 \\
      --label "Odometer" --state acc2 --time 09:38:15 \\
      --notes "Verified 36104 km on dash by timwelchnz (GitHub wican-fw#478)"

  # Several PIDs captured together into one session
  canair import uds BMS:2101=6101FFF8... BMS:2102=6102... --label "SOC snapshot"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from canlib.capture_types import CaptureRecord

_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _parse_spec(spec: str) -> tuple[str, str, str]:
    """Split ``ECU:PID=PAYLOAD`` into (ecu, pid, payload). Raises ValueError."""
    if "=" not in spec:
        raise ValueError(f"{spec!r}: expected ECU:PID=PAYLOAD (missing '=')")
    left, payload = spec.split("=", 1)
    if ":" not in left:
        raise ValueError(f"{spec!r}: expected ECU:PID before '=' (missing ':')")
    ecu, pid = left.split(":", 1)
    ecu, pid, payload = ecu.strip(), pid.strip(), payload.replace(" ", "").strip()
    if not ecu or not pid or not payload:
        raise ValueError(f"{spec!r}: ECU, PID and PAYLOAD must all be non-empty")
    return ecu, pid.upper(), payload.upper()


def _build_capture(spec: str, name_index: dict[str, int]) -> tuple[CaptureRecord, list[str]]:
    """Resolve/validate one spec into a capture dict. Returns (capture, warnings)."""
    from canlib.ecus import resolve_tx, rx_addr_str
    from canlib.uds_parse import payload_echo_mismatch, payload_not_hex

    ecu, pid, payload = _parse_spec(spec)

    tx_id = resolve_tx(ecu, name_index)
    if tx_id is None:
        raise ValueError(
            f"{spec!r}: unknown ECU {ecu!r} — register it first "
            f"(canair ecu add / discover --register) or pass a hex TX id"
        )

    bad = payload_not_hex(payload)
    if bad:
        raise ValueError(f"{spec!r}: {bad}")

    warnings: list[str] = []
    mismatch = payload_echo_mismatch(pid, payload)
    if mismatch:
        warnings.append(f"{ecu}:{pid}: {mismatch}")

    return {"rx": rx_addr_str(tx_id), "pid": pid, "payload": payload}, warnings


def run(args) -> int:
    from canlib.captures import build_manual_session, save_session
    from canlib.ecus import build_name_tx_index
    from canlib.states import parse_states

    name_index = build_name_tx_index()

    captures: list[CaptureRecord] = []
    warnings: list[str] = []
    for spec in args.spec:
        try:
            capture, warns = _build_capture(spec, name_index)
        except ValueError as e:
            print(f"{_RED}error: {e}{_RESET}", file=sys.stderr)
            return 2
        # A payload capture must always be timestamped, or it is silently excluded
        # from every time-aligned analysis (and from the long-horizon counter
        # sweep, where the calendar span IS the evidence). An imported reading has
        # no true acquisition time, so it is stamped like --date is: the import
        # instant, overridable with --time.
        capture["time"] = args.time or datetime.now().strftime("%H:%M:%S")
        if args.capture_note:
            capture["notes"] = args.capture_note
        captures.append(capture)
        warnings.extend(warns)

    vehicle_states = parse_states(args.state) if args.state else []
    session = build_manual_session(
        captures,
        label=args.label,
        date=args.date,
        vehicle_states=vehicle_states,
        notes=args.notes,
    )

    for w in warnings:
        print(f"{_YELLOW}  warning: {w}{_RESET}", file=sys.stderr)

    fpath = save_session(session, args.dir)

    if args.json:
        print(json.dumps({"file": str(fpath), "session": session, "warnings": warnings}))
    else:
        for c in captures:
            print(f"{_GREEN}  ✓ {c['rx']} {c['pid']}{_RESET}  {_DIM}{c['payload']}{_RESET}")
    return 0


def add_uds_parser(subparsers) -> argparse.ArgumentParser:
    """Register the ``uds`` kind under ``canair import``."""
    parser = subparsers.add_parser(
        "uds",
        help="Record externally-provided UDS payloads into captures/ (device-free)",
        description="Record externally-provided UDS payloads into the active profile "
        "(device-free capture import).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "spec",
        nargs="+",
        metavar="ECU:PID=PAYLOAD",
        help="One or more captures, e.g. CLU:22B002=62B002...  (ECU short name or "
        "hex TX id; PID/DID; reassembled UDS payload, SID-first)",
    )
    parser.add_argument("--label", required=True, help="Session label (what this reading is)")
    parser.add_argument(
        "--state",
        action="append",
        metavar="STATE",
        help="Vehicle power state(s) during capture (e.g. acc2, ready). Repeatable.",
    )
    parser.add_argument("--notes", help="Session notes (context, source/attribution)")
    parser.add_argument(
        "--capture-note",
        dest="capture_note",
        help="Per-capture note applied to each imported payload",
    )
    parser.add_argument(
        "--time",
        metavar="HH:MM:SS",
        help="Capture time (default: the import instant, like --date defaults to "
        "today). A payload capture is always timestamped so it can participate in "
        "time-aligned analysis",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD", help="Capture date (default: today; sets the target file)"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(_import_kind="uds", func=run)
    return parser
