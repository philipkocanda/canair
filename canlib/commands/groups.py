"""``canair groups`` — list and edit the profile's capture/monitor selector groups.

A *group* is a named, reusable query stored in ``profiles/<name>/groups.yaml`` —
a list of ECU / ECU:PID selectors recalled on the command line with the ``@``
sigil (``canair monitor @charging``). It's the saved-query analogue of
``canair bus``/``canair states``: a bare ``canair groups`` lists the vocabulary,
and the edit subcommands surgically modify ``groups.yaml`` (comment-preserving,
re-validated, reverted on failure) so you never hand-edit it.

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

from canlib import ansi

NAME = "groups"


# ANSI colors — emitted only when stdout is a TTY (piped output stays plain).
class GroupRecord(TypedDict):
    """One group row in the ``canair groups`` output / ``--json`` payload."""

    name: str
    description: str | None
    members: list[str]


def cmd_list(args) -> int:
    from canlib.ecu_groups import GROUP_SIGIL, GroupError, load_groups
    from canlib.profile import active

    prof = active()
    try:
        groups = load_groups(prof)
    except GroupError as e:
        print(f"{ansi.c('Invalid groups.yaml:', ansi.RED)} {e}", file=sys.stderr)
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


def _run_edit(args, action) -> int:
    from canlib.groups_edit import GroupsEditError

    try:
        path = action()
    except GroupsEditError as e:
        raise SystemExit(f"{ansi.c('  Error: ' + str(e), ansi.RED)}") from None
    print(f"{ansi.c('  ✓ ' + args._groups_msg, ansi.GREEN)}  {ansi.c(f'({path.name})', ansi.DIM)}")
    return 0


def cmd_add(args) -> int:
    from canlib.groups_edit import add_group

    args._groups_msg = f"added group @{args.name.strip().lower()}"
    return _run_edit(
        args,
        lambda: add_group(args.name, args.members, description=args.description),
    )


def cmd_rm(args) -> int:
    from canlib.groups_edit import remove_group

    args._groups_msg = f"removed group @{args.name.strip().lower()}"
    return _run_edit(args, lambda: remove_group(args.name))


def cmd_rename(args) -> int:
    from canlib.groups_edit import rename_group

    args._groups_msg = f"renamed @{args.old.strip().lower()} → @{args.new.strip().lower()}"
    return _run_edit(args, lambda: rename_group(args.old, args.new))


def cmd_set_description(args) -> int:
    from canlib.groups_edit import set_group_description

    verb = "cleared" if not args.value else "set"
    args._groups_msg = f"{verb} description on @{args.name.strip().lower()}"
    return _run_edit(args, lambda: set_group_description(args.name, args.value or None))


def cmd_set_members(args) -> int:
    from canlib.groups_edit import set_group_members

    args._groups_msg = f"set members on @{args.name.strip().lower()}"
    return _run_edit(args, lambda: set_group_members(args.name, args.members))


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
    rm.add_argument("name")
    rm.set_defaults(_groups_func=cmd_rm)

    ren = sub.add_parser("rename", help="Rename a group")
    ren.add_argument("old")
    ren.add_argument("new")
    ren.set_defaults(_groups_func=cmd_rename)

    sd = sub.add_parser("set-description", help="Set/clear a group's description")
    sd.add_argument("name")
    sd.add_argument("value", nargs="?", default=None, help="Description (omit to clear)")
    sd.set_defaults(_groups_func=cmd_set_description)

    sm = sub.add_parser("set-members", help="Replace a group's member selectors")
    sm.add_argument("name")
    sm.add_argument("members", nargs="+", metavar="SEL", help="Member selectors (ECU or ECU:PID)")
    sm.set_defaults(_groups_func=cmd_set_members)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    func = getattr(args, "_groups_func", None)
    if func is None:
        return cmd_list(args)
    return func(args)


if __name__ == "__main__":
    import sys as _sys

    from canlib.cli import main

    _sys.exit(main(["groups", *_sys.argv[1:]]))
