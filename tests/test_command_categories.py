"""Tests for the top-level ``canair --help`` command categories.

The subcommand list is grouped under labelled categories (Live device /
Analysis / Authoring / Import·export / Setup) driven by a single central map in
``canlib.commands._categories``. These tests guard that map against drift with
the actual registered subcommands and check the grouped help renders correctly.
"""

from __future__ import annotations

from canlib.cli import build_parser
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
            marker = f"{title}:"
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
        other_idx = text.index(f"{OTHER_TITLE}:")
        assert text.index("bix", other_idx) > other_idx
