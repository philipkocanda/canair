"""Tests for the shared Textual help modal (canlib.tui_help).

Unit-covers the BINDINGS->rows derivation (including key-label rendering for
every shipped TUI) and drives the ``?`` help modal headlessly.
"""

from __future__ import annotations

import re
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

    @pytest.mark.parametrize(
        ("key", "shown"),
        [
            ("right_square_bracket", "]"),
            ("left_square_bracket", "["),
            ("greater_than_sign", ">"),
            ("less_than_sign", "<"),
            ("colon", ":"),
            ("equals_sign", "="),
            ("plus", "+"),
            ("minus", "-"),
            ("underscore", "_"),
            ("comma", ","),
            ("full_stop", "."),
            ("question_mark", "?"),
            ("up", "↑"),
            ("escape", "esc"),
        ],
    )
    def test_symbolic_key_names_render_as_symbols(self, key, shown):
        """Regression: symbolic bindings used to leak their raw Textual identifier
        (``]`` showed as ``right_square_bracket``) because the label came from a
        hand-written table that only listed a few names."""

        class _Fake:
            BINDINGS: ClassVar = [Binding(key, "noop", "do a thing")]

        assert bindings_help_rows(_Fake()) == [(shown, "do a thing")]

    def test_modifier_keys_pass_through(self):
        class _Fake:
            BINDINGS: ClassVar = [Binding("shift+tab", "noop", "prev")]

        assert bindings_help_rows(_Fake()) == [("shift+tab", "prev")]


# A displayed key should never look like a raw Textual identifier. Every real
# label is either a symbol, a single letter/digit, or a short word (space, tab,
# esc, pgup) — none of which contain an underscore.
_RAW_IDENTIFIER = re.compile(r"[a-z]{2,}_[a-z]{2,}")


def _canair_tui_apps():
    from canlib.commands._captures_step_tui import CapturesStepApp
    from canlib.commands._decode_plot_tui import PlotApp
    from canlib.commands._sniff_tui import SniffApp
    from canlib.modes._monitor_tui import MonitorApp

    return [CapturesStepApp, PlotApp, MonitorApp, SniffApp]


class TestRealAppsHaveReadableHelp:
    """Guards every shipped TUI's cheat-sheet, so a new symbolic binding can't
    silently reintroduce a raw key identifier."""

    def test_no_raw_key_identifiers_in_any_app(self):
        offenders = []
        for app in _canair_tui_apps():
            for keys, desc in bindings_help_rows(app.__new__(app)):
                if _RAW_IDENTIFIER.search(keys):
                    offenders.append(f"{app.__name__}: {keys!r} ({desc})")
        assert offenders == []

    def test_every_app_advertises_some_bindings(self):
        for app in _canair_tui_apps():
            rows = bindings_help_rows(app.__new__(app))
            assert rows, f"{app.__name__} advertises no bindings"
            assert all(desc for _keys, desc in rows)

    def test_step_app_shows_its_symbolic_keys(self):
        from canlib.commands._captures_step_tui import CapturesStepApp

        rows = {
            desc: keys
            for keys, desc in bindings_help_rows(CapturesStepApp.__new__(CapturesStepApp))
        }
        assert rows["+100 frames"] == "]"
        assert rows["-100 frames"] == "["
        assert rows["goto frame"] == ":"
        assert rows["wider tolerance"] == ">"
        assert rows["tighter tolerance"] == "<"
        assert rows["next frame"] == "→/l/n/space"


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
