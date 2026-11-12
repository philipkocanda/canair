"""Direction A — "Desktop": a menu bar, a toolbar, and a status bar of fixed zones.

The layout of a classic desktop application, drawn in a terminal::

    canair  Record  Poll  View  Signal  Actuators  Help      Overnight charge — battery · CHARGING
    Pause  Pick…  │  View: full ▾  Filter: all ▾  Ruler  │  Label…  New  │  Edit…  Verify  Ship
    (values)
    ✓ link home wican-ws 12ms │ LIVE #412 every 2.0s │ ● REC 04:12 1,204 frames │   CHARGING BMS 81
    ▶ BMS 2101 SOC_BMS 85.50 % · ✓ verified · B09/2                        (or the last message)

* The menus teach the keys: every item shows its key on the right, and every command is in one.
  ``F10`` opens the first menu, ``←``/``→`` move between menus, a click opens one.
* The toolbar holds the commands used most, and shows the state of each (a toggle that is on is
  drawn reversed, a mode shows its value: ``View: full ▾``). It never reflows.
* The status bar has four zones in a fixed order (link, poll, record, car). The message line
  below it carries the last message, or the selected signal when there is none: a message
  never pushes a status zone away.
* Narrow: the toolbar goes, the menu titles shorten, the zones take two lines.
"""

from __future__ import annotations

from typing import ClassVar

from chrome import (
    NARROW,
    MenuTitle,
    MockupApp,
    Part,
    ValuesView,
    car_parts,
    fit,
    fit_menu_bar,
    flash_markup,
    link_parts,
    poll_parts,
    record_parts,
)
from commands import COMMANDS, MENUS, enabled, state
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Static

from canair.render.glyphs import CURSOR, OK

SHORT = {"Record": "Rec", "Signal": "Sig", "Actuators": "Act"}

#: The toolbar: command ids, ``|`` for a group break.
TOOLBAR = [
    "pause",
    "pick",
    "|",
    "view",
    "filter",
    "rulers",
    "|",
    "label",
    "new",
    "|",
    "edit",
    "verify",
    "ship",
]


class ToolButton(Static):
    """A one-row toolbar button: its label, its key (dim), and its state."""

    DEFAULT_CSS = """
    ToolButton { width: auto; padding: 0 1; }
    ToolButton:hover { background: $accent; color: $background; }
    ToolButton.-on { text-style: reverse; }
    ToolButton.-off { color: $text-muted; }
    """

    def __init__(self, cmd_id: str) -> None:
        super().__init__("")
        self.cmd_id = cmd_id

    def paint(self, app: DesktopApp) -> None:
        m = app.m
        if self.cmd_id == "view":
            label, key, on = f"View: {m.view_mode} ▾", "V", False
            self.tooltip = "Choose the view mode (V cycles)"
        elif self.cmd_id == "filter":
            label, key, on = f"Filter: {m.filter_mode} ▾", "F", False
            self.tooltip = "Choose which signals show (F cycles)"
        else:
            cmd = COMMANDS[self.cmd_id]
            label = cmd.label.replace("Pause polling", "Pause").replace("Byte ruler", "Ruler")
            label = label.replace("Label recording…", "Label…").replace("Edit signal…", "Edit…")
            label = label.replace("Finish and start new", "New").replace("Ship to AutoPID", "Ship")
            label = label.replace("Select ECUs and PIDs…", "Pick…")
            if self.cmd_id == "pause" and m.paused:
                label = "Resume"
            key, on = cmd.key_label, bool(state(m, cmd))
            self.set_class(not enabled(m, cmd), "-off")
            self.tooltip = f"{cmd.help} ({key})"
        self.set_class(on, "-on")
        self.update(f"{escape(label)} [dim]{escape(key)}[/]")

    def on_click(self) -> None:
        app: DesktopApp = self.app  # type: ignore[assignment]
        if self.cmd_id in ("view", "filter"):
            items = [c for c in MENUS["View"] if c and c.id.startswith(f"{self.cmd_id}:")]
            app.open_menu("View", items=items, x=self.region.x, y=self.region.y + 1)
        else:
            app.action_cmd(self.cmd_id)


