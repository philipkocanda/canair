"""Tests for canlib.tui_status — the width-aware TUI status/hint bar.

The bar exists because every canair TUI docks a long ``… · ? help · q quit`` line
at a fixed height: on a narrow terminal (a phone SSH client at ~45 columns) Rich
soft-wrapped it and the fixed height clipped the overflow, silently hiding the
pointer to the ``?`` cheat-sheet. These tests pin the two guarantees that fixes:
the line always fits, and the essentials are the last thing dropped.
"""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from canlib.tui_status import (
    P_ESSENTIAL,
    P_HIGH,
    P_LOW,
    P_NORMAL,
    StatusBar,
    StatusItem,
    compose_status,
)


def _visible(markup: str) -> str:
    return Text.from_markup(markup).plain


class TestStatusItem:
    def test_width_excludes_markup(self):
        assert StatusItem("[dim]q quit[/]").width == len("q quit")

    def test_default_priority_is_normal(self):
        assert StatusItem("x").priority == P_NORMAL


class TestComposeStatus:
    ITEMS = (
        StatusItem("cycle 12", P_NORMAL),
        StatusItem("3 cmds", P_LOW),
        StatusItem("captured 40", P_NORMAL),
        StatusItem("state READY", P_HIGH),
        StatusItem("? help", P_ESSENTIAL),
        StatusItem("q quit", P_ESSENTIAL),
    )

    def test_everything_fits_on_a_wide_terminal(self):
        out = _visible(compose_status(list(self.ITEMS), 200))
        assert out == "cycle 12 · 3 cmds · captured 40 · state READY · ? help · q quit"

    def test_never_exceeds_the_width(self):
        for width in range(10, 80):
            assert len(_visible(compose_status(list(self.ITEMS), width))) <= max(
                width, len("captured 40")
            )

    def test_lowest_priority_drops_first(self):
        out = _visible(compose_status(list(self.ITEMS), 60))
        assert "3 cmds" not in out
        assert "state READY" in out

    def test_essentials_survive_a_phone_width(self):
        out = _visible(compose_status(list(self.ITEMS), 45))
        assert "? help" in out and "q quit" in out

    def test_one_item_always_survives(self):
        # Even a width that fits nothing keeps the single most essential item, so
        # the bar is never blank (the widget's ellipsis handles the overflow).
        out = _visible(compose_status(list(self.ITEMS), 1))
        assert out in {"? help", "q quit"}

    def test_ties_drop_right_to_left(self):
        items = [StatusItem("aaaa"), StatusItem("bbbb"), StatusItem("cccc")]
        assert _visible(compose_status(items, 11)) == "aaaa · bbbb"

    def test_empty_items_are_ignored(self):
        items = [StatusItem(""), StatusItem("kept"), StatusItem("")]
        assert _visible(compose_status(items, 80)) == "kept"

    def test_no_items_is_empty(self):
        assert compose_status([], 80) == ""


class _BarApp(App):
    """Minimal host for the bar (it needs a real layout to know its width)."""

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        self.query_one("#status", StatusBar).set_lines(
            [
                StatusItem("a long contextual segment about the run", P_LOW),
                StatusItem("? help", P_ESSENTIAL),
                StatusItem("q quit", P_ESSENTIAL),
            ]
        )


class TestStatusBarWidget:
    @pytest.mark.asyncio
    async def test_narrow_terminal_keeps_the_help_pointer(self):
        app = _BarApp()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status", StatusBar)
            plain = bar.render().plain
            assert "? help" in plain and "q quit" in plain
            assert "long contextual" not in plain  # shed, not clipped
            # One line: nothing wrapped out of the docked region.
            assert len(plain.splitlines()) == 1

    @pytest.mark.asyncio
    async def test_widening_brings_the_detail_back(self):
        app = _BarApp()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status", StatusBar)
            assert "long contextual" not in bar.render().plain
            await pilot.resize_terminal(120, 10)
            await pilot.pause()
            assert "long contextual" in bar.render().plain
