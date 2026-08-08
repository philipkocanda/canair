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


class TestRemoveStateComments:
    """Removing a state must not re-label its neighbours' comments.

    ruamel keeps a sequence item's leading comment on the *previous* item, so a
    naive delete drops the following state's comment and orphans the removed
    state's own comment onto its successor — silently mislabelling it.
    """

    SRC = """states:
  # charging comment
  - name: CHARGING
    when: "BMS.BATTERY_CURRENT < -1"

  # deepsleep comment
  - name: DEEPSLEEP
    when: "__no_response__"

  # --- vocabulary-only states ---
  # (no auto-suggest predicate)
  - name: SLEEP
    description: Light sleep.

  # trailing comment
  - name: ALL
    description: Every state.
"""

    def _prep(self, tmp_path):
        prof = _Prof(tmp_path)
        prof.states_file.write_text(self.SRC)
        return prof

    def test_middle_removal_keeps_next_comment(self, tmp_path):
        prof = self._prep(tmp_path)
        remove_state("DEEPSLEEP", profile=prof)
        text = prof.states_file.read_text()
        assert "# --- vocabulary-only states ---" in text
        assert "# (no auto-suggest predicate)" in text
        assert "# deepsleep comment" not in text
        assert "# charging comment" in text
        assert _names(prof) == ["CHARGING", "SLEEP", "ALL"]

    def test_first_removal_keeps_next_comment(self, tmp_path):
        prof = self._prep(tmp_path)
        remove_state("CHARGING", profile=prof)
        text = prof.states_file.read_text()
        assert "# deepsleep comment" in text
        assert "# charging comment" not in text
        assert _names(prof) == ["DEEPSLEEP", "SLEEP", "ALL"]

    def test_last_removal_keeps_own_comment_out(self, tmp_path):
        prof = self._prep(tmp_path)
        remove_state("ALL", profile=prof)
        text = prof.states_file.read_text()
        assert "# trailing comment" not in text
        assert "# --- vocabulary-only states ---" in text
        assert _names(prof) == ["CHARGING", "DEEPSLEEP", "SLEEP"]

    def test_uncommented_removal_leaves_neighbours_alone(self, tmp_path):
        prof = _Prof(tmp_path)
        prof.states_file.write_text(
            "states:\n  # a comment\n  - name: READY\n  - name: SLEEP\n  - name: ALL\n"
        )
        remove_state("SLEEP", profile=prof)
        text = prof.states_file.read_text()
        assert "# a comment" in text
        assert _names(prof) == ["READY", "ALL"]


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


class TestImpliesField:
    """`implies:` — writing, clearing, key placement, and hierarchy validation."""

    def test_add_state_with_implies(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("DRIVING", implies="ready", profile=prof)
        assert _states(prof)[1]["implies"] == ["READY"]

    def test_set_and_clear_implies(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("ACC2", profile=prof)
        add_state("DRIVING", profile=prof)
        set_state_field("DRIVING", "implies", "ready, acc2", profile=prof)
        assert _states(prof)[2]["implies"] == ["READY", "ACC2"]
        set_state_field("DRIVING", "implies", None, profile=prof)
        assert "implies" not in _states(prof)[2]

    def test_implies_is_written_as_a_flow_list_above_when(self, tmp_path):
        # Key order matters: appending after `when:` lands the key after that
        # entry's trailing comment, which for the last entry is the comment
        # introducing the next state.
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("DRIVING", when="ESC.SPEED > 0.5", profile=prof)
        set_state_field("DRIVING", "implies", "READY", profile=prof)
        text = prof.states_file.read_text()
        assert "implies: [READY]" in text
        assert text.index("implies:") < text.index("when: ESC.SPEED")

    def test_undeclared_target_reverts(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("DRIVING", profile=prof)
        original = prof.states_file.read_text()
        with pytest.raises(StatesEditError, match="undeclared state"):
            set_state_field("DRIVING", "implies", "NOPE", profile=prof)
        assert prof.states_file.read_text() == original

    def test_self_implication_reverts(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("DRIVING", profile=prof)
        with pytest.raises(StatesEditError, match="itself"):
            set_state_field("DRIVING", "implies", "driving", profile=prof)

    def test_cycle_reverts(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("A", profile=prof)
        add_state("B", implies="A", profile=prof)
        with pytest.raises(StatesEditError, match="cycle"):
            set_state_field("A", "implies", "B", profile=prof)
        assert "implies" not in _states(prof)[0]

    def test_implies_all_is_rejected(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("ALL", profile=prof)
        add_state("DRIVING", profile=prof)
        with pytest.raises(StatesEditError, match="ALL"):
            set_state_field("DRIVING", "implies", "ALL", profile=prof)

    def test_remove_state_drops_dangling_references(self, tmp_path):
        # Without this the removal would fail its own re-parse and revert.
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("DRIVING", implies="READY", profile=prof)
        remove_state("READY", profile=prof)
        assert _names(prof) == ["DRIVING"]
        assert "implies" not in _states(prof)[0]

    def test_remove_state_keeps_other_targets(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("ACC", profile=prof)
        add_state("READY", profile=prof)
        add_state("DRIVING", implies="READY, ACC", profile=prof)
        remove_state("READY", profile=prof)
        assert _states(prof)[1]["implies"] == ["ACC"]

    def test_rename_state_retargets_references(self, tmp_path):
        prof = _Prof(tmp_path)
        add_state("READY", profile=prof)
        add_state("DRIVING", implies="READY", profile=prof)
        rename_state("READY", "LIVE", profile=prof)
        assert _states(prof)[1]["implies"] == ["LIVE"]


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
