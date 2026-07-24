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
# (the common modern-vehicle protocol). Editable in profile.yaml afterwards.
DEFAULT_INIT = "ATSP6;ATS0;ATAL;ATST96;"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="List, inspect, and create vehicle profiles",
        description="List, inspect, and create vehicle profiles — the per-vehicle\n"
        "bundles (ecus/, profile.yaml, captures/, states.yaml, out/) that hold all\n"
        "the reverse-engineering data.\n\n"
        "Subcommands:\n"
        "  list            list every discovered profile (bundled + user)\n"
        "  show [NAME]     details of a profile (ECU/PID counts, paths); default active\n"
        "  path [NAME]     print a profile's root directory (handy for scripting)\n"
        "  create NAME     scaffold a new empty profile bundle\n\n"
        "A bare `canair profile` lists profiles. Select the active profile with the\n"
        "global --profile flag, CANAIR_PROFILE, or default_profile in config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair profile                              # list discovered profiles
  canair profile show                         # details of the active profile
  canair profile show ioniq-2017              # details of a named profile
  canair profile path                         # print the active profile's directory
  canair profile create ev6 --car-model "Kia EV6 2022"
  canair profile create ev6 --car-model "Kia EV6 2022" --set-default
""",
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
        description="Create a new vehicle profile bundle (ecus/, profile.yaml, captures/, out/).",
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
        "--set-default",
        action="store_true",
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
        print('Create one with `canair profile create <name> --car-model "..."`,')
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
    print(f"ecus:       {prof.ecus_dir}  ({'ok' if prof.ecus_dir.is_dir() else 'MISSING'})")
    print(
        f"profile:    {prof.root / 'profile.yaml'}  ({'ok' if (prof.root / 'profile.yaml').exists() else 'MISSING'})"
    )
    print(f"captures:   {prof.captures_dir}  ({'ok' if prof.captures_dir.is_dir() else 'MISSING'})")
    print(f"out:        {prof.out_dir}")

    # States (optional): list the declared vocabulary, marking auto-suggest rules.
    from canlib.states import StatePredicateError, load_states

    if prof.states_file.exists():
        try:
            rules = load_states(prof)
            names = ", ".join(f"{r.name}*" if r.predicate else r.name for r in rules)
            print(f"states:     {prof.states_file}  ({len(rules)} states: {names})")
            print("            (* = has an auto-suggest predicate)")
        except StatePredicateError as ex:
            print(f"states:     {prof.states_file}  (INVALID: {ex})")
    else:
        print(f"states:     {prof.states_file}  (none — optional)")
    return 0


def _cmd_path(args) -> int:
    print(_resolve(args.name).root)
    return 0


# Starter states.yaml — the shared base power-state vocabulary. Add `when:`
# predicates over decoded ECU.PARAM values to enable state auto-suggestion.
_STATES_TEMPLATE = """\
# {car_model} — vehicle operating states
#
# Canonical, ordered operating states for capture sessions (`state:` field).
# Add a `when:` predicate over decoded PID values to auto-suggest the state at
# save time (first match wins). See canlib/schema/states_schema.yaml and
# `canair validate states`. Predicate grammar: ECU.PARAM names, and/or/not,
# == != < <= > >=, numeric/'string' literals, and the sentinels
# __no_response__ / __responded__.

states:
  - name: charging
    description: HV battery actively charging (implies plugged).
    # when: "BMS.BATTERY_CURRENT < -1"
  - name: ready
    description: HV active, driveable.
    # when: "VCU.EV_READY == 1"
  - name: deep sleep
    description: No ECU responded (12V standby only).
    when: "__no_response__"
  - name: sleep
    description: Light sleep / 12V standby (unplugged).
  - name: plugged
    description: Charge cable connected, not necessarily charging.
  - name: acc
    description: Accessory power (ACC1).
  - name: acc2
    description: Full ignition, no HV (ACC2/IGN).
  - name: parked
    description: Stationary, gear in Park.
  - name: driving
    description: In motion.
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
    (root / "ecus").mkdir(parents=True, exist_ok=True)
    (root / "captures").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)

    (root / "profile.yaml").write_text(
        f"# {car_model} — vehicle profile settings (created by `canair profile create`)\n"
        f"# Per-ECU definitions live in ecus/. Populate with `canair discover --register`,\n"
        f"# `canair identity --write`, or by hand. Validate with `canair validate`.\n"
        f'car_model: "{car_model}"\n'
        f'init: "{init}"\n'
    )
    (root / "states.yaml").write_text(_STATES_TEMPLATE.format(car_model=car_model))

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
