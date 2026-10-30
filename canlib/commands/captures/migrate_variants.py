"""``canair captures migrate-variants ID`` — tag untagged history with the car it came from."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canlib.captures import io as capture_io
from canlib.captures.dates import parse_iso_date


def cmd_migrate_variants(args) -> int:
    from canlib.migrations.variants import stamp_untagged
    from canlib.profile import active
    from canlib.variants import load_variants, normalize_variant_id

    variant = normalize_variant_id(args.id)
    declared = load_variants(active())
    if variant not in declared:
        known = ", ".join(declared) or "none (no variants.yaml)"
        print(f"error: no variant {variant!r} in this profile (declared: {known})", file=sys.stderr)
        return 2
    if args.date and (args.since or args.until):
        print("error: --date cannot be combined with --since/--until", file=sys.stderr)
        return 2
    since = args.date or args.since
    until = args.date or args.until

    # Only the profile's own store: a layered profile's base is someone else's history.
    cdir = capture_io.resolve_captures_dir(args.dir)
    if not cdir.is_dir():
        print(f"error: no such captures directory: {cdir}", file=sys.stderr)
        return 2
    capture_io.ensure_migrated(cdir)
    results = stamp_untagged(
        capture_io.iter_capture_files(cdir),
        variant,
        since=since,
        until=until,
        label=args.label,
        dry_run=args.dry_run,
    )
    total = sum(len(r.sessions) for r in results)
    if args.json:
        payload = {
            "variant": variant,
            "dry_run": args.dry_run,
            "captures_dir": str(cdir),
            "sessions": total,
            "files": [{"file": r.path.name, "sessions": len(r.sessions)} for r in results],
        }
        print(json.dumps(payload, indent=2))
        return 0
    if not total:
        print(f"  No untagged sessions in scope in {cdir}.")
        return 0
    verb = "Would tag" if args.dry_run else "Tagged"
    print(f"  {verb} {total} untagged session(s) in {cdir} as {variant} (attribution: migrated):")
    for r in results:
        print(f"    {r.path.name}  {len(r.sessions)} session(s)")
    if args.dry_run:
        print("  Re-run without --dry-run to write.")
    return 0


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate-variants",
        help="Tag untagged capture sessions with the profile variant they came from",
        description="Tag the active profile's untagged capture sessions with the variant "
        "(a variants.yaml ID) of the car that recorded them, attributed 'migrated'.\n\n"
        "For history recorded before the profile declared its variants. Scope it to "
        "the sessions you know came from that car with --date/--since/--until/--label, "
        "and run it once per car. Sessions that already carry a variant are never "
        "touched, and only the profile's own captures are written (a layered "
        "profile's base is left alone). Writes by default; --dry-run previews. To "
        "correct a tag afterwards, use `captures uds --set-variant`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("id", metavar="ID", help="The variant the recorded car is (e.g. 28kwh)")
    parser.add_argument("--date", type=parse_iso_date, metavar="YYYY-MM-DD", help="Only this day")
    parser.add_argument(
        "--since", type=parse_iso_date, metavar="YYYY-MM-DD", help="Only on or after this day"
    )
    parser.add_argument(
        "--until", type=parse_iso_date, metavar="YYYY-MM-DD", help="Only on or before this day"
    )
    parser.add_argument("--label", help="Only sessions whose label contains this text")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(func=cmd_migrate_variants)
    return parser
