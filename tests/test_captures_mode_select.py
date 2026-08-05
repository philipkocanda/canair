"""Tests for the ``captures uds`` mode/flag-combination policy.

``resolve_mode`` is the declarative replacement for ~100 lines of ``if``/``print``/
``return 2`` that used to sit inline in ``run``, where each rule was reachable only
by driving the whole CLI. Here the rules are asserted directly.

Namespaces come from the *real* parser wherever the combination is reachable, so
the defaults under test are the shipped ones. A few guards cover combinations
argparse already rejects (its ``standalone`` mutually exclusive group) — those are
built by overriding attributes on a parsed namespace, and are kept because ``run``
is also entered directly from tests.
"""

import argparse

import pytest

from canlib.commands import captures as cap
from canlib.commands.captures.mode_select import (
    STANDALONE_MODES,
    VIEW_MODES,
    ModeError,
    has_scope_filter,
    resolve_mode,
)
from canlib.commands.captures.query import build_query


def _args(*argv: str) -> argparse.Namespace:
    """Parse ``captures uds ARGV`` through the shipped parser."""
    parser = cap.add_parser(argparse.ArgumentParser().add_subparsers())
    return parser.parse_args(["uds", *argv])


def _resolve(*argv: str):
    args = _args(*argv)
    return resolve_mode(args, build_query(args.query))


def _forced(argv: list[str], **overrides):
    """A namespace argparse would refuse, for the defensive guards."""
    args = _args(*argv)
    for key, value in overrides.items():
        setattr(args, key, value)
    return resolve_mode(args, build_query(args.query))


class TestModeSelection:
    def test_bare_query_is_the_list_view(self):
        assert _resolve("BMS:2102") == "list"

    def test_two_token_query_is_the_list_view(self):
        assert _resolve("BMS", "2102") == "list"

    @pytest.mark.parametrize("flag,mode", [("--diff", "diff"), ("--step", "step")])
    def test_view_flags(self, flag, mode):
        assert _resolve("BMS:2102", flag) == mode

    @pytest.mark.parametrize(
        "flag,mode",
        [
            ("--summary", "summary"),
            ("--sessions", "sessions"),
            ("--backfill-states", "backfill_states"),
        ],
    )
    def test_standalone_modes_take_no_query(self, flag, mode):
        assert _resolve(flag) == mode

    def test_latest_reads_its_selection_from_the_query(self):
        # --latest is a view, not an aggregate: it accepts a QUERY.
        assert _resolve("--latest") == "latest"
        assert _resolve("BMS", "--latest") == "latest"

    def test_delete_with_a_query(self):
        assert _resolve("OBC:2101", "--delete") == "delete"

    def test_set_state_with_a_scope_filter(self):
        assert _resolve("--set-state", "ACC", "--label", "ACC only") == "set_state"

    def test_recover_wins_over_everything(self):
        # It reconciles journals without reading captures, so it is resolved before
        # any QUERY/scope rule can reject the invocation.
        assert _forced([], recover=True, limit=-5, delete=True) == "recover"


class TestRejections:
    def test_delete_without_a_query_is_refused(self):
        err = _resolve("--delete")
        assert isinstance(err, ModeError)
        assert "--delete requires a QUERY" in err.message
        assert err.code == 2

    def test_set_state_without_a_scope_filter_is_refused(self):
        err = _resolve("--set-state", "ACC")
        assert isinstance(err, ModeError)
        assert "--set-state requires a scope filter" in err.message

    @pytest.mark.parametrize(
        "scope", [["--label", "x"], ["--state", "x"], ["--date", "2026-04-15"], ["--today"]]
    )
    def test_any_scope_filter_satisfies_set_state(self, scope):
        assert _resolve("--set-state", "ACC", *scope) == "set_state"

    def test_negative_limit_is_refused(self):
        err = _resolve("BMS:2102", "--limit", "-1")
        assert isinstance(err, ModeError)
        assert "--limit must be >= 0" in err.message

    @pytest.mark.parametrize("mode", STANDALONE_MODES)
    def test_standalone_modes_reject_a_query(self, mode):
        overrides = {"set_state": "ACC"} if mode == "set_state" else {mode: True}
        # --set-state also needs a scope filter to get past its own guard.
        if mode == "set_state":
            overrides["label"] = "x"
        err = _forced(["BMS:2102"], **overrides)
        assert isinstance(err, ModeError)
        assert "do not take a QUERY argument" in err.message

    @pytest.mark.parametrize("mode", STANDALONE_MODES)
    def test_standalone_modes_reject_latest(self, mode):
        overrides = {"set_state": "ACC", "label": "x"} if mode == "set_state" else {mode: True}
        err = _forced([], latest=True, **overrides)
        assert isinstance(err, ModeError)
        assert "--latest cannot be combined with" in err.message

    @pytest.mark.parametrize("mode", STANDALONE_MODES)
    @pytest.mark.parametrize("view", VIEW_MODES)
    def test_standalone_modes_reject_the_views(self, mode, view):
        overrides = {"set_state": "ACC", "label": "x"} if mode == "set_state" else {mode: True}
        err = _forced([], **{view: True}, **overrides)
        assert isinstance(err, ModeError)
        assert "--diff/--step cannot be combined with" in err.message

    @pytest.mark.parametrize("view", VIEW_MODES)
    def test_latest_rejects_the_views(self, view):
        err = _resolve("BMS", "--latest", f"--{view}")
        assert isinstance(err, ModeError)
        assert "--latest cannot be combined with --diff/--step" in err.message

    def test_no_query_and_no_mode_asks_for_a_query(self):
        err = _resolve()
        assert isinstance(err, ModeError)
        assert "Specify a QUERY" in err.message
        assert err.with_ecu_hint
        assert not err.to_stderr  # a usage hint, not an error stream message


class TestModeErrorReporting:
    def test_errors_go_to_stderr_with_code_2(self, capsys):
        err = ModeError("error: nope")
        assert err.report() == 2
        cap_out = capsys.readouterr()
        assert cap_out.err.strip() == "error: nope"
        assert cap_out.out == ""

    def test_the_query_hint_goes_to_stdout_with_the_ecu_list(self, capsys):
        err = ModeError("Specify a QUERY", to_stderr=False, with_ecu_hint=True)
        assert err.report() == 2
        cap_out = capsys.readouterr()
        assert "Specify a QUERY" in cap_out.out
        # ecu_hint() names some real ECUs from the active profile.
        assert "BMS" in cap_out.out
        assert cap_out.err == ""


class TestHasScopeFilter:
    def test_bare_invocation_has_none(self):
        assert not has_scope_filter(_args("BMS:2102"))

    @pytest.mark.parametrize(
        "scope",
        [
            ["--label", "x"],
            ["--state", "x"],
            ["--date", "2026-04-15"],
            ["--today"],
            ["--since", "2026-04-15"],
            ["--until", "2026-04-15"],
            ["--last-sessions", "2"],
        ],
    )
    def test_each_scope_flag_counts(self, scope):
        assert has_scope_filter(_args("BMS:2102", *scope))
