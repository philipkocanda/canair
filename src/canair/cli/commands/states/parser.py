"""The ``canair states`` parser."""

from __future__ import annotations

import argparse

from canair.cli.commands._edit import subcommand_dispatch

from .edit import (
    cmd_add,
    cmd_rename,
    cmd_rm,
    cmd_set_description,
    cmd_set_excludes,
    cmd_set_implies,
    cmd_set_predicate,
)
from .epilog import EPILOG
from .listing import cmd_list

NAME = "states"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List and edit the profile's vehicle-state vocabulary (vehicle_states.yaml)",
        description="List the active profile's vehicle operating states, or edit the "
        "vocabulary (add/rm/rename/set-description/set-predicate/set-implies/set-excludes). Read-only "
        "companion of the state auto-suggestion used when recording captures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("--json", action="store_true", help="Output the list as JSON")
    sub = parser.add_subparsers(dest="states_command")

    lst = sub.add_parser("list", help="List the vocabulary, or look up one state's ECUs (default)")
    lst.add_argument(
        "state",
        nargs="?",
        help="A state name (e.g. READY) — show which ECUs are readable/awake in it",
    )
    lst.add_argument("--json", action="store_true", help="Output as JSON")
    lst.set_defaults(_states_func=cmd_list)

    add = sub.add_parser("add", help="Add a new state to the vocabulary")
    add.add_argument("name", help="State name (normalized to UPPERCASE)")
    add.add_argument("--description", "-d", help="Human-readable description")
    add.add_argument("--when", "-w", metavar="EXPR", help="Auto-suggest predicate over ECU.PARAM")
    add.add_argument(
        "--implies",
        "-i",
        metavar="STATES",
        help="Comma-separated broader states this one specializes (e.g. READY) — when both "
        "match, the live view shows this one",
    )
    add.add_argument(
        "--excludes",
        "-x",
        metavar="STATES",
        help="Comma-separated states this one cannot hold at the same instant (e.g. PARKED) — "
        "a session tagged with both sequenced through them",
    )
    add.set_defaults(_states_func=cmd_add)

    rm = sub.add_parser("rm", help="Remove a state from the vocabulary")
    rm.add_argument("name")
    rm.set_defaults(_states_func=cmd_rm)

    ren = sub.add_parser(
        "rename",
        help="Rename a state everywhere the profile references it",
        description="Rename a state in vehicle_states.yaml and in every reference: the "
        "vehicle_states lists in ecus/, every capture session's states and state_spans "
        "timeline, the DTC log and the raw-CAN log index. All or nothing: a failed write "
        "restores every file. Rewriting recorded data asks first unless --yes.",
    )
    ren.add_argument("old")
    ren.add_argument("new")
    ren.add_argument(
        "--dry-run", action="store_true", help="List the files and references; write nothing"
    )
    ren.add_argument(
        "--yes", "-y", action="store_true", help="Rewrite recorded data without asking"
    )
    ren.set_defaults(_states_func=cmd_rename)

    sd = sub.add_parser("set-description", help="Set/clear a state's description")
    sd.add_argument("name")
    sd.add_argument("value", nargs="?", default=None, help="Description (omit to clear)")
    sd.set_defaults(_states_func=cmd_set_description)

    sp = sub.add_parser("set-predicate", help="Set/clear a state's when: auto-suggest predicate")
    sp.add_argument("name")
    sp.add_argument(
        "value", nargs="?", default=None, metavar="EXPR", help="Predicate (omit to clear)"
    )
    sp.set_defaults(_states_func=cmd_set_predicate)

    si = sub.add_parser(
        "set-implies",
        help="Set/clear which broader states this one specializes",
        description="Declare the specificity hierarchy: DRIVING implies READY means that when "
        "both predicates match, DRIVING is the more specific (and shown) reading. Targets must "
        "be declared states and the hierarchy must stay acyclic.",
    )
    si.add_argument("name")
    si.add_argument(
        "value",
        nargs="?",
        default=None,
        metavar="STATES",
        help="Comma-separated state names (omit to clear)",
    )
    si.set_defaults(_states_func=cmd_set_implies)

    sx = sub.add_parser(
        "set-excludes",
        help="Set/clear which states this one cannot hold at the same instant",
        description="Declare mutual exclusivity: DRIVING excludes PARKED means the two cannot "
        "describe one instant, so a session tagged with both sequenced through them and needs a "
        "state_spans timeline (canair captures uds --backfill-state-spans) for per-capture "
        "filtering to be exact. The relation is symmetric — declaring it on either side is "
        "enough. Targets must be declared states and may not also be related by implies:.",
    )
    sx.add_argument("name")
    sx.add_argument(
        "value",
        nargs="?",
        default=None,
        metavar="STATES",
        help="Comma-separated state names (omit to clear)",
    )
    sx.set_defaults(_states_func=cmd_set_excludes)

    parser.set_defaults(func=run)
    return parser


run = subcommand_dispatch("_states_func", cmd_list)
