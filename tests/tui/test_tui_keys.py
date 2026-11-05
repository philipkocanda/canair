"""Key hints are built from the key roles, never written by hand."""

from __future__ import annotations

import pytest

from canair.tui.keys import (
    CANCEL_HINT,
    CLOSE_HINT,
    PAGE_HINT,
    ROLES,
    arrows_hint,
    display_key,
    hint,
    key_hint,
    side_hint,
)


class TestDisplayKey:
    @pytest.mark.parametrize(
        ("key", "shown"),
        [
            ("escape", "esc"),
            ("enter", "enter"),
            ("question_mark", "?"),
            ("up", "↑"),
            ("down", "↓"),
            ("pageup", "pgup"),
            ("pagedown", "pgdn"),
            ("ctrl+s", "ctrl+s"),
            ("shift+tab", "shift+tab"),
            ("space", "space"),
            ("tab", "tab"),
            ("q", "q"),
            ("greater_than_sign", ">"),
        ],
    )
    def test_the_notation_of_the_skill(self, key, shown):
        assert display_key(key) == shown


class TestKeyHint:
    def test_a_role_gives_its_key_and_its_words(self):
        assert key_hint("back", "cancel") == "esc cancel"
        assert key_hint("apply") == "ctrl+s apply"
        assert key_hint("help") == "? help"

    def test_an_alias_can_be_shown_instead_of_the_canonical_key(self):
        assert key_hint("increase", "zoom in", key="plus") == "+ zoom in"

    def test_words_the_role_does_not_allow_are_refused(self):
        with pytest.raises(ValueError, match="not a description"):
            key_hint("apply", "confirm")

    def test_a_key_the_role_does_not_own_is_refused(self):
        with pytest.raises(ValueError, match="not a key"):
            key_hint("apply", key="enter")

    def test_every_role_builds_a_hint(self):
        for role in ROLES:
            assert key_hint(role).endswith(ROLES[role].description)

    def test_ctrl_s_reads_apply_everywhere(self):
        """No role may call ctrl+s "save" or "confirm": one key, one word."""
        assert ROLES["apply"].descriptions == {"apply", "next / start", "next / apply"}


class TestPairsAndLines:
    def test_arrows(self):
        assert arrows_hint("select") == "↑/↓ select"
        assert arrows_hint("scroll") == "↑/↓ scroll"

    def test_sides(self):
        assert side_hint("frame") == "←/→ frame"

    def test_a_pair_needs_words_both_roles_allow(self):
        with pytest.raises(ValueError):
            arrows_hint("flip")
        with pytest.raises(ValueError):
            side_hint("planet")

    def test_hint_joins_role_names_and_built_hints(self):
        assert hint("apply", CANCEL_HINT) == "ctrl+s apply · esc cancel"
        assert hint(arrows_hint("scroll"), PAGE_HINT, CLOSE_HINT) == (
            "↑/↓ scroll · pgup/pgdn page · esc close"
        )

    def test_the_plot_endian_key_is_the_byte_order_role(self):
        """`e endian` stayed in the plot's hint after endian moved to `b`."""
        assert key_hint("byte_order") == "b byte order"
        assert key_hint("edit", "annotate") == "e annotate"
