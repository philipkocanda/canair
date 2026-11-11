"""Global options after the subcommand, and no abbreviated long options."""

from __future__ import annotations

import argparse

import pytest

from canair.cli import _inject_default_subcommand, build_parser
from canair.cli.global_options import GLOBAL_OPTS_WITH_VALUE, hoist_global_options


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return build_parser()


def _parse(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    return parser.parse_args(hoist_global_options(parser, _inject_default_subcommand(argv)))


class TestHoistGlobalOptions:
    def test_trailing_profile_reaches_the_top_level(self, parser):
        ns = _parse(parser, ["decode", "MCU", "2102", "--profile", "X"])
        assert ns.profile == "X"
        assert ns.query == ["MCU", "2102"]

    def test_equals_form(self, parser):
        assert _parse(parser, ["decode", "MCU", "2102", "--profile=X"]).profile == "X"

    def test_a_value_between_positionals_is_not_taken_as_one(self, parser):
        # Unhoisted, argparse read X as the QUERY and 2102 as an extra argument.
        ns = _parse(parser, ["investigate", "MCU", "--profile", "X", "2102"])
        assert (ns.profile, ns.ecu, ns.pid) == ("X", "MCU", "2102")

    def test_after_a_flag_of_a_command_with_a_default_kind(self, parser):
        ns = _parse(parser, ["captures", "--summary", "--profile", "X"])
        assert ns.profile == "X"
        assert ns.query == []

    def test_every_global_option_moves(self, parser):
        ns = _parse(
            parser,
            ["decode", "MCU", "--profiles-dir", "D", "--variant", "V", "--revision", "MCU=r"],
        )
        assert (ns.profiles_dir, ns.variant, ns.revision_pins) == ("D", "V", ["MCU=r"])

    def test_an_option_the_subcommand_declares_keeps_its_local_meaning(self, parser):
        argv = ["pids", "upsert-param", "MCU", "2102", "N", "B1", "--revision", "r1"]
        ns = _parse(parser, [*argv, "--profile", "X"])
        assert ns.profile == "X"
        assert ns.revision == "r1"
        assert ns.revision_pins is None

    def test_a_later_profile_wins_over_a_leading_one(self, parser):
        ns = _parse(parser, ["--profile", "A", "decode", "MCU", "--profile", "B"])
        assert ns.profile == "B"

    @pytest.mark.parametrize(
        "argv",
        [
            ["decode", "MCU", "--", "--profile", "X"],  # after `--` it is data
            ["decode", "MCU", "2102", "--profile"],  # no value: argparse reports it
            ["--profile", "X", "decode", "MCU"],  # already in front
            ["--profile", "X"],  # no command
        ],
    )
    def test_left_alone(self, parser, argv):
        assert hoist_global_options(parser, argv) == argv

    def test_the_set_matches_the_top_level_parser(self, parser):
        declared = {
            opt
            for action in parser._actions
            if action.nargs != 0 and not isinstance(action, argparse._SubParsersAction)
            for opt in action.option_strings
        }
        assert declared == GLOBAL_OPTS_WITH_VALUE


class TestNoAbbreviations:
    def test_a_prefix_of_a_long_option_is_rejected(self, parser, capsys):
        with pytest.raises(SystemExit) as exc:
            _parse(parser, ["decode", "MCU", "2102", "--lab", "x"])
        assert exc.value.code == 2
        assert "unrecognized arguments: --lab" in capsys.readouterr().err

    def test_every_subcommand_parser_inherits_it(self, parser):
        def walk(p):
            yield p
            for action in p._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for child in set(action.choices.values()):
                        yield from walk(child)

        assert all(not p.allow_abbrev for p in walk(parser))
