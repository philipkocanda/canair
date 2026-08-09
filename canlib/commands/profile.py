"""``canair profile`` — inspect and manage vehicle profiles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from canlib.config import load_config, user_config_file
from canlib.profile import (
    ProfileError,
    active,
    config_dir_hint,
    discover_profiles,
    extends_target,
    profile_layers,
    resolve_profile,
)
from canlib.profile_create import DEFAULT_INIT, adopt_profile, create_profile, overlay_profile

NAME = "profile"
ALIASES = ["prof"]


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="List, inspect, and create vehicle profiles",
        description="List, inspect, and create vehicle profiles — the per-vehicle\n"
        "bundles that hold all the reverse-engineering data. `show` lists every\n"
        "member of a bundle (definitions, captures, references, generated output).\n\n"
        "Subcommands:\n"
        "  list            list every discovered profile (bundled + user)\n"
        "  show [NAME]     details of a profile (ECU/PID counts, paths); default active\n"
        "  path [NAME]     print a profile's root directory (handy for scripting)\n"
        "  use NAME        set NAME as the default profile (default_profile in config)\n"
        "  create NAME     scaffold a new empty profile bundle\n"
        "  adopt NAME      copy a read-only profile to ~/.config/canair/profiles/ to write to it\n"
        "  overlay NAME    record your own captures over a read-only profile's definitions\n\n"
        "A bare `canair profile` opens an interactive arrow-key picker on a TTY (choose\n"
        "the default profile); piped/non-interactive it prints the list. Select the\n"
        "active profile with the global --profile flag, CANAIR_PROFILE, or\n"
        "default_profile in config (set the last with `canair profile use NAME`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair profile                              # interactive picker (TTY) / list (piped)
  canair profile show                         # details of the active profile
  canair profile show ioniq-2017              # details of a named profile
  canair profile path                         # print the active profile's directory
  canair profile use ioniq-2017               # set the default profile
  canair profile create ev6 --car-model "Kia EV6 2022"
  canair profile create ev6 --car-model "Kia EV6 2022" --set-default
  canair profile adopt ioniq-2017              # writable copy under ~/.config/canair
  canair profile overlay ioniq-2017            # keep its definitions, record your own captures
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

    use = sub.add_parser(
        "use",
        help="Set the default profile (default_profile in the user config)",
        description="Set NAME as the default profile — an alias for "
        "`canair config set default_profile NAME`. The name must be a discovered "
        "profile.",
    )
    use.add_argument("name", help="Profile name to set as default")
    use.set_defaults(_profile_func=_cmd_use)

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

    adopt = sub.add_parser(
        "adopt",
        help="Copy a profile to ~/.config/canair/profiles/ so you can write to it",
        description="Copy a discovered profile into ~/.config/canair/profiles/<name>, "
        "where it shadows the original by name and is writable. Use it when the "
        "profile you are reading is read-only or lives in an install snapshot "
        "(site-packages), whose contents a reinstall replaces. Generated out/ is "
        "not copied — regenerate it with `canair wican autopid write`.",
        epilog="Working on a repo-bundled profile you intend to contribute back? "
        "Point canair at the checkout instead — `canair config set profiles_dir "
        "<clone>/profiles` — so your edits stay in git.",
    )
    adopt.add_argument("name", help="Name of the discovered profile to copy")
    adopt.add_argument(
        "--set-default",
        action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    adopt.add_argument(
        "--force", action="store_true", help="Overwrite an existing user copy of this profile"
    )
    adopt.set_defaults(_profile_func=_cmd_adopt)

    ovl = sub.add_parser(
        "overlay",
        help="Record into your own captures/ while definitions stay with the base profile",
        description="Create a capture layer over a discovered profile: a bundle at "
        "~/.config/canair/profiles/<name>/ holding an `extends:` marker and an empty "
        "captures/. Nothing is copied, so the base profile's definitions keep "
        "resolving (and keep tracking upstream) while every capture you record lands "
        "in your layer. Analysis reads both layers; the base layer's captures are "
        "read-only. Use `adopt` instead when you need to edit definitions too.",
    )
    ovl.add_argument("name", help="Name of the discovered profile to layer onto")
    ovl.add_argument(
        "--set-default",
        action="store_true",
        help="Set this profile as default_profile in the user config",
    )
    ovl.set_defaults(_profile_func=_cmd_overlay)

    parser.set_defaults(func=run, _profile_func=_cmd_default)
    return parser


def _resolve(name: str | None):
    return resolve_profile(name) if name else active()


def _cmd_default(args) -> int:
    """Bare ``canair profile``: an interactive picker on a TTY, else the list.

    On an interactive terminal, launch an arrow-key selector over the discovered
    profiles; choosing one sets it as ``default_profile`` (like ``profile use``).
    When stdin/stdout aren't a TTY (piped, scripted) fall back to the plain list
    so the command stays composable and non-interactive.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _cmd_pick(args)
    return _cmd_list(args)


