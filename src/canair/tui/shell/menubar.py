"""The menu bar, the width ladder of its titles, and the dropdown a title opens.

The menu bar never drops a title. As the terminal narrows it steps down through
:data:`LADDER`: the app's name and its document title go first, then the space between titles,
then the titles shorten (``Record`` → ``Rec``) and finally become one letter each. Every menu
stays one click, one ``f10`` or one ``alt+letter`` away on any screen down to 12 columns. The
document title (a monitor's recording label) takes whatever the titles leave.

A dropdown (:class:`MenuScreen`) draws each item with its key on the right, ``[x]`` for a toggle
and ``(•)`` for a choice, and disables what cannot run now. It stays inside the screen: on a screen
narrower than the menu, a label gives way (``…``), never its key, and the border names the menu.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar, Literal

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from canair.render.glyphs import ELLIPSIS
from canair.tui.keys import DISMISS_DESCRIPTION, bind
from canair.tui.modals import KitModal
from canair.tui.status import StatusItem, compose_status

from .commands import Command, Menu, MenuItem

TitleForm = Literal["full", "short", "letter"]

#: The forms the bar steps down through: (title form, columns per title beyond its label, whether
#: the app name shows). The first that fits the terminal wins.
LADDER: tuple[tuple[TitleForm, int, bool], ...] = (
    ("full", 2, True),
    ("full", 2, False),
    ("full", 1, False),
    ("short", 1, False),
    ("letter", 1, False),
)

#: The columns the app name takes, with its padding.
_NAME_PAD = 2
#: The columns the document title should keep before the app name gives way to it.
TITLE_ROOM = 24


class MenuTitle(Static):
    """One clickable title. The underlined first letter is its ``alt`` mnemonic."""

    DEFAULT_CSS = """
    MenuTitle { width: auto; padding: 0 1; }
    MenuTitle:hover, MenuTitle.-open { text-style: reverse; }
    """

    def __init__(self, menu: Menu) -> None:
        super().__init__("")
        self.menu = menu
        self.show_form("full", 2)

    def label(self, form: TitleForm) -> str:
        title = self.menu.title
        return {"full": title, "short": self.menu.short or title, "letter": title[0]}[form]

    def show_form(self, form: TitleForm, pad: int) -> None:
        label = self.label(form)
        self.styles.padding = (0, 1) if pad == 2 else (0, 0, 0, 1)
        self.update(f"[u]{escape(label[0])}[/u]{escape(label[1:])}")

    def on_click(self) -> None:
        self.post_message(MenuBar.Open(self.menu.title, self))


class MenuBar(Horizontal):
    """The app name, the menu titles and the document title, on one row."""

    DEFAULT_CSS = """
    MenuBar { height: 1; background: $primary; color: $text; }
    MenuBar #menu-name { width: auto; padding: 0 1; text-style: bold; }
    MenuBar #menu-doc {
        width: 1fr; padding: 0 1; content-align: right middle; text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    class Open(Message):
        """A title asks for its menu (a click)."""

        def __init__(self, title: str, widget: MenuTitle) -> None:
            super().__init__()
            self.title = title
            self.widget = widget

    def __init__(self, menus: Sequence[Menu], name: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._menus = tuple(menus)
        self._app_name = name
        self._doc: list[StatusItem] = []

    def compose(self) -> ComposeResult:
        yield Static(escape(self._app_name), id="menu-name")
        for menu in self._menus:
            yield MenuTitle(menu)
        yield Static("", id="menu-doc")

    def titles(self) -> list[MenuTitle]:
        return list(self.query(MenuTitle))

    def title_widget(self, title: str) -> MenuTitle:
        return next(t for t in self.titles() if t.menu.title == title)

    def set_document(self, items: Sequence[StatusItem]) -> None:
        """The document title (most important item first), fitted into what the titles leave."""
        self._doc = list(items)
        self.fit()

    def mark_open(self, title: str | None) -> None:
        for widget in self.titles():
            widget.set_class(widget.menu.title == title, "-open")

    def on_resize(self) -> None:
        self.fit()

    def fit(self) -> None:
        """Pick the widest ladder step that fits, and give the rest to the document title."""
        if not self.is_mounted:
            return
        width = self.size.width or (self.app.size.width if self.is_attached else 80)
        titles = self.titles()
        name_w = len(self._app_name) + _NAME_PAD
        for form, pad, with_name in LADDER:
            needed = sum(len(t.label(form)) + pad for t in titles)
            if with_name:
                needed += name_w + TITLE_ROOM
            if needed <= width:
                break
        for t in titles:
            t.show_form(form, pad)
        self.query_one("#menu-name").display = with_name
        used = sum(len(t.label(form)) + pad for t in titles) + (name_w if with_name else 0)
        room = max(0, width - used - 2)
        doc = self.query_one("#menu-doc", Static)
        doc.update(compose_status(self._doc, room) if room >= 4 else "")


def menu_row(cmd: Command, state: bool | None, width: int) -> Text:
    """One dropdown row: the state mark, the label, the key right-aligned in ``width``."""
    if cmd.kind == "toggle":
        mark = "[x] " if state else "[ ] "
    elif cmd.kind == "choice":
        mark = "(•) " if state else "( ) "
    else:
        mark = "    "
    key = cmd.key_label
    label = cmd.label
    gap = 2 if key else 0
    room = width - len(mark) - len(key) - gap
    if len(label) > room:
        label = label[: max(1, room - 1)] + ELLIPSIS
    text = Text(mark, style="bold" if state else "dim")
    text.append(label)
    text.append(" " * max(gap, width - text.cell_len - len(key)))
    text.append(key, style="dim")
    return text


def menu_width(items: Sequence[MenuItem]) -> int:
    """The width the menu's rows want: the mark, the longest label, two spaces, the key."""
    return max((4 + len(c.label) + 2 + len(c.key_label) for c in items if c is not None), default=8)


#: The columns a dropdown spends on its border and padding.
_DROPDOWN_FRAME = 4

MenuResult = tuple[Literal["run", "switch"], str]


class MenuScreen(KitModal["MenuResult | None"]):
    """A dropdown under a menu title (or above a status zone).

    Dismisses with ``("run", command id)``, ``("switch", menu title)`` when ``←``/``→`` or an
    ``alt`` mnemonic moves to another menu, or ``None`` when closed.
    """

    DEFAULT_CSS = """
    MenuScreen { align: left top; background: transparent; }
    MenuScreen > OptionList {
        width: auto; height: auto; max-height: 90%; padding: 0 1; border: round $accent;
        background: $background; color: $text;
    }
    MenuScreen > OptionList > .option-list--option-highlighted { text-style: reverse; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("back", "close", desc=DISMISS_DESCRIPTION),
        *bind("menu", "close", desc=DISMISS_DESCRIPTION),
        *bind("prev", "switch(-1)", desc="prev"),
        *bind("next", "switch(1)", desc="next"),
    ]

    def __init__(
        self,
        menu: Menu,
        menus: Sequence[Menu],
        *,
        x: int,
        y: int,
        unavailable: Callable[[Command], str | None],
        state: Callable[[Command], bool | None],
    ) -> None:
        super().__init__()
        self.menu = menu
        self.menus = tuple(menus)
        self.x, self.y = x, y
        self._unavailable = unavailable
        self._state = state
        self._rows_width = menu_width(menu.items)

    def compose(self) -> ComposeResult:
        self._rows_width = min(self._rows_width, max(8, self.app.size.width - _DROPDOWN_FRAME))
        options: list[Option | None] = [
            None
            if item is None
            else Option(
                menu_row(item, self._state(item), self._rows_width),
                id=item.id,
                disabled=self._unavailable(item) is not None,
            )
            for item in self.menu.items
        ]
        yield OptionList(*options)

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        x = max(0, min(self.x, self.app.size.width - (self._rows_width + _DROPDOWN_FRAME)))
        options.styles.offset = (x, self.y)
        options.border_title = escape(self.menu.title)
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(("run", event.option.id or ""))

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key.startswith("alt+") and len(event.key) == 5:
            target = next((m for m in self.menus if m.mnemonic == event.key[-1]), None)
            if target is not None:
                event.stop()
                self.dismiss(("switch", target.title))

    def action_close(self) -> None:
        self.dismiss(None)

    def action_switch(self, delta: int) -> None:
        if len(self.menus) < 2 or self.menu not in self.menus:
            return
        i = self.menus.index(self.menu)
        self.dismiss(("switch", self.menus[(i + delta) % len(self.menus)].title))
