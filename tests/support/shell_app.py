"""A minimal app on the application shell, for the tests of ``canair.tui.shell``."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widgets import Static

from canair.tui.keys import bind
from canair.tui.shell.app import SHELL_BINDINGS, ShellMixin
from canair.tui.shell.commands import SEPARATOR, Catalogue, Command, Menu
from canair.tui.shell.contextline import ContextLine
from canair.tui.shell.menubar import MenuBar
from canair.tui.shell.messages import Messages
from canair.tui.shell.palette import ShellCommands
from canair.tui.shell.statusline import StatusLine, Zone
from canair.tui.status import P_HIGH, P_LOW, StatusItem

CATALOGUE = Catalogue(
    [
        Menu(
            "Record",
            (
                Command("label", "Label recording…", "label", role="session", help="Name it"),
                SEPARATOR,
                Command("quit", "Quit", "quit", role="quit"),
            ),
            short="Rec",
        ),
        Menu(
            "Poll",
            (
                Command("pause", "Pause polling", "toggle_pause", role="pause", kind="toggle"),
                Command("faster", "Poll faster", "faster", role="increase"),
            ),
        ),
        Menu(
            "Signal",
            (Command("edit", "Edit signal with a rather long label…", "edit", role="edit"),),
            short="Sig",
        ),
        Menu("Help", (Command("help", "Keyboard shortcuts", "noop", role="help"),)),
    ]
)


class ShellTestApp(ShellMixin, App):
    """Records the actions the commands reach; ``edit`` needs a selection it never has."""

    ENABLE_COMMAND_PALETTE = True
    COMMANDS: ClassVar = {ShellCommands}

    BINDINGS: ClassVar[list[BindingType]] = [
        *bind("quit", "quit"),
        *bind("quit_force", "quit", show=False, priority=True),
        *SHELL_BINDINGS,
        *bind("pause", "run_command('pause')"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.shell_messages = Messages()
        self.paused = False
        self.ran: list[str] = []
        self.clicked: list[str] = []

    def compose(self) -> ComposeResult:
        yield MenuBar(CATALOGUE.menus, "canair", id="menubar")
        with VerticalScroll(id="body"):
            yield Static("values")
        yield StatusLine(id="status")
        yield ContextLine(id="context")

    def on_mount(self) -> None:
        self.query_one("#body").focus()
        self.shell_menu_bar().set_document([StatusItem("[b]A recording label[/]", P_HIGH)])
        self.shell_changed()

    def shell_catalogue(self) -> Catalogue:
        return CATALOGUE

    def command_unavailable(self, cmd: Command) -> str | None:
        return "Select a signal first" if cmd.id == "edit" else None

    def command_state(self, cmd: Command) -> bool | None:
        return self.paused if cmd.id == "pause" else None

    def shell_changed(self) -> None:
        link = Zone("link", (StatusItem("ok", P_HIGH), StatusItem("home 12ms", P_LOW)))
        poll = Zone("poll", (StatusItem("LIVE", P_HIGH),))
        car = Zone("car", (StatusItem("CHARGING", P_HIGH),))
        self.query_one(StatusLine).set_zones([link, poll], car)
        self.query_one(ContextLine).set_line([StatusItem("context")], self.shell_messages.item())

    def on_status_line_zone_clicked(self, event: StatusLine.ZoneClicked) -> None:
        self.clicked.append(event.zone)

    def action_label(self) -> None:
        self.ran.append("label")

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.ran.append("pause")

    def action_faster(self) -> None:
        self.ran.append("faster")

    def action_noop(self) -> None:
        self.ran.append("noop")
