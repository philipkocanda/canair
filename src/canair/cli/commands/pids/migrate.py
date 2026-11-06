"""``canair pids migrate-categories``."""

from __future__ import annotations

import argparse

from canair.cli.commands._edit import add_edit_args
from canair.cli.profile_context import target_profile
from canair.yaml_edit.echo import echo_edit
from canair.yaml_edit.pids import (
    Migrated,
    find_ecu_file,
    migrate_categories,
)

from .common import apply_in_scope, ecu_names


def add_parsers(sub) -> None:
    """Register the kinds of this group under ``canair pids``."""
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
