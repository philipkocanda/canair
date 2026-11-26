"""``canair pids`` kinds that sweep a profile's PIDs: ``hoist-defaults``, ``migrate-categories``."""

from __future__ import annotations

import argparse

from canair.cli.commands._edit import add_edit_args
from canair.cli.profile_context import target_profile
from canair.core.edit_errors import EditError
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import (
    Migrated,
    find_ecu_file,
    hoist_defaults,
    migrate_categories,
)

from .common import apply_in_scope, ecu_names


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
    hp = sub.add_parser(
        "hoist-defaults",
        help="Move fields repeated across a PID's parameters up to the PID",
        description=(
            "Find PIDs whose parameters repeat the same unit, min, max, verified, "
            "source or source_links and state the value once on the PID instead. A field "
            "moves only when it changes no signal: every parameter must state its own "
            "non-empty value, and one value must be shared by more than half of them (the "
            "rest keep theirs as overrides). The result is verified before it is kept. Name "
            "an ECU (and PID) to narrow the sweep; with neither, the whole profile is "
            "processed. --dry-run lists what would move."
        ),
    )
    hp.add_argument("ecu", nargs="?", help="Limit to this ECU (default: every ECU)")
    hp.add_argument("pid", nargs="?", help="Limit to this PID")
    hp.add_argument("--dry-run", action="store_true", help="List what would move; write nothing")
    add_edit_args(hp)
    hp.set_defaults(_pids_func=cmd_hoist_defaults)

    mc = sub.add_parser(
        "migrate-categories",
        help="Replace the retired ha_class field with category",
        description=(
            "One-time migration off ha_class (a Home Assistant device class on nearly every "
            "signal). An ha_class the signal's unit already implies is removed; any other "
            "becomes a category (battery -> state_of_charge, light -> lamp, "
            "battery_charging -> charging), and one with no matching category is removed. "
            "A PID-level ha_class is resolved into its signals, as category is not "
            "inherited. Afterwards `canair validate pids` lists the two-state categories "
            "that still need --value maps. Name an ECU to narrow the sweep; --dry-run lists "
            "what would change."
        ),
    )
    mc.add_argument("ecu", nargs="?", help="Limit to this ECU (default: every ECU)")
    mc.add_argument("--dry-run", action="store_true", help="List what would change; write nothing")
    add_edit_args(mc)
    mc.set_defaults(_pids_func=cmd_migrate_categories)


def cmd_migrate_categories(args: argparse.Namespace) -> int:
    ecus = [args.ecu] if args.ecu else ecu_names(args.dir)
    kept = dropped = 0
    for ecu in ecus:
        found: list[Migrated] = []

        def do(ecu=ecu, found=found):
            found.extend(
                migrate_categories(
                    ecu, dry_run=args.dry_run, pids_dir=args.dir, profile=target_profile(args)
                )
            )

        if args.dry_run:
            do()
            fpath = find_ecu_file(ecu, pids_dir=args.dir, profile=target_profile(args))
        else:
            fpath = apply_in_scope(args, do, ecu)
        for m in found:
            where = f"{m.pid}/{m.param}" if m.param else f"{m.pid} (PID default)"
            change = f"category: {m.category}" if m.category else "removed"
            print(f"  {ecu} {where}: ha_class {m.ha_class!r} -> {change} ({m.reason})")
            if m.param and m.category:
                kept += 1
            else:
                dropped += 1
        if found and not args.dry_run:
            echo_edit(f"{ecu} migrated {len(found)} ha_class field(s)", fpath)
    verb = "Would migrate" if args.dry_run else "Migrated"
    print(f"  {verb} {kept + dropped} ha_class field(s): {kept} to category, {dropped} removed.")
    return 0


def cmd_hoist_defaults(args: argparse.Namespace) -> int:
    if args.pid and not args.ecu:
        raise EditError("a PID needs its ECU")
    ecus = [args.ecu] if args.ecu else ecu_names(args.dir)
    pids = fields = 0
    for ecu in ecus:
        found: list = []

        def do(ecu=ecu, found=found):
            found.extend(
                hoist_defaults(
                    ecu,
                    args.pid,
                    dry_run=args.dry_run,
                    pids_dir=args.dir,
                    profile=target_profile(args),
                )
            )

        if args.dry_run:
            do()
            fpath = find_ecu_file(ecu, pids_dir=args.dir, profile=target_profile(args))
        else:
            fpath = apply_in_scope(args, do, ecu)
        by_pid: dict[str, list] = {}
        for h in found:
            by_pid.setdefault(h.pid, []).append(h)
        for pid, hoisted in by_pid.items():
            names = ", ".join(f"{h.field} ({len(h.params)}/{h.total})" for h in hoisted)
            print(f"  {ecu} {pid}: {names}")
        if found and not args.dry_run:
            echo_edit(f"{ecu} hoisted {len(found)} shared field(s)", fpath)
        pids += len(by_pid)
        fields += len(found)
    verb = "Would hoist" if args.dry_run else "Hoisted"
    print(f"  {verb} {fields} field(s) across {pids} PID(s).")
    return 0
