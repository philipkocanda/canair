"""Tests for command-help domain tags (UDS / CAN / UDS+CAN).

Every data/bus command advertises its data domain near the top of its help so a
user (or agent) can tell at a glance whether it works on diagnostic UDS captures,
raw broadcast-CAN frame logs, or both.
"""

from __future__ import annotations

from canlib.cli import build_parser
from canlib.commands._domain import DOMAINS, apply_domain_tags, tag_for


class TestTagFor:
    def test_known_domains(self):
        assert tag_for("decode") == "[UDS]"
        assert tag_for("sniff") == "[CAN]"
        assert tag_for("captures") == "[UDS+CAN]"

    def test_untagged_meta_command(self):
        # Pure-meta commands belong to no data domain.
        assert tag_for("config") is None
        assert tag_for("profile") is None
        assert tag_for("nonexistent") is None


class TestApplyDomainTags:
    def _subparsers(self):
        parser = build_parser()
        return next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")

    def test_descriptions_prefixed(self):
        sub = self._subparsers()
        for name, tag in ((n, tag_for(n)) for n in DOMAINS):
            parser = sub.choices[name]
            if parser.description:
                assert parser.description.lstrip().startswith(tag), name

    def test_command_map_help_not_prefixed(self):
        # The overview command map is grouped by category and each command's own
        # help states its domain, so the one-liners must NOT carry the tag.
        sub = self._subparsers()
        for action in sub._choices_actions:
            if action.help:
                assert not action.help.startswith("["), action.dest

    def test_idempotent(self):
        # Applying twice must not double-prefix.
        sub = self._subparsers()
        apply_domain_tags(sub)
        assert not sub.choices["decode"].description.startswith("[UDS] [UDS]")
