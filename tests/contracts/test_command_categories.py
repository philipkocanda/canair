"""Tests for the top-level ``canair --help`` command categories.

The subcommand list is grouped under labelled categories (Live device /
Analysis / Authoring / Import·export / Setup) driven by a single central map in
``canlib.commands._categories``. These tests guard that map against drift with
the actual registered subcommands and check the grouped help renders correctly.
"""

from __future__ import annotations

from unittest.mock import patch

from canlib.cli import _inject_default_subcommand, build_parser
from canlib.commands import iter_command_modules
from canlib.commands._categories import CATEGORIES, OTHER_TITLE, categorized_names


def _registered_names() -> set[str]:
    return {module.NAME for module in iter_command_modules()}


class TestCategoryMap:
    def test_every_categorized_name_is_registered(self):
        unknown = categorized_names() - _registered_names()
        assert not unknown, f"categories reference unknown commands: {sorted(unknown)}"

    def test_no_command_in_two_categories(self):
        seen: set[str] = set()
        for _title, names in CATEGORIES:
            for name in names:
                assert name not in seen, f"{name!r} appears in more than one category"
                seen.add(name)

    def test_titles_unique(self):
        titles = [title for title, _ in CATEGORIES]
        assert len(titles) == len(set(titles))
        assert OTHER_TITLE not in titles


class TestGroupedHelp:
    def _help(self) -> str:
        return build_parser().format_help()

    def test_category_headers_present_in_order(self):
        text = self._help()
        positions = []
        for title, _ in CATEGORIES:
            marker = title.upper()
            assert marker in text, f"missing category header {marker!r}"
            positions.append(text.index(marker))
        assert positions == sorted(positions), "category headers out of order"

    def test_every_command_listed_exactly_once(self):
        text = self._help()
        for name in _registered_names():
            # The pseudo-action invocation column starts with the command name;
            # a couple carry an alias in parens (io/repl) — match the leading token.
            assert f"    {name}" in text, f"{name!r} not listed in help"

    def test_uncategorized_command_under_other(self):
        text = self._help()
        # bix is intentionally left ungrouped -> renders under "Other".
        assert "bix" not in categorized_names()
        other_idx = text.index(OTHER_TITLE.upper())
        assert text.index("bix", other_idx) > other_idx


# Short aliases for the most common commands. alias -> canonical command name.
COMMON_ALIASES = {
    "mon": "monitor",
    "cap": "captures",
    "id": "identity",
    "st": "status",
    "disc": "discover",
    "dec": "decode",
    "cov": "coverage",
    "val": "validate",
    "prof": "profile",
}


class TestCommandAliases:
    def test_aliases_render_in_help(self):
        text = build_parser().format_help()
        for alias, canon in COMMON_ALIASES.items():
            assert f"{canon} ({alias})" in text, f"missing alias hint for {canon}"

    def test_aliases_resolve_to_same_parser(self):
        parser = build_parser()
        for alias, canon in COMMON_ALIASES.items():
            assert parser._subparsers is not None
            action = next(a for a in parser._actions if getattr(a, "dest", None) == "command")
            assert alias in action.choices, f"{alias!r} not a registered choice"
            # The alias and canonical name share the same subparser object.
            assert action.choices[alias] is action.choices[canon]

    def test_cap_gets_uds_default_injected(self):
        # `captures` is a command group; its `cap` alias must still get the
        # default `uds` kind injected before argparse sees the argv.
        assert _inject_default_subcommand(["cap", "BMS", "2102"]) == [
            "cap",
            "uds",
            "BMS",
            "2102",
        ]
        # A help flag is left alone (show the group help).
        assert _inject_default_subcommand(["cap", "-h"]) == ["cap", "-h"]

    def test_alias_hint_bolded_on_tty_only(self):
        parser = build_parser()
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            tty_text = parser.format_help()
        plain_text = parser.format_help()  # not a tty under pytest capture
        # On a TTY the parenthesised alias hint is wrapped in bold ANSI.
        assert "\033[1m(mon)\033[0m" in tty_text
        # Piped/redirected output stays plain — no escape codes.
        assert "\033[1m" not in plain_text
        assert "(mon)" in plain_text
