"""``canair dtc log`` — the last logged DTC scan, offline (no device).

The log itself is :mod:`canlib.dtc.log`; what it says now (open codes, totals) is
:mod:`canlib.dtc.history`. Only entries recorded for the selected car and its ECU
revisions are shown (:mod:`canlib.dtc.scope`).
"""

from __future__ import annotations

import argparse
import json

from canlib import ansi
from canlib.commands._live import ecu_completer


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "log",
        help="Show the last logged DTC scan, offline (no device)",
        description="Show the most recent logged DTC scan without touching the device "
        "(useful when the WiCAN is offline): each code decoded, and the change since the "
        "previous scan of the same scope. The scope is the full sweep (`canair dtc --all`) "
        "by default, or one ECU when named.\n\n"
        "Only scans recorded for the selected car (--variant) and its ECU revisions are "
        "used: a code read from another car, or from another revision of the ECU, says "
        "nothing about this one. The log lives in user state "
        "(<config dir>/dtc_log/<profile>.yaml), not in the profile, so reinstalling or "
        "re-cloning canair cannot erase it; `canair config` shows where.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair dtc log                       Last logged full sweep, with the change since the one before
  canair dtc log BMS                   Last logged scan of one ECU
  canair dtc log --json                Machine-readable (the scan, the previous one, the diff)
""",
    )
    parser.add_argument(
        "dtc", metavar="ECU", nargs="?", help="ECU name or TX ID (omit for the full sweep)"
    ).completer = ecu_completer
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(func=run)
    return parser


def _scope(ecu: str | None) -> str | None:
    """The log scope for ``ecu`` (``all`` when None), or None for an unknown ECU."""
    if not ecu:
        return "all"
    from canlib.dtc.refs import ecu_ref
    from canlib.ecus import resolve_tx

    tx_id = resolve_tx(ecu)
    return None if tx_id is None else ecu_ref(tx_id)


def run(args) -> int:
    from canlib.dtc.history import log_stats
    from canlib.dtc.log import (
        clears_between,
        diff_scans,
        latest_matching,
        prior_matching,
        render_scan,
        scope_label,
    )

    scope = _scope(args.dtc)
    if scope is None:
        from canlib.commands._hints import ecu_hint

        print(f"Unknown ECU '{args.dtc}'.\n")
        print(ecu_hint())
        return 2

    stats = log_stats()
    entry = latest_matching(scope)
    previous = prior_matching(scope, entry.get("timestamp", "")) if entry else None
    since = (
        clears_between(previous.get("timestamp", ""), entry.get("timestamp", ""))
        if entry and previous
        else []
    )
    if getattr(args, "json", False):
        out = {
            "scope": scope,
            "scan": entry,
            "previous": previous,
            "clears_since": since,
            "diff": diff_scans(previous, entry, since) if entry and previous else None,
            "log": stats.as_dict(),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0 if entry else 1

    if entry is None:
        if stats.unnamed:
            print(
                "No car selected: this profile has several variants, and a DTC belongs to "
                "the car it was read from. Pick one with --variant ID "
                "(or `canair profile use NAME --variant ID`)."
            )
        else:
            print(
                f"No logged DTC scan for scope '{scope_label(scope)}'. Run `canair dtc "
                + (args.dtc if args.dtc else "--all")
                + "` while connected to record one."
            )
        _print_footer(stats)
        return 1

    for line in render_scan(entry, previous, since):
        print(line)
    _print_footer(stats)
    return 0


def _print_footer(stats) -> None:
    others = (
        f"; {stats.other_cars} more for another car or ECU revision" if stats.other_cars else ""
    )
    print(f"  {ansi.DIM}{stats.scans} scan(s) logged in {stats.path}{others}{ansi.RESET}\n")


def log_hint() -> str | None:
    """One line saying what the DTC log holds, for a bare ``canair dtc``; None when empty."""
    from canlib.profile import ProfileError

    try:
        from canlib.dtc.history import log_stats

        stats = log_stats()
    except (ProfileError, OSError):
        return None
    if not stats.scans and not stats.other_cars:
        return None
    if stats.unnamed:
        return (
            f"DTC log: {stats.other_cars} scan(s) logged, but no car is selected "
            "(--variant ID) — `canair dtc log`."
        )
    return f"DTC log: {describe_stats(stats)} — `canair dtc log` (offline)."


def describe_stats(stats) -> str:
    """``9 DTC(s) seen in 3 scan(s), 9 open on 3 ECU(s), last scan …`` (open in red)."""
    text = f"{stats.seen} DTC(s) seen in {stats.scans} scan(s)"
    if stats.open_codes:
        text += ", " + ansi.c(f"{stats.open_codes} open on {len(stats.open)} ECU(s)", ansi.RED)
    elif stats.scans:
        text += ", none open"
    if stats.last_scan:
        text += f", last scan {stats.last_scan}"
    if stats.other_cars:
        text += f" (+{stats.other_cars} scan(s) for another car or ECU revision)"
    return text
