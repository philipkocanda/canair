"""The bare ``canair profile``: an arrow-key picker, or the list."""

from __future__ import annotations

import sys
from pathlib import Path

from canair.profile import (
    ProfileError,
    active,
    selectable_profiles,
)
from canair.yaml_edit.echo import echo_edit

from .listing import cmd_list
from .use import choose_variant


def cmd_default(args) -> int:
    """Bare ``canair profile``: an interactive picker on a TTY, else the list.

    On an interactive terminal, launch an arrow-key selector over the discovered
    profiles; choosing one sets it as ``default_profile`` (like ``profile use``).
    When stdin/stdout aren't a TTY (piped, scripted) fall back to the plain list
    so the command stays composable and non-interactive.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        return cmd_pick(args)
    return cmd_list(args)


def cmd_pick(args) -> int:
    """Interactive arrow-key profile picker → set default_profile."""
    from canair.config import set_config_value
    from canair.tui import select_from_list

    profiles = selectable_profiles(getattr(args, "profiles_dir", None))
    if not profiles:
        return cmd_list(args)  # prints the "no profiles" guidance

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
        print("Cancelled — no change.", file=sys.stderr)
        return 0

    name, root = chosen
    path = set_config_value("default_profile", name)
    what = (
        f"'{name}' is already the default profile"
        if name == active_name
        else f"default_profile = {name}"
    )
    variant = choose_variant(name, getattr(args, "profiles_dir", None), None)
    if variant:
        from canair.config import set_config_key

        set_config_key(f"profile_settings.{name}.variant", variant)
        what += f", variant = {variant}"
    echo_edit(what, path)
    print(f"  profile: {root}")
    return 0