def _cmd_pick(args) -> int:
    """Interactive arrow-key profile picker → set default_profile."""
    from canlib.config import set_config_value
    from canlib.tui import select_from_list

    profiles = discover_profiles(getattr(args, "profiles_dir", None))
    if not profiles:
        return _cmd_list(args)  # prints the "no profiles" guidance

    try:
        active_name = active().name
    except ProfileError:
        active_name = None

    items = list(profiles.items())
    initial = next((i for i, (n, _) in enumerate(items) if n == active_name), 0)

    def _render(item: tuple[str, Path]) -> str:
        name, root = item
        current = "  [green](current default)[/green]" if name == active_name else ""
        return f"[bold]{name}[/bold]{current}\n     [dim]{root}[/dim]"

    chosen = select_from_list(
        items,
        title="Select the default vehicle profile",
        render=_render,
        initial=initial,
        footer="↑/↓ move · enter set as default · q/esc cancel",
    )
    if chosen is None:
        print("Cancelled — no change.")
        return 0

    name, root = chosen
    if name == active_name:
        print(f"'{name}' is already the default profile.")
        return 0
    path = set_config_value("default_profile", name)
    print(f"default_profile = {name}")
    print(f"  ({root})")
    print(f"Saved to {path}")
    return 0


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
        if extends_target(root):
            for base in profile_layers(name, getattr(args, "profiles_dir", None))[1:]:
                print(f"    \tlayered over {base}")

    if not load_config().get("default_profile"):
        print()
        if len(profiles) == 1:
            print("No default_profile set (the single discovered profile is used).")
        else:
            print("No default_profile set.")
        print("Set one with `canair profile use <name>`.")

    _warn_if_bundled_snapshot()
    return 0


def _warn_if_bundled_snapshot() -> None:
    """Warn contributors that a bare `canair` reads a frozen copy of the bundled
    profiles, so edits to the git checkout's `profiles/` aren't picked up."""
    from canlib.install_context import bundled_profiles_are_snapshot

    if bundled_profiles_are_snapshot():
        print()
        print(
            "note: running the `uv tool install` copy — its bundled profiles are a\n"
            "      frozen snapshot, so edits to the repo's profiles/ won't appear here.\n"
            "      Run `uv run canair` from the repo root (or reinstall) to pick them up."
        )


def _detail_can_buses(prof) -> str:
    from canlib.can_buses import load_can_buses

    buses = load_can_buses(prof)
    codes = ", ".join(b.code for b in buses) if buses else "empty"
    return f"{len(buses)} buses: {codes}"


def _detail_states(prof) -> str:
    from canlib.states import StatePredicateError, load_states

    try:
        rules = load_states(prof)
    except StatePredicateError as ex:
        return f"INVALID: {ex}"
    names = ", ".join(f"{r.name}*" if r.predicate else r.name for r in rules)
    # A second line is printed as an indented continuation (see _cmd_show).
    return f"{len(rules)} states: {names}\n(* = has an auto-suggest predicate)"


def _detail_groups(prof) -> str:
    from canlib.ecu_groups import GroupError, load_groups

    try:
        groups = load_groups(prof)
    except GroupError as ex:
        return f"INVALID: {ex}"
    names = ", ".join(f"@{g}" for g in groups) if groups else "empty"
    return f"{len(groups)} groups: {names}"


def _detail_signals(prof) -> str:
    from canlib.signals import load_signals

    names = sorted(d.path.name for d in load_signals(profile=prof))
    return f"{len(names)} files: {', '.join(names) if names else 'empty'}"


def _detail_references(prof) -> str:
    return f"{sum(1 for p in prof.references_dir.rglob('*') if p.is_file())} files"


# Richer "what's in it" line per member, where a bare ok/missing isn't useful.
# A member with no entry here still gets listed (see _cmd_show) — the registry
# decides *what* is shown, this only decides how much detail.
_MEMBER_DETAIL = {
    "can_buses.yaml": _detail_can_buses,
    "vehicle_states.yaml": _detail_states,
    "groups.yaml": _detail_groups,
    "signals": _detail_signals,
    "references": _detail_references,
}


