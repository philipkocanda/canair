"""``canair states`` — list and edit the profile's vehicle-state vocabulary.

The vehicle-state vocabulary lives in ``profiles/<name>/vehicle_states.yaml`` —
a hand-curated, ordered list of the car's power/operating states (the analogue
of ``can_buses.yaml`` for the state axis). A capture session's ``vehicle_states``
and each PID/DID/research entry's ``vehicle_states`` draw from this vocabulary,
and states with a ``when:`` predicate are auto-suggested from decoded PID values.

A bare ``canair states`` lists the vocabulary (like ``canair bus``); the edit
subcommands surgically modify ``vehicle_states.yaml`` (comment-preserving,
re-validated, reverted on failure) so you never hand-edit it.

Examples:
  canair states                                   # list the vocabulary + usage
  canair states --json                            # machine-readable
  canair states READY                             # which ECUs are readable in READY
  canair states CHARGING --json                   # reverse lookup as JSON
  canair states add PRECONDITION --description "Cabin pre-conditioning"
  canair states set-predicate CHARGING "BMS.BATTERY_CURRENT < -1"
  canair states set-description ACC "Accessory power (ACC1)"
  canair states rename ACC2 IGN
  canair states rm PRECONDITION
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TypedDict

from canlib import ansi

NAME = "states"


# ANSI colors — emitted only when stdout is a TTY (piped output stays plain).
class StateRecord(TypedDict):
    """One state row in the ``canair states`` output / ``--json`` payload."""

    name: str
    description: str | None
    when: str | None
    uses: int
    broken: list[dict[str, str]]


def _collect_state_usage(obj, counts: dict[str, int]) -> None:
    """Recursively tally every ``vehicle_states`` token found under ``obj``.

    Walks the loaded ECU registry (dicts/lists) so ECU-level, per-PID, per-DID,
    iocontrol, research, and scan_log references are all counted uniformly.
    Tokens are upper-cased so the tally matches the canonical vocabulary.
    """
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "vehicle_states" and isinstance(val, list):
                for tok in val:
                    t = str(tok).strip().upper()
                    if t:
                        counts[t] = counts.get(t, 0) + 1
            else:
                _collect_state_usage(val, counts)
    elif isinstance(obj, list):
        for item in obj:
            _collect_state_usage(item, counts)


def _load_usage() -> dict[str, int]:
    """Count state-token references across the active profile's ecus/."""
    from canlib.pids import load_pids

    counts: dict[str, int] = {}
    try:
        _collect_state_usage(load_pids().get("ecus", {}), counts)
    except Exception:
        pass
    return counts


