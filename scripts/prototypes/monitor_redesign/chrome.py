"""Pieces the three mockups share: the app shell, the values pane, the menus, the status data.

Only the chrome differs between the directions, so everything else lives here: the fake session
ticking, the command dispatch, the signal cursor, and a status model (link, poll, record, car)
that each direction lays out its own way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from commands import COMMANDS, MENUS, Command, enabled, run, state
from fake import FakeMonitor
from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import App
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from canair.render.glyphs import FAIL, OK, WARN
from canair.tui import styles
from canair.tui.flash import Flash
from canair.tui.keys import bind
from canair.tui.scroll import reveal_marker
from canair.uds.subfunctions import subfunction_name

#: Below this width a direction switches to its narrow layout (a phone SSH client is ~45).
NARROW = 72


# -- the status model -------------------------------------------------------------------
@dataclass(frozen=True)
class Part:
    """One fact for a status bar: markup and how long it survives a squeeze (higher = longer)."""

    markup: str
    priority: int = 10

    @property
    def width(self) -> int:
        return Text.from_markup(self.markup).cell_len


def session_word(mode: str) -> str:
    """``81`` → ``81 standard``: the hex plus the short name from the UDS registry."""
    name = subfunction_name(0x10, int(mode, 16)) or ""
    return f"{mode} {name.replace('DiagnosticSession', '').replace('Session', '')}".strip()


def link_parts(m: FakeMonitor, *, word: bool = True) -> list[Part]:
    """The link's verdict, device, transport and round trip. ``word`` names it ("✓ link")
    where nothing else does; a labelled zone shows "✓ ok"."""
    h = m.health
    if m.reconnecting:
        verdict = Part(f"[b {styles.WARNING}]⟳ reconnecting…[/]", 40)
    elif h.verdict == "lossy":
        verdict = Part(f"[b {styles.ERROR}]{FAIL} {h.drops} drop{'s' * (h.drops != 1)}[/]", 30)
    elif h.verdict == "coping":
        verdict = Part(f"[{styles.WARNING}]{WARN} {h.resyncs} resync[/]", 25)
    else:
        verdict = Part(f"[{styles.OK}]{OK} {'link' if word else 'ok'}[/]", 25)
    return [
        verdict,
        Part(f"[{styles.NAME}]{m.device}[/]", 22),
        Part(f"[dim]{m.transport}[/]", 5),
        Part(f"{h.rtt_ms:.0f}[dim]ms[/]", 8),
    ]


def poll_parts(m: FakeMonitor) -> list[Part]:
    if m.paused:
        mode = Part("[reverse] PAUSED [/]", 40)
    else:
        mode = Part(f"[{styles.OK}]LIVE[/]", 20)
    return [mode, Part(f"[dim]#[/]{m.cycle}", 15), Part(f"[dim]every[/] {m.rate}", 12)]


def record_parts(m: FakeMonitor) -> list[Part]:
    if not m.recording:
        return [Part("[dim]not recording[/]", 5)]
    dot = styles.RECORDING if int(time.monotonic()) % 2 == 0 else styles.MUTED
    mins, secs = divmod(int(m.elapsed), 60)
    return [
        Part(f"[{dot}]●[/] [{styles.RECORDING}]REC[/]", 35),
        Part(f"{mins:02d}:{secs:02d}", 18),
        Part(f"{m.captured:,}[dim] frames ·[/] {m.unique:,}[dim] unique[/]", 6),
    ]


def car_parts(m: FakeMonitor) -> list[Part]:
    parts = [Part(f"[b]{m.suggested_state}[/]", 21)]
    if m.actuators_on:
        parts.insert(0, Part(f"[{styles.WARNING}]{WARN} {m.actuators_on} ON[/]", 40))
    sess = " · ".join(f"[{styles.NAME}]{e}[/] {session_word(s)}" for e, s in m.sessions.items())
    parts.append(Part(sess, 7))
    return parts


def fit(parts: list[Part], width: int, sep: str = "  ") -> list[Part]:
    """Drop the lowest-priority parts (rightmost first) until they fit ``width``."""
    kept = list(parts)
    sep_w = Text.from_markup(sep).cell_len
    while kept and sum(p.width for p in kept) + sep_w * (len(kept) - 1) > width:
        drop = min(range(len(kept)), key=lambda i: (kept[i].priority, -i))
        kept.pop(drop)
    return kept


def split_line(left: list[Part], right: list[Part], width: int, sep: str = "  ") -> str:
    """``left`` from the left edge, ``right`` pinned to the right one, squeezed as one line."""
    tagged = [(0, p) for p in left] + [(1, p) for p in right]
    sep_w = Text.from_markup(sep).cell_len
    while len(tagged) > 1:
        used = sum(p.width for _, p in tagged) + sep_w * len(tagged)
        if used <= width:
            break
        tagged.pop(min(range(len(tagged)), key=lambda i: (tagged[i][1].priority, -i)))
    head = sep.join(p.markup for side, p in tagged if side == 0)
    tail = sep.join(p.markup for side, p in tagged if side == 1)
    pad = max(1, width - Part(head).width - Part(tail).width)
    return f"{head}{' ' * pad}{tail}"


def flash_markup(flash: Flash) -> str:
    item = flash.item()
    return item.markup if item is not None else ""


# -- the values pane --------------------------------------------------------------------
class ValuesView(VerticalScroll):
    """The production body (real renderer, real profile) with the signal cursor."""

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("move_down", "app.select(1)", show=False),
        *bind("move_up", "app.select(-1)", show=False),
        *bind("axis_start", "scroll_home", show=False),
        *bind("axis_end", "scroll_end", show=False),
    ]

    DEFAULT_CSS = """
    ValuesView { height: 1fr; scrollbar-size-vertical: 1; background: transparent; }
    ValuesView > Static { height: auto; padding: 0 1; background: transparent; }
    """

    def compose(self):
        yield Static("", markup=False)

    def show(self, m: FakeMonitor) -> None:
        width = (self.content_size.width or self.app.size.width) - 3
        self.query_one(Static).update(m.render(width))

    def reveal(self) -> None:
        reveal_marker(self, self.query_one(Static))


# -- menus ------------------------------------------------------------------------------
def menu_prompt(m: FakeMonitor, cmd: Command, width: int) -> Text:
    """One dropdown row: ``[x]``/``(•)`` for state, the label, the key right-aligned."""
    on = state(m, cmd)
    mark = {"toggle": "[x] " if on else "[ ] ", "choice": "(•) " if on else "( ) "}.get(
        cmd.kind, "    "
    )
    text = Text(mark, style="bold" if on else "dim")
    text.append(cmd.label)
    key = cmd.key_label
    text.append(" " * max(2, width - text.cell_len - len(key)))
    text.append(key, style="dim")
    return text


def menu_width(items: list[Command | None]) -> int:
    return max(4 + len(c.label) + 2 + len(c.key_label) for c in items if c is not None)


class MenuScreen(ModalScreen[tuple[str, str] | None]):
    """A dropdown under a menu title. ``←``/``→`` move to the next menu, ``esc`` closes.

    Dismisses with ``("run", command id)`` or ``("switch", menu name)``.
    """

    DEFAULT_CSS = """
    MenuScreen { align: left top; background: transparent; }
    MenuScreen OptionList {
        width: auto; height: auto; max-height: 80%; border: round $accent; padding: 0 1;
        background: $background; color: $text;
    }
    MenuScreen OptionList > .option-list--option-highlighted { text-style: reverse; }
    MenuScreen #menu-bar { dock: top; height: 1; width: 100%; background: $primary; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "close"),
        Binding("left", "switch(-1)", "prev menu", show=False),
        Binding("right", "switch(1)", "next menu", show=False),
        Binding("f10", "close", "close", show=False),
    ]

    def __init__(
        self,
        m: FakeMonitor,
        menu: str,
        x: int,
        y: int = 1,
        *,
        items=None,
        names=None,
        bar: bool = False,
    ) -> None:
        super().__init__()
        self.m, self.menu, self.x, self.y = m, menu, x, y
        self.items: list[Command | None] = list(items or MENUS[menu])
        self.names = list(names or ([menu] if items else MENUS))
        self.bar = bar

    def compose(self):
        if self.bar:
            # The menu bar exists only while a menu is open (Midnight Commander's F9).
            markup, col = "", 0
            for name in self.names:
                title = f" {name} "
                if name == self.menu:
                    self.x, self.y = col, 0
                    markup += f"[reverse]{title}[/]"
                else:
                    markup += f" [u]{name[0]}[/u]{name[1:]} "
                col += len(title)
            yield Static(markup, id="menu-bar")
        width = menu_width(self.items)
        options = [
            None
            if c is None
            else Option(menu_prompt(self.m, c, width), id=c.id, disabled=not enabled(self.m, c))
            for c in self.items
        ]
        yield OptionList(*options)

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.styles.offset = (self.x, self.y)
        menu.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(("run", event.option.id or ""))

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_switch(self, delta: int) -> None:
        if len(self.names) < 2:
            return
        i = self.names.index(self.menu)
        self.dismiss(("switch", self.names[(i + delta) % len(self.names)]))


