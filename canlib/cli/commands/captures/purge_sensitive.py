"""``canair captures purge-sensitive`` — remove stored replies to sensitive PIDs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from canlib import ansi
from canlib.captures import io as capture_io


def cmd_purge_sensitive(
    captures_dir: Path | None, *, dry_run: bool = False, yes: bool = False, as_json: bool = False
) -> int:
    from canlib.captures.sensitive_purge import purge_dir
    from canlib.privacy.sensitive import set_for_captures

    from . import layers

    cdir = capture_io.resolve_captures_dir(captures_dir)
    if not cdir.is_dir():
        print(f"error: no such captures directory: {cdir}", file=sys.stderr)
        return 2
    sset = set_for_captures(captures_dir)
    plan = purge_dir(cdir, sset, dry_run=True)
    if blocked := layers.refusal([r.path for r in plan], "purged", captures_dir):
        print(blocked if blocked.startswith("error:") else f"error: {blocked}", file=sys.stderr)
        return 1
    total = sum(r.removed for r in plan)
    if as_json:
        if not dry_run and plan and not yes:
            print(json.dumps({"error": "rewriting captures needs --yes (or --dry-run)"}))
            return 1
        if not dry_run and plan:
            purge_dir(cdir, sset)
        out = {
            "dry_run": dry_run,
            "captures_dir": str(cdir),
            "removed": total,
            "files": [
                {"file": r.path.name, "removed": r.removed, "sessions_dropped": r.sessions_dropped}
                for r in plan
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    if not plan:
        print(f"  No capture of a sensitive PID in {cdir}.")
        return 0
    verb = "Would remove" if dry_run else "Removing"
    print(f"  {verb} {total} capture(s) of sensitive PIDs from {len(plan)} file(s) in {cdir}:")
    for r in plan:
        dropped = f", {r.sessions_dropped} session(s) left empty" if r.sessions_dropped else ""
        print(f"    {r.path.name}  {r.removed} capture(s){dropped}")
    if dry_run:
        print(f"  {ansi.DIM}Re-run without --dry-run to write.{ansi.RESET}")
        return 0
    if not yes:
        if not sys.stdin.isatty():
            print("error: refusing to rewrite captures without --yes (not a TTY)", file=sys.stderr)
            return 1
        if input("  Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("  Aborted.")
            return 1
    purge_dir(cdir, sset)
    print(
        f"  Done. {ansi.DIM}Already committed or pushed? The data stays in git history "
        f"until that is rewritten.{ansi.RESET}"
    )
    return 0


def add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "purge-sensitive",
        help="Remove stored replies to sensitive PIDs from capture files",
        description="Remove every stored reply to a PID or signal marked `sensitive: true` "
        "(see `canair pids set-sensitive`) from the active profile's capture files.\n\n"
        "No command saves such a reply, but a PID marked sensitive after it was "
        "recorded still has history on disk: analyses already leave it out, `validate "
        "captures` reports it, and this removes it. A session left with no capture is "
        "dropped. A layered profile's read-only base is refused (exit 1). Asks before "
        "writing unless --yes; --dry-run previews.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--yes", "-y", action="store_true", help="Do not ask for confirmation")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--dir", type=Path, default=None, help="Captures directory (default: active profile)"
    )
    parser.set_defaults(
        func=lambda args: cmd_purge_sensitive(
            args.dir, dry_run=args.dry_run, yes=args.yes, as_json=args.json
        )
    )
    return parser
