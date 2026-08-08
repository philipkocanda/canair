"""Tests for `canair validate states` — the implies: hierarchy checks."""

from __future__ import annotations

from canlib.commands.validate.other import _state_hierarchy_errors

DECLARED = {"ACC", "ACC2", "READY", "DRIVING", "CHARGING", "PLUGGED", "ALL"}


def _errors(entries: list[tuple[str, object]]) -> list[str]:
    return _state_hierarchy_errors(entries, DECLARED)


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
