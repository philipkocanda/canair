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
from pathlib import Path

from canair.config import set_config_value, user_profiles_dir
from canair.core.prompt import confirm
from canair.install.context import installed_snapshot_kind
from canair.profile import selectable_profiles
from canair.profile.scaffold import adopt_profile, create_profile
from canair.render import messages
from canair.yaml_edit.echo import echo_edit

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


def _say(text: str = "") -> None:
    """The wizard is a dialogue, so it speaks on stderr and leaves stdout to the command."""
    print(text, file=sys.stderr)


def _prompt(text: str) -> str:
    """Ask for a value (a menu choice or a name). The wizard only runs on a terminal."""
    print(text, end="", flush=True, file=sys.stderr)
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return ""


def run_first_run_setup(args) -> None:
    """Interactively pick or create a profile and persist it as the default.

    Best-effort and non-fatal: any error or an empty answer just falls through
    to canair's normal profile resolution (which prints its own guidance).
    """
    profiles = selectable_profiles(getattr(args, "profiles_dir", None))

    _say("\n  Welcome to canair! Let's pick a vehicle profile to work with.\n")
    _say("  Profiles are directories bundling one vehicle's data. New ones you")
    _say(f"  create live under:\n    {user_profiles_dir()}\n")

    items = sorted(profiles.items())
    if items:
        _say("  Discovered profiles:\n")
        for n, (name, path) in enumerate(items, 1):
            _say(f"    {n}) {name}")
            _say(f"       {path}")
        _say("\n    n) create a new blank profile")
        _say("    s) skip for now\n")
        choice = _prompt(f"  Choose [1-{len(items)}/n/s]: ").lower()
    else:
        _say("  No profiles found yet.\n")
        choice = "n"

    if choice in ("", "s"):
        _say(
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
        path = _offer_adopt_if_snapshot(args, name, path)
        config_path = set_config_value("default_profile", name)
        _say()
        echo_edit(f"default profile set to '{name}'", config_path)
        _say(f"    profile: {path}")
        _say("    Change it anytime: canair config set default_profile NAME\n")
        return

    _say(
        "\n  Unrecognized choice — skipping. Set one later with "
        "`canair config set default_profile NAME`.\n"
    )


def _offer_adopt_if_snapshot(args, name: str, path: Path) -> Path:
    """Offer to adopt a profile that only exists inside the install snapshot.

    Recording such a profile as the default is a trap: every ``--save``, PID edit
    and state edit lands under ``site-packages`` and the next reinstall deletes it.
    Adopting first is the difference between keeping your captures and losing them,
    so ask before the choice is persisted rather than warning after each write.
    """
    kind = installed_snapshot_kind(path)
    if not kind:
        return path

    _say()
    messages.note(
        f"'{name}' ships inside the {kind} install snapshot:\n{path}\n"
        "Anything you record or edit there is deleted by the next reinstall.\n"
        f"A copy under {user_profiles_dir()} is yours and survives."
    )
    if not confirm("Copy it there now?", yes=bool(getattr(args, "yes", False))):
        _say(f"  Left as-is. Copy it later with `canair profile adopt {name}`.")
        return path

    try:
        _source, dest = adopt_profile(name)
    except (LookupError, ValueError, FileExistsError, OSError) as e:
        messages.error(f"copy failed: {e}")
        messages.hint(f"do it later with `canair profile adopt {name}`")
        return path
    _say()
    echo_edit(f"copied '{name}'", dest)
    return dest


def _create_new_profile_interactive(args) -> None:
    name = _prompt("\n  New profile name (e.g. my-car): ")
    if not name:
        _say("  No name given — skipping.\n")
        return
    car_model = _prompt("  Car model description (e.g. 'VW e-Golf 2019'): ")

    dest = user_profiles_dir() / name
    _say(f"\n  This will create:\n    {dest}\n")

    try:
        root = create_profile(
            name,
            car_model=car_model or name,
            set_default=True,
        )
    except (ValueError, FileExistsError, OSError) as e:
        messages.error(f"profile creation failed: {e}")
        messages.hint("set one later with `canair profile create`")
        return

    echo_edit(f"created profile '{name}' and set it as your default", root)
    _say("    Next: read `canair --help`, then `canair discover` with the car plugged in.\n")
