"""Tests for canlib.states — predicate compilation, loading, and suggestion."""

import pytest

from canlib.states import (
    StatePredicateError,
    StateRule,
    compile_predicate,
    implication_cycle,
    implied_closure,
    load_states,
    most_specific_states,
    parse_implies,
    state_names,
    state_options,
    suggest_state,
    suggest_states,
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


class TestSpecificityHierarchy:
    """`implies:` — parsing, cycles, closure, and narrowing to the most specific."""

    def _rules(self):
        # DRIVING -> READY -> ACC2 -> ACC, CHARGING -> PLUGGED (file order matters
        # only for states unrelated in the hierarchy).
        return [
            StateRule("CHARGING", implies=("PLUGGED",)),
            StateRule("READY", implies=("ACC2",)),
            StateRule("PLUGGED"),
            StateRule("ACC"),
            StateRule("ACC2", implies=("ACC",)),
            StateRule("PARKED"),
            StateRule("DRIVING", implies=("READY",)),
        ]

    def test_parse_implies_normalizes(self):
        assert parse_implies("ready, acc2") == ("READY", "ACC2")
        assert parse_implies(["ready", "READY"]) == ("READY",)
        assert parse_implies(None) == ()
        assert parse_implies("") == ()

    def test_implied_closure_is_transitive(self):
        assert implied_closure(self._rules(), "DRIVING") == {"READY", "ACC2", "ACC"}
        assert implied_closure(self._rules(), "ACC") == set()

    def test_implied_closure_excludes_itself(self):
        rules = [StateRule("A", implies=("B",)), StateRule("B", implies=("A",))]
        assert "A" not in implied_closure(rules, "A")

    def test_driving_wins_over_the_ready_it_implies(self):
        # The reported bug: both are true while driving; the one-slot view must
        # name the specific one even though READY is declared first.
        assert most_specific_states(self._rules(), ["READY", "DRIVING"]) == ["DRIVING"]

    def test_transitive_matches_are_all_dropped(self):
        matched = ["ACC", "ACC2", "READY", "DRIVING"]
        assert most_specific_states(self._rules(), matched) == ["DRIVING"]

    def test_unrelated_matches_all_survive_in_given_order(self):
        # The input order is preserved (suggest_states already yields file order);
        # most_specific_states only removes entailed states, it never re-sorts.
        assert most_specific_states(self._rules(), ["CHARGING", "PARKED"]) == [
            "CHARGING",
            "PARKED",
        ]
        assert most_specific_states(self._rules(), ["PARKED", "CHARGING"]) == [
            "PARKED",
            "CHARGING",
        ]

    def test_single_match_is_returned_unchanged(self):
        assert most_specific_states(self._rules(), ["READY"]) == ["READY"]
        assert most_specific_states([], ["READY"]) == ["READY"]

    def test_suggest_state_applies_the_hierarchy(self):
        rules = [
            StateRule("READY", predicate=compile_predicate("VCU.EV_READY == 1"), implies=()),
            StateRule(
                "DRIVING",
                predicate=compile_predicate("ESC.SPEED > 0.5"),
                implies=("READY",),
            ),
        ]
        values = {"VCU.EV_READY": 1, "ESC.SPEED": 42}
        assert suggest_state(rules, values, {"VCU", "ESC"}) == "DRIVING"
        # Stationary in READY: the broader state is then the most specific match.
        assert suggest_state(rules, {"VCU.EV_READY": 1, "ESC.SPEED": 0}, {"VCU", "ESC"}) == "READY"

    def test_suggest_states_keeps_every_match(self):
        rules = [
            StateRule("READY", predicate=compile_predicate("VCU.EV_READY == 1")),
            StateRule(
                "DRIVING", predicate=compile_predicate("ESC.SPEED > 0.5"), implies=("READY",)
            ),
        ]
        matched, _false = suggest_states(
            rules, {"VCU.EV_READY": 1, "ESC.SPEED": 42}, {"VCU", "ESC"}
        )
        assert matched == ["READY", "DRIVING"]

    def test_implication_cycle_detected(self):
        rules = [
            StateRule("A", implies=("B",)),
            StateRule("B", implies=("C",)),
            StateRule("C", implies=("A",)),
        ]
        cycle = implication_cycle(rules)
        assert cycle is not None
        assert cycle[0] == cycle[-1]
        assert set(cycle) == {"A", "B", "C"}

    def test_self_implication_is_a_cycle(self):
        assert implication_cycle([StateRule("A", implies=("A",))]) == ["A", "A"]

    def test_acyclic_hierarchy_has_no_cycle(self):
        assert implication_cycle(self._rules()) is None

    def test_undeclared_target_is_not_a_cycle(self):
        # An undeclared target is validate's problem, not the cycle checker's.
        assert implication_cycle([StateRule("A", implies=("NOPE",))]) is None

    def test_load_states_reads_implies(self, tmp_path):
        (tmp_path / "vehicle_states.yaml").write_text(
            "states:\n"
            "  - name: READY\n"
            "  - name: DRIVING\n"
            "    implies: [READY]\n"
            "  - name: CHARGING\n"
            "    implies: READY, DRIVING\n"
        )

        class _P:
            states_file = tmp_path / "vehicle_states.yaml"

        rules = load_states(_P())
        assert rules[0].implies == ()
        assert rules[1].implies == ("READY",)
        assert rules[2].implies == ("READY", "DRIVING")

    def test_load_states_rejects_a_cycle(self, tmp_path):
        (tmp_path / "vehicle_states.yaml").write_text(
            "states:\n  - name: A\n    implies: [B]\n  - name: B\n    implies: [A]\n"
        )

        class _P:
            states_file = tmp_path / "vehicle_states.yaml"

        with pytest.raises(StatePredicateError, match="cycle"):
            load_states(_P())


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
        # Base POWER_STATES not already declared are appended (SLEEP/ACC/RUN/CRANK).
        assert "SLEEP" in names
        assert "RUN" in names
        # An EV state NOT declared by this profile is NOT offered (no longer base).
        assert "READY" not in names
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
            "ACC",
            "RUN",
            "CRANK",
            "ALL",
        }


