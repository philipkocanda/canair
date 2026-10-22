"""Capture-store maintenance: ``--recover`` and the repair kinds.

Whole-store file operations that take no QUERY — reconciling the journals a
killed ``--save`` session left behind, and the on-disk repairs (legacy YAML →
JSON, the capture field ``ecu`` → ``rx``, legacy discover responders' missing
``tx``, and multi-DID replies stored whole under one DID). The QUERY-driven
mutating modes live in :mod:`delete`, :mod:`backfill`, :mod:`set_state` and
:mod:`session_fields`.
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


def _discover_addressing(captures_dir: Path | None):
    """The registry and addressing rule of the profile that owns the captures."""
    from canlib.addressing import resolve_mode, resolve_rx_offset
    from canlib.discover_migrate import Addressing
    from canlib.ecus import load_ecus
    from canlib.pids import load_pids
    from canlib.profile import active, profile_for_path

    # A layered profile's captures sit in the overlay; its definitions in the root.
    prof = active() if captures_dir is None else profile_for_path(captures_dir)
    data = load_pids(prof.ecus_dir)
    return Addressing(
        ecus=load_ecus(prof.ecus_dir),
        rx_offset=resolve_rx_offset(data),
        mode=resolve_mode(data),
    )


def cmd_migrate_discover(
    captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False
) -> int:
    """Give legacy discover responders their probed ``tx`` and the profile's ``rx``."""
    import json as _json
    from dataclasses import asdict

    from canlib.discover_migrate import migrate_dir

    cdir = resolve_captures_dir(captures_dir)
    results = migrate_dir(cdir, _discover_addressing(captures_dir), dry_run=dry_run)
    touched = [r for r in results if r.repairs or r.skipped]

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "files": [
                    {
                        "file": r.path.name,
                        "repairs": [asdict(x) for x in r.repairs],
                        "skipped": [asdict(x) for x in r.skipped],
                    }
                    for r in touched
                ],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 1 if any(r.skipped for r in results) else 0

    if not touched:
        print(f"  No legacy discover responders to repair in {cdir}.")
        return 0
    verb = "Would repair" if dry_run else "Repaired"
    for r in touched:
        print(f"  {r.path.name}: {verb.lower()} {len(r.repairs)} responder(s)")
        for x in r.repairs:
            moved = "" if x.rx == x.old_rx else f"  {ansi.YELLOW}(was {x.old_rx}){ansi.RESET}"
            print(
                f"    [{x.session_idx}] {x.tx} → {x.rx}{moved}  {ansi.DIM}{x.response}{ansi.RESET}"
            )
        for x in r.skipped:
            print(f"    ! [{x.session_idx}] {x.response}: {x.reason}")
    total = sum(len(r.repairs) for r in results)
    print(f"  {verb} {total} responder(s).")
    print(
        "  The transport these sessions recorded cannot be recovered from the data; "
        "correct it with `canair captures uds --set-transport`."
    )
    if dry_run:
        print("  Re-run without --dry-run to write.")
    return 1 if any(r.skipped for r in results) else 0


def cmd_split_batched(
    captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False
) -> int:
    """Split multi-DID replies stored whole under one DID into one capture per DID."""
    import json as _json

    from canlib.capture_io import resolve_capture_layers
    from canlib.ecus import build_rx_index
    from canlib.unsplit_batches import split_unsplit_batches

    cdir = resolve_captures_dir(captures_dir)
    report = split_unsplit_batches(resolve_capture_layers(captures_dir), cdir, dry_run=dry_run)
    names = build_rx_index()

    def _row(s) -> dict:
        return {
            "file": s.where.path.name,
            "session": s.where.session,
            "capture": s.where.capture,
            "time": s.where.time,
            "ecu": names.get(s.rx, hex(s.rx)),
            "pid": s.pid,
            "split_into": list(s.pids),
        }

    if as_json:
        _json.dump(
            {
                "dry_run": dry_run,
                "captures_dir": str(cdir),
                "split": [_row(s) for s in report.split],
                "read_only": [_row(s) for s in report.read_only],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 1 if report.read_only else 0

    if not report.split and not report.read_only:
        print(f"  No multi-DID replies stored under a single DID in {cdir}.")
        return 0
    verb = "Would split" if dry_run else "Split"
    for s in report.split:
        row = _row(s)
        print(
            f"  {verb.lower()} {row['file']} [{s.where.session}].{s.where.capture} "
            f"{row['ecu']} {s.pid} @ {s.where.time} → {' + '.join(s.pids)}"
        )
    for s in report.read_only:
        row = _row(s)
        print(f"  ! {s.where.path} {row['ecu']} {s.pid} @ {s.where.time}: read-only base layer")
    print(f"  {verb} {len(report.split)} capture(s).")
    if dry_run:
        print("  Re-run without --dry-run to write.")
    return 1 if report.read_only else 0


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


def _add_migrate_discover_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "migrate-discover",
        help="Repair legacy discover captures: record each responder's probed TX",
        description="Give legacy discover captures each responder's probed `tx`, and "
        "the `rx` the profile's addressing gives it.\n\n"
        "Those captures stored only an `rx` per responder, and for an address not "
        "in the ECU registry it was computed as TX + 0x08 whatever the profile's "
        "addressing: block said. The TX is recoverable exactly: a registered "
        "responder was labelled `NAME (status)`, an unregistered one with the bare "
        "status. Idempotent; a responder whose ECU name no longer resolves is "
        "reported, not guessed (exit 1). The session transport cannot be recovered "
        "this way — use `captures uds --set-transport`. Performs the repair by "
        "default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview repairs without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_migrate_discover(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser


def _add_split_batched_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "split-batched",
        help="Split multi-DID replies stored whole under one DID into per-DID captures",
        description="Find service-22 captures whose payload is a whole multi-DID reply "
        "(62 D1 <data> D2 <data> …) stored under its first DID, and replace each with "
        "one capture per DID at the same time.\n\n"
        "A batched reply starts like a single reply to its first DID, so a batch reply "
        "that leaked into a per-DID read passed the echo check and was saved whole; "
        "`validate captures` reports these. A row is split only when it walks exactly "
        "through DIDs of the same ECU at each DID's established length, so a padded "
        "or merely long reply is left alone. The split rows drop `elapsed_ms` (one "
        "round trip answered them all). Idempotent; a layered profile's read-only base "
        "is reported, not rewritten (exit 1). Performs the split by default; pass "
        "--dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview splits without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_split_batched(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser
