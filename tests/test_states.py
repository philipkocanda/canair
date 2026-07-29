"""Tests for canlib.states — predicate compilation, loading, and suggestion."""

import pytest

from canlib.states import (
    StatePredicateError,
    StateRule,
    compile_predicate,
    load_states,
    state_names,
    state_options,
    suggest_state,
)


class TestCompilePredicate:
    def test_simple_comparison(self):
        p = compile_predicate("BMS.BATTERY_CURRENT < -1")
        assert p({"BMS.BATTERY_CURRENT": -5}, set()) is True
        assert p({"BMS.BATTERY_CURRENT": 3}, set()) is False

    def test_boolean_and_or_not(self):
        p = compile_predicate("A.X == 1 and (B.Y > 2 or not C.Z == 0)")
        assert p({"A.X": 1, "B.Y": 5, "C.Z": 0}, set()) is True
        assert p({"A.X": 1, "B.Y": 0, "C.Z": 0}, set()) is False
        assert p({"A.X": 0, "B.Y": 5, "C.Z": 1}, set()) is False

    def test_no_response_sentinel(self):
        p = compile_predicate("__no_response__")
        assert p({}, set()) is True
        assert p({}, {"BMS"}) is False

    def test_responded_sentinel(self):
        p = compile_predicate("__responded__")
        assert p({}, {"BMS"}) is True
        assert p({}, set()) is False

    def test_string_comparison(self):
        p = compile_predicate("VCU.GEAR == 'P'")
        assert p({"VCU.GEAR": "P"}, set()) is True
        assert p({"VCU.GEAR": "D"}, set()) is False

    def test_chained_comparison(self):
        p = compile_predicate("0 < A.X < 10")
        assert p({"A.X": 5}, set()) is True
        assert p({"A.X": 15}, set()) is False


class TestPredicateSafety:
    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('x')",
            "open('f')",
            "A.X.method()",
            "A[0]",
            "lambda: 1",
            "A.X + 1",  # arithmetic not allowed
            "A.X if B else C",
        ],
    )
    def test_disallowed_syntax_rejected(self, expr):
        with pytest.raises(StatePredicateError):
            compile_predicate(expr)

    def test_syntax_error_rejected(self):
        with pytest.raises(StatePredicateError):
            compile_predicate("A.X ==")


class TestSuggestState:
    def _rules(self):
        return [
            StateRule("charging", predicate=compile_predicate("BMS.BATTERY_CURRENT < -1")),
            StateRule("ready", predicate=compile_predicate("VCU.EV_READY == 1")),
            StateRule("deep sleep", predicate=compile_predicate("__no_response__")),
            StateRule("vocab-only", predicate=None),
        ]

    def test_first_match_wins(self):
        rules = self._rules()
        values = {"BMS.BATTERY_CURRENT": -5, "VCU.EV_READY": 1}
        assert suggest_state(rules, values, {"BMS", "VCU"}) == "charging"

    def test_second_rule_when_first_false(self):
        rules = self._rules()
        values = {"BMS.BATTERY_CURRENT": 0, "VCU.EV_READY": 1}
        assert suggest_state(rules, values, {"BMS", "VCU"}) == "ready"

    def test_missing_param_skips_rule(self):
        rules = self._rules()
        # No BMS/VCU values, but nothing responded → deep sleep matches.
        assert suggest_state(rules, {}, set()) == "deep sleep"

    def test_missing_param_no_match_returns_none(self):
        rules = self._rules()
        # Something responded (not deep sleep) but no usable values → None.
        assert suggest_state(rules, {}, {"BCM"}) is None


class TestStatesFileFallback:
    """Profile.states_file: prefer vehicle_states.yaml, fall back to legacy."""

    def _profile(self, tmp_path):
        from canlib.profile import Profile

        return Profile(tmp_path.name, tmp_path)

    def test_prefers_canonical_name(self, tmp_path):
        (tmp_path / "vehicle_states.yaml").write_text("states: []\n")
        (tmp_path / "states.yaml").write_text("states: []\n")
        assert self._profile(tmp_path).states_file.name == "vehicle_states.yaml"

    def test_falls_back_to_legacy(self, tmp_path):
        (tmp_path / "states.yaml").write_text("states: []\n")
        p = self._profile(tmp_path).states_file
        assert p.name == "states.yaml"
        assert p.exists()

    def test_defaults_to_canonical_when_absent(self, tmp_path):
        p = self._profile(tmp_path).states_file
        assert p.name == "vehicle_states.yaml"
        assert not p.exists()


class TestLoadStates:
    def _write(self, tmp_path, text):
        (tmp_path / "vehicle_states.yaml").write_text(text)

        class _P:
            states_file = tmp_path / "vehicle_states.yaml"

        return _P()

    def test_absent_returns_empty(self, tmp_path):
        class _P:
            states_file = tmp_path / "nope.yaml"

        assert load_states(_P()) == []

    def test_loads_rules_and_predicates(self, tmp_path):
        prof = self._write(
            tmp_path,
            "states:\n"
            "  - name: charging\n"
            "    description: charging\n"
            '    when: "BMS.BATTERY_CURRENT < -1"\n'
            "  - name: parked\n",
        )
        rules = load_states(prof)
        assert [r.name for r in rules] == ["charging", "parked"]
        assert rules[0].predicate is not None
        assert rules[1].predicate is None

    def test_invalid_predicate_raises(self, tmp_path):
        prof = self._write(tmp_path, 'states:\n  - name: x\n    when: "A.X.foo()"\n')
        with pytest.raises(StatePredicateError):
            load_states(prof)

    def test_missing_name_raises(self, tmp_path):
        prof = self._write(tmp_path, "states:\n  - description: no name\n")
        with pytest.raises(StatePredicateError):
            load_states(prof)

    def test_state_names_swallows_errors(self, tmp_path):
        prof = self._write(tmp_path, 'states:\n  - name: x\n    when: "bad("\n')
        assert state_names(prof) == []


class TestStateOptions:
    def _write(self, tmp_path, text):
        (tmp_path / "states.yaml").write_text(text)

        class _P:
            states_file = tmp_path / "states.yaml"

        return _P()

    def test_declared_states_first_then_base(self, tmp_path):
        prof = self._write(
            tmp_path,
            "states:\n  - name: charging\n    description: HV charging\n  - name: parked\n",
        )
        opts = state_options(prof)
        names = [n for n, _ in opts]
        # Declared states keep file order, are UPPER-cased, and come before base.
        assert names[:2] == ["CHARGING", "PARKED"]
        assert opts[0] == ("CHARGING", "HV charging")
        # Base POWER_STATES not already declared are appended (e.g. READY, SLEEP).
        assert "READY" in names
        assert "SLEEP" in names
        # The ALL meta-token is always offered.
        assert "ALL" in names
        # No duplicates.
        assert len(names) == len(set(names))

    def test_absent_states_file_returns_base(self, tmp_path):
        class _P:
            states_file = tmp_path / "nope.yaml"

        names = [n for n, _ in state_options(_P())]
        assert set(names) == {
            "SLEEP",
            "PLUGGED",
            "ACC",
            "ACC2",
            "READY",
            "CHARGING",
            "ALL",
        }
