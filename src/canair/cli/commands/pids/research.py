"""``canair pids`` kinds that edit a ``research:`` entry."""

from __future__ import annotations

import argparse

from canair.cli.commands._edit import add_edit_args, run_edit
from canair.cli.profile_context import target_profile
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import (
    add_research_entry,
    delete_research_entry,
    set_research_status,
    set_research_text,
)

from .common import apply_in_scope, words_verb


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
    ar = sub.add_parser("add-research", help="Append a research: entry")
    ar.add_argument("ecu")
    ar.add_argument("--type", required=True, choices=["scan", "decode", "verify", "iocontrol_scan"])
    ar.add_argument("--target", required=True)
    ar.add_argument("--status", required=True, choices=["pending", "captured", "nrc", "done"])
    ar.add_argument("--priority", choices=["P1", "P2", "P3"])
    ar.add_argument(
        "--prereq",
        "--vehicle-states",
        dest="prereq",
        action="append",
        type=str.upper,
        help="Power state(s) prerequisite for this research (repeatable). "
        "Validated against the profile's vehicle-state vocabulary at write time.",
    )
    ar.add_argument("--date")
    ar.add_argument(
        "--created", metavar="YYYY-MM-DD", help="Override auto creation date (default: today)"
    )
    ar.add_argument(
        "--updated", metavar="YYYY-MM-DD", help="Override auto updated date (default: today)"
    )
    ar.add_argument("--result")
    ar.add_argument("--notes")
    ar.add_argument("--source", action="append", metavar="SRC")
    ar.add_argument("--what-to-test", action="append", metavar="ITEM")
    ar.add_argument("--capture-protocol", metavar="TEXT")
    ar.add_argument(
        "--revision",
        dest="revisions",
        action="append",
        metavar="ID",
        help="An ECU revision the task concerns (repeatable; absent = every revision): a "
        "car on another revision doesn't list it",
    )
    add_edit_args(ar)
    ar.set_defaults(_pids_func=cmd_add_research)

    ss = sub.add_parser("set-status", help="Update a research item's status")
    ss.add_argument("ecu")
    ss.add_argument("target")
    ss.add_argument("status", choices=["pending", "captured", "nrc", "done"])
    ss.add_argument(
        "--type",
        choices=["scan", "decode", "verify", "iocontrol_scan"],
        help="Disambiguate when multiple items share the target",
    )
    ss.add_argument(
        "--index",
        type=int,
        help="Disambiguate by 0-based position among matches (after --type filtering); "
        "needed when several items share the same target and type",
    )
    add_edit_args(ss)
    ss.set_defaults(_pids_func=cmd_set_status)

    rr = sub.add_parser("rm-research", help="Remove a research: item")
    rr.add_argument("ecu")
    rr.add_argument("target")
    rr.add_argument(
        "--type",
        choices=["scan", "decode", "verify", "iocontrol_scan"],
        help="Disambiguate when multiple items share the target",
    )
    rr.add_argument(
        "--index",
        type=int,
        help="Disambiguate by 0-based position among matches (after --type filtering); "
        "needed when several items share the same target and type",
    )
    add_edit_args(rr)
    rr.set_defaults(_pids_func=cmd_rm_research)

    for command, field, what in _RESEARCH_TEXT_COMMANDS:
        p = sub.add_parser(
            command,
            help=f"Set (or clear) a research item's {what}",
            description=(
                f"Set a research: item's {field}: without touching status: or other fields.\n\n"
                "Omit VALUE to clear the field. Short text stays inline; longer text "
                "becomes a word-wrapped folded block scalar. Disambiguate with --type/--index "
                "the same way as set-status/rm-research."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        p.add_argument("ecu")
        p.add_argument("target")
        p.add_argument("value", nargs="?", help=f"New {field} text (omit to clear the field)")
        p.add_argument(
            "--type",
            choices=["scan", "decode", "verify", "iocontrol_scan"],
            help="Disambiguate when multiple items share the target",
        )
        p.add_argument(
            "--index",
            type=int,
            help="Disambiguate by 0-based position among matches (after --type filtering); "
            "needed when several items share the same target and type",
        )
        add_edit_args(p)
        p.set_defaults(_pids_func=cmd_set_research_text, research_field=field)


def cmd_add_research(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: add_research_entry(
            args.ecu,
            type=args.type,
            target=args.target,
            status=args.status,
            priority=args.priority,
            vehicle_states=args.prereq or None,
            created=args.created,
            updated=args.updated,
            date=args.date,
            result=args.result,
            notes=args.notes,
            sources=args.source or None,
            what_to_test=args.what_to_test or None,
            capture_protocol=args.capture_protocol,
            revisions=args.revisions or None,
            pids_dir=args.dir,
            profile=target_profile(args),
        ),
        f"research {args.ecu} {args.type} {args.target} [{args.status}]",
        args=args,
    )


def cmd_set_status(args: argparse.Namespace) -> int:
    return run_edit(
        lambda: set_research_status(
            args.ecu,
            args.target,
            args.status,
            type=args.type,
            index=args.index,
            pids_dir=args.dir,
            profile=target_profile(args),
        ),
        f"{args.ecu} research {args.target} -> {args.status}",
        args=args,
    )


def cmd_rm_research(args: argparse.Namespace) -> int:
    def do():
        delete_research_entry(
            args.ecu,
            args.target,
            type=args.type,
            index=args.index,
            pids_dir=args.dir,
            profile=target_profile(args),
        )

    fpath = apply_in_scope(args, do)
    echo_edit(f"removed {args.ecu} research {args.target}", fpath)
    return 0


#: The research free-text editors: (subcommand, research field, what --help calls it).
_RESEARCH_TEXT_COMMANDS = (
    ("set-research-notes", "notes", "free-text notes"),
    ("set-research-result", "result", "short result summary"),
    ("set-research-capture-protocol", "capture_protocol", "capture recipe"),
)


def cmd_set_research_text(args: argparse.Namespace) -> int:
    """Set or clear the research free-text field the subcommand names."""
    field, text = args.research_field, args.value

    what = words_verb(text)
    return run_edit(
        lambda: set_research_text(
            args.ecu,
            args.target,
            field,
            text,
            type=args.type,
            index=args.index,
            pids_dir=args.dir,
            profile=target_profile(args),
        ),
        f"{args.ecu} research {args.target} {field} {what}",
        args=args,
    )
