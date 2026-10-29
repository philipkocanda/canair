"""The shared :class:`CheckSelectionList` and :class:`CheckBox` widgets.

Two regressions these guard:

- a checklist must tell checked from unchecked by the glyph it draws, not by
  colour alone; and
- both glyphs must be the **same cell width**, or ticking a row shifts its text
  (a non-ASCII tick is often drawn two cells wide — see ``tui.widgets``).
"""

from __future__ import annotations

import pytest
from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets.selection_list import Selection

from canlib.tui import widgets
from canlib.tui.widgets import CheckBox, CheckSelectionList


def test_both_glyphs_are_the_same_width():
    assert cell_len(widgets._CHECKED) == cell_len(widgets._UNCHECKED)


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
    return lst.render_line(row).text[: len(widgets._CHECKED)]


class TestCheckSelectionList:
    @pytest.mark.asyncio
    async def test_checked_and_unchecked_glyphs_differ(self):
        app = _Harness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            assert _gutter(app, 0) == widgets._CHECKED
            assert _gutter(app, 1) == widgets._UNCHECKED

    @pytest.mark.asyncio
    async def test_glyph_follows_a_toggle(self):
        app = _Harness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            lst = app.query_one("#list", CheckSelectionList)
            lst.deselect("a")
            lst.select("b")
            await pilot.pause()
            assert _gutter(app, 0) == widgets._UNCHECKED
            assert _gutter(app, 1) == widgets._CHECKED


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
            assert widgets._CHECKED in app.query_one("#on", CheckBox).render().plain
            assert widgets._UNCHECKED in app.query_one("#off", CheckBox).render().plain

    @pytest.mark.asyncio
    async def test_glyph_follows_a_toggle(self):
        app = _BoxHarness()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            box = app.query_one("#off", CheckBox)
            box.toggle()
            await pilot.pause()
            assert widgets._CHECKED in box.render().plain
