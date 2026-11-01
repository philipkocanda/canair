"""``canair profile`` — inspect and manage vehicle profiles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from canlib.config import load_config, user_config_file
from canlib.profile import (
    ProfileError,
    active,
    alias_index,
    config_dir_hint,
    profile_layers,
    resolve_profile,
    selectable_profiles,
)
from canlib.profile_create import DEFAULT_INIT, adopt_profile, create_profile, overlay_profile
from canlib.profile_names import convention_problem

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
        "the default profile, then your car when it has several variants — re-select the\n"
        "current profile to change only the variant); piped it prints the list. Select the\n"
        "active profile with the global --profile flag, CANAIR_PROFILE, or\n"
        "default_profile in config (set the last with `canair profile use NAME`).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair profile                              # interactive picker (TTY) / list (piped)
  canair profile show                         # details of the active profile
  canair profile show hyundai-ioniq-ae-ev              # details of a named profile
  canair profile path                         # print the active profile's directory
  canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # set the default profile + your car
  canair profile create kia-ev6-cv-ev --car-model "Kia EV6 (CV)"
  canair profile create kia-ev6-cv-ev --car-model "Kia EV6 (CV)" --set-default
  canair profile adopt hyundai-ioniq-ae-ev              # writable copy under ~/.config/canair
  canair profile overlay hyundai-ioniq-ae-ev            # keep its definitions, record your own captures
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
        help="Set the default profile, and which of its variants your car is",
        description="Set NAME as the default profile (default_profile in the user config) "
        "and, with --variant, which of its variants your car is "
        "(profile_settings.NAME.variant, remembered per profile), and with --pin, which "
        "revision an ECU of it is. On a terminal, a profile "
        "with several variants and no --variant opens a picker. The name must be a "
        "discovered profile; an old, renamed name is accepted and saved as the current one.",
    )
    use.add_argument("name", help="Profile name to set as default")
    use.add_argument(
        "--variant",
        dest="use_variant",
        metavar="ID",
        help="The variant your car is (a variants.yaml ID), remembered for this profile",
    )
    use.add_argument(
        "--pin",
        dest="pins",
        action="append",
        metavar="ECU=ID",
        help="Which revision an ECU of your car is (one of its revisions: IDs), remembered "
        "for this profile (profile_settings.NAME.revisions). Repeat for several ECUs. For "
        "when canair cannot tell on its own; overridden for one command by --revision",
    )
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


def _current_name(name: str, profiles_dir) -> str:
    """``name``, or the current name of the profile it is an old name of."""
    index = alias_index(profiles_dir)
    profiles = selectable_profiles(profiles_dir, index)
    target = index.target(name)
    if name not in profiles and target in profiles:
        print(f"note: '{name}' was renamed; using its current name, '{target}'.")
        return target
    return name


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

    profiles = selectable_profiles(getattr(args, "profiles_dir", None))
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
    path = set_config_value("default_profile", name)
    if name == active_name:
        print(f"'{name}' is already the default profile.")
    else:
        print(f"default_profile = {name}")
    print(f"  ({root})")
    variant = _choose_variant(name, getattr(args, "profiles_dir", None), None)
    if variant:
        from canlib.config import set_config_key

        set_config_key(f"profile_settings.{name}.variant", variant)
        print(f"profile_settings.{name}.variant = {variant}")
    print(f"Saved to {path}")
    return 0


def _cmd_list(args) -> int:
    profiles_dir = getattr(args, "profiles_dir", None)
    index = alias_index(profiles_dir)
    profiles = selectable_profiles(profiles_dir, index)
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
        old = index.aliases_of(name)
        layers = profile_layers(name, profiles_dir, old)
        if len(layers) > 1:
            for layer in layers[:-1]:
                if layer != root:
                    print(f"    \tcaptures also from {layer}")
            print(f"    \tlayered over {layers[-1]}")
        successor = index.target(name)
        if successor and successor in profiles:
            print(f"    \t(an old profile name: the bundled successor is {successor})")
    renamed = {a: t for a, t in index.targets.items() if t in profiles and a not in profiles}
    if renamed:
        print()
        print("Renamed (old names still resolve):")
        for alias, target in sorted(renamed.items()):
            print(f"  {alias} → {target}")

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
    from canlib.ecus.ecu_groups import GroupError, load_groups

    try:
        groups = load_groups(prof)
    except GroupError as ex:
        return f"INVALID: {ex}"
    names = ", ".join(f"@{g}" for g in groups) if groups else "empty"
    return f"{len(groups)} groups: {names}"


def _detail_actions(prof) -> str:
    from canlib.actions import ActionError, load_actions

    try:
        actions = load_actions(prof)
    except ActionError as ex:
        return f"INVALID: {ex}"
    return f"{len(actions)} actions: {', '.join(actions) if actions else 'empty'}"


def _detail_signals(prof) -> str:
    from canlib.signals import load_signals

    names = sorted(d.path.name for d in load_signals(profile=prof))
    return f"{len(names)} files: {', '.join(names) if names else 'empty'}"


def _detail_variants(prof) -> str:
    from canlib.variants import VariantsError, describe, load_variants

    try:
        variants = load_variants(prof)
    except VariantsError as ex:
        return f"INVALID: {ex}"
    if not variants:
        return "empty"
    # Each further line is printed as an indented continuation (see _cmd_show).
    return "\n".join(
        [f"{len(variants)} variants", *(f"  {vid}: {describe(v)}" for vid, v in variants.items())]
    )


def _detail_references(prof) -> str:
    return f"{sum(1 for p in prof.references_dir.rglob('*') if p.is_file())} files"


# Richer "what's in it" line per member, where a bare ok/missing isn't useful.
# A member with no entry here still gets listed (see _cmd_show) — the registry
# decides *what* is shown, this only decides how much detail.
_MEMBER_DETAIL = {
    "can_buses.yaml": _detail_can_buses,
    "vehicle_states.yaml": _detail_states,
    "groups.yaml": _detail_groups,
    "actions.yaml": _detail_actions,
    "variants.yaml": _detail_variants,
    "signals": _detail_signals,
    "references": _detail_references,
}


# How `profile show` names where the selected variant came from.
_VARIANT_SOURCE = {
    "flag": "--variant",
    "env": "CANAIR_VARIANT",
    "config": "profile_settings in the config",
    "alias": "the old profile name",
    "only": "the profile's only variant",
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
    from canlib.profile_names import also_known_as

    if known_as := also_known_as(prof.root).all():
        print(f"known as:   {', '.join(known_as)}")
    if prof.variant:
        print(
            f"variant:    {prof.variant}  (from {_VARIANT_SOURCE[prof.variant_source or 'only']})"
        )
    from canlib.pids import revision_resolutions
    from canlib.variants.revisions import describe_resolution

    for name, res in revision_resolutions(prof).items():
        print(f"revision:   {name} {describe_resolution(res)}")
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

    from canlib.dtc.log import load_log, log_file

    dtc_log = log_file(prof)
    scans = len(load_log(dtc_log)["scans"]) if dtc_log.exists() else 0
    detail = f"{scans} scan(s) — user state" if scans else "none — user state"
    print(f"{'dtc log:':<12}{dtc_log}  ({detail})")
    return 0


def _cmd_path(args) -> int:
    print(_resolve(args.name).root)
    return 0


def _cmd_use(args) -> int:
    from canlib.config import set_config_value

    profiles_dir = getattr(args, "profiles_dir", None)
    name = _current_name(args.name.strip(), profiles_dir)
    profiles = selectable_profiles(profiles_dir)
    if name not in profiles:
        avail = ", ".join(profiles) or "none"
        print(f"error: profile '{name}' not found. Available: {avail}.", file=sys.stderr)
        return 1
    variant = _choose_variant(name, profiles_dir, getattr(args, "use_variant", None))
    if variant == "":
        return 2
    # Checked before anything is written, so a bad pin leaves the config as it was.
    pins = _checked_pins(name, profiles_dir, variant, getattr(args, "pins", None) or [])
    if pins is None:
        return 2
    from canlib.config import set_config_key

    path = set_config_value("default_profile", name)
    print(f"default_profile = {name}")
    if variant:
        set_config_key(f"profile_settings.{name}.variant", variant)
        print(f"profile_settings.{name}.variant = {variant}")
    for ecu, rid in pins:
        set_config_key(f"profile_settings.{name}.revisions.{ecu}", rid)
        print(f"profile_settings.{name}.revisions.{ecu} = {rid}")
    print(f"Saved to {path}")
    return 0


def _checked_pins(
    name: str, profiles_dir, variant: str | None, texts: list[str]
) -> list[tuple[str, str]] | None:
    """``--pin ECU=ID`` values checked against the profile, or None after reporting one.

    Each must name an ECU that declares the revision, and one the chosen car (the variant
    being set, else the one remembered) can carry.
    """
    if not texts:
        return []
    from canlib.pids import load_raw_pids
    from canlib.variants import VariantsError, load_variants
    from canlib.variants.revisions import RevisionError, canonical_ecu, parse_pin, resolve
    from canlib.variants.selection import configured_variant

    try:
        prof = resolve_profile(name, profiles_dir)
        ecus = load_raw_pids(prof).get("ecus") or {}
        vid = variant or configured_variant(prof)
        car = load_variants(prof).get(vid) if vid else None
        pins = [parse_pin(t) for t in texts]
        resolve(ecus, car, [(e, r, "flag") for e, r in pins])
    except (ProfileError, VariantsError, RevisionError) as e:
        print(f"error: --pin: {e}", file=sys.stderr)
        return None
    return [(canonical_ecu(e, ecus) or e, r) for e, r in pins]


def _choose_variant(name: str, profiles_dir, requested: str | None) -> str | None:
    """The variant to remember for ``name``: the one requested, else picked on a TTY.

    Returns None when there is nothing to choose (or the choice was skipped), and ""
    after reporting an invalid ``requested`` variant.
    """
    from canlib.variants import VariantsError, describe, load_variants, normalize_variant_id
    from canlib.variants.selection import configured_variant

    try:
        prof = resolve_profile(name, profiles_dir)
        variants = load_variants(prof)
        remembered = configured_variant(prof)
    except (ProfileError, VariantsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return ""
    if requested:
        vid = normalize_variant_id(requested)
        if vid not in variants:
            known = ", ".join(variants) or "none (no variants.yaml)"
            print(f"error: '{name}' has no variant {vid!r}. Declared: {known}.", file=sys.stderr)
            return ""
        return vid
    if len(variants) < 2:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            f"note: '{name}' describes {len(variants)} variants; name yours with "
            f"`canair profile use {name} --variant ID` ({', '.join(variants)})."
        )
        return None
    from canlib.tui import select_from_list

    items = list(variants.items())

    def _render(item) -> str:
        current = "  [green](current)[/green]" if item[0] == remembered else ""
        return f"[bold]{item[0]}[/bold]{current}\n     [dim]{describe(item[1])}[/dim]"

    chosen = select_from_list(
        items,
        title=f"Which {name} is your car?",
        render=_render,
        initial=next((i for i, (vid, _) in enumerate(items) if vid == remembered), 0),
        footer="↑/↓ move · enter choose · q/esc skip",
    )
    return chosen[0] if chosen else None


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
    problem = convention_problem(name)
    if problem:
        print(
            f"\nnote: {problem}. Bundled profiles must follow it; rename before contributing "
            "(see docs/concepts/profiles.md#naming-a-profile)."
        )
    return 0


def _cmd_adopt(args) -> int:
    name = _current_name(args.name.strip(), getattr(args, "profiles_dir", None))
    try:
        source, dest = adopt_profile(
            name,
            profiles_dir=getattr(args, "profiles_dir", None),
            set_default=args.set_default,
            force=args.force,
        )
    except LookupError:
        profiles = selectable_profiles(getattr(args, "profiles_dir", None))
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
    name = _current_name(args.name.strip(), getattr(args, "profiles_dir", None))
    try:
        base, dest = overlay_profile(
            name,
            profiles_dir=getattr(args, "profiles_dir", None),
            set_default=args.set_default,
        )
    except LookupError:
        profiles = selectable_profiles(getattr(args, "profiles_dir", None))
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
