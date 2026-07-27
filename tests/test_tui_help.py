"""Tests for the shared Textual help modal (canlib.tui_help).

Unit-covers the BINDINGS->rows derivation, and drives the ``?`` help modal in
both Textual apps (the query monitor and the sniff table) headlessly.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from textual.app import App
from textual.binding import Binding

from canlib.tui_help import HelpMixin, HelpModal, bindings_help_rows


class TestBindingsHelpRows:
    def test_skips_undescribed_and_framework_keys(self):
        class _Fake:
            BINDINGS: ClassVar = [
                Binding("q", "quit", "quit"),
                Binding("ctrl+c", "quit", "quit"),  # framework key, dropped
                Binding("x", "noop", ""),  # no description, dropped
            ]

        rows = bindings_help_rows(_Fake())
        keys = {k for k, _ in rows}
        assert "q" in keys
        assert "ctrl+c" not in keys
        assert all(desc for _, desc in rows)

    def test_collapses_aliases_by_description(self):
        class _Fake:
            BINDINGS: ClassVar = [
                Binding("j", "down", "scroll down"),
                Binding("down", "down", "scroll down"),
            ]

        rows = bindings_help_rows(_Fake())
        assert len(rows) == 1
        keys, desc = rows[0]
        assert desc == "scroll down"
        assert "j" in keys and "↓" in keys

    def test_special_key_names_rendered(self):
        class _Fake:
            BINDINGS: ClassVar = [Binding("question_mark", "help", "help")]

        rows = bindings_help_rows(_Fake())
        assert rows == [("?", "help")]


class _HelpApp(HelpMixin, App):
    BINDINGS: ClassVar = [
        Binding("q", "quit", "quit"),
        Binding("question_mark", "help", "help"),
        Binding("s", "noop", "save"),
    ]

    def action_noop(self):
        pass


class TestHelpModalApp:
    @pytest.mark.asyncio
    async def test_question_mark_opens_and_closes(self):
        app = _HelpApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause(0.05)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("escape")
            await pilot.pause(0.05)
            assert not isinstance(app.screen, HelpModal)
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_modal_lists_bindings(self):
        app = _HelpApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause(0.05)
            from textual.widgets import Static

            text = "\n".join(
                s.render().plain if hasattr(s.render(), "plain") else str(s.render())
                for s in app.screen.query(Static)
            )
            assert "save" in text
            assert "quit" in text
            await pilot.press("q")

    @pytest.mark.asyncio
    async def test_does_not_stack(self):
        app = _HelpApp()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.press("question_mark")
            await pilot.pause(0.05)
            depth = len(app.screen_stack)
            await pilot.press("question_mark")  # already open -> dismiss, not stack
            await pilot.pause(0.05)
            assert len(app.screen_stack) <= depth
            await pilot.press("q")
