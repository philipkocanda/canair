"""The mixin that puts an app on the shell: menus, palette, messages, the keys that reach them.

Mix :class:`ShellMixin` into an ``App`` before ``App``, list :data:`SHELL_BINDINGS` in the app's
``BINDINGS`` (Textual merges bindings from widget classes only, so a mixin cannot declare them),
set ``COMMANDS = {ShellCommands}`` and ``ENABLE_COMMAND_PALETTE = True``, compose a
:class:`~.menubar.MenuBar`, and implement:

* :meth:`ShellMixin.shell_catalogue` — the app's menus;
* :meth:`ShellMixin.command_unavailable` and :meth:`ShellMixin.command_state` — whether a
  command can run now, and whether a toggle is on;
* :meth:`ShellMixin.shell_changed` — repaint the bars after a message or a menu closes.

A command runs its app action through :meth:`ShellMixin.run_command`, from a menu, the palette or
the app's own key, so all three refuse an unavailable command the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from canair.tui.flash import Severity
from canair.tui.keys import bind

from .commands import Catalogue, Command
from .menubar import MenuBar, MenuResult, MenuScreen
from .messages import Messages

#: The shell's own keys, for the app's ``BINDINGS``.
SHELL_BINDINGS: list[Binding] = [*bind("menu", "menu"), *bind("palette", "command_palette")]


class ShellMixin:
    """Menus, the palette and the message policy for an app (see the module docstring)."""

    #: The name the menu bar shows on its left on a wide terminal.
    SHELL_NAME: ClassVar[str] = "canair"

    shell_messages: Messages

    if TYPE_CHECKING:

        def call_next(self, callback, *args) -> None: ...

    @property
    def _app(self) -> App:
        assert isinstance(self, App)
        return self

    # -- what the app supplies ------------------------------------------------------------
    def shell_catalogue(self) -> Catalogue:
        raise NotImplementedError

    def command_unavailable(self, cmd: Command) -> str | None:
        return None

    def command_state(self, cmd: Command) -> bool | None:
        return None

    def shell_changed(self) -> None:
        """Repaint whatever shows messages or menu state. The app overrides it."""

    # -- messages ---------------------------------------------------------------------------
    def message(self, text: str, severity: Severity = "info", *, secs: float | None = None) -> None:
        """Show ``text`` on the bottom line (an error until the next key) and keep it in the log."""
        self.shell_messages.show(text, severity, secs=secs)
        self.shell_changed()

    # -- commands ---------------------------------------------------------------------------
    async def run_command(self, cmd_id: str) -> None:
        """Run a command's action, or say why it cannot run now."""
        cmd = self.shell_catalogue()[cmd_id]
        why = self.command_unavailable(cmd)
        if why is not None:
            self.message(why, "warning")
            return
        await self._app.run_action(cmd.action)

    async def action_run_command(self, cmd_id: str) -> None:
        await self.run_command(cmd_id)

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """The palette lists the app's commands only, not Textual's theme and quit entries."""
        return ()

    # -- menus ------------------------------------------------------------------------------
    def shell_menu_bar(self) -> MenuBar:
        # The app's own screen, not the top one: a menu or the palette may be open above it.
        return self._app.screen_stack[0].query_one(MenuBar)

    def menu_open(self) -> bool:
        return len(self._app.screen_stack) > 1

    def action_menu(self) -> None:
        self.open_menu(self.shell_catalogue().menus[0].title)

    def open_menu(self, title: str, *, x: int | None = None, y: int | None = None) -> None:
        """Drop the menu ``title`` down under its title, or at ``x``/``y`` when given."""
        if self.menu_open():
            return
        catalogue = self.shell_catalogue()
        menu = catalogue.menu(title)
        bar = self.shell_menu_bar()
        if x is None:
            x = bar.title_widget(title).region.x
        if y is None:
            y = bar.region.y + 1
        bar.mark_open(title)

        def done(result: MenuResult | None) -> None:
            bar.mark_open(None)
            self.shell_changed()
            if result is None:
                return
            kind, value = result
            if kind == "run":
                self.call_next(self.run_command, value)
            else:
                self.call_next(self.open_menu, value)

        self._app.push_screen(
            MenuScreen(
                menu,
                catalogue.menus,
                x=x,
                y=y,
                unavailable=self.command_unavailable,
                state=self.command_state,
            ),
            done,
        )

    def open_menu_above(self, title: str, *, x: int, bottom: int) -> None:
        """Open a menu so that it ends just above row ``bottom`` (a status zone's row)."""
        rows = len(self.shell_catalogue().menu(title).items) + 2  # the border
        self.open_menu(title, x=x, y=max(1, bottom - rows))

    def on_menu_bar_open(self, message: MenuBar.Open) -> None:
        self.open_menu(message.title)

    def on_key(self, event: events.Key) -> None:
        """Any key drops an error that was waiting for one; ``alt+letter`` opens its menu."""
        if self.shell_messages.key_pressed():
            self.shell_changed()
        if self.menu_open() or not (event.key.startswith("alt+") and len(event.key) == 5):
            return
        menu = self.shell_catalogue().by_mnemonic(event.key[-1])
        if menu is not None:
            event.stop()
            event.prevent_default()
            self.open_menu(menu.title)