class DesktopApp(MockupApp):
    CSS = """
    Screen { layout: vertical; background: transparent; }
    #menubar { height: 1; background: $primary; color: $text; }
    #menubar MenuTitle.-open { text-style: reverse; }
    #brand { width: auto; padding: 0 1; text-style: bold; }
    #doc { width: 1fr; content-align: right middle; padding: 0 1; text-wrap: nowrap;
           text-overflow: ellipsis; }
    #toolbar { height: 1; border-bottom: none; }
    #toolbar .gap { width: 1; color: $text-muted; }
    #status { height: auto; padding: 0 1; border-top: solid $primary; }
    #message { height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    .-narrow #toolbar { display: none; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f10", "menu('Record')", "menu", show=False),
        *(Binding(f"alt+{name[0].lower()}", f"menu('{name}')", show=False) for name in MENUS),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            yield Static("canair", id="brand")
            for name in MENUS:
                yield MenuTitle(name, short=SHORT.get(name))
            yield Static("", id="doc")
        with Horizontal(id="toolbar"):
            for item in TOOLBAR:
                yield Static("│", classes="gap") if item == "|" else ToolButton(item)
        yield ValuesView()
        yield Static("", id="status")
        yield Static("", id="message")

    def action_menu(self, name: str) -> None:
        self.open_menu(name)

    def menu_x(self, name: str) -> int:
        title = next(t for t in self.base.query(MenuTitle) if t.menu == name)
        return title.region.x

    def refresh_view(self) -> None:
        if not self.is_mounted:
            return
        m = self.m
        self.base.query_one(ValuesView).show(m)
        titles = list(self.base.query(MenuTitle))
        self.base.query_one("#brand").display = fit_menu_bar(titles, self.size.width, room=24)
        for title in titles:
            title.set_class(title.menu == self.menu_open, "-open")
        states = f" [dim]·[/] {', '.join(m.states)}" if m.states else ""
        self.base.query_one("#doc", Static).update(f"[b]{escape(m.label)}[/]{states}")
        for button in self.base.query(ToolButton):
            button.paint(self)
        self.base.query_one("#status", Static).update(self.status_markup())
        self.base.query_one("#message", Static).update(self.message_markup())

    def status_markup(self) -> str:
        """The four zones, ``│`` between them, the car zone pinned to the right edge."""
        width = max(20, self.size.width - 2)
        zones = [link_parts(self.m), poll_parts(self.m), record_parts(self.m)]
        car = car_parts(self.m)
        if self.size.width < NARROW:
            line1 = self._zones_line(zones[:2], [], width)
            line2 = self._zones_line(zones[2:], car, width)
            return f"{line1}\n{line2}"
        return self._zones_line(zones, car, width)

    def _zones_line(self, left: list[list[Part]], right: list[Part], width: int) -> str:
        sep = " [dim]│[/] "
        flat = [(i, p) for i, zone in enumerate(left) for p in zone] + [(-1, p) for p in right]
        # Squeeze the whole line at once so the least important fact anywhere goes first.
        while True:
            groups = [[p for j, p in flat if j == i] for i in range(len(left))]
            right_parts = [p for j, p in flat if j == -1]
            text = sep.join(" ".join(p.markup for p in g) for g in groups if g)
            tail = " ".join(p.markup for p in right_parts)
            used = Part(text).width + Part(tail).width + 2
            if used <= width or len(flat) <= 1:
                break
            drop = min(range(len(flat)), key=lambda k: (flat[k][1].priority, -k))
            flat.pop(drop)
        pad = max(1, width - Part(text).width - Part(tail).width)
        return f"{text}{' ' * pad}{tail}"

    def message_markup(self) -> str:
        msg = flash_markup(self.flash)
        if msg:
            return msg
        d = self.m.selected_detail()
        if d is None:
            hint = "F10 menus · ↑/↓ select a signal · ? help"
            return f"[dim]{escape(hint)}[/]"
        mark = f"[green]{OK} verified[/]" if d["verified"] else "[yellow]? unverified[/]"
        parts = [
            f"[b]{CURSOR} [cyan]{d['ecu']}[/] [cyan]{d['pid']}[/] {escape(d['name'])}[/]",
            f"{d['value']} {escape(d['unit'])}".strip(),
            mark,
            f"[dim]{escape(d['expression'])}[/]",
        ]
        return " [dim]·[/] ".join(
            p.markup for p in fit([Part(p) for p in parts], self.size.width - 2, " · ")
        )
