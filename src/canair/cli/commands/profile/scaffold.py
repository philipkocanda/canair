"""``canair profile create``, ``adopt`` and ``overlay``."""

from __future__ import annotations

import sys

from canair.config.file import user_config_file
from canair.profile.discovery import selectable_profiles
from canair.profile.names import convention_problem
from canair.profile.scaffold import DEFAULT_INIT, adopt_profile, create_profile, overlay_profile
from canair.render import messages
from canair.yaml_edit.echo import echo_edit

from .common import current_name


def cmd_create(args) -> int:
    name = args.name.strip()
    if not name:
        messages.error("profile name cannot be empty")
        return 2

    # car_model: flag, else prompt when interactive, else error.
    car_model = args.car_model
    if not car_model:
        if sys.stdin.isatty():
            try:
                print("Vehicle description (car_model): ", end="", flush=True, file=sys.stderr)
                car_model = input().strip()
            except (EOFError, KeyboardInterrupt):
                car_model = ""
        if not car_model:
            messages.error("--car-model is required")
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
        messages.error(str(e))
        return 1
    except ValueError as e:
        messages.error(str(e))
        return 2

    init = args.init or DEFAULT_INIT
    if args.set_default:
        default_note = f"\nSet as default_profile in {user_config_file()}."
    else:
        default_note = (
            f"\nSelect it with `canair --profile {name} ...`, "
            "or set `default_profile` in your config."
        )

    echo_edit(f"created profile '{name}'", root)
    print(f"  car_model: {car_model}")
    print(f"  init:      {init}")
    print(default_note.lstrip("\n"))
    problem = convention_problem(name)
    if problem:
        messages.note(
            f"{problem}. Bundled profiles must follow it; rename before contributing "
            "(see docs/concepts/profiles.md#naming-a-profile)"
        )
    return 0


def cmd_adopt(args) -> int:
    name = current_name(args.name.strip(), getattr(args, "profiles_dir", None))
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
        messages.error(f"profile '{name}' not found. Available: {avail}.")
        return 1
    except FileExistsError as e:
        messages.error(f"{e}. Overwrite it with --force.")
        return 1
    except ValueError as e:
        messages.error(str(e))
        return 2

    echo_edit(f"adopted '{name}'", dest)
    print(f"  from: {source}")
    print(f"\n'{name}' now resolves to your copy, which writes survive a reinstall.")
    print("The copy no longer tracks upstream, so `canair contribute` may report a")
    print("rollback once upstream moves on — for contribution work point canair at a")
    print("checkout instead: `canair config set profiles_dir <clone>/profiles`.")
    if args.set_default:
        print(f"\nSet as default_profile in {user_config_file()}.")
    return 0


def cmd_overlay(args) -> int:
    name = current_name(args.name.strip(), getattr(args, "profiles_dir", None))
    try:
        base, dest = overlay_profile(
            name,
            profiles_dir=getattr(args, "profiles_dir", None),
            set_default=args.set_default,
        )
    except LookupError:
        profiles = selectable_profiles(getattr(args, "profiles_dir", None))
        avail = ", ".join(profiles) or "none"
        messages.error(f"profile '{name}' not found. Available: {avail}.")
        return 1
    except FileExistsError as e:
        messages.error(f"{e}.")
        messages.hint("a layer is only ever created once: remove that directory, or use it as-is")
        return 1
    except ValueError as e:
        messages.error(str(e))
        return 2

    echo_edit(f"layered '{name}' over {base}", dest)
    print("  base: definitions, read-only captures; overlay: everything you record")
    print(f"\n`--profile {name}` now reads both layers and records into yours.")
    print("Definitions still belong to the base, so editing them is refused — run")
    print(f"`canair profile adopt {name}` if you need to change PIDs or signals too.")
    if args.set_default:
        print(f"\nSet as default_profile in {user_config_file()}.")
    return 0
