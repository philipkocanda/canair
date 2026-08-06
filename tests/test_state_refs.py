"""Tests for vehicle-state predicate ↔ signal-registry cross-validation.

A ``when:`` predicate reads decoded signals by ``ECU.PARAM``. The evaluator
deliberately cannot distinguish a *missing* signal from a *not-polled* one (both
are UNKNOWN, which is what stops a partially-polled cycle mislabelling a
session) — so a renamed signal silently disables its state. That happened: the
``PARKED`` predicate referenced ``VCU.GEAR_PARK`` after the signal was renamed,
``canair validate all`` stayed green, and only the backfill-states tests noticed.

These tests cover the machinery that closes the hole (``canlib.state_refs``), the
``canair validate states`` gate that reports it, and a guard that every predicate
in every bundled profile still resolves.
"""

from __future__ import annotations

import argparse

import pytest

from canlib import profile
from canlib.commands import validate as validate_cmd
from canlib.constants import BUNDLED_PROFILES_DIR
from canlib.decoding import decodes_to_number
from canlib.pids import clear_cache
from canlib.state_refs import (
    IGNORED_PID,
    MALFORMED,
    NOT_NUMERIC,
    UNKNOWN_ECU,
    UNKNOWN_PARAM,
    check_references,
    states_referencing,
)
from canlib.states import StatePredicateError, predicate_references


@pytest.fixture(autouse=True)
def _restore_active_profile():
    saved = profile._active
    clear_cache()
    yield
    profile._active = saved
    clear_cache()


# A registry with one signal of each shape the resolver must tell apart.
PIDS_DATA = {
    "ecus": {
        "VCU": {
            "tx_id": 0x7E2,
            "identity": {"alias": "HCU"},
            "pids": {
                "2101": {
                    "status": "active",
                    "parameters": {
                        "GEAR_P": {"expression": "B10:0"},
                        "VIN_TEXT": {"expression": "B03", "type": "ascii"},
                        "PLACEHOLDER": {"notes": "not decoded yet"},
                    },
                },
                "2102": {
                    "status": "ignored",
                    "parameters": {"DEAD_SIGNAL": {"expression": "B04"}},
                },
            },
        },
        "ESC": {
            "tx_id": 0x7D1,
            "pids": {"22C101": {"parameters": {"REAL_SPEED_KMH": {"expression": "B06/10"}}}},
        },
    }
}


class TestPredicateReferences:
    """canlib.states.predicate_references — what a `when:` expression reads."""

    def test_dotted_names_only_once_each(self):
        expr = "VCU.GEAR_P == 1 or ESC.REAL_SPEED_KMH > 0.5 or VCU.GEAR_P == 2"
        assert predicate_references(expr) == ["VCU.GEAR_P", "ESC.REAL_SPEED_KMH"]

    def test_sentinels_are_not_references(self):
        assert predicate_references("__no_response__") == []
        assert predicate_references("__responded__ and BMS.SOC > 1") == ["BMS.SOC"]

    def test_source_order_is_preserved(self):
        assert predicate_references("C.C == 1 and A.A == 1 and B.B == 1") == ["C.C", "A.A", "B.B"]

    def test_malformed_reference_is_returned_not_rejected(self):
        # A bare word parses fine and merely never resolves — the caller reports it.
        assert predicate_references("GEAR_P == 1") == ["GEAR_P"]

    def test_bad_syntax_still_raises(self):
        with pytest.raises(StatePredicateError):
            predicate_references("VCU.GEAR_P ==")
        with pytest.raises(StatePredicateError):
            predicate_references("open('x')")


class TestDecodesToNumber:
    """canlib.decoding.decodes_to_number — only numerics reach a predicate."""

    @pytest.mark.parametrize("ptype", [None, "numeric", "enum", "bitmask", "bcd"])
    def test_numeric_yielding_types(self, ptype):
        assert decodes_to_number({"expression": "B04", "type": ptype}) is True

    @pytest.mark.parametrize("ptype", ["ascii", "date", "struct", "ASCII"])
    def test_text_yielding_types(self, ptype):
        assert decodes_to_number({"expression": "B04", "type": ptype}) is False

    def test_no_expression_never_decodes(self):
        assert decodes_to_number({"notes": "placeholder"}) is False
        assert decodes_to_number(None) is False


