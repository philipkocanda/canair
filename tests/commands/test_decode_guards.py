"""``decode``'s argument-combination guards — the modifier-requires-base-view table.

``--changes-only`` means nothing without ``--compact``, ``--group-by`` nothing
without ``--stats``, ``--corr-transform`` nothing without ``--corr``. Accepting
one silently would read to the user as "the flag did nothing" instead of "you
asked for the wrong base view", so each is rejected with a message and exit 2.

These messages were entirely untested before the guards became a declarative
table in :data:`canlib.commands.decode.entry._MODIFIER_REQUIRES` — the point of the
table is that adding a modifier cannot forget its guard, which only holds if
something asserts the table *is* what runs.
"""

from __future__ import annotations

import argparse

import pytest

from canlib.commands import decode
from canlib.commands.decode.entry import _MODIFIER_REQUIRES, _flag, check_modifier_flags


def _args(**overrides) -> argparse.Namespace:
    """A namespace with every guarded dest falsy, then the overrides applied."""
    base = {dest: None for pair in _MODIFIER_REQUIRES for dest in pair}
    return argparse.Namespace(**{**base, **overrides})


class TestFlagSpelling:
    def test_dest_to_flag(self):
        assert _flag("corr_transform") == "--corr-transform"
        assert _flag("stats") == "--stats"

    def test_every_guarded_dest_is_a_real_decode_flag(self):
        """A guard naming a dest no flag defines would produce advice you can't follow."""
        sub = argparse.ArgumentParser().add_subparsers()
        parser = decode.add_parser(sub)
        options = {opt for action in parser._actions for opt in action.option_strings}
        for modifier, required in _MODIFIER_REQUIRES:
            assert _flag(modifier) in options, modifier
            assert _flag(required) in options, required


class TestCheckModifierFlags:
    def test_nothing_set_is_fine(self):
        assert check_modifier_flags(_args()) is None

    @pytest.mark.parametrize("modifier,required", _MODIFIER_REQUIRES, ids=lambda v: str(v))
    def test_modifier_without_its_base_view_is_rejected(self, modifier, required):
        msg = check_modifier_flags(_args(**{modifier: True}))
        assert msg == f"{_flag(modifier)} requires {_flag(required)}"

    @pytest.mark.parametrize("modifier,required", _MODIFIER_REQUIRES, ids=lambda v: str(v))
    def test_modifier_with_its_base_view_passes(self, modifier, required):
        assert check_modifier_flags(_args(**{modifier: True, required: True})) is None

    def test_reports_one_at_a_time(self):
        """Several unmet guards report the first, not a merged message."""
        msg = check_modifier_flags(_args(changes_only=True, group_by="state"))
        assert msg == "--changes-only requires --compact"


class TestThroughRun:
    """End to end: the guard reaches the user as exit code 2 on stderr.

    Both tests here pin that the guards are *pure argument validation*: they must
    fire before any profile is loaded, so an invalid flag combination costs
    nothing and reports the same message whichever profile is active. That is
    asserted directly, by making a profile load explode — a plain "did it print
    the message" test does not catch a guard moved later, because a literal
    ``ECU:PID`` selector resolves without error and the failure only surfaces
    deeper in the pipeline.
    """

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["NOSUCH:0000", "--changes-only"], "--changes-only requires --compact"),
            (["NOSUCH:0000", "--group-by", "state"], "--group-by requires --stats"),
            (["NOSUCH:0000", "--corr-transform", "delta"], "--corr-transform requires --corr"),
        ],
    )
    def test_exits_2_with_the_guard_message(self, argv, expected, capsys):
        sub = argparse.ArgumentParser().add_subparsers()
        parser = decode.add_parser(sub)
        assert decode.run(parser.parse_args(argv)) == 2
        assert expected in capsys.readouterr().err

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["NOSUCH:0000", "--changes-only"], "--changes-only requires --compact"),
            (["NOSUCH:0000", "--group-by", "state"], "--group-by requires --stats"),
            (["NOSUCH:0000", "--corr-transform", "delta"], "--corr-transform requires --corr"),
        ],
    )
    def test_guard_fires_before_any_profile_is_loaded(self, argv, expected, monkeypatch, capsys):
        def _explode():
            raise AssertionError("load_pids() called — a guard was moved after profile loading")

        monkeypatch.setattr("canlib.commands.decode.entry.load_pids", _explode)
        sub = argparse.ArgumentParser().add_subparsers()
        parser = decode.add_parser(sub)
        assert decode.run(parser.parse_args(argv)) == 2
        assert expected in capsys.readouterr().err
