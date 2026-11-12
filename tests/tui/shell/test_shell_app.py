"""The application shell in an app: the menu bar, the dropdowns, the palette, messages, clicks."""

from canair.tui.shell.menubar import MenuBar, MenuScreen, MenuTitle
from canair.tui.shell.statusline import StatusLine
from tests.support.shell_app import ShellTestApp


def _plain(widget) -> str:
    rendered = widget.render()
    return rendered.plain if hasattr(rendered, "plain") else str(rendered)


def _titles(app) -> list[str]:
    return [_plain(t) for t in app.query(MenuTitle)]


class TestMenuBarLadder:
    async def test_a_wide_bar_shows_the_name_titles_and_document(self):
        app = ShellTestApp()
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            assert _titles(app) == ["Record", "Poll", "Signal", "Help"]
            assert app.query_one("#menu-name").display
            assert "A recording label" in _plain(app.query_one("#menu-doc"))

    async def test_the_bar_never_drops_a_title(self):
        for width, expected in [
            (30, ["Record", "Poll", "Signal", "Help"]),
            (22, ["Rec", "Poll", "Sig", "Help"]),
            (12, ["R", "P", "S", "H"]),
        ]:
            app = ShellTestApp()
            async with app.run_test(size=(width, 12)) as pilot:
                await pilot.pause()
                assert _titles(app) == expected, width
                assert not app.query_one("#menu-name").display
                last = list(app.query(MenuTitle))[-1]
                assert last.region.right <= width, width


class TestMenus:
    async def test_f10_opens_the_first_menu_and_arrows_move_between_menus(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("f10")
            await pilot.pause()
            assert isinstance(app.screen, MenuScreen) and app.screen.menu.title == "Record"
            await pilot.press("right")
            await pilot.pause()
            assert app.screen.menu.title == "Poll"
            assert app.query_one(MenuBar).title_widget("Poll").has_class("-open")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, MenuScreen)
            assert not app.query_one(MenuBar).title_widget("Poll").has_class("-open")

    async def test_enter_runs_the_item_through_its_action(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("alt+p")
            await pilot.pause()
            assert app.screen.menu.title == "Poll"
            await pilot.press("enter")
            await pilot.pause()
            assert app.ran == ["pause"] and app.paused

    async def test_a_click_on_a_title_opens_its_menu(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.click(MenuTitle, offset=(1, 0))
            await pilot.pause()
            assert isinstance(app.screen, MenuScreen) and app.screen.menu.title == "Record"

    async def test_an_unavailable_command_is_disabled_and_refused_with_its_reason(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("alt+s")
            await pilot.pause()
            from textual.widgets import OptionList

            assert app.screen.query_one(OptionList).get_option_at_index(0).disabled
            await pilot.press("escape")
            await app.run_command("edit")
            await pilot.pause()
            assert "Select a signal first" in _plain(app.query_one("#context"))

    async def test_a_dropdown_stays_on_a_small_screen(self):
        app = ShellTestApp()
        async with app.run_test(size=(24, 14)) as pilot:
            await pilot.press("alt+s")
            await pilot.pause()
            from textual.widgets import OptionList

            options = app.screen.query_one(OptionList)
            assert options.region.right <= 24
            assert options.border_title == "Signal"


class TestPalette:
    async def test_the_palette_finds_and_runs_a_command(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause(0.2)
            await pilot.press(*"faster")
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.ran == ["faster"]


class TestMessagesAndClicks:
    async def test_an_error_stays_until_a_key_and_then_goes(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            app.message("Session switch failed", "error")
            await pilot.pause()
            assert "✗ Session switch failed" in _plain(app.query_one("#context"))
            await pilot.press("j")
            await pilot.pause()
            assert "Session switch failed" not in _plain(app.query_one("#context"))
            assert len(app.shell_messages.history) == 1

    async def test_a_click_on_a_zone_names_the_zone(self):
        app = ShellTestApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.click(StatusLine, offset=(2, 0))
            await pilot.click(StatusLine, offset=(78, 0))
            await pilot.pause()
            assert app.clicked == ["link", "car"]

    async def test_a_narrow_status_takes_two_lines(self):
        app = ShellTestApp()
        async with app.run_test(size=(45, 20)) as pilot:
            await pilot.pause()
            assert len(_plain(app.query_one(StatusLine)).splitlines()) == 2
