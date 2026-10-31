#!/usr/bin/env python3
"""Regenerate the committed WiCAN AutoPID JSON of every bundled profile.

Runs ``canair wican autopid write`` once per profile under ``profiles/`` and, on a profile
with several variants, once per variant, so each car's ``out/`` files match its current
``ecus/`` definitions. The release workflow runs it on the release PR's branch; run it by
hand whenever you want ``out/`` current between releases.

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


def _write(profile_dir: Path, variant: str | None, env: dict[str, str]) -> bool:
    cmd = [sys.executable, "-m", "canlib.cli", "--profile", str(profile_dir)]
    if variant:
        cmd += ["--variant", variant]
    cmd += ["wican", "autopid", "write"]
    label = profile_dir.name + (f" ({variant})" if variant else "")
    result = subprocess.run(cmd, env=env, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"✗ {label}\n{result.stdout}{result.stderr}", file=sys.stderr)
        return False
    print(f"✓ {label}")
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
                ok &= _write(profile_dir, variant, env)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
