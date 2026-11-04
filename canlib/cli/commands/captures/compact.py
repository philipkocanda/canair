"""``canair captures compact`` — rewrite capture files in the folded, one-per-line form."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canlib import ansi
from canlib.captures import io as capture_io


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _fail(msg: str, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps({"error": msg}))
    else:
        print(msg if msg.startswith("error:") else f"error: {msg}", file=sys.stderr)
    return code


def cmd_compact(captures_dir: Path | None, *, dry_run: bool = False, as_json: bool = False) -> int:
    from canlib.migrations.compact import CompactError, compact_paths

    from . import layers

    cdir = capture_io.resolve_captures_dir(captures_dir)
    if not cdir.is_dir():
        return _fail(f"no such captures directory: {cdir}", as_json, 2)
    capture_io.ensure_migrated(cdir)
    files = capture_io.iter_capture_files(cdir)
    try:
        # Plan first, then write: a layered profile's base is read-only, so the
        # files the rewrite would touch are checked before any of them is.
        plan = compact_paths(files, dry_run=True)
        if blocked := layers.refusal(
            [r.path for r in plan if r.changed], "compacted", captures_dir
        ):
            return _fail(blocked, as_json, 1)
        results = plan if dry_run else compact_paths(files)
    except CompactError as e:
        return _fail(str(e), as_json, 1)

    changed = [r for r in results if r.changed]
    before = sum(r.before for r in results)
    after = sum(r.after for r in results)
    if as_json:
        out = {
            "dry_run": dry_run,
            "captures_dir": str(cdir),
            "bytes_before": before,
            "bytes_after": after,
            "files": [{"file": r.path.name, "before": r.before, "after": r.after} for r in changed],
        }
        print(json.dumps(out, indent=2))
        return 0

    if not changed:
        print(f"  All {len(results)} capture file(s) in {cdir} are already compact.")
        return 0
    verb = "Would compact" if dry_run else "Compacted"
    print(f"  {verb} {len(changed)} of {len(results)} capture file(s) in {cdir}:")
    for r in changed:
        print(f"    {r.path.name}  {_size(r.before)} → {_size(r.after)}")
    pct = 100 * (before - after) / before if before else 0
    print(f"  Total {_size(before)} → {_size(after)} ({pct:.0f}% smaller).")
    if dry_run:
        print(f"  {ansi.DIM}Re-run without --dry-run to write.{ansi.RESET}")
    return 0


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "compact",
        help="Rewrite capture files with repeated polls folded, one capture per line",
        description="Rewrite the active profile's capture files (captures/*.json) in "
        "the compact on-disk form.\n\n"
        "A poll that repeats the previous payload of the same ECU and PID is stored "
        "as a millisecond gap on the run's first capture (`repeat_ms` / "
        "`repeat_inline_ms`), and each capture is one line. Lossless: loading expands "
        "every repeat back into its own capture, so every command sees the same "
        "data. Every write already uses this form, so this only converts files no "
        "command has rewritten since. Idempotent; each file is verified before it "
        "is replaced, and a layered profile's read-only base is refused (exit 1). "
        "Performs the rewrite by default; pass --dry-run to preview.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_compact(args.dir, dry_run=args.dry_run, as_json=args.json)
    )
    return parser