def _cmd_show(args) -> int:
    from canlib.profile import BUNDLE_MEMBERS

    prof = _resolve(args.name)
    meta = prof.meta
    print(f"name:       {prof.name}")
    print(f"root:       {prof.root}")
    for overlay in prof.overlays:
        print(f"overlay:    {overlay}  (your layer — captures land here)")
    print(f"car_model:  {meta.get('car_model', '?')}")
    print(f"init:       {meta.get('init', '?')}")

    for member in BUNDLE_MEMBERS:
        path = prof.member_path(member)
        if not prof.member_exists(member):
            detail = "MISSING" if member.required else "none — optional"
        elif member.name in _MEMBER_DETAIL:
            detail = _MEMBER_DETAIL[member.name](prof)
        else:
            detail = "ok"
        head, _, rest = detail.partition("\n")
        print(f"{member.display_label + ':':<12}{path}  ({head})")
        for line in rest.splitlines():
            print(f"{'':<12}{line}")
        # The raw-CAN frame-log store lives inside captures/.
        if member.name == "captures":
            for layer in prof.capture_layers[:-1]:
                print(f"{'':<12}{layer}  (base layer, read-only)")
            if prof.can_dir.is_dir():
                idx = "index ok" if prof.can_index_file.exists() else "no index"
            else:
                idx = "none — optional"
            print(f"{'can logs:':<12}{prof.can_dir}  ({idx})")

    return 0


def _cmd_path(args) -> int:
    print(_resolve(args.name).root)
    return 0


def _cmd_use(args) -> int:
    from canlib.config import set_config_value

    name = args.name.strip()
    profiles = discover_profiles(getattr(args, "profiles_dir", None))
    if name not in profiles:
        avail = ", ".join(profiles) or "none"
        print(f"error: profile '{name}' not found. Available: {avail}.", file=sys.stderr)
        return 1
    path = set_config_value("default_profile", name)
    print(f"default_profile = {name}")
    print(f"Saved to {path}")
    return 0


def _cmd_create(args) -> int:
    name = args.name.strip()
    if not name:
        print("error: profile name cannot be empty", file=sys.stderr)
        return 2

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

    try:
        root = create_profile(
            name,
            car_model=car_model,
            init=args.init,
            path=args.path,
            set_default=args.set_default,
            force=args.force,
        )
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    init = args.init or DEFAULT_INIT
    if args.set_default:
        default_note = f"\nSet as default_profile in {user_config_file()}."
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


def _cmd_adopt(args) -> int:
    name = args.name.strip()
    try:
        source, dest = adopt_profile(
            name,
            profiles_dir=getattr(args, "profiles_dir", None),
            set_default=args.set_default,
            force=args.force,
        )
    except LookupError:
        profiles = discover_profiles(getattr(args, "profiles_dir", None))
        avail = ", ".join(profiles) or "none"
        print(f"error: profile '{name}' not found. Available: {avail}.", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"error: {e}. Overwrite it with --force.", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"Adopted '{name}'")
    print(f"  from: {source}")
    print(f"  to:   {dest}")
    print(f"\n'{name}' now resolves to your copy, which writes survive a reinstall.")
    print("The copy no longer tracks upstream, so `canair contribute` may report a")
    print("rollback once upstream moves on — for contribution work point canair at a")
    print("checkout instead: `canair config set profiles_dir <clone>/profiles`.")
    if args.set_default:
        print(f"\nSet as default_profile in {user_config_file()}.")
    return 0


def _cmd_overlay(args) -> int:
    name = args.name.strip()
    try:
        base, dest = overlay_profile(
            name,
            profiles_dir=getattr(args, "profiles_dir", None),
            set_default=args.set_default,
        )
    except LookupError:
        profiles = discover_profiles(getattr(args, "profiles_dir", None))
        avail = ", ".join(profiles) or "none"
        print(f"error: profile '{name}' not found. Available: {avail}.", file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(f"error: {e}.", file=sys.stderr)
        print(
            "A layer is only ever created once. Remove that directory, or use it as-is.",
            file=sys.stderr,
        )
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"Layered '{name}'")
    print(f"  base:    {base}  (definitions, read-only captures)")
    print(f"  overlay: {dest}  (everything you record)")
    print(f"\n`--profile {name}` now reads both layers and records into yours.")
    print("Definitions still belong to the base, so editing them is refused — run")
    print(f"`canair profile adopt {name}` if you need to change PIDs or signals too.")
    if args.set_default:
        print(f"\nSet as default_profile in {user_config_file()}.")
    return 0


def run(args) -> int:
    return args._profile_func(args)
