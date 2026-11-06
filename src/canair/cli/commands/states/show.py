"""``canair states STATE``: which ECUs are readable in a state."""

from __future__ import annotations

import json
import sys

from canair.profile import active
from canair.render import ansi, messages
from canair.states.implies import exclusive_pairs
from canair.states.predicates import StatePredicateError
from canair.states.selectors import ecus_in_state
from canair.states.vocabulary import allowed_states, load_states


def cmd_show_state(args) -> int:
    """Reverse lookup: which ECUs are readable/awake in a given state.

    An ECU matches when the requested state is among its resolved states
    (ECU-level ``vehicle_states``, else the union of its PIDs'); an ECU tagged
    ``ALL`` matches every state. The source of each match is shown so it's clear
    whether the state is declared at the ECU level or only inferred from PIDs.
    """
    from canair.ecus.pids import load_pids
    from canair.ecus.registry import load_ecus

    prof = active()
    target = str(args.state).strip().upper()

    try:
        rules = load_states(prof)
    except StatePredicateError as e:
        messages.error(f"invalid vehicle_states.yaml: {e}")
        return 1

    vocab = allowed_states(prof)
    if target not in vocab:
        if args.json:
            json.dump(
                {"state": target, "error": "unknown state", "known": sorted(vocab)}, sys.stdout
            )
            print()
        else:
            messages.error(f"unknown state {target} (not in vehicle_states.yaml)")
            messages.hint(f"known states: {', '.join(sorted(vocab))}")
        return 1

    rule = next((r for r in rules if r.name.upper() == target), None)
    matches = ecus_in_state(target, load_pids(), prof, rules)
    implies = list(rule.implies) if rule else []
    specialized_by = [r.name for r in rules if target in r.implies]
    # excludes: is symmetric, so a pair declared on either side is reported here.
    excludes = sorted(
        {s for a, b in exclusive_pairs(rules) if target in (a, b) for s in (a, b)} - {target}
    )

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
                "implies": implies,
                "specialized_by": specialized_by,
                "excludes": excludes,
                "ecus": [
                    {
                        "name": m["name"],
                        "tx": f"0x{m['tx_id']:03X}" if m.get("tx_id") else None,
                        "bus": m["bus"],
                        "source": m["source"],
                        "via": m.get("via"),
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
    if implies:
        print(f"  {ansi.c('specializes: ' + ', '.join(implies), ansi.DIM)}")
    if specialized_by:
        print(f"  {ansi.c('specialized by: ' + ', '.join(specialized_by), ansi.DIM)}")
    if excludes:
        print(f"  {ansi.c('excludes: ' + ', '.join(excludes), ansi.DIM)}")

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
        if m.get("via"):
            src = f"{src} ({m['via']})"
        src_c = ansi.c(f"{src:<18}", ansi.GREEN if m["source"] == "all" else ansi.DIM)
        name_c = ansi.c(f"{m['name']:<12}", ansi.CYAN)
        print(f"  {name_c} {tx:<6} {src_c} {ansi.c(bus, ansi.CYAN)}")
    print()
    return 0
