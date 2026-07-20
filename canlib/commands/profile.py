"""``canair profile`` — inspect and manage vehicle profiles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from canlib.profile import (
    ProfileError,
    active,
    config_dir_hint,
    discover_profiles,
    resolve_profile,
)

NAME = "profile"

# Default ELM327 init string for a new profile: ISO 15765-4 CAN 11-bit/500 kbit
# (the common modern-vehicle protocol). Editable in pids/_meta.yaml afterwards.
DEFAULT_INIT = "ATSP6;ATS0;ATAL;ATST96;"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect and manage vehicle profiles",
        description="List, inspect, and create vehicle profiles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="profile_command")

    lst = sub.add_parser("list", help="List discovered profiles")
    lst.set_defaults(_profile_func=_cmd_list)

    show = sub.add_parser("show", help="Show details of a profile (default: active)")
    show.add_argument("name", nargs="?", help="Profile name (default: active)")
    show.set_defaults(_profile_func=_cmd_show)

    pth = sub.add_parser("path", help="Print the root directory of a profile")
    pth.add_argument("name", nargs="?", help="Profile name (default: active)")
    pth.set_defaults(_profile_func=_cmd_path)

    crt = sub.add_parser(
        "create",
        aliases=["init", "new"],
        help="Scaffold a new empty profile",
        description="Create a new vehicle profile bundle (pids/, ecus.yaml, captures/, out/).",
    )
    crt.add_argument("name", help="Profile name (used as the directory name)")
    crt.add_argument("--car-model", help="Vehicle description (e.g. 'VW e-Golf 2019')")
    crt.add_argument("--init", help=f"ELM327 init string (default: {DEFAULT_INIT})")
    crt.add_argument(
        "--path",
        type=Path,
        help="Create at this directory instead of ~/.config/canair/profiles/<name>",
    )
    crt.add_argument(
        "--set-default", action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    crt.add_argument("--force", action="store_true", help="Allow a non-empty target directory")
    crt.set_defaults(_profile_func=_cmd_create)

    parser.set_defaults(func=run, _profile_func=_cmd_list)
    return parser


def _resolve(name: str | None):
    return resolve_profile(name) if name else active()


def _cmd_list(args) -> int:
    profiles = discover_profiles(getattr(args, "profiles_dir", None))
    if not profiles:
        print("No profiles found.")
        print("Create one with `canair profile create <name> --car-model \"...\"`,")
        print(f"or add a bundle under {config_dir_hint()}.")
        return 0
    try:
        active_name = active().name
    except ProfileError:
        active_name = None
    for name, root in profiles.items():
        marker = "*" if name == active_name else " "
        print(f"{marker} {name}\t{root}")
    return 0


def _cmd_show(args) -> int:
    prof = _resolve(args.name)
    meta = prof.meta
    print(f"name:       {prof.name}")
    print(f"root:       {prof.root}")
    print(f"car_model:  {meta.get('car_model', '?')}")
    print(f"init:       {meta.get('init', '?')}")
    print(f"pids:       {prof.pids_dir}  ({'ok' if prof.pids_dir.is_dir() else 'MISSING'})")
    print(f"ecus.yaml:  {prof.ecus_file}  ({'ok' if prof.ecus_file.exists() else 'MISSING'})")
    print(f"captures:   {prof.captures_dir}  ({'ok' if prof.captures_dir.is_dir() else 'MISSING'})")
    print(f"out:        {prof.out_dir}")
    return 0


def _cmd_path(args) -> int:
    print(_resolve(args.name).root)
    return 0


_ECUS_TEMPLATE = """\
# {car_model} — ECU address registry
# TX id is the OBD-II request arbitration ID; RX id is TX + 8.
# Populate with `canair discover --register`, `canair identity --write`,
# or by hand. Validate with `canair validate ecus`.
ecus:
"""


def _cmd_create(args) -> int:
    from canlib.config import set_config_value, user_profiles_dir

    name = args.name.strip()
    if not name:
        print("error: profile name cannot be empty", file=sys.stderr)
        return 2

    root = args.path if args.path else user_profiles_dir() / name
    root = Path(root)

    if root.exists() and any(root.iterdir()) and not args.force:
        print(
            f"error: {root} already exists and is not empty (use --force to proceed).",
            file=sys.stderr,
        )
        return 1

    # car_model: flag, else prompt when interactive, else error.
    car_model = args.car_model
    if not car_model:
        if sys.stdin.isatty():
            try:
                car_model = input("Vehicle description (car_model): ").strip()
            except (EOFError, KeyboardInterrupt):
                car_model = ""
        if not car_model:
            print("error: --car-model is required", file=sys.stderr)
            return 2

    init = args.init or DEFAULT_INIT

    # Scaffold the bundle.
    (root / "pids").mkdir(parents=True, exist_ok=True)
    (root / "captures").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)

    meta = root / "pids" / "_meta.yaml"
    meta.write_text(
        f"# {car_model} — PID definitions (created by `canair profile create`)\n"
        f'car_model: "{car_model}"\n'
        f'init: "{init}"\n'
    )
    (root / "ecus.yaml").write_text(_ECUS_TEMPLATE.format(car_model=car_model))

    if args.set_default:
        cfg = set_config_value("default_profile", name)
        default_note = f"\nSet as default_profile in {cfg}."
    else:
        default_note = (
            f"\nSelect it with `canair --profile {name} ...`, "
            "or set `default_profile` in your config."
        )

    print(f"Created profile '{name}' at {root}")
    print(f"  car_model: {car_model}")
    print(f"  init:      {init}")
    print(default_note.lstrip("\n"))
    return 0


def run(args) -> int:
    return args._profile_func(args)
