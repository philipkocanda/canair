"""``canair pids`` — safely add/update ecus/ parameters and research entries.

A thin, non-interactive wrapper over canlib.pids_edit for the reverse-
engineering workflow. Every edit is:

  1. applied surgically (comments/formatting preserved, YAML re-parsed), then
  2. schema-checked in-process (canair validate pids) — reverted if it fails.

Subcommands:
  upsert-param ECU PID NAME EXPR   Add/update a parameter (expression required)
  add-research ECU ...             Append a research: entry
  set-status  ECU TARGET STATUS    Update a research item's status
  set-pid-status ECU PID STATUS    Set a PID's lifecycle (active|draft|static|ignored)

Examples:
  # Record a decoded parameter
  canair pids upsert-param MCU 2102 MCU_MOTOR_TORQUE "[S12:S13]/100" \\
      --unit Nm --min -300 --max 300 --unverified --source "Soul VMCU CSV" \\
      --notes "signed 16-bit BE at B12:B13"

  # Track a new investigation, then close it out
  canair pids add-research MCU --type decode --target 2103 \\
      --status captured --priority P2 --prereq charging --notes "27 bytes, zeros"
  canair pids set-status MCU 2103 done --type decode
"""

from __future__ import annotations

import argparse
from pathlib import Path

from canlib.pids import PID_STATUSES
from canlib.pids_edit import (
    PidsEditError,
    add_research_entry,
    find_ecu_file,
    set_pid_status,
    set_research_status,
    upsert_parameter,
)
from canlib.states import POWER_STATES

NAME = "pids"

_GREEN = "\033[92m"
_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _schema_validate(fpath: Path) -> tuple[bool, str]:
    """Validate a single pids file in-process. Returns (ok, output)."""
    from canlib.commands.validate import validate_pids_file

    return validate_pids_file(fpath)


def _guarded(ecu: str, pids_dir: Path, do_edit, *, validate: bool):
    """Snapshot -> edit -> schema-validate -> commit or roll back."""
    fpath = find_ecu_file(ecu, pids_dir=pids_dir)
    snapshot = fpath.read_text()
    do_edit()
    if validate:
        ok, out = _schema_validate(fpath)
        if not ok:
            fpath.write_text(snapshot)
            print(out)
            raise SystemExit(f"{_RED}  Schema validation failed — reverted {fpath.name}{_RESET}")
    return fpath


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    sp.add_argument(
        "--no-validate", action="store_true", help="Skip the post-edit schema validation gate"
    )


def cmd_upsert_param(args: argparse.Namespace) -> int:
    def do():
        upsert_parameter(
            args.ecu, args.pid, args.name, args.expression,
            unit=args.unit, ha_class=args.ha_class, mqtt_topic=args.mqtt_topic,
            min=args.min, max=args.max, source=args.source,
            source_links=args.source_link or None, verified=args.verified,
            notes=args.notes, enabled=args.enabled, display=args.display,
            pids_dir=args.dir,
        )

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(f"{_GREEN}  ✓ {args.ecu} {args.pid} {args.name}{_RESET}  {_DIM}({fpath.name}){_RESET}")
    return 0


def cmd_add_research(args: argparse.Namespace) -> int:
    def do():
        add_research_entry(
            args.ecu, type=args.type, target=args.target, status=args.status,
            priority=args.priority, vehicle_states=args.prereq or None,
            created=args.created, updated=args.updated,
            date=args.date, result=args.result, notes=args.notes,
            sources=args.source or None, what_to_test=args.what_to_test or None,
            capture_protocol=args.capture_protocol,
            pids_dir=args.dir,
        )

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{_GREEN}  ✓ research {args.ecu} {args.type} {args.target} "
        f"[{args.status}]{_RESET}  {_DIM}({fpath.name}){_RESET}"
    )
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    def do():
        set_research_status(args.ecu, args.target, args.status, type=args.type, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{_GREEN}  ✓ {args.ecu} research {args.target} -> {args.status}{_RESET}  "
        f"{_DIM}({fpath.name}){_RESET}"
    )
    return 0


