"""Integration tests for the sniff Textual app (canlib.commands._sniff_tui)."""

from __future__ import annotations

import pytest

from canlib.commands._sniff_tui import SniffApp
from canlib.commands.sniff import SniffStats


def _plain(renderable) -> str:
    return renderable.plain if hasattr(renderable, "plain") else str(renderable)


class TestSniffApp:
    @pytest.mark.asyncio
    async def test_help_modal_opens_with_question_mark(self):
        from canlib.tui_help import HelpModal

        app = SniffApp(SniffStats(), host="10.0.0.1")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("question_mark")
            await pilot.pause(0.1)
            assert isinstance(app.screen, HelpModal)
            from textual.widgets import Static

            text = "\n".join(_plain(s.render()) for s in app.screen.query(Static))
            assert "clear" in text and "quit" in text
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, HelpModal)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_status_line_advertises_help(self):
        app = SniffApp(SniffStats(), host="10.0.0.1")
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.1)
            assert "? help" in _plain(app.query_one("#status").render())
            await pilot.press("q")
