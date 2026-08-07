"""Capture-store maintenance: ``--recover`` and the two ``migrate`` kinds.

Whole-store file operations that take no QUERY — reconciling the journals a
killed ``--save`` session left behind, and the two one-time on-disk format
migrations (legacy YAML → JSON, and the capture field ``ecu`` → ``rx``). The
QUERY-driven mutating modes live in :mod:`delete`, :mod:`backfill` and
:mod:`set_state`.
"""

import argparse
import sys
from pathlib import Path

from canlib import ansi
from canlib.capture_io import resolve_captures_dir


def cmd_recover(captures_dir: Path | None, discard: bool = False) -> int:
    """Reconcile (or discard) orphaned capture journals left by a killed session."""
    from canlib.capture_journal import list_orphans
    from canlib.capture_journal import recover as _recover

    cdir = resolve_captures_dir(captures_dir)
    orphans = list_orphans(cdir)
    if not orphans:
        print("  No orphaned capture journals found.")
        return 0

    verb = "Discarding" if discard else "Recovering"
    print(f"  {verb} {len(orphans)} orphaned journal(s) in {cdir}/.journal/:")
    recovered = 0
    for path in orphans:
        try:
            written = _recover(path, discard=discard)
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {path.name}: {ex}")
            continue
        if discard:
            print(f"    - {path.name} (discarded)")
        elif written is not None:
            print(f"    \u2192 {path.name} \u2192 {written.name}")
            recovered += 1
        else:
            print(f"    - {path.name} (empty; removed)")
    if not discard:
        print(f"  Recovered {recovered} session(s).")
    return 0


def cmd_migrate(captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False) -> int:
    """Convert legacy captures/*.yaml → *.json for the active profile (or --dir)."""
    import json as _json

    from canlib.capture_migrate import MigrationError, migrate_dir

    cdir = resolve_captures_dir(captures_dir)
    try:
        results = migrate_dir(cdir, dry_run=dry_run)
    except MigrationError as e:
        if as_json:
            _json.dump({"error": str(e)}, sys.stdout)
            print()
        else:
            print(f"  {ansi.YELLOW}migration aborted:{ansi.RESET} {e}", file=sys.stderr)
        return 1

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "migrated": [
                    {"from": r.yaml_path.name, "to": r.json_path.name, "captures": r.captures}
                    for r in results
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not results:
        print(f"  No legacy .yaml capture files in {cdir} (already JSON).")
        return 0
    caps = sum(r.captures for r in results)
    verb = "Would convert" if dry_run else "Converted"
    print(f"  {verb} {len(results)} file(s), {caps} capture(s):")
    for r in results:
        print(
            f"    {r.yaml_path.name} \u2192 {r.json_path.name}  {ansi.DIM}({r.captures} caps){ansi.RESET}"
        )
    if dry_run:
        print("  Re-run without --dry-run to write.")
    return 0


def cmd_migrate_rx(
    captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False
) -> int:
    """Rename the capture ``ecu`` field → ``rx`` for the active profile (or --dir)."""
    import json as _json

    from canlib.capture_field_migrate import migrate_dir

    cdir = resolve_captures_dir(captures_dir)
    results = migrate_dir(cdir, dry_run=dry_run)
    touched = [r for r in results if r.renamed]
    total = sum(r.renamed for r in results)

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "renamed_total": total,
                "files": [{"file": r.path.name, "renamed": r.renamed} for r in touched],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0

    if not touched:
        print(f"  No `ecu` fields to rename in {cdir} (already `rx`).")
        return 0
    verb = "Would rename" if dry_run else "Renamed"
    print(f"  {verb} {total} `ecu` field(s) \u2192 `rx` across {len(touched)} file(s):")
    for r in touched:
        print(f"    {r.path.name}  {ansi.DIM}({r.renamed} field(s)){ansi.RESET}")
    if dry_run:
        print("  Re-run without --dry-run to write.")
    return 0


def orphan_notice(captures_dir: Path | None = None) -> None:
    """Print a one-line notice if orphaned journals exist (best-effort, silent on error)."""
    try:
        from canlib.capture_journal import list_orphans

        cdir = resolve_captures_dir(captures_dir)
        orphans = list_orphans(cdir)
    except Exception:
        return
    if orphans:
        print(
            f"  Note: {len(orphans)} orphaned capture journal(s) from a previous "
            "session \u2014 run `canair captures uds --recover` to save (or --discard)."
        )


# ---------------------------------------------------------------------------
# Parsers for the migrate kinds
# ---------------------------------------------------------------------------


def _add_migrate_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate",
        help="Convert legacy captures/*.yaml to JSON (captures/*.json)",
        description="Convert the active profile's legacy per-day capture files "
        "(captures/YYYY-MM-DD.yaml) to JSON (captures/YYYY-MM-DD.json).\n\n"
        "Capture data is stored as JSON (parses ~60x faster than YAML); this is "
        "the supported one-time migration for a profile created before the "
        "cutover. Each file is round-trip verified before the YAML is replaced. "
        "Performs the migration by default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview conversions without writing"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_migrate(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser


def _add_migrate_rx_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate-rx",
        help="Rename the legacy capture `ecu` field to `rx` (captures/*.json)",
        description="Rename the persisted capture field `ecu` \u2192 `rx` in the active "
        "profile's capture files.\n\n"
        "The field holds the ECU CAN *response* address (RX = request TX + 8), not "
        "an ECU name, so it was renamed to `rx` to stop it being confused with the "
        "resolved short name. Renames at the capture level and inside "
        "scan_results.responding[]; idempotent (a file already on `rx` is left "
        "untouched). Readers tolerate the legacy `ecu` key, so this migration is "
        "safe to defer. Performs the rename by default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_migrate_rx(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser
