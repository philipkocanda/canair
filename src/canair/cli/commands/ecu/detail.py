"""``canair ecu ECU``: one ECU's details and PID stats."""

from __future__ import annotations

import json
import sys

from canair.render import ansi

from .records import IDENTITY_FIELDS


def _print_dtcs(rec: dict) -> None:
    """Open DTCs in red, from the DTC log (offline: as of the latest logged scan)."""
    from canair.dtc.describe import describe_dtc

    status = rec.get("dtcs")
    if status is None:
        print(
            f"\n  {ansi.BOLD}DTCs{ansi.RESET}  {ansi.DIM}none logged — "
            f"`canair dtc {rec['name']}` reads them{ansi.RESET}"
        )
        return
    codes = status["open"]
    event = "the --clear" if status.get("cleared") else "the scan"
    when = f"{ansi.DIM}(as of {event} of {status['scanned']}){ansi.RESET}"
    if not codes:
        print(f"\n  {ansi.BOLD}DTCs{ansi.RESET}  {ansi.GREEN}none open{ansi.RESET} {when}")
        return
    print(f"\n  {ansi.BOLD}DTCs{ansi.RESET}  {ansi.c(f'{len(codes)} open', ansi.RED)} {when}")
    width = max(len(c) for c in codes)
    for code in codes:
        meaning = describe_dtc(code, ecu=rec["name"]).get("meaning") or ""
        print(f"    {ansi.c(f'{code:<{width}}', ansi.RED)}  {meaning}".rstrip())


def cmd_detail(rec: dict, as_json: bool) -> int:
    if as_json:
        json.dump(rec, sys.stdout, indent=2, default=str)
        print()
        return 0

    # Header
    title = f"{ansi.BOLD}{ansi.CYAN}{rec['name']}{ansi.RESET}"
    if rec.get("alias"):
        title += f" {ansi.DIM}(alias: {rec['alias']}){ansi.RESET}"
    print(f"\n  {title}")
    if rec.get("description"):
        print(f"  {rec['description']}")

    # Addresses / protocol
    proto = rec.get("id_protocol") or "?"
    print(
        f"\n  {ansi.DIM}TX{ansi.RESET} {rec['tx']}    {ansi.DIM}RX{ansi.RESET} {rec['rx']}    "
        f"{ansi.DIM}protocol{ansi.RESET} {proto}"
    )
    if rec.get("can_bus"):
        labels = rec.get("can_bus_labels") or rec["can_bus"]
        # Render "CODE (Name)" when a human label differs from the bare code.
        parts = [
            f"{code} ({label})" if label and label != code else code
            for code, label in zip(rec["can_bus"], labels, strict=False)
        ]
        print(f"  {ansi.DIM}CAN bus{ansi.RESET} {', '.join(parts)}")

    # Identity fields
    note = rec.get("identity_note")
    if rec["identity"] or note:
        print(f"\n  {ansi.BOLD}Identity{ansi.RESET}")
        for key, label in IDENTITY_FIELDS:
            if key in rec["identity"]:
                print(f"    {label:<12} {rec['identity'][key]}")
        if note:
            print(f"    {ansi.DIM}{note}{ansi.RESET}")

    # Stats
    stats = rec.get("stats")
    if stats is None:
        print(
            f"\n  {ansi.YELLOW}No PID definitions{ansi.RESET} "
            f"{ansi.DIM}(no pids: — identity-only module){ansi.RESET}"
        )
    else:
        print(f"\n  {ansi.BOLD}Stats{ansi.RESET}")
        verified = stats["verified"]
        params = stats["params"]
        vcolor = (
            ansi.GREEN if params and verified == params else (ansi.YELLOW if verified else ansi.DIM)
        )
        print(
            f"    {'PIDs':<14} {stats['pids']}"
            + (f"  {ansi.DIM}(+{stats['ignored']} ignored){ansi.RESET}" if stats["ignored"] else "")
        )
        print(f"    {'Signals':<14} {params}")
        print(f"    {'Verified':<14} {vcolor}{verified}/{params}{ansi.RESET}")
        print(f"    {'Captures':<14} {rec['captures']}")
        if stats["research_total"]:
            print(
                f"    {'Research':<14} {stats['research_open']} open "
                f"{ansi.DIM}/ {stats['research_total']} total{ansi.RESET}"
            )
        if stats["iocontrol"] or stats["iocontrol_discoveries"]:
            extra = (
                f"  {ansi.DIM}(+{stats['iocontrol_discoveries']} discoveries){ansi.RESET}"
                if stats["iocontrol_discoveries"]
                else ""
            )
            print(f"    {'IO-control':<14} {stats['iocontrol']}{extra}")
        if stats["routines"]:
            print(f"    {'Routines':<14} {stats['routines']}")
        if rec.get("vehicle_states"):
            avail = ", ".join(str(a) for a in rec["vehicle_states"])
            print(f"    {'States':<14} {avail}")

    _print_dtcs(rec)

    # Per-PID breakdown
    if rec["pid_list"]:
        print(f"\n  {ansi.BOLD}PIDs{ansi.RESET}")
        for p in rec["pid_list"]:
            flags = []
            status = p.get("status", "active")
            if status != "active":
                flags.append(f"{ansi.DIM}{status}{ansi.RESET}")
            caps = p["captures"]
            if not caps:
                flags.append(f"{ansi.YELLOW}no capture{ansi.RESET}")
            flag_str = ("  " + " ".join(flags)) if flags else ""
            vcolor = ansi.GREEN if p["params"] and p["verified"] == p["params"] else ansi.DIM
            cap_seg = f"  {ansi.DIM}{caps} cap{ansi.RESET}"
            print(
                f"    {ansi.CYAN}{p['pid']:<8}{ansi.RESET} "
                f"{p['params']:>2} signals  {vcolor}{p['verified']:>2} verified{ansi.RESET}"
                f"{cap_seg}{flag_str}"
            )
        print(
            f"\n  {ansi.DIM}Tip: `canair ecu {rec['name']} pids` shows each PID's "
            f"latest decoded state.{ansi.RESET}"
        )

    # Notes last (can be long/multiline)
    if rec.get("notes"):
        notes = " ".join(str(rec["notes"]).split())
        print(f"\n  {ansi.BOLD}Notes{ansi.RESET}\n    {notes}")
    print()
    return 0
