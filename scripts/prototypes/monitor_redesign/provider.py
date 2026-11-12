"""The command palette's provider: every command in the catalogue, searchable by name.

Shared by direction B and the combination, which both open Textual's palette with ``ctrl+p``.
An entry shows its menu, its toggle state and its key, so the palette teaches the keys as a menu
does.
"""

from __future__ import annotations

from functools import partial

from commands import COMMANDS, Command, enabled, state
from rich.text import Text
from textual.command import DiscoveryHit, Hit, Hits, Provider


def palette_row(cmd: Command, on: bool | None, ok: bool) -> Text:
    mark = {True: "[x] ", False: "[ ] "}.get(on, "") if cmd.kind == "toggle" else ""
    text = Text(f"{cmd.menu} › ", style="dim")
    text.append(mark + cmd.label, style="" if ok else "dim")
    if cmd.key_label:
        text.append(f"   {cmd.key_label}", style="bold")
    return text


class MonitorCommands(Provider):
    """Every monitor command, searchable by its menu, label and help line."""

    def _entries(self):
        app = self.app
        m = app.m  # type: ignore[attr-defined]
        for cmd in COMMANDS.values():
            if cmd.id == "palette":
                continue
            run_it = partial(app.action_cmd, cmd.id)  # type: ignore[attr-defined]
            yield cmd, run_it, state(m, cmd), enabled(m, cmd)

    async def discover(self) -> Hits:
        for cmd, run_it, on, ok in self._entries():
            yield DiscoveryHit(palette_row(cmd, on, ok), run_it, help=cmd.help or None)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for cmd, run_it, on, ok in self._entries():
            score = matcher.match(f"{cmd.menu} {cmd.label} {cmd.help}")
            if score > 0:
                yield Hit(score, palette_row(cmd, on, ok), run_it, help=cmd.help or None)
