"""``canair profile`` — inspect and manage vehicle profiles."""

from __future__ import annotations

import argparse

from canlib.profile import (
    ProfileError,
    active,
    config_dir_hint,
    discover_profiles,
    resolve_profile,
)

NAME = "profile"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect and manage vehicle profiles",
        description="List available vehicle profiles and show the active one.",
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

    parser.set_defaults(func=run, _profile_func=_cmd_list)
    return parser


def _resolve(name: str | None):
    return resolve_profile(name) if name else active()


def _cmd_list(args) -> int:
    profiles = discover_profiles(getattr(args, "profiles_dir", None))
    if not profiles:
        print("No profiles found.")
        print(f"Add one under {config_dir_hint()} or the repo's profiles/ directory.")
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


def run(args) -> int:
    return args._profile_func(args)
