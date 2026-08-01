#!/usr/bin/env python3
"""Generate the docs' CLI screenshots (SVG) and animations (GIF).

Static command output is rendered with `freeze` (SVG); interactive TUI and
montage clips with `vhs` (GIF). Everything runs against the bundled, read-only
`ioniq-2017` profile with NO device attached, so the assets are reproducible on
any machine and contain no owner-specific data. The manifest is
`docs/screenshots/shots.yaml`.

Usage:
    python3 scripts/gen_screenshots.py                  # regenerate all assets
    python3 scripts/gen_screenshots.py --only bus ecu   # regenerate a subset
    python3 scripts/gen_screenshots.py --check           # CI: verify assets + commands (no render)

`--check` is intentionally light and needs neither `freeze` nor `vhs`: image
output is not byte-reproducible, so it never diffs pixels. Instead it verifies
(a) every manifest asset exists on disk, (b) there are no orphan asset files,
and (c) every declared command still runs device-free against the bundled
profile — so a renamed command or dropped flag fails the check and prompts a
regenerate. Rendering (freeze/vhs) is a local, on-demand step.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOTS_DIR = REPO_ROOT / "docs" / "screenshots"
MANIFEST = SHOTS_DIR / "shots.yaml"


def _isolated_env(cols: int | None = None) -> dict[str, str]:
    """Environment that makes canair output reproducible and device-free.

    Neutralizes any local user config (so a `--wican`/`default_profile` in the
    author's config can't leak in), silences the background update check, and
    forces colour + a fixed width so the rendered output is stable.
    """
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="canair-shots-")
    env["CANAIR_NO_UPDATE_CHECK"] = "1"
    env["FORCE_COLOR"] = "1"
    env.pop("CANAIR_PROFILE", None)
    env.pop("CANAIR_PROFILES_DIR", None)
    if cols is not None:
        env["COLUMNS"] = str(cols)
    return env


def _load_manifest() -> dict:
    with MANIFEST.open() as fh:
        return yaml.safe_load(fh)


def _all_entries(manifest: dict) -> list[dict]:
    """Every shot/animation entry, in manifest order."""
    return list(manifest.get("shots", [])) + list(manifest.get("animations", []))


def _asset_path(entry: dict) -> Path:
    ext = "gif" if entry["kind"] == "anim" else "svg"
    return SHOTS_DIR / f"{entry['id']}.{ext}"


def _rel(path: Path) -> str:
    """Repo-relative display path, tolerant of paths outside the repo (tests)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _checks_for(entry: dict) -> list[list[str]]:
    """The device-free argv list(s) that validate this entry's command(s)."""
    if entry["kind"] == "rich":
        return [list(entry["command"])]
    # Animations declare their exercised commands explicitly (the interactive
    # tape itself can't be validated non-interactively).
    if "checks" in entry:
        return [list(c) for c in entry["checks"]]
    return []


# ── Rendering (freeze / vhs) ────────────────────────────────────────────────


def _base_argv(manifest: dict) -> list[str]:
    """Global flags every invocation shares.

    A *relative* ``--profiles-dir`` keeps the absolute repo path (which contains
    the author's username) out of any ``source:`` line the commands print, so no
    identifying path is baked into the committed images.
    """
    return [
        "--profiles-dir",
        manifest.get("profiles_dir", "profiles"),
        "--profile",
        manifest["profile"],
    ]


def _render_rich(entry: dict, manifest: dict, freeze_config: Path) -> None:
    out = _asset_path(entry)
    command = " ".join(["uv", "run", "canair", *_base_argv(manifest), *entry["command"]])
    subprocess.run(
        ["freeze", "-c", str(freeze_config), "-o", str(out), "--execute", command],
        cwd=REPO_ROOT,
        env=_isolated_env(cols=entry.get("cols")),
        check=True,
    )
    print(f"  rendered {out.relative_to(REPO_ROOT)}")


def _render_anim(entry: dict) -> None:
    tape = SHOTS_DIR / entry["tape"]
    subprocess.run(
        ["vhs", str(tape)],
        cwd=REPO_ROOT,
        env=_isolated_env(),
        check=True,
    )
    print(f"  rendered {_asset_path(entry).relative_to(REPO_ROOT)}")


def generate(manifest: dict, only: set[str] | None) -> int:
    for tool, kinds in (("freeze", {"rich"}), ("vhs", {"anim"})):
        needed = any(
            e["kind"] in kinds for e in _all_entries(manifest) if not only or e["id"] in only
        )
        if needed and shutil.which(tool) is None:
            print(
                f"error: `{tool}` is required to render these assets but is not installed.\n"
                "Install the Charmbracelet tools:  brew install charmbracelet/tap/freeze vhs",
                file=sys.stderr,
            )
            return 1

    freeze_config = SHOTS_DIR / manifest["freeze_config"]
    rendered = 0
    for entry in _all_entries(manifest):
        if only and entry["id"] not in only:
            continue
        print(f"• {entry['id']} ({entry['kind']})")
        if entry["kind"] == "rich":
            _render_rich(entry, manifest, freeze_config)
        else:
            _render_anim(entry)
        rendered += 1

    if only:
        missing = only - {e["id"] for e in _all_entries(manifest)}
        if missing:
            print(f"error: unknown shot id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    print(f"\nRendered {rendered} asset(s) to {SHOTS_DIR.relative_to(REPO_ROOT)}.")
    return 0


# ── Currency check (no render, no heavy binaries) ───────────────────────────


def _run_command(base_argv: list[str], argv: list[str]) -> int:
    """Run one canair command device-free, non-interactively; return exit code."""
    proc = subprocess.run(
        [sys.executable, "-m", "canlib.cli", *base_argv, *argv],
        cwd=REPO_ROOT,
        env=_isolated_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc.returncode


def check(manifest: dict) -> int:
    entries = _all_entries(manifest)
    base_argv = _base_argv(manifest)
    problems: list[str] = []

    # (a) every declared asset exists.
    expected: set[Path] = set()
    for entry in entries:
        path = _asset_path(entry)
        expected.add(path)
        if not path.exists():
            problems.append(f"missing asset: {_rel(path)} (shot '{entry['id']}')")

    # (b) no orphan assets (an image with no manifest entry).
    for path in sorted([*SHOTS_DIR.glob("*.svg"), *SHOTS_DIR.glob("*.gif")]):
        if path not in expected:
            problems.append(f"orphan asset (not in manifest): {_rel(path)}")

    # (c) every declared command still runs device-free.
    for entry in entries:
        for argv in _checks_for(entry):
            rc = _run_command(base_argv, argv)
            if rc != 0:
                problems.append(
                    f"command failed (exit {rc}) for shot '{entry['id']}': canair {' '.join(argv)}"
                )

    if problems:
        print("Screenshots are out of date / stale:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nRegenerate with `python3 scripts/gen_screenshots.py` (needs `freeze` + `vhs`).",
            file=sys.stderr,
        )
        return 1

    print(f"Screenshots are up to date ({len(entries)} asset(s) present, all commands run).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="Verify assets are present and commands still run; do not render (CI).",
    )
    ap.add_argument(
        "--only",
        nargs="+",
        metavar="ID",
        help="Regenerate only the named shot id(s).",
    )
    args = ap.parse_args(argv)

    manifest = _load_manifest()
    if args.check:
        return check(manifest)
    return generate(manifest, set(args.only) if args.only else None)


if __name__ == "__main__":
    sys.exit(main())
