#!/usr/bin/env python3
"""Regenerate the committed WiCAN AutoPID JSON of every bundled profile.

Runs ``canair wican autopid write`` once per profile under ``profiles/`` and, on a profile
with several variants, once per variant, so each car's ``out/`` files match its current
``ecus/`` definitions: the verified-only ``autopid-v<N>.json`` and, with
``--include-unverified``, ``autopid-v<N>-unverified.json``. The release workflow runs it on
the release PR's branch; run it by hand whenever you want ``out/`` current between releases.

Usage:
    uv run python scripts/gen_autopid.py                    # every bundled profile
    uv run python scripts/gen_autopid.py hyundai-kona-os-ev # only the named profile(s)
    uv run python scripts/gen_autopid.py --profiles-dir DIR # profiles in DIR instead

Each run gets an empty user config, so a local default profile, revision pin or remembered
revision detection cannot change what is generated: the output is identical on every machine.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "profiles"


def _variants(profile_dir: Path) -> list[str]:
    """The profile's variant IDs, or ``[]`` when it declares at most one."""
    from canlib.profile import resolve_profile
    from canlib.variants import load_variants

    variants = list(load_variants(resolve_profile(str(profile_dir))))
    return variants if len(variants) > 1 else []


def _isolated_env(config_home: str) -> dict[str, str]:
    env = dict(os.environ, XDG_CONFIG_HOME=config_home, CANAIR_NO_UPDATE_CHECK="1")
    for var in ("CANAIR_PROFILE", "CANAIR_PROFILES_DIR", "CANAIR_VARIANT"):
        env.pop(var, None)
    return env


def _write(
    profile_dir: Path,
    variant: str | None,
    unverified: bool,
    firmware: str | None,
    env: dict[str, str],
) -> bool:
    """Write one build; ``False`` only when a verified-only build fails.

    An unverified build may legitimately not fit (a candidate's name over firmware v6's
    limit, say), and that must not hold back the verified files every release ships, so its
    failure is reported as a warning instead.
    """
    cmd = [sys.executable, "-m", "canlib.cli", "--profile", str(profile_dir)]
    if variant:
        cmd += ["--variant", variant]
    cmd += ["wican", "autopid", "write"]
    if unverified:
        cmd.append("--include-unverified")
    if firmware:
        cmd += ["--firmware", firmware]
    label = profile_dir.name + (f" ({variant})" if variant else "")
    label += ", with unverified" if unverified else ", verified only"
    if firmware:
        label += f", v{firmware}"
    result = subprocess.run(cmd, env=env, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✓ {label}")
        return True
    errors = [line for line in result.stderr.splitlines() if line.strip()]
    if not unverified:
        print(f"✗ {label}\n{result.stdout}{result.stderr}", file=sys.stderr)
        return False
    print(f"! {label}: skipped what does not fit\n" + "\n".join(errors), file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS"):
        detail = "%0A".join(errors)  # the annotation's own newline escape
        print(f"::warning title=Unverified AutoPID build skipped::{label}%0A{detail}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("profiles", nargs="*", help="profile names (default: every bundled profile)")
    ap.add_argument(
        "--profiles-dir", type=Path, default=PROFILES_DIR, help="default: the repo's profiles/"
    )
    args = ap.parse_args(argv)

    root = args.profiles_dir.resolve()
    dirs = (
        [root / name for name in args.profiles]
        if args.profiles
        else sorted(p.parent for p in root.glob("*/profile.yaml"))
    )
    missing = [d.name for d in dirs if not (d / "profile.yaml").is_file()]
    if missing:
        print(f"error: no such profile in {root}: {', '.join(missing)}", file=sys.stderr)
        return 2

    ok = True
    with tempfile.TemporaryDirectory(prefix="canair-genautopid-") as config_home:
        env = _isolated_env(config_home)
        for profile_dir in dirs:
            for variant in _variants(profile_dir) or [None]:
                for unverified in (False, True):
                    # Per firmware for the unverified build, so a v6 table that does not
                    # fit still leaves its v4 file written.
                    for firmware in (None,) if not unverified else ("4", "6"):
                        ok &= _write(profile_dir, variant, unverified, firmware, env)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
