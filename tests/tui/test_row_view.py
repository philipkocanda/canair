"""The live row table shared by the io and routines views (canair.tui.row_view)."""

from __future__ import annotations

import pytest
from rich.text import Text

from canair.tui.apps.iocontrol.app import IOControlApp
from canair.tui.row_view import LiveTable, undimmed
from tests.support.iocontrol import io_controller


def test_undimmed_brightens_the_base_style_and_the_spans():
    text = Text("6FBC0103", style="dim")
    text.stylize("dim cyan", 0, 2)
    out = undimmed(text)
    assert out.plain == text.plain
    assert not out.style.dim  # type: ignore[union-attr]
    assert all(not span.style.dim for span in out.spans)  # type: ignore[union-attr]
    assert out.spans[0].style.color.name == "cyan"  # type: ignore[union-attr]


def _styles(table: LiveTable, y: int) -> dict[str, str]:
    return {s.text.strip(): str(s.style) for s in table.render_line(y) if s.text.strip()}


@pytest.mark.asyncio
async def test_the_cursor_row_is_bright_on_a_dark_bar_and_the_others_keep_their_dim():
    app = IOControlApp(io_controller())
    async with app.run_test(size=(100, 12)) as pilot:
        await pilot.pause(0.2)
        table = app.query_one(LiveTable)
        cursor, other = _styles(table, 1), _styles(table, 2)
        assert "dim" not in " ".join(s.replace("not dim", "") for s in cursor.values())
        assert all("on color(8)" in s for s in cursor.values())
        assert "bright" not in cursor["BC01"] and "cyan" in cursor["BC01"]
        assert "dim" in other["·"]
        # Moving the cursor dims the row it left and brightens the one it reached.
        await pilot.press("j")
        await pilot.pause(0.2)
        assert "on color(8)" not in _styles(table, 1)["BC01"]
        assert "not dim" in _styles(table, 2)["·"]
        await pilot.press("q")
