"""``canair groups`` — list and edit the profile's capture/monitor selector groups.

A *group* is a named, reusable query stored in ``profiles/<name>/groups.yaml`` —
a list of ECU / ECU:PID selectors recalled on the command line with the ``@``
sigil (``canair monitor @charging``). It's the saved-query analogue of
``canair bus``/``canair states``: a bare ``canair groups`` lists the vocabulary,
and the edit subcommands surgically modify ``groups.yaml`` (comment-preserving,
re-validated, reverted on failure).

Examples:
  canair groups                                   # list the groups
  canair groups --json                            # machine-readable
  canair groups add commute BMS:2101 VCU MCU --description "My commute set"
  canair groups set-members charging BMS:2101 BMS:2105 OBC VCU MCU
  canair groups set-description driving "Powertrain + chassis while driving"
  canair groups rename body comfort
  canair groups rm commute
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TypedDict

from canair.cli.commands._edit import run_edit, set_or_clear, subcommand_dispatch
from canair.cli.profile_context import active
from canair.ecus.ecu_groups import GROUP_SIGIL, GroupError, load_groups
from canair.render import ansi, messages
from canair.yaml_edit.groups import (
    add_group,
    remove_group,
    rename_group,
    set_group_description,
    set_group_members,
)

NAME = "groups"


class GroupRecord(TypedDict):
    """One group row in the ``canair groups`` output / ``--json`` payload."""

    name: str
    description: str | None
    members: list[str]


def cmd_list(args) -> int:
    prof = active()
    try:
        groups = load_groups(prof)
    except GroupError as e:
        messages.error(f"invalid groups.yaml: {e}")
        return 1

    records: list[GroupRecord] = [
        {"name": g.name, "description": g.description or None, "members": list(g.members)}
        for g in groups.values()
    ]

    if args.json:
        json.dump(
            {"groups": records, "source": str(prof.groups_file)},
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    if not groups:
        print(
            f"\n  No selector groups declared for profile {ansi.c(prof.name, ansi.CYAN)} "
            f"{ansi.c('(no groups.yaml)', ansi.DIM)}.\n"
            f"  Add one with {ansi.c('canair groups add NAME SEL [SEL ...]', ansi.CYAN)}.\n"
        )
        return 0

    print(
        f"\n  {ansi.c('Selector groups', ansi.BOLD)} — {len(groups)} group(s) in "
        f"{ansi.c(prof.name, ansi.CYAN)}  {ansi.c(f'(use as {GROUP_SIGIL}name)', ansi.DIM)}\n"
    )
    for r in records:
        name = ansi.c(f"{GROUP_SIGIL}{r['name']}", ansi.CYAN)
        print(f"  {name}")
        if r["description"]:
            print(f"    {ansi.c(r['description'], ansi.DIM)}")
        print(f"    {ansi.c('members:', ansi.DIM)} {' '.join(r['members'])}")

    print(f"\n  {ansi.c(f'source: {prof.groups_file}', ansi.DIM)}\n")
    return 0


def _name(raw: str) -> str:
    return f"@{raw.strip().lower()}"


def cmd_add(args) -> int:
    return run_edit(
        lambda: add_group(args.name, args.members, description=args.description, profile=active()),
        f"added group {_name(args.name)}",
    )


def cmd_rm(args) -> int:
    return run_edit(
        lambda: remove_group(args.name, profile=active()), f"removed group {_name(args.name)}"
    )


def cmd_rename(args) -> int:
    return run_edit(
        lambda: rename_group(args.old, args.new, profile=active()),
        f"renamed {_name(args.old)} → {_name(args.new)}",
    )


def cmd_set_description(args) -> int:
    return run_edit(
        lambda: set_group_description(args.name, args.value or None, profile=active()),
        f"{set_or_clear(args.value)} description on {_name(args.name)}",
    )


def cmd_set_members(args) -> int:
    return run_edit(
        lambda: set_group_members(args.name, args.members, profile=active()),
        f"set members on {_name(args.name)}",
    )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List and edit the profile's capture/monitor selector groups (groups.yaml)",
        description="List the active profile's named selector groups, or edit the "
        "vocabulary (add/rm/rename/set-description/set-members). Reference a group "
        "on the command line with the @ sigil, e.g. `canair monitor @charging`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument("--json", action="store_true", help="Output the list as JSON")
    sub = parser.add_subparsers(dest="groups_command")

    lst = sub.add_parser("list", help="List the groups (default)")
    lst.add_argument("--json", action="store_true", help="Output as JSON")
    lst.set_defaults(_groups_func=cmd_list)

    add = sub.add_parser("add", help="Add a new group")
    add.add_argument("name", help="Group name (lower-cased)")
    add.add_argument("members", nargs="+", metavar="SEL", help="Member selectors (ECU or ECU:PID)")
    add.add_argument("--description", "-d", help="Human-readable description")
    add.set_defaults(_groups_func=cmd_add)

    rm = sub.add_parser("rm", help="Remove a group")
    rm.add_argument("name", help="Group name")
    rm.set_defaults(_groups_func=cmd_rm)

    ren = sub.add_parser("rename", help="Rename a group")
    ren.add_argument("old", help="Current group name")
    ren.add_argument("new", help="New group name (lower-cased)")
    ren.set_defaults(_groups_func=cmd_rename)

    sd = sub.add_parser("set-description", help="Set/clear a group's description")
    sd.add_argument("name", help="Group name")
    sd.add_argument("value", nargs="?", default=None, help="Description (omit to clear)")
    sd.set_defaults(_groups_func=cmd_set_description)

    sm = sub.add_parser("set-members", help="Replace a group's member selectors")
    sm.add_argument("name", help="Group name")
    sm.add_argument("members", nargs="+", metavar="SEL", help="Member selectors (ECU or ECU:PID)")
    sm.set_defaults(_groups_func=cmd_set_members)

    parser.set_defaults(func=run)
    return parser


run = subcommand_dispatch("_groups_func", cmd_list)