def cmd_set_pid_status(args: argparse.Namespace) -> int:
    def do():
        set_pid_status(args.ecu, args.pid, args.status, pids_dir=args.dir)

    fpath = _guarded(args.ecu, args.dir, do, validate=not args.no_validate)
    print(
        f"{_GREEN}  ✓ {args.ecu} {args.pid} status -> {args.status}{_RESET}  "
        f"{_DIM}({fpath.name}){_RESET}"
    )
    return 0


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Safely edit ecus/ parameters and research entries",
        description="Safely edit ecus/ parameters and research entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    sub = parser.add_subparsers(dest="pids_command", required=True)

    up = sub.add_parser("upsert-param", help="Add or update a parameter")
    up.add_argument("ecu")
    up.add_argument("pid")
    up.add_argument("name")
    up.add_argument("expression")
    up.add_argument("--unit")
    up.add_argument("--ha-class", dest="ha_class")
    up.add_argument("--mqtt-topic", dest="mqtt_topic")
    up.add_argument("--min")
    up.add_argument("--max")
    up.add_argument("--source")
    up.add_argument("--source-link", action="append", metavar="URL")
    up.add_argument("--display")
    up.add_argument("--notes")
    ver = up.add_mutually_exclusive_group()
    ver.add_argument("--verified", dest="verified", action="store_true", default=None)
    ver.add_argument("--unverified", dest="verified", action="store_false")
    en = up.add_mutually_exclusive_group()
    en.add_argument("--enabled", dest="enabled", action="store_true", default=None)
    en.add_argument("--disabled", dest="enabled", action="store_false")
    _add_common(up)
    up.set_defaults(_pids_func=cmd_upsert_param)

    ar = sub.add_parser("add-research", help="Append a research: entry")
    ar.add_argument("ecu")
    ar.add_argument("--type", required=True, choices=["scan", "decode", "verify", "iocontrol_scan"])
    ar.add_argument("--target", required=True)
    ar.add_argument("--status", required=True, choices=["pending", "captured", "nrc", "done"])
    ar.add_argument("--priority", choices=["P1", "P2", "P3"])
    ar.add_argument(
        "--prereq", "--vehicle-states", dest="prereq", action="append",
        choices=list(POWER_STATES),
    )
    ar.add_argument("--date")
    ar.add_argument("--created", metavar="YYYY-MM-DD", help="Override auto creation date (default: today)")
    ar.add_argument("--updated", metavar="YYYY-MM-DD", help="Override auto updated date (default: today)")
    ar.add_argument("--result")
    ar.add_argument("--notes")
    ar.add_argument("--source", action="append", metavar="SRC")
    ar.add_argument("--what-to-test", action="append", metavar="ITEM")
    ar.add_argument("--capture-protocol", metavar="TEXT")
    _add_common(ar)
    ar.set_defaults(_pids_func=cmd_add_research)

    ss = sub.add_parser("set-status", help="Update a research item's status")
    ss.add_argument("ecu")
    ss.add_argument("target")
    ss.add_argument("status", choices=["pending", "captured", "nrc", "done"])
    ss.add_argument(
        "--type", choices=["scan", "decode", "verify", "iocontrol_scan"],
        help="Disambiguate when multiple items share the target",
    )
    _add_common(ss)
    ss.set_defaults(_pids_func=cmd_set_status)

    sps = sub.add_parser("set-pid-status", help="Set a PID's lifecycle status")
    sps.add_argument("ecu")
    sps.add_argument("pid")
    sps.add_argument("status", choices=list(PID_STATUSES))
    _add_common(sps)
    sps.set_defaults(_pids_func=cmd_set_pid_status)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    try:
        return args._pids_func(args)
    except PidsEditError as e:
        raise SystemExit(f"{_RED}  Error: {e}{_RESET}") from None


if __name__ == "__main__":
    import sys

    from canlib.cli import main

    sys.exit(main(["pids", *sys.argv[1:]]))
