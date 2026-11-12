"""The command palette's provider: every command of the app's catalogue, searchable by name.

Textual's palette (``ctrl+p``) lists what this provider yields. An entry shows its menu, its
label, its toggle state and its key, so the palette teaches the keys as the menus do. Its help
line is the command's help. A command that cannot run now is listed, dimmed; choosing it shows
why.
"""

from __future__ import annotations

from functools import partial
from typing import cast

from rich.text import Text
from textual.command import DiscoveryHit, Hit, Hits, Provider

from .app import ShellMixin
from .commands import Command, Menu


def palette_row(menu: Menu, cmd: Command, state: bool | None, available: bool) -> Text:
    """``View › [x] Byte ruler   r``: the menu, the state mark, the label, the key."""
    mark = ""
    if cmd.kind == "toggle":
        mark = "[x] " if state else "[ ] "
    elif cmd.kind == "choice":
        mark = "(•) " if state else "( ) "
    text = Text(f"{menu.title} › ", style="dim")
    text.append(mark + cmd.label, style="" if available else "dim")
    if cmd.key_label:
        text.append(f"   {cmd.key_label}", style="bold")
    return text


class ShellCommands(Provider):
    """The app's catalogue as palette entries; running one runs the command."""

    def _entries(self):
        host = cast(ShellMixin, self.app)
        run = host.run_command
        for menu, cmd in host.shell_catalogue().commands():
            available = host.command_unavailable(cmd) is None
            row = palette_row(menu, cmd, host.command_state(cmd), available)
            yield f"{menu.title} {cmd.label} {cmd.help}", row, partial(run, cmd.id), cmd.help

    async def discover(self) -> Hits:
        for _words, row, run, help_text in self._entries():
            yield DiscoveryHit(row, run, help=help_text or None)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for words, row, run, help_text in self._entries():
            score = matcher.match(words)
            if score > 0:
                yield Hit(score, row, run, help=help_text or None)
