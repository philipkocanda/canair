"""The shared :class:`CheckSelectionList` widget.

The regression it guards: a checklist must tell checked from unchecked by the
glyph it draws, not by colour alone.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets.selection_list import Selection

from canlib.tui.widgets import CheckBox, CheckSelectionList


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield CheckSelectionList(
            Selection("alpha", "a", True),
            Selection("beta", "b", False),
            id="list",
        )


def _gutter(app: _Harness, row: int) -> str:
    """The leading checkbox glyph rendered on ``row``."""
    lst = app.query_one("#list", CheckSelectionList)
    return lst.render_line(row).text[:3]


class TestCheckSelectionList:
    @pytest.mark.asyncio
    async def test_checked_and_unchecked_glyphs_differ(self):
        app = _Harness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            assert _gutter(app, 0) == "[✔]"
            assert _gutter(app, 1) == "[ ]"

    @pytest.mark.asyncio
    async def test_glyph_follows_a_toggle(self):
        app = _Harness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            lst = app.query_one("#list", CheckSelectionList)
            lst.deselect("a")
            lst.select("b")
            await pilot.pause()
            assert _gutter(app, 0) == "[ ]"
            assert _gutter(app, 1) == "[✔]"


class _BoxHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield CheckBox("on-box", value=True, id="on")
        yield CheckBox("off-box", value=False, id="off")


class TestCheckBox:
    @pytest.mark.asyncio
    async def test_on_and_off_glyphs_differ(self):
        app = _BoxHarness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            assert "[✔]" in app.query_one("#on", CheckBox).render().plain
            assert "[ ]" in app.query_one("#off", CheckBox).render().plain

    @pytest.mark.asyncio
    async def test_glyph_follows_a_toggle(self):
        app = _BoxHarness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            box = app.query_one("#off", CheckBox)
            box.toggle()
            await pilot.pause()
            assert "[✔]" in box.render().plain
