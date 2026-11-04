"""Tests for `canair validate states` — the implies:/excludes: relation checks."""

from __future__ import annotations

from canlib.commands.validate.other import _state_relation_errors

DECLARED = {"ACC", "ACC2", "READY", "DRIVING", "CHARGING", "PLUGGED", "PARKED", "ALL"}


def _errors(entries: list[tuple[str, object]]) -> list[str]:
    return _state_relation_errors(entries, [], DECLARED)


def _excl_errors(entries: list[tuple[str, object]]) -> list[str]:
    return _state_relation_errors([], entries, DECLARED)


class TestImpliesShape:
    def test_list_and_string_forms_accepted(self):
        assert _errors([("DRIVING", ["READY"]), ("CHARGING", "PLUGGED")]) == []

    def test_non_list_rejected(self):
        errs = _errors([("DRIVING", 3)])
        assert len(errs) == 1
        assert "must be a list of state names" in errs[0]


class TestImpliesTargets:
    def test_undeclared_target(self):
        errs = _errors([("DRIVING", ["NOPE"])])
        assert errs == ["state 'DRIVING': implies 'NOPE', which is not a declared state"]

    def test_self_implication(self):
        assert _errors([("DRIVING", ["driving"])]) == ["state 'DRIVING': implies itself"]

    def test_implying_all_is_rejected(self):
        errs = _errors([("DRIVING", ["ALL"])])
        assert len(errs) == 1
        assert "ALL is the meta-token" in errs[0]

    def test_all_cannot_imply_anything(self):
        errs = _errors([("ALL", ["READY"])])
        assert "the ALL meta-token cannot imply anything" in errs[-1]

    def test_every_bad_target_is_reported(self):
        errs = _errors([("DRIVING", ["NOPE", "ALSO_NOPE"])])
        assert len(errs) == 2


class TestImpliesAcyclicity:
    def test_chain_is_fine(self):
        entries: list[tuple[str, object]] = [
            ("DRIVING", ["READY"]),
            ("READY", ["ACC2"]),
            ("ACC2", ["ACC"]),
        ]
        assert _errors(entries) == []

    def test_diamond_is_fine(self):
        entries: list[tuple[str, object]] = [
            ("DRIVING", ["READY", "ACC2"]),
            ("READY", ["ACC"]),
            ("ACC2", ["ACC"]),
        ]
        assert _errors(entries) == []

    def test_cycle_reported(self):
        entries: list[tuple[str, object]] = [("READY", ["DRIVING"]), ("DRIVING", ["READY"])]
        errs = _errors(entries)
        assert any("cycle" in e and "must be acyclic" in e for e in errs)


class TestExcludes:
    """`excludes:` — mutually exclusive states, the complement of `implies:`."""

    def test_list_and_string_forms_accepted(self):
        assert _excl_errors([("DRIVING", ["PARKED"]), ("CHARGING", "DRIVING")]) == []

    def test_non_list_rejected(self):
        errs = _excl_errors([("DRIVING", 3)])
        assert len(errs) == 1
        assert "excludes must be a list" in errs[0]

    def test_self_exclusion_rejected(self):
        errs = _excl_errors([("DRIVING", ["DRIVING"])])
        assert errs == ["state 'DRIVING': excludes itself"]

    def test_undeclared_target_rejected(self):
        errs = _excl_errors([("DRIVING", ["TOWED"])])
        assert len(errs) == 1
        assert "not a declared state" in errs[0]

    def test_all_cannot_be_excluded(self):
        errs = _excl_errors([("DRIVING", ["ALL"])])
        assert len(errs) == 1
        assert "meta-token" in errs[0]

    def test_all_cannot_exclude(self):
        errs = _excl_errors([("ALL", ["DRIVING"])])
        assert any("ALL meta-token cannot" in e for e in errs)

    def test_implied_pair_cannot_also_be_exclusive(self):
        errs = _state_relation_errors([("DRIVING", ["READY"])], [("DRIVING", ["READY"])], DECLARED)
        assert len(errs) == 1
        assert "both implied and mutually exclusive" in errs[0]

    def test_transitively_implied_pair_is_caught(self):
        errs = _state_relation_errors(
            [("DRIVING", ["READY"]), ("READY", ["ACC2"])],
            [("DRIVING", ["ACC2"])],
            DECLARED,
        )
        assert len(errs) == 1
        assert "'ACC2' and 'DRIVING'" in errs[0]

    def test_unrelated_exclusive_pair_is_fine(self):
        assert (
            _state_relation_errors([("DRIVING", ["READY"])], [("DRIVING", ["PARKED"])], DECLARED)
            == []
        )
