"""``canair action`` — list, run and edit the profile's guarded, make-specific actions.

An *action* is a named request (or a few) the active profile defines in
``actions.yaml`` for something canair has no built-in command for, such as
putting a Hyundai/Kia to sleep. Each one carries the live reads and the condition
that must hold before it may be sent (see :mod:`canlib.actions`).

Examples:
  canair action                                  # list the profile's actions
  canair action run sleep --dry-run              # read + check the car, send nothing
  canair action run sleep                        # check, confirm, send
  canair action run sleep --yes                  # no prompt (scripts, agents)
  canair action add sleep --read IGPM:22BC03 \\
      --require "IGPM.IGN1_SWITCH == 0" --request functional:10D1:none
  canair action set sleep --description "Put every ECU to sleep"
  canair action rm sleep
"""

from __future__ import annotations

import argparse
import json
import sys

from canlib import ansi
from canlib.commands._live import add_connection_args, finalize_live_parser
from canlib.edit_echo import echo_edit

NAME = "action"


def _records(actions) -> list[dict]:
    return [
        {
            "name": a.name,
            "description": a.description or None,
            "reads": list(a.reads),
            "require": a.require,
            "requests": [
                {"to": r.target, "send": r.send, "response": r.response} for r in a.requests
            ],
        }
        for a in actions.values()
    ]


def cmd_list(args) -> int:
    from canlib.actions import ActionError, load_actions
    from canlib.profile import active

    prof = active()
    try:
        actions = load_actions(prof)
    except ActionError as e:
        print(f"{ansi.c('Invalid actions.yaml:', ansi.RED)} {e}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        json.dump(
            {"actions": _records(actions), "source": str(prof.actions_file)}, sys.stdout, indent=2
        )
        print()
        return 0
    if not actions:
        print(
            f"\n  No actions defined for profile {ansi.c(prof.name, ansi.CYAN)} "
            f"{ansi.c('(no actions.yaml)', ansi.DIM)}.\n"
        )
        return 0
    print(
        f"\n  {ansi.c('Actions', ansi.BOLD)} — {len(actions)} in {ansi.c(prof.name, ansi.CYAN)}\n"
    )
    for a in actions.values():
        print(f"  {ansi.c(a.name, ansi.CYAN)}")
        if a.description:
            print(f"    {ansi.c(a.description, ansi.DIM)}")
        print(f"    {ansi.c('sends:', ansi.DIM)} {', '.join(r.describe() for r in a.requests)}")
        print(f"    {ansi.c('reads:', ansi.DIM)} {' '.join(a.reads)}")
        print(f"    {ansi.c('requires:', ansi.DIM)} {a.require}")
    print(f"\n  {ansi.c(f'source: {prof.actions_file}', ansi.DIM)}")
    print(f"  {ansi.c('run one with: canair action run NAME [--dry-run]', ansi.DIM)}\n")
    return 0


def _run_edit(message: str, edit) -> int:
    from canlib.actions import ActionError
    from canlib.profile import require_writable_definitions

    require_writable_definitions()
    try:
        path = edit()
    except ActionError as e:
        raise SystemExit(ansi.c(f"  Error: {e}", ansi.RED)) from None
    echo_edit(message, path)
    return 0


def _requests(specs):
    from canlib.actions import parse_request_spec

    return [parse_request_spec(s) for s in specs] if specs else None


def cmd_add(args) -> int:
    from canlib.actions import ActionError
    from canlib.actions.edit import add_action

    def edit():
        if not args.read or not args.require or not args.request:
            raise ActionError("add needs --read, --require and at least one --request")
        return add_action(
            args.name,
            reads=args.read,
            require=args.require,
            requests=_requests(args.request) or [],
            description=args.description,
        )

    return _run_edit(f"added action {args.name.strip().lower()}", edit)


def cmd_set(args) -> int:
    from canlib.actions.edit import update_action

    return _run_edit(
        f"updated action {args.name.strip().lower()}",
        lambda: update_action(
            args.name,
            description=args.description,
            reads=args.read,
            require=args.require,
            requests=_requests(args.request),
        ),
    )


def cmd_rm(args) -> int:
    from canlib.actions.edit import remove_action

    return _run_edit(
        f"removed action {args.name.strip().lower()}", lambda: remove_action(args.name)
    )


def _add_definition_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--description", "-d", help="Human-readable description ('' clears it)")
    p.add_argument(
        "--read",
        action="append",
        metavar="ECU:PID",
        help="A PID to read live before sending (repeatable; replaces the list on `set`)",
    )
    p.add_argument(
        "--require",
        metavar="EXPR",
        help="Condition over ECU.SIGNAL values that must be TRUE to send, e.g. "
        "'IGPM.IGN1_SWITCH == 0 and IGPM.IGN2_SWITCH == 0' (no 'or'/'not')",
    )
    p.add_argument(
        "--request",
        action="append",
        metavar="TARGET:HEX[:none]",
        help="A request to send: an ECU name or 'functional', the request hex, and ':none' "
        "when no reply is expected (repeatable, sent in order; replaces the list on `set`)",
    )


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List, run and edit the profile's guarded, make-specific actions (actions.yaml)",
        description="Profile-defined actions: named requests for make-specific things such "
        "as putting the car to sleep. Each action declares live reads and a condition that "
        "must be TRUE before it is sent; its requests still pass the command blocklist, "
        "which only a code-owned profile quirk can relax for that exact request.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:" + __doc__.split("Examples:")[1]
        if __doc__ and "Examples:" in __doc__
        else "",
    )
    parser.add_argument("--json", action="store_true", help="Output the list as JSON")
    sub = parser.add_subparsers(dest="action_command")

    lst = sub.add_parser("list", help="List the actions (default)")
    lst.add_argument("--json", action="store_true", help="Output as JSON")
    lst.set_defaults(_action_func=cmd_list)

    run_p = sub.add_parser(
        "run",
        help="Check the car and send an action (live)",
        description="Read the action's signals live, refuse unless its condition is TRUE, "
        "ask for confirmation (unless --yes), re-check, then send its requests. --unsafe "
        "has no effect on an action.",
    )
    run_p.add_argument("action", metavar="NAME", help="Action name (see `canair action`)")
    run_p.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt")
    run_p.add_argument(
        "--dry-run", action="store_true", help="Read and check the car, but send nothing"
    )
    add_connection_args(run_p)
    finalize_live_parser(run_p)

    add = sub.add_parser("add", help="Add an action")
    add.add_argument("name", help="Action name (lower-cased)")
    _add_definition_args(add)
    add.set_defaults(_action_func=cmd_add)

    st = sub.add_parser("set", help="Change fields of an action")
    st.add_argument("name")
    _add_definition_args(st)
    st.set_defaults(_action_func=cmd_set)

    rm = sub.add_parser("rm", help="Remove an action")
    rm.add_argument("name")
    rm.set_defaults(_action_func=cmd_rm)

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    func = getattr(args, "_action_func", None)
    return cmd_list(args) if func is None else func(args)
