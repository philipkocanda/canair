"""Validate a profile's ecus/, profile.yaml, and captures/ against their schemas.

Validators merged into one subcommand:

  * ``validate pids`` — per-ECU definition files in ecus/ vs pids_schema.yaml
    (also validates profile.yaml). ``validate ecus`` is an alias.
  * ``validate captures`` — capture files in captures/ vs captures_schema.json
  * ``validate states`` — states.yaml (power-state vocabulary + predicates)
  * ``validate can-buses`` — can_buses.yaml (per-profile CAN bus vocabulary)
  * ``validate signals`` — signals/ broadcast signal definitions (domain B)
  * ``validate can`` — captures/can/index.yaml (raw-CAN log index)
  * ``validate all`` (default) — run all of them

Split by domain into submodules (pids/captures/other) under this package; the
public API and the ``canair validate`` CLI are re-exported here so callers that
``from canlib.commands.validate import …`` keep working.
"""

import argparse

from .captures import (
    _capture_echo_warnings,
    _capture_missing_time_warnings,
    _capture_nonhex_warnings,
    _capture_state_warnings,
    _run_captures,
    load_valid_rx_addrs,
    validate_captures_file,
)
from .other import (
    _run_can,
    _run_can_buses,
    _run_signals,
    _run_states,
    check_signals_doc,
)
from .pids import (
    _duplicate_param_errors,
    _run_pids,
    _validate_param_type,
    check_pci_bytes,
    collect_pids_validation,
    load_schema,
    validate_ecu_file,
    validate_expression,
    validate_meta,
    validate_pids_file,
)

NAME = "validate"

__all__ = [
    "NAME",
    "_capture_echo_warnings",
    "_capture_missing_time_warnings",
    "_capture_nonhex_warnings",
    "_capture_state_warnings",
    "_duplicate_param_errors",
    "_run_can",
    "_run_can_buses",
    "_run_signals",
    "_validate_param_type",
    "add_parser",
    "check_pci_bytes",
    "check_signals_doc",
    "collect_pids_validation",
    "load_schema",
    "load_valid_rx_addrs",
    "run",
    "validate_captures_file",
    "validate_ecu_file",
    "validate_expression",
    "validate_meta",
    "validate_pids_file",
]


def add_parser(subparsers):
    parser = subparsers.add_parser(
        NAME,
        help="Check a profile's ecus/, profile.yaml, and captures/ against their schemas",
        description="Validate a profile's data files against their schemas and\n"
        "report problems.\n\n"
        "Pick a target (default: all):\n"
        "  pids      the per-ECU ecus/ files (identity/scan_log/dtcs/pids/...)\n"
        "  captures  the captures/ payload files (+ soft warnings, see below)\n"
        "  ecus      alias for pids\n"
        "  states    states.yaml (vehicle power-state vocabulary + predicates)\n"
        "  can-buses can_buses.yaml (per-profile CAN bus segment vocabulary)\n"
        "  signals   signals/ broadcast signal-definition files (domain B)\n"
        "  can       captures/can/index.yaml (raw-CAN log index)\n"
        "  all       everything above\n\n"
        "`validate captures` also emits soft warnings for out-of-vocabulary vehicle\n"
        "states, SID/PID/DID echo mismatches (misfiled frames), non-hex payloads\n"
        "(e.g. a stored 'NO DATA'), and untimed payload captures. Pass --strict to\n"
        "promote the untimed-payload warning to an error — the CI / new-data gate.\n\n"
        "Run this after editing ecus/ or adding captures; `canair pids` already\n"
        "validates each edit, so this is the whole-profile check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair validate                     # validate everything (pids + captures + states)
  canair validate --stats             # + a count summary (ECUs/PIDs/params/verified)
  canair validate pids                # just the ecus/ definition files
  canair validate captures            # just captures/ (with soft warnings)
  canair validate captures --strict   # treat untimed-payload warnings as errors (CI)
  canair validate states              # just states.yaml
  canair validate pids ecus/bms.yaml  # a specific ECU file only
""",
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=["pids", "captures", "ecus", "states", "can-buses", "signals", "can", "all"],
        default="all",
        help="What to validate (default: all)",
    )
    parser.add_argument(
        "files", nargs="*", help="Specific ecus/ files (only with target=pids/ecus)"
    )
    parser.add_argument("--stats", action="store_true", help="Show parameter statistics (pids)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat soft warnings that gate new data (currently: untimed payload "
        "captures) as errors — for CI / new-capture checks",
    )
    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    strict = getattr(args, "strict", False)
    if args.target in ("pids", "ecus"):
        return _run_pids(args.files or None, args.stats)
    if args.target == "captures":
        return _run_captures(strict=strict)
    if args.target == "states":
        return _run_states()
    if args.target == "can-buses":
        return _run_can_buses()
    if args.target == "signals":
        return _run_signals()
    if args.target == "can":
        return _run_can()
    # all: the ecus/ files are validated once via _run_pids (they are the registry).
    rc_p = _run_pids(None, args.stats)
    print()
    rc_c = _run_captures(strict=strict)
    print()
    rc_s = _run_states()
    print()
    rc_cb = _run_can_buses()
    print()
    rc_sig = _run_signals()
    print()
    rc_can = _run_can()
    return rc_p or rc_c or rc_s or rc_cb or rc_sig or rc_can
