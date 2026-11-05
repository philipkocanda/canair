"""A transient status message states its severity: a failure never looks like a success."""

from __future__ import annotations

import time

import pytest

from canair.tui.flash import Flash, severity_of


def _markup(flash: Flash) -> str:
    item = flash.item()
    assert item is not None
    return item.markup


class TestFlash:
    def test_empty_shows_nothing(self):
        assert Flash().item() is None

    @pytest.mark.parametrize(
        ("severity", "colour", "glyph"),
        [("error", "red", "✗"), ("warning", "yellow", "⚠"), ("success", "green", "✓")],
    )
    def test_each_severity_has_its_colour_and_a_glyph(self, severity, colour, glyph):
        flash = Flash()
        flash.show("Something happened", severity)
        markup = _markup(flash)
        assert colour in markup
        assert glyph in markup

    def test_info_is_plain(self):
        flash = Flash()
        flash.show("View: full")
        assert _markup(flash) == "View: full"

    def test_an_error_is_never_green(self):
        flash = Flash()
        flash.show("Save failed: disk full", "error")
        assert "green" not in _markup(flash)

    def test_no_trailing_period(self):
        flash = Flash()
        flash.show("No parameter selected.", "warning")
        assert flash.message == "No parameter selected"

    def test_markup_in_the_text_is_not_parsed(self):
        flash = Flash()
        flash.show("Saved [ready] label")
        assert "\\[ready]" in _markup(flash)

    def test_expires(self):
        flash = Flash()
        flash.show("gone soon", secs=0.01)
        time.sleep(0.02)
        assert flash.item() is None
        assert flash.message == ""

    def test_stays_until_replaced_without_a_timeout(self):
        flash = Flash()
        flash.show("stays")
        assert flash.live()

    def test_clear(self):
        flash = Flash()
        flash.show("x")
        flash.clear()
        assert flash.item() is None


class TestSeverityOf:
    @pytest.mark.parametrize(
        "message",
        [
            "Save failed: boom",
            "Edit failed: boom",
            "Toggle failed: x",
            "BMS: session switch failed (timeout)",
            "Could not start new session: x",
            "Unknown ECU FOO.",
            "Invalid session mode 'zz'.",
        ],
    )
    def test_failures_are_errors(self, message):
        assert severity_of(message) == "error"

    @pytest.mark.parametrize(
        "message",
        [
            "No parameter selected.",
            "SOC: no definition to edit.",
            "No payloads captured yet — nothing to save.",
            "Start a new session needs --save (nothing is being recorded).",
        ],
    )
    def test_refusals_are_warnings(self, message):
        assert severity_of(message) == "warning"

    def test_the_rest_takes_the_caller_default(self):
        assert severity_of("Saved 3 payload(s)") == "success"
        assert severity_of("At first frame", ok="info") == "info"