def cmd_show_state(args) -> int:
    """Reverse lookup: which ECUs are readable/awake in a given state.

    An ECU matches when the requested state is among its resolved states
    (ECU-level ``vehicle_states``, else the union of its PIDs'); an ECU tagged
    ``ALL`` matches every state. The source of each match is shown so it's clear
    whether the state is declared at the ECU level or only inferred from PIDs.
    """
    from canlib.ecus import load_ecus
    from canlib.pids import load_pids
    from canlib.profile import active
    from canlib.states import (
        StatePredicateError,
        allowed_states,
        ecus_in_state,
        load_states,
    )

    prof = active()
    target = str(args.state).strip().upper()

    try:
        rules = load_states(prof)
    except StatePredicateError as e:
        print(f"{ansi.c('Invalid vehicle_states.yaml:', ansi.RED)} {e}", file=sys.stderr)
        return 1

    vocab = allowed_states(prof)
    if target not in vocab:
        if args.json:
            json.dump(
                {"state": target, "error": "unknown state", "known": sorted(vocab)}, sys.stdout
            )
            print()
        else:
            print(
                f"\n  {ansi.c('Unknown state', ansi.RED)} {ansi.c(target, ansi.YELLOW)} "
                f"{ansi.c('(not in vehicle_states.yaml).', ansi.DIM)}"
            )
            print(f"  {ansi.c('Known states:', ansi.DIM)} {', '.join(sorted(vocab))}\n")
        return 1

    rule = next((r for r in rules if r.name.upper() == target), None)
    matches = ecus_in_state(target, load_pids(), prof)

    # Join CAN-bus segment(s) from the registry for context.
    ecus = load_ecus()
    by_tx = {tx: info for tx, info in ecus.items() if isinstance(info, dict)}
    for m in matches:
        info = by_tx.get(m.get("tx_id")) or {}
        m["bus"] = info.get("can_bus") or []

    if args.json:
        json.dump(
            {
                "state": target,
                "description": (rule.description or None) if rule else None,
                "when": (rule.expr or None) if rule else None,
                "ecus": [
                    {
                        "name": m["name"],
                        "tx": f"0x{m['tx_id']:03X}" if m.get("tx_id") else None,
                        "bus": m["bus"],
                        "source": m["source"],
                    }
                    for m in matches
                ],
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    header = f"ECUs readable in {target}"
    print(
        f"\n  {ansi.c(header, ansi.BOLD)} — {len(matches)} ECU(s) in {ansi.c(prof.name, ansi.CYAN)}"
    )
    if rule and rule.description:
        print(f"  {ansi.c(rule.description, ansi.DIM)}")
    if rule and rule.expr:
        print(f"  {ansi.c('when: ' + rule.expr, ansi.DIM)}")

    if not matches:
        print(
            f"\n  {ansi.c('No ECUs declare this state.', ansi.YELLOW)} "
            f"{ansi.c('Annotate an ECU/PID with `vehicle_states: [' + target + ']`.', ansi.DIM)}\n"
        )
        return 0

    _SOURCE_LABEL = {
        "ecu": "ECU-level",
        "pids": "via PIDs",
        "all": "ALL (every state)",
    }
    print()
    hdr = f"{'NAME':<12} {'TX':<6} {'SOURCE':<18} BUS"
    print(f"  {ansi.c(hdr, ansi.DIM)}")
    for m in matches:
        tx = f"0x{m['tx_id']:03X}" if m.get("tx_id") else "—"
        bus = "/".join(m["bus"]) if m["bus"] else "—"
        src = _SOURCE_LABEL.get(m["source"], m["source"])
        src_c = ansi.c(f"{src:<18}", ansi.GREEN if m["source"] == "all" else ansi.DIM)
        name_c = ansi.c(f"{m['name']:<12}", ansi.CYAN)
        print(f"  {name_c} {tx:<6} {src_c} {ansi.c(bus, ansi.CYAN)}")
    print()
    return 0


def _load_broken_refs(rules) -> dict[str, list[dict[str, str]]]:
    """``{STATE: [{ref, kind, message}, …]}`` for predicates that can never match.

    A ``when:`` reference to a signal the registry doesn't define is invisible at
    evaluation time (indistinguishable from a not-polled signal), so the listing
    surfaces it here — otherwise a permanently-dead predicate still shows a
    healthy ``●``. Best-effort: an unloadable registry simply reports nothing.
    """
    from canlib.pids import load_pids
    from canlib.state_refs import check_references

    try:
        pids_data = load_pids()
        if not pids_data.get("ecus"):
            return {}
        issues = check_references([(r.name, r.expr) for r in rules if r.expr], pids_data)
    except Exception:
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for issue in issues:
        out.setdefault(issue.state, []).append(
            {"ref": issue.ref, "kind": issue.kind, "message": issue.message}
        )
    return out


def cmd_list(args) -> int:
    from canlib.profile import active
    from canlib.states import StatePredicateError, load_states

    if getattr(args, "state", None):
        return cmd_show_state(args)

    prof = active()
    try:
        rules = load_states(prof)
    except StatePredicateError as e:
        print(f"{ansi.c('Invalid vehicle_states.yaml:', ansi.RED)} {e}", file=sys.stderr)
        return 1

    usage = _load_usage()
    broken = _load_broken_refs(rules)
    records: list[StateRecord] = [
        {
            "name": r.name,
            "description": r.description or None,
            "when": r.expr or None,
            "uses": usage.get(r.name.upper(), 0),
            "broken": broken.get(r.name, []),
        }
        for r in rules
    ]

    # Tokens referenced by an ECU but absent from the declared vocabulary.
    declared = {r.name.upper() for r in rules}
    undeclared = sorted(t for t in usage if t not in declared)

    if args.json:
        json.dump(
            {
                "states": records,
                "undeclared": [{"name": t, "uses": usage[t]} for t in undeclared],
                "source": str(prof.states_file),
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    if not rules:
        print(
            f"\n  No vehicle states declared for profile {ansi.c(prof.name, ansi.CYAN)} "
            f"{ansi.c('(no vehicle_states.yaml)', ansi.DIM)}.\n"
            f"  Add one with {ansi.c('canair states add NAME', ansi.CYAN)} "
            f"or declare them in {prof.states_file}.\n"
        )
        return 0

    print(
        f"\n  {ansi.c('Vehicle states', ansi.BOLD)} — {len(rules)} state(s) in {ansi.c(prof.name, ansi.CYAN)}\n"
    )
    header = f"{'STATE':<14} {'USES':>4}  {'AUTO':<4}  DESCRIPTION"
    print(f"  {ansi.c(header, ansi.DIM)}")
    for r in records:
        name = ansi.c(f"{r['name']:<14}", ansi.CYAN)
        n = r["uses"]
        n_str = f"{n:>4}" if n else ansi.c(f"{0:>4}", ansi.YELLOW)
        if r["broken"]:
            auto = ansi.c(f"{'✗':<4}", ansi.RED)
        elif r["when"]:
            auto = ansi.c(f"{'●':<4}", ansi.GREEN)
        else:
            auto = ansi.c(f"{'—':<4}", ansi.DIM)
        desc = str(r["description"] or "")
        print(f"  {name} {n_str}  {auto}  {ansi.c(desc, ansi.DIM)}")
        if r["when"]:
            print(f"  {'':<14} {'':>4}        {ansi.c('when: ' + r['when'], ansi.DIM)}")
        for issue in r["broken"]:
            print(
                f"  {'':<14} {'':>4}        {ansi.c(issue['ref'] + ' — ' + issue['message'], ansi.RED)}"
            )

    print(f"\n  {ansi.c('● = auto-suggested from decoded PIDs (has a when: predicate)', ansi.DIM)}")
    if any(r["broken"] for r in records):
        print(
            f"  {ansi.c('✗ = predicate references a signal that does not exist — it can', ansi.DIM)}\n"
            f"  {ansi.c('    never match (see `canair validate states`)', ansi.DIM)}"
        )

    if undeclared:
        print(
            f"\n  {ansi.c('Undeclared tokens', ansi.YELLOW)} "
            f"{ansi.c('(used in ecus/ but absent from vehicle_states.yaml):', ansi.DIM)}"
        )
        for tok in undeclared:
            print(f"    {ansi.c(tok, ansi.YELLOW)}  {usage[tok]} use(s)")

    print(f"\n  {ansi.c(f'source: {prof.states_file}', ansi.DIM)}\n")
    return 0


def _run_edit(args, action) -> int:
    from canlib.states import StatePredicateError
    from canlib.states_edit import StatesEditError

    try:
        path = action()
    except (StatesEditError, StatePredicateError) as e:
        raise SystemExit(f"{ansi.c('  Error: ' + str(e), ansi.RED)}") from None
    print(f"{ansi.c('  ✓ ' + args._states_msg, ansi.GREEN)}  {ansi.c(f'({path.name})', ansi.DIM)}")
    return 0


def _warn_unresolved_refs(state: str, expr: str | None) -> None:
    """Warn when a just-written ``when:`` predicate reads signals that don't exist.

    Authoring a predicate before its signal is defined is legitimate, so this
    never blocks the write — but an unresolvable reference makes the state
    permanently un-suggestable and the evaluator can't report it (see
    :mod:`canlib.state_refs`), so it must not pass silently either.
    ``canair validate states`` is the hard gate.
    """
    if not expr:
        return
    try:
        from canlib.pids import load_pids
        from canlib.state_refs import check_references

        issues = check_references([(state, expr)], load_pids())
    except Exception:
        return  # never let an advisory check fail a successful write
    for issue in issues:
        print(f"  {ansi.c('warn:', ansi.YELLOW)} {issue.ref} — {issue.message}")
    if issues:
        print(
            f"    {ansi.c('this predicate can never match until the reference resolves', ansi.DIM)}"
        )


def cmd_add(args) -> int:
    from canlib.states_edit import add_state

    args._states_msg = f"added state {args.name.upper()}"
    rc = _run_edit(
        args,
        lambda: add_state(args.name, description=args.description, when=args.when),
    )
    _warn_unresolved_refs(args.name.upper(), args.when)
    return rc


def cmd_rm(args) -> int:
    from canlib.states_edit import remove_state

    args._states_msg = f"removed state {args.name.upper()}"
    return _run_edit(args, lambda: remove_state(args.name))


def cmd_rename(args) -> int:
    from canlib.states_edit import rename_state

    args._states_msg = f"renamed {args.old.upper()} → {args.new.upper()}"
    rc = _run_edit(args, lambda: rename_state(args.old, args.new))
    print(
        f"  {ansi.c('note:', ansi.YELLOW)} existing ecus/ and captures/ references still use "
        f"{args.old.upper()!r} — update them (e.g. re-run the migration script)."
    )
    return rc


def cmd_set_description(args) -> int:
    from canlib.states_edit import set_state_field

    args._states_msg = f"set description on {args.name.upper()}"
    return _run_edit(args, lambda: set_state_field(args.name, "description", args.value))


def cmd_set_predicate(args) -> int:
    from canlib.states_edit import set_state_field

    verb = "cleared" if not args.value else "set"
    args._states_msg = f"{verb} when: predicate on {args.name.upper()}"
    rc = _run_edit(args, lambda: set_state_field(args.name, "when", args.value or None))
    _warn_unresolved_refs(args.name.upper(), args.value)
    return rc


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List and edit the profile's vehicle-state vocabulary (vehicle_states.yaml)",
        description="List the active profile's vehicle operating states, or edit the "
        "vocabulary (add/rm/rename/set-description/set-predicate). Read-only companion "
        "of the state auto-suggestion used when recording captures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
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
    add.set_defaults(_states_func=cmd_add)

    rm = sub.add_parser("rm", help="Remove a state from the vocabulary")
    rm.add_argument("name")
    rm.set_defaults(_states_func=cmd_rm)

    ren = sub.add_parser("rename", help="Rename a state (references are NOT rewritten)")
    ren.add_argument("old")
    ren.add_argument("new")
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

    parser.set_defaults(func=run)
    return parser


def run(args) -> int:
    func = getattr(args, "_states_func", None)
    if func is None:
        return cmd_list(args)
    return func(args)


if __name__ == "__main__":
    import sys as _sys

    from canlib.cli import main

    _sys.exit(main(["states", *_sys.argv[1:]]))
