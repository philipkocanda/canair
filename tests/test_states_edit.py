"""Tests for canlib.states_edit — surgical vehicle_states.yaml editing."""

from __future__ import annotations

from pathlib import Path

import pytest

from canlib import states_edit, yaml_io
from canlib.states import StatePredicateError
from canlib.states_edit import (
    StatesEditError,
    add_state,
    normalize_name,
    remove_state,
    rename_state,
    set_state_field,
)


class _Prof:
    def __init__(self, path: Path):
        self.states_file = path / "vehicle_states.yaml"


def _states(prof: _Prof) -> list[dict]:
    return yaml_io.safe_load(prof.states_file.read_text())["states"]


def _names(prof: _Prof) -> list[str]:
    return [s["name"] for s in _states(prof)]


class TestNormalizeName:
    def test_uppercases_and_trims(self):
        assert normalize_name("  charging ") == "CHARGING"

    def test_alphanumeric_word_ok(self):
        assert normalize_name("acc2") == "ACC2"
        assert normalize_name("deepsleep") == "DEEPSLEEP"

    def test_rejects_empty(self):
        with pytest.raises(StatesEditError):
            normalize_name("   ")

    def test_rejects_space(self):
        with pytest.raises(StatesEditError):
            normalize_name("deep sleep")

    def test_rejects_punctuation(self):
        with pytest.raises(StatesEditError):
            normalize_name("acc!")


class TestAddState:
    def test_scaffolds_file_when_absent(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", description="Driveable", profile=prof)
        assert prof.states_file.exists()
        assert _names(prof) == ["READY"]
        assert _states(prof)[0]["description"] == "Driveable"

    def test_uppercases_name(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("charging", profile=prof)
        assert _names(prof) == ["CHARGING"]

    def test_with_predicate(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("CHARGING", when="BMS.BATTERY_CURRENT < -1", profile=prof)
        assert _states(prof)[0]["when"] == "BMS.BATTERY_CURRENT < -1"

    def test_rejects_duplicate_case_insensitive(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        with pytest.raises(StatesEditError):
            add_state("ready", profile=prof)

    def test_rejects_bad_predicate(self, tmp_path):
        prof = _Prof(tmp_path)
        with pytest.raises(StatePredicateError):
            add_state("READY", when="bad(", profile=prof)
        # Nothing written on the fail-fast path.
        assert not prof.states_file.exists()

    def test_preserves_comments(self, tmp_path):
        prof = _Prof(tmp_path)
        prof.states_file.write_text("# top comment\nstates:\n  - name: READY\n")
        add_state("CHARGING", profile=prof)
        assert "# top comment" in prof.states_file.read_text()
        assert _names(prof) == ["READY", "CHARGING"]


class TestRemoveState:
    def test_removes(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("CHARGING", profile=prof)
        remove_state("ready", profile=prof)
        assert _names(prof) == ["CHARGING"]

    def test_missing_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        with pytest.raises(StatesEditError):
            remove_state("NOPE", profile=prof)

    def test_no_file_raises(self, tmp_path):
        with pytest.raises(StatesEditError):
            remove_state("READY", profile=_Prof(tmp_path))


class TestRenameState:
    def test_renames(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("ACC2", profile=prof)
        rename_state("acc2", "IGN", profile=prof)
        assert _names(prof) == ["IGN"]

    def test_missing_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        with pytest.raises(StatesEditError):
            rename_state("NOPE", "X", profile=prof)

    def test_collision_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("CHARGING", profile=prof)
        with pytest.raises(StatesEditError):
            rename_state("READY", "charging", profile=prof)


class TestSetStateField:
    def test_sets_description(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        set_state_field("ready", "description", "HV active", profile=prof)
        assert _states(prof)[0]["description"] == "HV active"

    def test_clears_description(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", description="x", profile=prof)
        set_state_field("READY", "description", None, profile=prof)
        assert "description" not in _states(prof)[0]

    def test_sets_and_clears_predicate(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("CHARGING", profile=prof)
        set_state_field("CHARGING", "when", "BMS.BATTERY_CURRENT < -1", profile=prof)
        assert _states(prof)[0]["when"] == "BMS.BATTERY_CURRENT < -1"
        set_state_field("CHARGING", "when", None, profile=prof)
        assert "when" not in _states(prof)[0]

    def test_bad_predicate_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("CHARGING", profile=prof)
        with pytest.raises(StatePredicateError):
            set_state_field("CHARGING", "when", "bad(", profile=prof)

    def test_bad_field_raises(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        with pytest.raises(StatesEditError):
            set_state_field("READY", "bogus", "x", profile=prof)


class TestReparseGuard:
    def test_duplicate_after_edit_reverts(self, tmp_path, monkeypatch):
        # Force a corrupt write and confirm the file is reverted to the original.
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        original = prof.states_file.read_text()

        def _boom(path):
            raise StatesEditError("simulated invalid file")

        monkeypatch.setattr(states_edit, "_reparse_validate", _boom)
        with pytest.raises(StatesEditError):
            add_state("CHARGING", profile=prof)
        assert prof.states_file.read_text() == original
