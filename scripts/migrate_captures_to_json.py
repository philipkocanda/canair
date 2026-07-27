#!/usr/bin/env python3
"""Migrate the diagnostic capture store from per-day YAML to JSON.

Converts ``<profile>/captures/YYYY-MM-DD.yaml`` → ``…/YYYY-MM-DD.json`` for the
JSON capture-store cutover (``plans/2026-07-27-captures-json-storage.md``). Each
file is round-trip verified before anything is written/deleted (see
``canlib.capture_migrate``).

Defaults to a DRY RUN; pass ``--apply`` to write. Scope with ``--profile NAME``
or ``--all`` (default: the active profile). The user-facing equivalent is
``canair captures migrate``; this script exists to convert *all* bundled/
discovered profiles in one pass.

    uv run python scripts/migrate_captures_to_json.py            # dry run, active profile
    uv run python scripts/migrate_captures_to_json.py --all --apply
"""

from __future__ import annotations

import argparse
import sys

from canlib.capture_migrate import MigrationError, migrate_dir
from canlib.profile import Profile, active, discover_profiles, resolve_profile


def _migrate_profile(prof: Profile, apply: bool) -> tuple[int, int]:
    """Migrate one profile's captures. Returns (files, captures) converted."""
    cap_dir = prof.captures_dir
    if not cap_dir.is_dir():
        print(f"  {prof.name}: no captures/ — skipping")
        return 0, 0
    try:
        results = migrate_dir(cap_dir, dry_run=not apply)
    except MigrationError as e:
        print(f"  {prof.name}: ERROR — {e}", file=sys.stderr)
        raise
    if not results:
        print(f"  {prof.name}: no legacy .yaml capture files (already JSON?)")
        return 0, 0
    caps = sum(r.captures for r in results)
    verb = "converted" if apply else "would convert"
    print(f"  {prof.name}: {verb} {len(results)} file(s), {caps} capture(s)")
    for r in results:
        print(f"      {r.yaml_path.name} -> {r.json_path.name}  ({r.captures} caps)")
    return len(results), caps


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--profile", default=None, help="Profile name (default: active)")
    scope.add_argument("--all", action="store_true", help="Migrate every discovered profile")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = ap.parse_args()

    if args.all:
        profiles = [resolve_profile(name) for name in discover_profiles()]
    else:
        profiles = [resolve_profile(args.profile) if args.profile else active()]

    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {len(profiles)} profile(s)\n")
    total_files = total_caps = 0
    try:
        for prof in profiles:
            files, caps = _migrate_profile(prof, args.apply)
            total_files += files
            total_caps += caps
    except MigrationError:
        return 1

    print(
        f"\n{'Converted' if args.apply else 'Would convert'} "
        f"{total_files} file(s), {total_caps} capture(s)."
    )
    if total_files and not args.apply:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