class TestCheckReferences:
    """canlib.state_refs.check_references — why a reference cannot resolve."""

    def _kinds(self, expr):
        return [i.kind for i in check_references([("S", expr)], PIDS_DATA)]

    def _one(self, expr):
        issues = check_references([("S", expr)], PIDS_DATA)
        assert len(issues) == 1, issues
        return issues[0]

    def test_resolvable_references_are_silent(self):
        assert self._kinds("VCU.GEAR_P == 1 or ESC.REAL_SPEED_KMH > 0.5") == []

    def test_sentinel_only_predicate_is_silent(self):
        assert self._kinds("__no_response__") == []

    def test_renamed_signal_is_reported(self):
        # The exact regression: the signal was renamed out from under the predicate.
        issue = self._one("VCU.GEAR_PARK == 1")
        assert issue.kind == UNKNOWN_PARAM
        assert "GEAR_PARK" in issue.message

    def test_unknown_ecu(self):
        issue = self._one("NOPE.FOO == 1")
        assert issue.kind == UNKNOWN_ECU
        assert "NOPE" in issue.message

    def test_alias_names_the_canonical_ecu(self):
        issue = self._one("HCU.GEAR_P == 1")
        assert issue.kind == UNKNOWN_ECU
        assert "alias" in issue.message and "VCU" in issue.message

    def test_lowercase_ecu_never_resolves(self):
        # The decoded value map is keyed by the upper-cased ECU name.
        issue = self._one("vcu.GEAR_P == 1")
        assert issue.kind == UNKNOWN_ECU
        assert "UPPERCASE" in issue.message

    def test_param_case_mismatch_suggests_the_real_name(self):
        issue = self._one("VCU.gear_p == 1")
        assert issue.kind == UNKNOWN_PARAM
        assert "GEAR_P" in issue.message

    def test_signal_defined_on_another_ecu_says_where(self):
        issue = self._one("VCU.REAL_SPEED_KMH > 1")
        assert issue.kind == UNKNOWN_PARAM
        assert "ESC" in issue.message

    def test_bare_name_is_malformed(self):
        issue = self._one("GEAR_P == 1")
        assert issue.kind == MALFORMED

    def test_ignored_pid_is_never_polled(self):
        issue = self._one("VCU.DEAD_SIGNAL == 1")
        assert issue.kind == IGNORED_PID
        assert "ignored" in issue.message

    def test_non_numeric_signal_cannot_be_compared(self):
        issue = self._one("VCU.VIN_TEXT == 1")
        assert issue.kind == NOT_NUMERIC

    def test_undecodable_placeholder_signal(self):
        # Defined but with no expression: never decoded, so never in the value map.
        assert self._kinds("VCU.PLACEHOLDER == 1") == [NOT_NUMERIC]

    def test_invalid_syntax_is_left_to_compile_predicate(self):
        assert check_references([("S", "VCU.GEAR_P ==")], PIDS_DATA) == []

    def test_issue_message_names_the_state_and_ref(self):
        issue = self._one("VCU.GEAR_PARK == 1")
        assert "state 'S'" in str(issue) and "VCU.GEAR_PARK" in str(issue)


def _mk_profile(tmp_path, states_yaml: str, *, with_ecus: bool = True):
    """A minimal profile bundle with a states vocabulary and one ECU definition."""
    root = tmp_path / "prof"
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    if with_ecus:
        (root / "ecus" / "vcu.yaml").write_text(
            "VCU:\n"
            "  tx_id: 0x7E2\n"
            "  pids:\n"
            "    2101:\n"
            "      status: active\n"
            "      parameters:\n"
            "        GEAR_P:\n"
            "          expression: B10:0\n"
        )
    (root / "vehicle_states.yaml").write_text(states_yaml)
    profile.set_active(str(root))
    clear_cache()
    return root


GOOD_STATES = "states:\n  - name: PARKED\n    when: VCU.GEAR_P == 1\n"
BROKEN_STATES = "states:\n  - name: PARKED\n    when: VCU.GEAR_PARK == 1\n"


