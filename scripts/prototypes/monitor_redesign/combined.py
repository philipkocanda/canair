"""Direction D — the combination of A and B: a menu bar, the palette, one zoned status line.

Three rows of chrome, as today, so the values get as many rows as they do now::

    canair  Record  Poll  View  Signal  Actuators  Help    Overnight charge — battery · CHARGING
    (values)
    ✓ link home 9ms │ LIVE #412 every 2.0s │ ● REC 04:12 1,204 frames   CHARGING · BMS 81 standard
    ▶ BMS 2101 SOC_BMS 85.5 % ✓ · e edit · v verify         F10 menu · ctrl+p commands · ? help

* **From A:** the menu bar (``F10``, ``alt+letter``, a click) with each item's key, ``[x]`` and
  ``(•)``; the recording title on its right. The status line keeps A's fixed zones (link │ poll
  │ recording, the car pinned right) and takes two lines when narrow instead of dropping facts.
  The bottom line carries the last message, or the selected signal when there is none.
* **From B:** ``ctrl+p`` opens the palette over the same catalogue. Only exceptions are badges
  (``PAUSED``, ``RECONNECTING``, ``✗ drops``, ``⚠ ON``); the normal state is plain text. The
  bottom line ends with the keys of what has focus and the two routes to every command.
* **Dropped:** A's toolbar and B's toasts. A mode that is not the default shows on the bottom
  line (``view signals``); the status zones are clickable (link → event log, poll → the Poll menu,
  recording → recording info, car → diagnostic session). A message never covers a value: an error
  stays until the next key, and every message also goes to the event log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from chrome import (
    MenuTitle,
    MockupApp,
    Part,
    ValuesView,
    car_parts,
    fit,
    link_parts,
    poll_parts,
    record_parts,
    split_line,
)
from commands import MENUS
from fake import PROFILE
from provider import MonitorCommands
from rich.markup import escape
from textual import events
from textual.app import ComposeResult, SystemCommand
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Static

from canair.render.glyphs import CURSOR, FAIL, OK, WARN
from canair.tui import styles
from canair.tui.keys import arrows_hint, bind, key_hint

SHORT = {"Record": "Rec", "Signal": "Sig", "Actuators": "Act"}
SEP = " [dim]│[/] "
SEP_W = 3

#: The command a click on a status zone runs.
ZONE_CLICK = {"link": "event_log", "record": "info", "car": "diag_session"}


@dataclass(frozen=True)
class Zone:
    name: str
    parts: list[Part]


def lay_out(left: list[Zone], right: Zone | None, width: int) -> tuple[str, list[tuple]]:
    """One status line: ``left`` zones split by ``│``, ``right`` pinned to the edge.

    The least important fact anywhere on the line goes first. Returns the markup and the column
    span of each zone, ``(start, end, name)``, which the click handler reads.
    """
    flat = [(i, p) for i, z in enumerate(left) for p in z.parts]
    if right is not None:
        flat += [(-1, p) for p in right.parts]
    while True:
        groups = [[p for j, p in flat if j == i] for i in range(len(left))]
        tail = [p for j, p in flat if j == -1]
        widths = [sum(p.width for p in g) + len(g) - 1 for g in groups if g]
        head_w = sum(widths) + SEP_W * max(0, len(widths) - 1)
        tail_w = sum(p.width for p in tail) + max(0, len(tail) - 1)
        if head_w + tail_w + 2 <= width or len(flat) <= 1:
            break
        flat.pop(min(range(len(flat)), key=lambda k: (flat[k][1].priority, -k)))
    spans, col, chunks = [], 0, []
    for zone, group in zip(left, groups, strict=True):
        if not group:
            continue
        text = " ".join(p.markup for p in group)
        w = Part(text).width
        spans.append((col, col + w, zone.name))
        chunks.append(text)
        col += w + SEP_W
    head = SEP.join(chunks)
    tail_text = " ".join(p.markup for p in tail)
    pad = max(1, width - head_w - tail_w)
    if right is not None and tail:
        spans.append((head_w + pad, head_w + pad + tail_w, right.name))
    return f"{head}{' ' * pad}{tail_text}", spans


class EventLogScreen(ModalScreen[None]):
    """Every message of this run, newest last. ``esc`` or ``q`` closes it."""

    DEFAULT_CSS = """
    EventLogScreen { align: center middle; background: $background 60%; }
    #log-box { width: 90%; height: 70%; border: round $accent; padding: 0 1;
               background: $background; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "dismiss", desc="close"),
        *bind("quit", "dismiss", desc="close"),
    ]

    def __init__(self, events_: list[str]) -> None:
        super().__init__()
        self.lines = events_

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="log-box") as box:
            box.border_title = "Event log"
            box.border_subtitle = escape(key_hint("back", "close"))
            yield Static("\n".join(self.lines) or "[dim]No messages yet[/]")


