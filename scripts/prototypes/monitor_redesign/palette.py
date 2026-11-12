"""Direction B — "Palette": no menus; a command palette, a mode-pill status line, toasts.

Keyboard first, and the most room for values::

    Overnight charge — battery · CHARGING, PLUGGED              hyundai-ioniq-ae-ev · home
    (values)
     LIVE  ● REC 04:12   ✓ home 9ms  #412 every 2.0s  1,204 frames         CHARGING  BMS 81 standard
    ↑/↓ select · p select ECUs/PIDs · s label · space pause · ctrl+p commands · ? help · q quit

* ``ctrl+p`` opens Textual's command palette over every command, searched by name ("rec",
  "ruler", "slow"). Each entry shows its key, so the palette teaches the keys as a menu does.
* The status line leads with pills for the states that change what the user should do: the
  poll mode (``LIVE``, ``PAUSED``, ``RECONNECTING``), ``REC``, a link fault, an output left on.
  Facts follow in a fixed order; the vehicle state is pinned right.
* The key line is contextual: it lists the keys of what has focus (a selected signal shows
  ``e edit · v verify · x ship``).
* Messages are toasts in the corner (``App.notify``), so neither bar ever changes for one.
* Narrow: the title loses its right half, the status keeps its pills, the key line keeps
  ``ctrl+p`` and ``?``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from chrome import (
    MockupApp,
    Part,
    ValuesView,
    car_parts,
    link_parts,
    poll_parts,
    record_parts,
    split_line,
)
from commands import run
from fake import PROFILE
from provider import MonitorCommands
from rich.markup import escape
from textual.app import ComposeResult, SystemCommand
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Static

from canair.render.glyphs import FAIL, OK, WARN
from canair.tui import styles
from canair.tui.keys import arrows_hint, key_hint

SEVERITY = {"error": "error", "warning": "warning", "success": "information", "info": "information"}


class PaletteApp(MockupApp):
    ENABLE_COMMAND_PALETTE = True
    COMMAND_PALETTE_BINDING = "ctrl+p"
    COMMANDS: ClassVar = {MonitorCommands}

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #title { height: 1; padding: 0 1; }
    #title-left { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
    #title-right { width: auto; color: $text-muted; }
    #status { height: 1; padding: 0 1; text-wrap: nowrap; }
    #keys { height: 1; padding: 0 1; color: $text-muted; text-wrap: nowrap;
            text-overflow: ellipsis; }
    .-narrow #title-right { display: none; }
    /* Toasts at the top, so a message never covers the status line. */
    ToastRack { dock: top; align: right top; margin-top: 1; margin-bottom: 0; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="title"):
            yield Static("", id="title-left")
            yield Static("", id="title-right")
        yield ValuesView()
        yield Static("", id="status")
        yield Static("", id="keys")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        return ()

    def action_cmd(self, cmd_id: str) -> None:
        if cmd_id == "quit":
            self.exit()
            return
        if cmd_id == "palette":
            self.action_command_palette()
            return
        msg, severity = run(self.m, cmd_id)
        self.show_message(msg, severity)
        self.refresh_view()

    def show_message(self, message: str, severity: str, secs: float = 4) -> None:
        # A toast's colour bar alone would carry the severity; the glyph carries it too.
        glyph = {"error": f"{FAIL} ", "warning": f"{WARN} ", "success": f"{OK} "}.get(severity, "")
        level = SEVERITY[severity]
        self.notify(escape(glyph + message), severity=level, timeout=secs)  # type: ignore[arg-type]

    def refresh_view(self) -> None:
        if not self.is_mounted:
            return
        m = self.m
        self.base.query_one(ValuesView).show(m)
        states = f" [dim]·[/] {', '.join(m.states)}" if m.states else ""
        self.base.query_one("#title-left", Static).update(f"[b]{escape(m.label)}[/]{states}")
        self.base.query_one("#title-right", Static).update(
            f"{PROFILE} [dim]·[/] {m.device} [dim]({m.transport})[/]"
        )
        self.base.query_one("#status", Static).update(self.status_markup())
        self.base.query_one("#keys", Static).update(escape(self.keys_text()))

    def pills(self) -> list[Part]:
        """The states that change what the user should do, drawn as reversed blocks."""
        m = self.m
        pills = []
        if m.reconnecting:
            pills.append(Part(f"[reverse {styles.WARNING}] ⟳ RECONNECTING [/]", 50))
        elif m.paused:
            pills.append(Part(f"[reverse {styles.WARNING}] PAUSED [/]", 50))
        else:
            pills.append(Part(f"[reverse {styles.OK}] LIVE [/]", 50))
        if m.recording:
            mins, secs = divmod(int(m.elapsed), 60)
            pills.append(Part(f"[reverse b] ● REC {mins:02d}:{secs:02d} [/]", 45))
        if m.health.verdict == "lossy":
            pills.append(Part(f"[reverse {styles.ERROR}] {FAIL} {m.health.drops} drops [/]", 48))
        if m.actuators_on:
            # An output this run switched on is the car's state: it outlives every other pill.
            pills.append(Part(f"[reverse {styles.WARNING}] {WARN} {m.actuators_on} ON [/]", 49))
        return pills

    def status_markup(self) -> str:
        m = self.m
        width = max(20, self.size.width - 2)
        link = [p for p in link_parts(m) if "drop" not in p.markup]
        facts = link + poll_parts(m)[1:] + record_parts(m)[2:]
        sessions = [p for p in car_parts(m) if "ON" not in p.markup][1:]
        right = [Part(f"[reverse {styles.NAME}] {m.suggested_state} [/]", 30), *sessions]
        return split_line(self.pills() + facts, right, width)

    def keys_text(self) -> str:
        palette = "ctrl+p commands"
        tail = [palette, key_hint("help"), key_hint("quit")]
        if self.narrow:
            return " · ".join(tail)
        if self.m.selected is not None:
            head = [
                key_hint("edit"),
                key_hint("verify"),
                key_hint("exclude", "toggle ship"),
                key_hint("back", "deselect"),
            ]
        else:
            head = [
                arrows_hint("select"),
                key_hint("pick", "select ECUs/PIDs"),
                key_hint("session", "label"),
                key_hint("pause"),
                key_hint("view"),
            ]
        return " · ".join(head + tail)
