"""An app's commands, described once: what each is called, where it sits, which key runs it.

The menu bar, the command palette and the help sheet all read the same :class:`Catalogue`, so a
command cannot be named one way in a menu and another in the palette, or show a key it is not
bound to. A command names the app *action* it runs (``"toggle_pause"``, ``"set_view('ecus')"``),
so the catalogue adds no second way of doing anything: the key, the menu item and the palette
entry all end in the same action method.

Whether a command is available now, and whether a toggle is on, is the app's business: the shell
asks it through :class:`CommandHost`.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from canair.tui.keys import ROLES, display_key

Kind = Literal["action", "toggle", "choice"]


@dataclass(frozen=True)
class Command:
    """One thing the app can do.

    ``role`` is the key role that runs it (:mod:`canair.tui.keys`); a command without one is
    reached through its menu and the palette only. ``kind`` says how its state is drawn: a
    ``toggle`` shows ``[x]``, a ``choice`` (one of a set, such as a view mode) shows ``(•)``.
    """

    id: str
    label: str
    action: str
    role: str | None = None
    help: str = ""
    kind: Kind = "action"

    def __post_init__(self) -> None:
        if self.role is not None and self.role not in ROLES:
            raise ValueError(f"command {self.id!r}: unknown key role {self.role!r}")

    @property
    def key_label(self) -> str:
        """The command's key as the help sheet writes it (``space``, ``V``), or ``""``."""
        return display_key(ROLES[self.role].keys[0]) if self.role else ""


#: A separator between two groups of items in a menu.
SEPARATOR = None

MenuItem = Command | None


@dataclass(frozen=True)
class Menu:
    """One menu of the menu bar: its title, a shorter title for a small screen, its items."""

    title: str
    items: tuple[MenuItem, ...]
    short: str = ""

    @property
    def mnemonic(self) -> str:
        """The letter that opens the menu with ``alt``, underlined in its title."""
        return self.title[0].lower()

    def commands(self) -> Iterator[Command]:
        return (item for item in self.items if item is not None)


class Catalogue:
    """The menus of an app, in menu-bar order, and every command in them by id."""

    def __init__(self, menus: Sequence[Menu]) -> None:
        self.menus = tuple(menus)
        letters = [m.mnemonic for m in self.menus]
        if len(set(letters)) != len(letters):
            raise ValueError(f"two menus share a mnemonic letter: {letters}")
        self._by_id: dict[str, Command] = {}
        for menu in self.menus:
            for cmd in menu.commands():
                if cmd.id in self._by_id:
                    raise ValueError(f"command {cmd.id!r} is in two menus")
                self._by_id[cmd.id] = cmd

    def __getitem__(self, cmd_id: str) -> Command:
        return self._by_id[cmd_id]

    def __contains__(self, cmd_id: object) -> bool:
        return cmd_id in self._by_id

    def commands(self) -> Iterator[tuple[Menu, Command]]:
        """Every command with the menu it sits in, in menu order."""
        for menu in self.menus:
            for cmd in menu.commands():
                yield menu, cmd

    def menu(self, title: str) -> Menu:
        return next(m for m in self.menus if m.title == title)

    def by_mnemonic(self, letter: str) -> Menu | None:
        return next((m for m in self.menus if m.mnemonic == letter.lower()), None)


class CommandHost(Protocol):
    """What the shell asks the app about its commands."""

    def shell_catalogue(self) -> Catalogue: ...

    def command_unavailable(self, cmd: Command) -> str | None:
        """Why the command cannot run now (shown as a warning), or None when it can."""
        ...

    def command_state(self, cmd: Command) -> bool | None:
        """Whether a toggle or a choice is on now; None for a plain action."""
        ...
