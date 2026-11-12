"""The application shell's pieces without an app: the catalogue, messages and line layout."""

import pytest

from canair.tui.shell.commands import SEPARATOR, Catalogue, Command, Menu
from canair.tui.shell.contextline import P_MESSAGE, routes
from canair.tui.shell.menubar import menu_row, menu_width
from canair.tui.shell.messages import Messages
from canair.tui.shell.statusline import Span, Zone, badge, fit_line, lay_out
from canair.tui.status import P_ESSENTIAL, P_HIGH, P_LOW, StatusItem


def _plain(markup: str) -> str:
    from rich.text import Text

    return Text.from_markup(markup).plain


class TestCatalogue:
    def test_a_command_takes_its_key_from_its_role(self):
        assert Command("p", "Pause", "pause", role="pause").key_label == "space"
        assert Command("x", "Legend", "legend").key_label == ""

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(ValueError, match="unknown key role"):
            Command("x", "X", "x", role="no_such_role")

    def test_two_menus_cannot_share_a_mnemonic(self):
        with pytest.raises(ValueError, match="mnemonic"):
            Catalogue([Menu("Poll", ()), Menu("Panes", ())])

    def test_a_command_sits_in_one_menu(self):
        cmd = Command("x", "X", "x")
        with pytest.raises(ValueError, match="two menus"):
            Catalogue([Menu("A", (cmd,)), Menu("B", (cmd,))])

    def test_lookups(self):
        cmd = Command("x", "X", "x")
        cat = Catalogue([Menu("View", (cmd, SEPARATOR))])
        assert cat["x"] is cmd and "x" in cat and "y" not in cat
        assert cat.by_mnemonic("V").title == "View"
        assert cat.by_mnemonic("q") is None
        assert [c.id for _m, c in cat.commands()] == ["x"]


class TestMessages:
    def test_an_error_stays_until_the_next_key(self):
        m = Messages()
        m.show("Session switch failed.", "error")
        assert m.current == "Session switch failed"  # no trailing period
        assert m.current  # still there: no expiry
        assert m.key_pressed() is True
        assert m.current == ""
        assert m.key_pressed() is False

    def test_other_messages_expire_and_ignore_keys(self):
        m = Messages()
        m.show("View: ranges", "info", secs=0)
        assert m.current == ""
        m.show("Saved", "success", secs=60)
        assert m.key_pressed() is False
        assert m.current == "Saved"

    def test_every_message_is_kept_with_its_severity_glyph(self):
        m = Messages()
        m.show("Saved", "success", secs=0)
        m.show("Lost 3 frames", "error")
        lines = [_plain(entry.markup()) for entry in m.history]
        assert lines[0].endswith("✓ Saved")
        assert lines[1].endswith("✗ Lost 3 frames")


def _zone(name: str, *items: tuple[str, int]) -> Zone:
    return Zone(name, tuple(StatusItem(text, prio) for text, prio in items))


class TestStatusLayout:
    def test_everything_fits_with_separators_and_a_pinned_right_zone(self):
        link = _zone("link", ("ok", P_HIGH), ("home", P_LOW))
        poll = _zone("poll", ("LIVE", P_HIGH))
        car = _zone("car", ("CHARGING", P_HIGH))
        text, spans = lay_out([link, poll], car, 40)
        plain = _plain(text)
        assert plain.startswith("ok home │ LIVE")
        assert plain.endswith("CHARGING") and len(plain) == 40
        assert spans == [Span(0, 7, "link"), Span(10, 14, "poll"), Span(32, 40, "car")]

    def test_the_least_important_fact_on_the_whole_line_goes_first(self):
        link = _zone("link", ("ok", P_HIGH), ("home 12ms wican-ws", P_LOW))
        poll = _zone("poll", ("PAUSED", P_ESSENTIAL))
        text, _spans = lay_out([link, poll], _zone("car", ("CHARGING", P_HIGH)), 30)
        plain = _plain(text)
        assert "home" not in plain
        assert "PAUSED" in plain and "CHARGING" in plain

    def test_a_zone_that_lost_every_fact_leaves_no_separator(self):
        link = _zone("link", ("home 12ms", P_LOW))
        poll = _zone("poll", ("LIVE", P_HIGH))
        text, spans = lay_out([link, poll], None, 6)
        assert _plain(text) == "LIVE"
        assert [s.zone for s in spans] == ["poll"]

    def test_a_badge_is_a_reversed_block(self):
        assert badge("PAUSED", "yellow") == "[reverse yellow] PAUSED [/]"

    def test_fit_line_keeps_the_right_side_pinned(self):
        line = fit_line([StatusItem("left", P_LOW)], [StatusItem("right", P_HIGH)], 20)
        assert _plain(line) == "left" + " " * 11 + "right"

    def test_fit_line_drops_by_priority(self):
        left = [StatusItem("a message that is long", P_MESSAGE)]
        line = _plain(fit_line(left, routes(), 40))
        assert line.startswith("a message that is long")
        assert line.endswith("? help")
        assert "f10" not in line  # the routes give way to the message first


class TestMenuRows:
    def test_a_row_shows_state_label_and_key(self):
        toggle = Command("r", "Byte ruler", "r", role="rulers", kind="toggle")
        row = menu_row(toggle, True, menu_width([toggle]))
        assert row.plain == "[x] Byte ruler  r"
        choice = Command("v", "Signals", "v", kind="choice")
        assert menu_row(choice, False, 12).plain.startswith("( ) Signals")

    def test_a_narrow_row_shortens_the_label_and_keeps_the_key(self):
        cmd = Command("p", "Select ECUs and PIDs…", "p", role="pick")
        row = menu_row(cmd, None, 16)
        assert row.plain.endswith("p")
        assert "…" in row.plain
        assert len(row.plain) <= 16