class TestEcuStates:
    """`ecu_states` resolves an ECU's readable states (ECU-level, else PID union)."""

    def test_ecu_level_wins(self):
        from canlib.states import ecu_states

        ecu = {
            "vehicle_states": ["ready", "acc"],
            "pids": {"2101": {"vehicle_states": ["CHARGING"]}},
        }
        # ECU-level field takes precedence; PID states are ignored when it's set.
        assert ecu_states(ecu) == ["READY", "ACC"] or set(ecu_states(ecu)) == {"READY", "ACC"}
        assert "CHARGING" not in ecu_states(ecu)

    def test_pid_union_fallback(self):
        from canlib.states import ecu_states

        ecu = {
            "pids": {
                "2101": {"vehicle_states": ["READY"]},
                "2102": {"vehicle_states": ["READY", "CHARGING"]},
            }
        }
        assert set(ecu_states(ecu)) == {"READY", "CHARGING"}

    def test_empty_when_no_states(self):
        from canlib.states import ecu_states

        assert ecu_states({"pids": {"2101": {}}}) == []
        assert ecu_states({}) == []
        assert ecu_states(None) == []

    def test_all_token_preserved(self):
        from canlib.states import ecu_states

        assert ecu_states({"vehicle_states": ["ALL"]}) == ["ALL"]


class TestEcusInState:
    """`ecus_in_state` is the reverse index — which ECUs are readable in a state."""

    def _data(self):
        return {
            "ecus": {
                "BMS": {"tx_id": 0x7E4, "vehicle_states": ["CHARGING", "READY"]},
                "CLU": {"tx_id": 0x7C6, "pids": {"22B002": {"vehicle_states": ["READY"]}}},
                "IGPM": {"tx_id": 0x770, "vehicle_states": ["ALL"]},
                "AMP": {"tx_id": 0x783},  # no states declared → never matches
            }
        }

    def test_ecu_level_match(self):
        from canlib.states import ecus_in_state

        names = {m["name"]: m["source"] for m in ecus_in_state("CHARGING", self._data())}
        # BMS declares it at the ECU level; IGPM matches via ALL.
        assert names["BMS"] == "ecu"
        assert names["IGPM"] == "all"
        assert "CLU" not in names  # CLU is READY-only
        assert "AMP" not in names

    def test_pid_union_match_and_source(self):
        from canlib.states import ecus_in_state

        matches = {m["name"]: m["source"] for m in ecus_in_state("READY", self._data())}
        assert matches["CLU"] == "pids"
        assert matches["BMS"] == "ecu"
        assert matches["IGPM"] == "all"

    def test_case_insensitive(self):
        from canlib.states import ecus_in_state

        assert [m["name"] for m in ecus_in_state("charging", self._data())] == ["BMS", "IGPM"]

    def test_querying_all_does_not_double_fan_out(self):
        from canlib.states import ecus_in_state

        # Querying ALL itself should return only ECUs that declare ALL, not every ECU.
        assert [m["name"] for m in ecus_in_state("ALL", self._data())] == ["IGPM"]


