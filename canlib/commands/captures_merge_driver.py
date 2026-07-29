"""``canair captures merge-driver`` — git merge driver for capture files.

Dated capture files (``captures/YYYY-MM-DD.json``) are append-only session logs
that two machines routinely both append to on the same day; git's line-based
3-way merge then conflicts (and misaligns *inside* records) even though the data
model — disjoint additions to a list — is trivially mergeable. This subcommand
is the git ``merge`` driver that resolves that class automatically by unioning
the session lists (:mod:`canlib.captures_merge`).

Two uses:

* ``canair captures merge-driver %O %A %B [%P]`` — invoked *by git* during a
  merge. Reads the base/ours/theirs versions, writes the union into the ours
  file (``%A``), and exits 0. On a genuine divergent edit (or unparseable input)
  it exits non-zero so git falls back to normal conflict markers.
* ``canair captures merge-driver --install`` — one-time local registration: write
  the ``[merge "canair-captures"]`` stanza into the repo's ``.git/config`` so the
  ``.gitattributes`` rule takes effect. Git deliberately never reads a driver
  *command* from a tracked file, so every clone must run this once; until it does,
  merges simply fall back to markers (nothing breaks).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .. import capture_io, captures_merge

# The driver name referenced by .gitattributes (`merge=canair-captures`) and the
# .git/config `[merge "canair-captures"]` stanza. Keep the three in sync.
DRIVER_NAME = "canair-captures"


def _run_driver(base: str, ours: str, theirs: str, path: str | None = None) -> int:
    """Perform the union merge git asked for. Returns a git-style exit code.

    ``ours`` (git's ``%A``) is both an input and the output file: on success the
    merged result is written back to it. ``path`` (git's ``%P``) is the merged
    file's real pathname, used only for diagnostics. Exit 0 = resolved; non-zero
    = leave it to git (normal conflict markers).
    """
    label = path or ours
    ours_path = Path(ours)
    try:
        base_doc = capture_io.load_capture_file(Path(base))
        ours_doc = capture_io.load_capture_file(ours_path)
        theirs_doc = capture_io.load_capture_file(Path(theirs))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"canair merge-driver: {label}: unreadable capture input ({exc})", file=sys.stderr)
        return 1

    try:
        merged = captures_merge.merge_documents(base_doc, ours_doc, theirs_doc)
    except captures_merge.MergeConflict as exc:
        print(f"canair merge-driver: {label}: {exc}", file=sys.stderr)
        return 1

    # Write via the shared seam so the on-disk shape (indent, ordering, trailing
    # newline) is byte-identical to what --save writes — the merged file must be
    # indistinguishable from a normally-written one.
    capture_io.dump_capture_file(ours_path, merged)
    return 0


def _git_dir() -> Path | None:
    """Absolute path to the current repo's .git dir, or None if not in a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    path = out.stdout.strip()
    return Path(path) if path else None


def _install(as_json: bool = False) -> int:
    """Register the driver in the local repo's .git/config. Idempotent."""
    if _git_dir() is None:
        msg = "not inside a git repository — run this from a canair clone"
        print(
            json.dumps({"installed": False, "error": msg}) if as_json else f"✗ {msg}",
            file=sys.stderr,
        )
        return 1

    # `canair` on PATH is the end-user install; contributors run `uv run canair`.
    # Use the argv[0] interpreter-agnostic form so the stanza works for either:
    # invoke the CLI as a subcommand of whatever `canair` resolves to at merge time.
    driver_cmd = "canair captures merge-driver %O %A %B %P"
    settings = {
        f"merge.{DRIVER_NAME}.name": "canair capture session-union merge",
        f"merge.{DRIVER_NAME}.driver": driver_cmd,
    }
    for key, value in settings.items():
        try:
            subprocess.run(
                ["git", "config", key, value], check=True, capture_output=True, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            err = f"failed to write git config {key}: {exc}"
            print(
                json.dumps({"installed": False, "error": err}) if as_json else f"✗ {err}",
                file=sys.stderr,
            )
            return 1

    if as_json:
        print(json.dumps({"installed": True, "driver": DRIVER_NAME, "command": driver_cmd}))
    else:
        print(f"✓ Registered git merge driver '{DRIVER_NAME}' in this repo's .git/config")
        print(f"    driver = {driver_cmd}")
        print("  Capture-file merges (profiles/*/captures/*.json) now auto-union sessions.")
    return 0


def run(args: argparse.Namespace) -> int:
    if getattr(args, "install", False):
        return _install(as_json=getattr(args, "json", False))
    if not (args.base and args.ours and args.theirs):
        print(
            "canair captures merge-driver: expected git's %O %A %B file arguments "
            "(or --install to register the driver in .git/config)",
            file=sys.stderr,
        )
        return 2
    return _run_driver(args.base, args.ours, args.theirs, getattr(args, "path", None))


def add_parser(kinds) -> argparse.ArgumentParser:
    """Register the ``merge-driver`` kind on the ``captures`` group subparsers."""
    parser = kinds.add_parser(
        "merge-driver",
        help="Git merge driver: auto-union capture-file sessions (or --install it)",
        description=(
            "Git merge driver for append-only capture files (captures/*.json).\n\n"
            "Invoked by git during a merge as `merge-driver %O %A %B %P`; unions the\n"
            "session lists so two machines' same-day appends merge cleanly instead of\n"
            "conflicting. Falls back to normal conflict markers on a genuine divergent\n"
            "edit.\n\n"
            "Run `canair captures merge-driver --install` once per clone to register it\n"
            "in .git/config (git never loads a driver command from a tracked file, so\n"
            "this local step is required; until then merges just fall back to markers)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Register the driver in this repo's .git/config (one-time, per clone)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("base", nargs="?", help="git %%O — common ancestor version")
    parser.add_argument("ours", nargs="?", help="git %%A — our version (also the output file)")
    parser.add_argument("theirs", nargs="?", help="git %%B — their version")
    parser.add_argument("path", nargs="?", help="git %%P — merged file's pathname (for messages)")
    parser.set_defaults(func=run)
    return parser
