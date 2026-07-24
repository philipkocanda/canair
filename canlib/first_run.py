"""First-run interactive setup: choose a vehicle profile.

On the very first invocation (when the starter ``config.yaml`` was just seeded)
and no profile is otherwise selected, offer the user a choice: use one of the
discovered built-in profiles, or create a new one. The chosen profile is written
to ``default_profile`` so subsequent runs are non-interactive.

Deliberately conservative — it only prompts when *all* of these hold, so it never
gets in the way of scripting or normal use:

* stdin/stdout are a TTY (interactive session),
* the config file was just seeded (genuine first run),
* no ``--profile`` / ``$CANAIR_PROFILE`` / ``default_profile`` is set, and
* the command being run actually needs a profile (not ``profile``/``config``/
  ``completion``, ``--help``, ``--version``).
"""

from __future__ import annotations

import os
import sys

from .config import set_config_value, user_profiles_dir
from .profile import discover_profiles

# Commands that manage config/profiles themselves, or don't touch a profile —
# never interrupt these with the wizard.
_SKIP_COMMANDS = {"profile", "config", "completion"}


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def should_offer(args, *, seeded: bool) -> bool:
    """Whether to run the first-run profile chooser for this invocation."""
    if not seeded or not _is_interactive():
        return False
    if os.environ.get("CANAIR_PROFILE") or getattr(args, "profile", None):
        return False
    if getattr(args, "command", None) in _SKIP_COMMANDS:
        return False
    if getattr(args, "func", None) is None:  # bare `canair` / help
        return False
    return True


def _prompt(text: str) -> str:
    try:
        return input(text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def run_first_run_setup(args) -> None:
    """Interactively pick or create a profile and persist it as the default.

    Best-effort and non-fatal: any error or an empty answer just falls through
    to canair's normal profile resolution (which prints its own guidance).
    """
    profiles = discover_profiles(getattr(args, "profiles_dir", None))

    print("\n  Welcome to canair! Let's pick a vehicle profile to work with.\n")
    print("  Profiles are directories bundling one vehicle's data. New ones you")
    print(f"  create live under:\n    {user_profiles_dir()}\n")

    items = sorted(profiles.items())
    if items:
        print("  Discovered profiles:\n")
        for n, (name, path) in enumerate(items, 1):
            print(f"    {n}) {name}")
            print(f"       {path}")
        print("\n    n) create a new blank profile")
        print("    s) skip for now\n")
        choice = _prompt(f"  Choose [1-{len(items)}/n/s]: ").lower()
    else:
        print("  No profiles found yet.\n")
        choice = "n"

    if choice in ("", "s"):
        print(
            "\n  Skipped. canair will ask you to pick a profile per-command "
            "(--profile NAME)\n  until you set one with "
            "`canair config set default_profile NAME`.\n"
        )
        return

    if choice == "n":
        _create_new_profile_interactive(args)
        return

    if choice.isdigit() and 1 <= int(choice) <= len(items):
        name, path = items[int(choice) - 1]
        set_config_value("default_profile", name)
        print(f"\n  ✓ Default profile set to '{name}'.")
        print(f"    ({path})")
        print("    Change it anytime: canair config set default_profile NAME\n")
        return

    print(
        "\n  Unrecognized choice — skipping. Set one later with "
        "`canair config set default_profile NAME`.\n"
    )


def _create_new_profile_interactive(args) -> None:
    from .commands.profile import create_profile  # local import to avoid cycles

    name = _prompt("\n  New profile name (e.g. my-car): ")
    if not name:
        print("  No name given — skipping.\n")
        return
    car_model = _prompt("  Car model description (e.g. 'VW e-Golf 2019'): ")

    dest = user_profiles_dir() / name
    print(f"\n  This will create:\n    {dest}\n")

    try:
        root = create_profile(
            name,
            car_model=car_model or name,
            set_default=True,
        )
    except (ValueError, FileExistsError, OSError) as e:
        print(f"  Profile creation failed: {e}")
        print("  Set one later with `canair profile create`.\n")
        return

    print(f"  ✓ Created profile '{name}' and set it as your default.")
    print(f"    ({root})")
    print("    Next: read `canair --help`, then `canair discover` with the car plugged in.\n")