class TestValidateStates:
    """`canair validate states` — the gate that must fail on a broken reference."""

    def _run(self, capsys):
        rc = validate_cmd.run(argparse.Namespace(target="states", files=[], stats=False))
        return rc, capsys.readouterr().out

    def test_resolvable_predicate_passes(self, tmp_path, capsys):
        _mk_profile(tmp_path, GOOD_STATES)
        rc, out = self._run(capsys)
        assert rc == 0
        assert "OK (1 states, 1 predicate(s))" in out

    def test_renamed_signal_fails_validation(self, tmp_path, capsys):
        _mk_profile(tmp_path, BROKEN_STATES)
        rc, out = self._run(capsys)
        assert rc == 1
        assert "VCU.GEAR_PARK" in out
        assert "PARKED" in out
        assert "canair states set-predicate" in out

    def test_predicate_without_references_still_passes(self, tmp_path, capsys):
        _mk_profile(tmp_path, "states:\n  - name: SLEEP\n    when: __no_response__\n")
        rc, _out = self._run(capsys)
        assert rc == 0

    def test_empty_registry_skips_the_check_out_loud(self, tmp_path, capsys):
        # A profile with no ECUs yet can't have its references resolved — say so
        # rather than reporting every reference as broken (or skipping silently).
        _mk_profile(tmp_path, BROKEN_STATES, with_ecus=False)
        rc, out = self._run(capsys)
        assert rc == 0
        assert "not checked" in out

    def test_syntax_error_is_still_reported_once(self, tmp_path, capsys):
        _mk_profile(tmp_path, "states:\n  - name: X\n    when: 'VCU.GEAR_P =='\n")
        rc, out = self._run(capsys)
        assert rc == 1
        assert "invalid when:" in out


class TestStatesReferencing:
    """The reverse lookup behind the rename/remove warnings."""

    def test_finds_the_state_reading_a_signal(self, tmp_path):
        _mk_profile(tmp_path, GOOD_STATES)
        assert states_referencing("VCU", "GEAR_P") == ["PARKED"]

    def test_case_insensitive(self, tmp_path):
        _mk_profile(tmp_path, GOOD_STATES)
        assert states_referencing("vcu", "gear_p") == ["PARKED"]

    def test_unreferenced_signal(self, tmp_path):
        _mk_profile(tmp_path, GOOD_STATES)
        assert states_referencing("VCU", "SOMETHING_ELSE") == []

    def test_no_vocabulary_is_not_an_error(self, tmp_path):
        root = tmp_path / "bare"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
        profile.set_active(str(root))
        assert states_referencing("VCU", "GEAR_P") == []


class TestPidsEditWarnsAboutPredicates:
    """`canair pids rename-param` / `rm-param` must not break a predicate silently."""

    def _args(self, root, **kw):
        base = {"dir": str(root / "ecus"), "no_validate": True}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_rename_warns(self, tmp_path, capsys):
        from canlib.commands import pids as pids_cmd

        root = _mk_profile(tmp_path, GOOD_STATES)
        pids_cmd.cmd_rename_param(
            self._args(root, ecu="VCU", pid="2101", old="GEAR_P", new="GEAR_PARK")
        )
        out = capsys.readouterr().out
        assert "PARKED" in out and "VCU.GEAR_P" in out

    def test_remove_warns(self, tmp_path, capsys):
        from canlib.commands import pids as pids_cmd

        root = _mk_profile(tmp_path, GOOD_STATES)
        pids_cmd.cmd_rm_param(self._args(root, ecu="VCU", pid="2101", name="GEAR_P"))
        out = capsys.readouterr().out
        assert "PARKED" in out

    def test_unreferenced_signal_is_quiet(self, tmp_path, capsys):
        from canlib.commands import pids as pids_cmd

        root = _mk_profile(tmp_path, "states:\n  - name: SLEEP\n    when: __no_response__\n")
        pids_cmd.cmd_rm_param(self._args(root, ecu="VCU", pid="2101", name="GEAR_P"))
        assert "warn" not in capsys.readouterr().out


class TestBundledProfiles:
    """Every predicate shipped in the repo must resolve — the recurrence guard.

    This is what would have failed the build when ``GEAR_PARK`` was renamed. It
    covers each bundled profile, so a rename in any of them is caught at once.
    """

    @pytest.mark.parametrize(
        "name", [p.name for p in sorted(BUNDLED_PROFILES_DIR.iterdir()) if p.is_dir()]
    )
    def test_predicates_resolve(self, name):
        from canlib.pids import load_pids
        from canlib.state_refs import profile_predicates

        prof = profile.set_active(name)
        issues = check_references(profile_predicates(prof), load_pids())
        assert issues == [], "\n".join(str(i) for i in issues)

    def test_the_reference_profile_actually_has_predicates(self):
        """Keeps the guard above from passing vacuously if predicate loading breaks."""
        from canlib.state_refs import profile_predicates

        prof = profile.set_active("ioniq-2017")
        predicates = profile_predicates(prof)
        assert len(predicates) >= 5
        assert any("VCU." in expr for _name, expr in predicates)