class StatusLine(Static):
    """The zoned status line. A click on a zone runs that zone's command."""

    def __init__(self, *, id: str) -> None:
        super().__init__("", id=id)
        #: Per line, the ``(start, end, zone)`` column spans that ``lay_out`` returned.
        self.spans: list[list[tuple]] = []

    def on_click(self, event: events.Click) -> None:
        app: CombinedApp = self.app  # type: ignore[assignment]
        line = self.spans[event.y] if event.y < len(self.spans) else []
        for start, end, name in line:
            if start <= event.x - 1 < end:
                app.zone_clicked(name, start + 1)
                return


class CombinedApp(MockupApp):
    ENABLE_COMMAND_PALETTE = True
    COMMAND_PALETTE_BINDING = "ctrl+p"
    COMMANDS: ClassVar = {MonitorCommands}

    CSS = """
    Screen { layout: vertical; background: transparent; }
    #menubar { height: 1; background: $primary; color: $text; }
    #menubar MenuTitle.-open { text-style: reverse; }
    #brand { width: auto; padding: 0 1; text-style: bold; }
    #doc { width: 1fr; content-align: right middle; padding: 0 1; text-wrap: nowrap;
           text-overflow: ellipsis; }
    #status { height: auto; padding: 0 1; text-wrap: nowrap; }
    #context { height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    .-narrow #brand { display: none; }
    .-narrow #menubar MenuTitle { padding: 0 0 0 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f10", "menu('Record')", "menu", show=False),
        *(Binding(f"alt+{name[0].lower()}", f"menu('{name}')", show=False) for name in MENUS),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            yield Static("canair", id="brand")
            for name in MENUS:
                yield MenuTitle(name, short=SHORT.get(name))
            yield Static("", id="doc")
        yield ValuesView()
        yield StatusLine(id="status")
        yield Static("", id="context")

    def get_system_commands(self, screen: Screen) -> tuple[SystemCommand, ...]:
        return ()

    # -- menus and clicks -------------------------------------------------------------------
    def action_menu(self, name: str) -> None:
        self.open_menu(name)

    def menu_x(self, name: str) -> int:
        return next(t for t in self.base.query(MenuTitle) if t.menu == name).region.x

    def zone_clicked(self, zone: str, x: int) -> None:
        if zone == "poll":
            items = MENUS["Poll"]
            y = self.base.query_one("#status").region.y - (len(items) + 2)
            self.open_menu("Poll", x=x, y=max(1, y))
        else:
            self.action_cmd(ZONE_CLICK[zone])

    # -- messages ---------------------------------------------------------------------------
    def show_message(self, message: str, severity: str, secs: float = 4) -> None:
        """Every message goes to the event log; an error stays until the next key."""
        glyph = {"error": f"{FAIL} ", "warning": f"{WARN} ", "success": f"{OK} "}.get(severity, "")
        self.events.append(f"[dim]{time.strftime('%H:%M:%S')}[/]  {escape(glyph + message)}")
        self.flash.show(message, severity, secs=None if severity == "error" else secs)  # type: ignore[arg-type]

    def on_key(self, event: events.Key) -> None:
        if self.flash.live() and self.flash.severity == "error":
            self.flash.clear()
            self.refresh_view()

    def action_cmd(self, cmd_id: str) -> None:
        if cmd_id == "event_log":
            if len(self.screen_stack) == 1:
                self.push_screen(EventLogScreen(self.events))
            return
        super().action_cmd(cmd_id)

    # -- painting ---------------------------------------------------------------------------
    def refresh_view(self) -> None:
        if not self.is_mounted:
            return
        m = self.m
        self.base.query_one(ValuesView).show(m)
        for title in self.base.query(MenuTitle):
            title.set_short(self.narrow)
            title.set_class(title.menu == self.menu_open, "-open")
        self.base.query_one("#doc", Static).update(self.doc_markup())
        status = self.base.query_one("#status", StatusLine)
        markup, status.spans = self.status_markup()
        status.update(markup)
        self.base.query_one("#context", Static).update(self.context_markup())

    def doc_markup(self) -> str:
        """The recording title on the menu bar: label, then states, then the profile."""
        m = self.m
        width = self.size.width - sum(t.region.width for t in self.base.query(MenuTitle)) - 12
        parts = [Part(f"[b]{escape(m.label)}[/]", 30)]
        if m.states:
            parts.append(Part(f"[dim]·[/] {', '.join(m.states)}", 20))
        parts.append(Part(f"[dim]· {PROFILE}[/]", 10))
        return " ".join(p.markup for p in fit(parts, max(0, width), " "))

    def zones(self) -> tuple[Zone, Zone, Zone, Zone]:
        """The four zones, with a badge (reversed block) for each exception only."""
        m = self.m
        if m.health.verdict == "lossy":
            drops = m.health.drops
            badge = f"[reverse {styles.ERROR}] {FAIL} {drops} drop{'s' * (drops != 1)} [/]"
            link = [Part(badge, 48), *link_parts(m)[1:]]
        else:
            link = link_parts(m)
        poll = poll_parts(m)
        if m.reconnecting:
            poll[0] = Part(f"[reverse {styles.WARNING}] ⟳ RECONNECTING [/]", 50)
        elif m.paused:
            poll[0] = Part(f"[reverse {styles.WARNING}] PAUSED [/]", 50)
        car = car_parts(m)
        if m.actuators_on:
            car[0] = Part(f"[reverse {styles.WARNING}] {WARN} {m.actuators_on} ON [/]", 52)
        return (
            Zone("link", link),
            Zone("poll", poll),
            Zone("record", record_parts(m)),
            Zone("car", car),
        )

    def status_markup(self) -> tuple[str, list[list[tuple]]]:
        width = max(20, self.size.width - 2)
        link, poll, record, car = self.zones()
        if self.narrow:
            line1, spans1 = lay_out([link, poll], None, width)
            line2, spans2 = lay_out([record], car, width)
            return f"{line1}\n{line2}", [spans1, spans2]
        line, spans = lay_out([link, poll, record], car, width)
        return line, [spans]

    def context_markup(self) -> str:
        """The message, or the selection and the keys of what has focus; the routes on the right."""
        m = self.m
        width = max(20, self.size.width - 2)
        right = [
            # Below the selection, so a narrow line keeps what is selected over the routes.
            Part("[dim]F10 menu[/]", 33),
            Part(f"[dim]{escape('ctrl+p commands')}[/]", 32),
            Part(f"[dim]{escape(key_hint('help'))}[/]", 45),
        ]
        msg = self.flash.item()
        if msg is not None:
            return split_line([Part(msg.markup, 60)], right, width, " [dim]·[/] ")
        left: list[Part] = []
        modes = [("view", m.view_mode, "full"), ("filter", m.filter_mode, "all")]
        left += [Part(f"[dim]{k}[/] [b]{v}[/]", 30) for k, v, default in modes if v != default]
        if m.show_rulers:
            left.append(Part("[b]ruler[/]", 30))
        d = m.selected_detail()
        if d is not None:
            mark = f"[{styles.OK}]{OK}[/]" if d["verified"] else f"[{styles.WARNING}]?[/]"
            value = f"{d['value']} {escape(d['unit'])}".strip()
            left.append(Part(f"[b]{CURSOR} {d['ecu']} {d['pid']} {escape(d['name'])}[/]", 35))
            left.append(Part(f"{value} {mark}", 34))
            hints = [key_hint("edit"), key_hint("verify"), key_hint("exclude", "toggle ship")]
            left += [Part(f"[dim]{escape(h)}[/]", 15 - i) for i, h in enumerate(hints)]
        else:
            hints = [
                arrows_hint("select"),
                key_hint("pick", "select ECUs/PIDs"),
                key_hint("session", "label"),
                key_hint("pause"),
            ]
            left += [Part(f"[dim]{escape(h)}[/]", 20 - 2 * i) for i, h in enumerate(hints)]
        return split_line(left, right, width, " [dim]·[/] ")
