"""Tests for the uds/can domain-kind spine's group-default routing.

`canair captures`, `correlate`, and `hunt` are command groups with `uds`/`can`
kinds; a bare invocation defaults to the mature domain-A (`uds`) surface via
`_inject_default_subcommand` (the same mechanism as `scan`/`ecu`). These tests
lock that muscle-memory-preserving behavior.
"""

from __future__ import annotations

import pytest

from canlib.cli import _GROUP_DEFAULTS, _inject_default_subcommand


class TestGroupDefaults:
    def test_registered_kinds(self):
        # The spine groups default to uds; scan/ecu keep their own defaults.
        # `captures` also carries a `migrate` kind (YAML→JSON store migration),
        # `migrate-rx` (the ecu→rx field rename), and `merge-driver` (the git
        # session-union merge driver).
        assert _GROUP_DEFAULTS["captures"] == (
            {"uds", "can", "migrate", "migrate-rx", "merge-driver"},
            "uds",
        )
        assert _GROUP_DEFAULTS["correlate"] == ({"uds", "can"}, "uds")
        assert _GROUP_DEFAULTS["hunt"] == ({"uds", "can"}, "uds")

    def test_states_group_default(self):
        # A bare token after `states` routes through the `list` kind (reverse lookup).
        kinds, default = _GROUP_DEFAULTS["states"]
        assert default == "list"
        assert {"list", "add", "rm", "rename"} <= kinds


class TestInjectDefaultSubcommand:
    @pytest.mark.parametrize(
        "argv,expected",
        [
            # Bare query / flags → uds injected.
            (["captures", "BMS", "2102"], ["captures", "uds", "BMS", "2102"]),
            (["captures", "--summary"], ["captures", "uds", "--summary"]),
            (["correlate", "--state", "driving"], ["correlate", "uds", "--state", "driving"]),
            (["hunt", "AAF", "2181"], ["hunt", "uds", "AAF", "2181"]),
            # Explicit kind → left untouched.
            (["captures", "can"], ["captures", "can"]),
            (["captures", "uds", "BMS"], ["captures", "uds", "BMS"]),
            (["captures", "migrate"], ["captures", "migrate"]),
            (["captures", "migrate", "--dry-run"], ["captures", "migrate", "--dry-run"]),
            (["captures", "migrate-rx"], ["captures", "migrate-rx"]),
            (["captures", "merge-driver", "--install"], ["captures", "merge-driver", "--install"]),
            (["correlate", "can", "drive.log"], ["correlate", "can", "drive.log"]),
            (["hunt", "can", "drive.log"], ["hunt", "can", "drive.log"]),
            # Help flag → left untouched (show group help).
            (["captures", "-h"], ["captures", "-h"]),
            (["hunt", "--help"], ["hunt", "--help"]),
            # A global option before the command is skipped to find the token.
            (
                ["--profile", "ioniq-2017", "captures", "BMS"],
                ["--profile", "ioniq-2017", "captures", "uds", "BMS"],
            ),
            # Non-group command → untouched.
            (["decode", "BMS", "2101"], ["decode", "BMS", "2101"]),
            # A bare state name after `states` → `list` injected (reverse lookup).
            (["states", "READY"], ["states", "list", "READY"]),
            (["states"], ["states", "list"]),
            (["states", "--json"], ["states", "list", "--json"]),
            (["states", "add", "FOO"], ["states", "add", "FOO"]),
            (["states", "-h"], ["states", "-h"]),
        ],
    )
    def test_injection(self, argv, expected):
        assert _inject_default_subcommand(argv) == expected
