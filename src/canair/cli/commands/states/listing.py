"""``canair states``: the vocabulary with its usage."""

from __future__ import annotations

import json
import sys
from typing import TypedDict

from canair.cli.profile_context import active
from canair.ecus.pids import load_pids, load_raw_pids
from canair.render import ansi, messages
from canair.states.predicates import StatePredicateError
from canair.states.refs import check_references_on_every_car
from canair.states.vocabulary import load_states

from .show import cmd_show_state


class StateRecord(TypedDict):
    """One state row in the ``canair states`` output / ``--json`` payload."""

    name: str
    description: str | None
    when: str | None
    implies: list[str]
    excludes: list[str]
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
    counts: dict[str, int] = {}
    try:
        _collect_state_usage(load_pids(profile=active()).get("ecus", {}), counts)
    except Exception:
        pass
    return counts


def _load_broken_refs(rules, profile) -> dict[str, list[dict[str, str]]]:
    """``{STATE: [{ref, kind, message}, …]}`` for predicates that can never match.

    A ``when:`` reference to a signal the registry doesn't define is invisible at
    evaluation time (indistinguishable from a not-polled signal), so the listing
    surfaces it here — otherwise a permanently-dead predicate still shows a
    healthy ``●``. Best-effort: an unloadable registry simply reports nothing.
    """
    try:
        if not load_raw_pids(profile).get("ecus"):
            return {}
        issues = check_references_on_every_car([(r.name, r.expr) for r in rules if r.expr], profile)
    except Exception:
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for issue in issues:
        out.setdefault(issue.state, []).append(
            {"ref": issue.ref, "kind": issue.kind, "message": issue.message}
        )
    return out


def cmd_list(args) -> int:
    if getattr(args, "state", None):
        return cmd_show_state(args)

    prof = active()
    try:
        rules = load_states(prof)
    except StatePredicateError as e:
        messages.error(f"invalid vehicle_states.yaml: {e}")
        return 1

    usage = _load_usage()
    broken = _load_broken_refs(rules, prof)
    records: list[StateRecord] = [
        {
            "name": r.name,
            "description": r.description or None,
            "when": r.expr or None,
            "implies": list(r.implies),
            "excludes": list(r.excludes),
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
        if r["implies"]:
            arrow = "specializes: " + ", ".join(r["implies"])
            print(f"  {'':<14} {'':>4}        {ansi.c(arrow, ansi.DIM)}")
        if r["excludes"]:
            excl = "excludes: " + ", ".join(r["excludes"])
            print(f"  {'':<14} {'':>4}        {ansi.c(excl, ansi.DIM)}")
        if r["when"]:
            print(f"  {'':<14} {'':>4}        {ansi.c('when: ' + r['when'], ansi.DIM)}")
        for issue in r["broken"]:
            print(
                f"  {'':<14} {'':>4}        {ansi.c(issue['ref'] + ' — ' + issue['message'], ansi.RED)}"
            )

    print(f"\n  {ansi.c('● = auto-suggested from decoded PIDs (has a when: predicate)', ansi.DIM)}")
    if any(r["implies"] for r in records):
        print(
            f"  {ansi.c('specializes: = this state is the more specific reading when both', ansi.DIM)}\n"
            f"  {ansi.c('    match, so the live view shows it instead of the broader one', ansi.DIM)}"
        )
    if any(r["excludes"] for r in records):
        print(
            f"  {ansi.c('excludes:    = the two cannot hold at one instant, so a session', ansi.DIM)}\n"
            f"  {ansi.c('    tagged with both sequenced through them and needs a timeline', ansi.DIM)}"
        )
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