class MenuTitle(Static):
    """A clickable menu title in a menu bar."""

    DEFAULT_CSS = (
        "MenuTitle { width: auto; padding: 0 1; } MenuTitle:hover { text-style: reverse; }"
    )

    def __init__(self, name: str, *, short: str | None = None) -> None:
        self.menu = name
        self.short = short or name
        super().__init__(f"[u]{name[0]}[/]{escape(name[1:])}")

    def on_click(self) -> None:
        self.app.open_menu(self.menu)  # type: ignore[attr-defined]

    def set_short(self, short: bool) -> None:
        label = self.short if short else self.menu
        self.update(f"[u]{label[0]}[/]{escape(label[1:])}")


# -- the app shell ----------------------------------------------------------------------
def command_bindings() -> list[BindingType]:
    """Every command's key, bound app-wide, so each direction answers the same keys."""
    out: list[BindingType] = []
    skip = {"accept", "back", "quit", "move_down", "move_up"}
    for cmd in COMMANDS.values():
        if cmd.role and cmd.role not in skip:
            out += bind(cmd.role, f"cmd('{cmd.id}')", show=False)
    return out


class MockupApp(App):
    """The shared shell: a ticking fake session, the commands, the flash, the narrow switch."""

    ENABLE_COMMAND_PALETTE = False
    TITLE = "canair monitor"

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *bind("back", "cmd('deselect')", show=False),
        Binding("ctrl+t", "toggle_theme", "theme", show=False),
        Binding("ctrl+b", "trouble", "simulate trouble", show=False),
        *command_bindings(),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.m = FakeMonitor()
        self.flash = Flash()
        self._next_tick = time.monotonic()

    def on_mount(self) -> None:
        if "ansi-dark" in self.available_themes:
            self.theme = "ansi-dark"
        self.set_interval(0.25, self._tick)
        self.apply_width(self.size.width)
        self.refresh_view()

    def on_resize(self, event: events.Resize) -> None:
        self.apply_width(event.size.width)
        self.repaint()

    def apply_width(self, width: int) -> None:
        self.base.set_class(width < NARROW, "-narrow")

    @property
    def narrow(self) -> bool:
        return self.size.width < NARROW

    @property
    def base(self):
        """The app's own screen: ``query_one`` on the app searches the top screen only, which
        is a menu or the palette while one is open."""
        return self.screen_stack[0]

    def _tick(self) -> None:
        now = time.monotonic()
        if now >= self._next_tick:
            self._next_tick = now + self.m.interval
            self.m.tick()
        self.repaint()

    def repaint(self) -> None:
        """``refresh_view`` from a timer or a resize, which can land while the DOM is torn down."""
        try:
            self.refresh_view()
        except NoMatches:
            pass

    def refresh_view(self) -> None:
        """Repaint every part of the chrome from ``self.m``; each direction overrides it."""

    def action_cmd(self, cmd_id: str) -> None:
        if cmd_id == "quit":
            self.exit()
            return
        if cmd_id == "palette" and self.ENABLE_COMMAND_PALETTE:
            self.action_command_palette()
            return
        msg, severity = run(self.m, cmd_id)
        self.show_message(msg, severity)
        self.refresh_view()

    def action_select(self, delta: int) -> None:
        self.m.move(delta)
        self.refresh_view()
        self.call_after_refresh(self.base.query_one(ValuesView).reveal)

    def action_toggle_theme(self) -> None:
        self.theme = "textual-dark" if self.theme == "ansi-dark" else "ansi-dark"

    def action_trouble(self) -> None:
        """Put the session in a bad way (``ctrl+b``): frames lost, an output on, paused."""
        m = self.m
        self.troubled = bad = not getattr(self, "troubled", False)
        m.health.drops, m.health.stale = (3, 1) if bad else (0, 0)
        m.actuators_on = 1 if bad else 0
        m.paused = bad
        if bad:
            self.show_message(
                "Session switch failed: BMS answered 7F 10 22 (conditionsNotCorrect)",
                "error",
                secs=30,
            )
        self.refresh_view()

    def show_message(self, message: str, severity: str, secs: float = 4) -> None:
        self.flash.show(message, severity, secs=secs)  # type: ignore[arg-type]

    def open_menu(
        self, name: str, *, items=None, x: int | None = None, y: int = 1, bar: bool = False
    ) -> None:
        """Drop the menu ``name`` down at ``x`` (the title's column by default)."""
        if len(self.screen_stack) > 1:
            return

        def done(result: tuple[str, str] | None) -> None:
            self.menu_open = None
            self.refresh_view()
            if result is None:
                return
            kind, value = result
            if kind == "run":
                self.action_cmd(value)
            else:
                self.open_menu(value, bar=bar)

        self.menu_open = name
        self.refresh_view()
        x = (0 if bar else self.menu_x(name)) if x is None else x
        self.push_screen(MenuScreen(self.m, name, x, y, items=items, bar=bar), done)

    def menu_x(self, name: str) -> int:
        return 0

    menu_open: str | None = None
