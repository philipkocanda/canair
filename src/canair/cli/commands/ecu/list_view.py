"""``canair ecu`` with no ECU: the list."""

from __future__ import annotations

import json
import sys

from canair.profile import active
from canair.render import ansi


def cmd_list(records: list[dict], as_json: bool, show_states: bool = False) -> int:
    if as_json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
        return 0

    # An ECU file with an empty `pids:` (identity-only, or a fresh seed) has a
    # definition but nothing to read, so count only ECUs with an active PID.
    n_pids = sum(1 for r in records if r.get("pids"))
    prof = active()
    print(f"\n  {ansi.BOLD}ECUs{ansi.RESET} — {len(records)} in {prof.name}, {n_pids} with PIDs\n")

    # Column header. BUS (and optional STATES) are last: they're the widest,
    # most-variable columns, so trailing them keeps the numeric columns aligned.
    bus_hdr = f"{'BUS':<12}" if show_states else "BUS"
    states_hdr = "  STATES" if show_states else ""
    print(
        f"  {ansi.DIM}{'NAME':<12} {'TX':<6} {'PROTO':<8} "
        f"{'PIDS':>4} {'VERIF':>7} {'CAPS':>5}  {bus_hdr}{states_hdr}{ansi.RESET}"
    )

    for r in records:
        name = r["name"]
        proto = r.get("id_protocol") or "?"
        bus = "/".join(r["can_bus"]) if r.get("can_bus") else "—"
        # Pad BUS only when a STATES column follows it (else it's trailing).
        bus_disp = f"{bus:<12}" if show_states else bus
        states = "/".join(r["states"]) if r.get("states") else "—"
        states_seg = f"  {states}" if show_states else ""
        if not r["has_pids"]:
            # Registry-only module: no PID data to summarise.
            print(
                f"  {ansi.CYAN}{name:<12}{ansi.RESET} {r['tx']:<6} {proto:<8} "
                f"{ansi.DIM}{'—':>4} {'—':>7} {'—':>5}{ansi.RESET}  {ansi.CYAN}{bus_disp}{ansi.RESET}{states_seg}"
            )
            continue
        params = r["params"]
        verified = r["verified"]
        vcolor = (
            ansi.GREEN if params and verified == params else (ansi.YELLOW if verified else ansi.DIM)
        )
        vstr = f"{verified}/{params}"
        caps = r.get("captures")
        if caps:
            cstr = f"{caps:>5}"
        else:
            cstr = f"{ansi.YELLOW}{'0':>5}{ansi.RESET}"
        print(
            f"  {ansi.CYAN}{name:<12}{ansi.RESET} {r['tx']:<6} {proto:<8} "
            f"{r['pids']:>4} {vcolor}{vstr:>7}{ansi.RESET} "
            f"{cstr}  {ansi.CYAN}{bus_disp}{ansi.RESET}{states_seg}"
        )
    print(f"\n  {ansi.DIM}source: {prof.ecus_dir}{ansi.RESET}\n")
    return 0