class TestKleeneLogic:
    """Three-valued (Kleene) evaluation: an unavailable param yields UNKNOWN."""

    def test_missing_param_is_unknown(self):
        from canlib.states import UNKNOWN

        p = compile_predicate("A.X > 0")
        assert p({}, set()) is UNKNOWN
        assert p({"A.X": 5}, set()) is True
        assert p({"A.X": -5}, set()) is False

    def test_or_true_dominates_unknown(self):
        # This is the OBC-only-charging regression: one operand True, other unpolled.
        p = compile_predicate("A.X < -1 or B.Y > 0.5")
        assert p({"B.Y": 2.0}, set()) is True  # A.X unknown, B.Y true -> True
        assert p({"A.X": -5}, set()) is True

    def test_or_all_false_or_unknown(self):
        from canlib.states import UNKNOWN

        p = compile_predicate("A.X < -1 or B.Y > 0.5")
        # A.X false, B.Y unknown -> UNKNOWN (can't rule it out)
        assert p({"A.X": 3}, set()) is UNKNOWN
        # both false -> False
        assert p({"A.X": 3, "B.Y": 0.0}, set()) is False

    def test_and_false_dominates_unknown(self):
        from canlib.states import UNKNOWN

        p = compile_predicate("A.X == 1 and B.Y == 1")
        assert p({"A.X": 0}, set()) is False  # A.X false, B.Y unknown -> False
        assert p({"A.X": 1}, set()) is UNKNOWN  # A.X true, B.Y unknown -> UNKNOWN
        assert p({"A.X": 1, "B.Y": 1}, set()) is True

    def test_not_of_unknown_is_unknown(self):
        from canlib.states import UNKNOWN

        p = compile_predicate("not A.X == 1")
        assert p({}, set()) is UNKNOWN

    def test_chained_comparison_unknown(self):
        from canlib.states import UNKNOWN

        p = compile_predicate("0 < A.X < 10")
        assert p({}, set()) is UNKNOWN
        assert p({"A.X": 5}, set()) is True
        assert p({"A.X": 15}, set()) is False

    def test_offline_responded_none_abstains(self):
        from canlib.states import UNKNOWN

        # responded=None (offline) -> the sentinels can't be evaluated.
        assert compile_predicate("__no_response__")({}, None) is UNKNOWN
        assert compile_predicate("__responded__")({}, None) is UNKNOWN


class TestSuggestStates:
    def _rules(self):
        return [
            StateRule("CHARGING", predicate=compile_predicate("BMS.CUR < -1 or OBC.A > 0.5")),
            StateRule("READY", predicate=compile_predicate("VCU.READY == 1")),
            StateRule("PARKED", predicate=compile_predicate("VCU.GEAR_PARK == 1")),
            StateRule("SLEEP", predicate=None),  # vocabulary-only
        ]

    def test_composite_match(self):
        matched, _false = suggest_states(self._rules(), {"VCU.READY": 1, "VCU.GEAR_PARK": 1}, None)
        assert matched == ["READY", "PARKED"]

    def test_obc_only_charging_regression(self):
        # BMS not polled, OBC current present -> CHARGING (was previously missed).
        matched, false = suggest_states(self._rules(), {"OBC.A": 2.85}, None)
        assert matched == ["CHARGING"]
        assert "CHARGING" not in false

    def test_definitely_false_reported(self):
        matched, false = suggest_states(self._rules(), {"VCU.READY": 0, "VCU.GEAR_PARK": 1}, None)
        assert matched == ["PARKED"]
        assert "READY" in false

    def test_unknown_neither_matched_nor_false(self):
        # Nothing decoded -> everything UNKNOWN -> both lists empty.
        matched, false = suggest_states(self._rules(), {}, None)
        assert matched == []
        assert false == []

    def test_suggest_state_wrapper_returns_first(self):
        assert suggest_state(self._rules(), {"VCU.READY": 1, "VCU.GEAR_PARK": 1}, None) == "READY"
        assert suggest_state(self._rules(), {}, None) is None
