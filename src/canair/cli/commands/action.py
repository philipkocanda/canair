"""``canair action`` — list, run and edit the profile's guarded, make-specific actions.

An *action* is a named request (or a few) the active profile defines in
``actions.yaml`` for something canair has no built-in command for, such as
putting a Hyundai/Kia to sleep. Each one carries the live reads and the condition
that must hold before it may be sent (see :mod:`canair.actions`).

Examples:
  canair action                                  # list the profile's actions
  canair action run sleep --dry-run              # read + check the car, send nothing
  canair action run sleep                        # check, confirm, send
  canair action run sleep --yes                  # no prompt (scripts, agents)
  canair action add sleep --read IGPM:22BC03 \\
      --require "IGPM.IGN1_SWITCH == 0" --request functional:10D1:none
  canair action add ig3-read --read IGPM:22BC07 --require "IGPM.IG3_RELAY_OUTPUT == 0" \\
      --hold IGPM --request IGPM:2FBC4403 --wait 3 --then-read VCU:2101 \\
      --release IGPM:2FBC4400
  canair action run ig3-read --save --label 'IG3 wake read'   # keep the reads
  canair action set sleep --description "Put every ECU to sleep"
  canair action rm sleep
"""

from __future__ import annotations

import argparse
import json
import sys

from canair.actions import (
    ActionError,
    load_actions,
    parse_hold_spec,
    parse_request_spec,
    parse_then,
)
from canair.cli.commands._edit import run_edit, subcommand_dispatch
from canair.cli.live import LiveOperation, add_connection_args, live_command
from canair.cli.live.handlers import actuators
from canair.cli.profile_context import active
from canair.render import ansi, messages
from canair.yaml_edit.actions import add_action, remove_action, update_action

NAME = "action"
_RUN = LiveOperation(actuators.handle_action)


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
            "hold": [h.describe() for h in a.hold],
            "then": {"wait": a.then.wait, "read": list(a.then.read)} if a.then else None,
            "release": [
                {"to": r.target, "send": r.send, "response": r.response} for r in a.release
            ],
        }
        for a in actions.values()
    ]


def cmd_list(args) -> int:
    prof = active()
    try:
        actions = load_actions(prof)
    except ActionError as e:
        messages.error(f"invalid actions.yaml: {e}")
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
        if a.hold:
            print(f"    {ansi.c('holds:', ansi.DIM)} {' '.join(h.describe() for h in a.hold)}")
        if a.then:
            then = f"wait {a.then.wait:g} s, read {' '.join(a.then.read)}"
            print(f"    {ansi.c('then:', ansi.DIM)} {then}")
        if a.release:
            released = ", ".join(r.describe() for r in a.release)
            print(f"    {ansi.c('releases:', ansi.DIM)} {released}")
    print(f"\n  {ansi.c(f'source: {prof.actions_file}', ansi.DIM)}")
    print(f"  {ansi.c('run one with: canair action run NAME [--dry-run]', ansi.DIM)}\n")
    return 0


def _requests(specs):
    return [parse_request_spec(s) for s in specs] if specs else None


def _hold(specs):
    if specs is None:
        return None
    return [parse_hold_spec(s) for s in specs if s]


def _release(specs):
    if specs is None:
        return None
    return [parse_request_spec(s) for s in specs if s]


def _then(args):
    reads = getattr(args, "then_read", None)
    if not reads:
        if getattr(args, "wait", None) is not None:
            raise ActionError("--wait needs at least one --then-read")
        return None
    return parse_then({"wait": args.wait or 0, "read": reads}, f"action {args.name!r}")


def cmd_add(args) -> int:
    def edit():
        if not args.read or not args.require or not args.request:
            raise ActionError("add needs --read, --require and at least one --request")
        return add_action(
            args.name,
            reads=args.read,
            require=args.require,
            requests=_requests(args.request) or [],
            description=args.description,
            hold=_hold(args.hold) or [],
            then=_then(args),
            release=_release(args.release) or [],
            profile=active(),
        )

    return run_edit(edit, f"added action {args.name.strip().lower()}")


def cmd_set(args) -> int:
    return run_edit(
        lambda: update_action(
            args.name,
            description=args.description,
            reads=args.read,
            require=args.require,
            requests=_requests(args.request),
            hold=_hold(args.hold),
            then=_then(args),
            release=_release(args.release),
            clear_then=args.no_then,
            profile=active(),
        ),
        f"updated action {args.name.strip().lower()}",
    )


def cmd_rm(args) -> int:
    return run_edit(
        lambda: remove_action(args.name, profile=active()),
        f"removed action {args.name.strip().lower()}",
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
    p.add_argument(
        "--hold",
        action="append",
        metavar="ECU[:MODE]",
        help="Open a diagnostic session on ECU (MODE two hex digits, default 03) before the "
        "requests and keep it alive until the release is sent, as an IOControl needs "
        "(repeatable; replaces the list on `set`, and --hold '' clears it)",
    )
    p.add_argument(
        "--then-read",
        action="append",
        metavar="ECU[:PID]",
        help="Read these after the requests, while the holds last (repeatable; the "
        "mini-language selectors of `canair read`; replaces the list on `set`)",
    )
    p.add_argument(
        "--wait",
        type=float,
        metavar="SECONDS",
        help="Seconds to wait between the requests and the --then-read reads (0-60)",
    )
    p.add_argument(
        "--release",
        action="append",
        metavar="TARGET:HEX[:none]",
        help="A request sent at the end, also on Ctrl-C or an error, that hands back what the "
        "requests drove. Required for an IOControl that drives an output (repeatable; "
        "replaces the list on `set`, and --release '' clears it)",
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
    run_p.add_argument(
        "--save", action="store_true", help="Save the action's then: reads to captures/"
    )
    run_p.add_argument(
        "--label",
        metavar="TEXT",
        help="Session label for --save (default: 'action NAME', so nothing is asked while "
        "the action holds an output)",
    )
    run_p.add_argument("--state", metavar="TEXT", help="Session state for --save")
    run_p.add_argument("--notes", metavar="TEXT", help="Session notes for --save")
    add_connection_args(run_p)
    run_p.set_defaults(func=live_command(_RUN))

    add = sub.add_parser("add", help="Add an action")
    add.add_argument("name", help="Action name (lower-cased)")
    _add_definition_args(add)
    add.set_defaults(_action_func=cmd_add)

    st = sub.add_parser("set", help="Change fields of an action")
    st.add_argument("name", help="Action name")
    _add_definition_args(st)
    st.add_argument("--no-then", action="store_true", help="Remove the action's then: reads")
    st.set_defaults(_action_func=cmd_set)

    rm = sub.add_parser("rm", help="Remove an action")
    rm.add_argument("name", help="Action name")
    rm.set_defaults(_action_func=cmd_rm)

    parser.set_defaults(func=run)
    return parser


run = subcommand_dispatch("_action_func", cmd_list)
