"""Tests for the universal ``help`` word alias (maps to ``-h``/``--help``).

``canair help``, ``canair help <cmd>``, ``canair <cmd> help`` and
``canair <group> <kind> help`` all resolve to the corresponding ``-h`` form,
without clobbering an argument value that merely contains "help".
"""

from __future__ import annotations

import pytest

from canlib.cli import _rewrite_help_tokens


class TestRewriteHelpTokens:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            # Leading `help` → top-level or command help.
            (["help"], ["-h"]),
            (["help", "decode"], ["decode", "-h"]),
            (["help", "captures", "uds"], ["captures", "uds", "-h"]),
            # Trailing `help` → command/group/kind help.
            (["decode", "help"], ["decode", "-h"]),
            (["captures", "help"], ["captures", "-h"]),
            (["captures", "uds", "help"], ["captures", "uds", "-h"]),
            # Global option before a leading `help` is preserved.
            (
                ["--profile", "ioniq-2017", "help", "decode"],
                ["--profile", "ioniq-2017", "decode", "-h"],
            ),
            # `-h`/`--help` already present → untouched.
            (["decode", "-h"], ["decode", "-h"]),
            (["decode", "--help"], ["decode", "--help"]),
            # A value that merely contains "help" is NOT clobbered.
            (["decode", "BMS", "helper"], ["decode", "BMS", "helper"]),
            (["query", "HELPDESK:2101"], ["query", "HELPDESK:2101"]),
            # No help token anywhere → untouched.
            (["decode", "BMS", "2101"], ["decode", "BMS", "2101"]),
            ([], []),
        ],
    )
    def test_rewrite(self, argv, expected):
        assert _rewrite_help_tokens(argv) == expected
