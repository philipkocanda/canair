"""Tests for canlib.pids_edit — surgical DID field editing.

Uses a tmp_path-backed copy of a single fixture YAML so tests don't mutate
the real pids/ directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.pids_edit import (
    EDITABLE_FIELDS,
    PidsEditError,
    find_ecu_file,
    promote_discovery,
    set_pid_notes,
    set_pid_variable_length,
    update_iocontrol_field,
)

FIXTURE_YAML = """\
# ─── Fake ECU for pids_edit tests ─────────────────────────────────────
TEST:
  tx_id: 0x7E0
  iocontrol:
    AA01:
      availability: []
      label: Inline label
      verified: false
      on: "2FAA0103"
      off: "2FAA0100"
      notes: "Single-line note."

    AA02:
      availability: []
      label: "Quoted: label"
      verified: true
      on: "2FAA0203"
      off: "2FAA0200"
      notes: >
        Multi-line
        block scalar
        content.

    AA03:
      availability: []
      label: No notes here
      verified: false
      on: "2FAA0303"
      off: "2FAA0300"

  iocontrol_discoveries:
    BB01:
      session: extended
      response: "6FBB010100"
      notes: ""
    BB02:
      session: extended
      response: "6FBB02010000"
      notes: ""
    BB03:
      session: extended
      response: "6FBB03"
      notes: ""

  pids:
    2200:
      parameters:
        DUMMY:
          expression: "B:0"
          unit: ""
"""


@pytest.fixture
def tmp_pids_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pids"
    d.mkdir()
    (d / "test.yaml").write_text(FIXTURE_YAML)
    return d


def _reload(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


class TestFindEcuFile:
    def test_locates_by_name(self, tmp_pids_dir: Path):
        p = find_ecu_file("TEST", pids_dir=tmp_pids_dir)
        assert p.name == "test.yaml"

    def test_case_insensitive(self, tmp_pids_dir: Path):
        assert find_ecu_file("test", pids_dir=tmp_pids_dir).name == "test.yaml"

    def test_unknown_raises(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            find_ecu_file("NOPE", pids_dir=tmp_pids_dir)


class TestUpdateLabel:
    def test_simple_replace(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA01", "label", "New label", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA01"]["label"] == "New label"
        # sibling fields preserved (YAML parses bare on/off keys as booleans)
        assert data["TEST"]["iocontrol"]["AA01"][True] == "2FAA0103"
        assert data["TEST"]["iocontrol"]["AA01"][False] == "2FAA0100"

    def test_replace_quoted(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA02", "label", "Plain again", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA02"]["label"] == "Plain again"

    def test_label_with_colon_gets_quoted(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA01", "label", "HVAC: fan", pids_dir=tmp_pids_dir)
        raw = (tmp_pids_dir / "test.yaml").read_text()
        assert '"HVAC: fan"' in raw
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA01"]["label"] == "HVAC: fan"

    def test_does_not_disturb_other_dids(self, tmp_pids_dir: Path):
        original = (tmp_pids_dir / "test.yaml").read_text()
        update_iocontrol_field("TEST", "AA01", "label", "Changed", pids_dir=tmp_pids_dir)
        updated = (tmp_pids_dir / "test.yaml").read_text()
        # AA02 and AA03 blocks unchanged
        assert '"Quoted: label"' in updated
        assert "No notes here" in updated
        # Header comment preserved
        assert original.splitlines()[0] == updated.splitlines()[0]

    def test_preserves_blank_line_between_dids(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA01", "label", "Changed", pids_dir=tmp_pids_dir)
        updated = (tmp_pids_dir / "test.yaml").read_text()
        # The blank line between AA01 and AA02 must remain (readability).
        assert "\n\n    AA02:" in updated


class TestUpdateVerified:
    def test_false_to_true(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA01", "verified", True, pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA01"]["verified"] is True

    def test_true_to_false(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA02", "verified", False, pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA02"]["verified"] is False


class TestUpdateNotes:
    def test_replace_inline_notes(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA01", "notes", "Updated.", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA01"]["notes"].strip() == "Updated."

    def test_replace_block_scalar_notes(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA02", "notes", "Line A\nLine B", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        got = data["TEST"]["iocontrol"]["AA02"]["notes"]
        # YAML block scalar folds newlines to spaces; ensure both lines survive
        assert "Line A" in got and "Line B" in got

    def test_add_notes_where_absent(self, tmp_pids_dir: Path):
        update_iocontrol_field("TEST", "AA03", "notes", "Brand new note.", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["AA03"]["notes"].strip() == "Brand new note."
        # Did not mutate other DIDs' notes
        assert "Single-line note." in data["TEST"]["iocontrol"]["AA01"]["notes"]

    def test_long_note_wraps_but_preserves_value(self, tmp_pids_dir: Path):
        # T3.4: a long single-line note is word-wrapped for readability but the
        # folded-scalar value round-trips unchanged, and long tokens/URLs are not
        # broken (which would corrupt the folded value).
        note = (
            "Candidate from canair hunt vs ESC:22C101:REAL_SPEED_KMH: r=+0.997 "
            "(n=66), fit y=0.6243*x+0.00. See "
            "https://docs.google.com/spreadsheets/d/1R2dW_4-ANg04TQdkHH52-8wdYItBkWDfppXcsqOc-m1M "
            "for the reference data. Enabled unverified pending scale confirmation."
        )
        update_iocontrol_field("TEST", "AA02", "notes", note, pids_dir=tmp_pids_dir)
        raw = (tmp_pids_dir / "test.yaml").read_text()
        # readability: at least one wrapped continuation line ≤ ~101 cols
        note_lines = [
            ln for ln in raw.splitlines() if ln.startswith("        ") and "http" not in ln
        ]
        assert note_lines and max(len(ln) for ln in note_lines) <= 101
        # value preserved + URL intact
        got = _reload(tmp_pids_dir / "test.yaml")["TEST"]["iocontrol"]["AA02"]["notes"].strip()
        assert got == note
        assert "1R2dW_4-ANg04TQdkHH52-8wdYItBkWDfppXcsqOc-m1M" in got


class TestGuards:
    def test_unknown_field(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            update_iocontrol_field("TEST", "AA01", "on", "2FAA0103", pids_dir=tmp_pids_dir)

    def test_unknown_did(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            update_iocontrol_field("TEST", "FFFF", "label", "x", pids_dir=tmp_pids_dir)

    def test_editable_fields_list(self):
        assert set(EDITABLE_FIELDS) == {"label", "verified", "notes"}


class TestPromoteDiscovery:
    def test_promotes_with_inferred_single_byte_state(self, tmp_pids_dir: Path):
        """BB01 response 6FBB010100 → tail=0100 → on=2FBB01030100 (replay state)."""
        promote_discovery("TEST", "BB01", "Fan speed", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        entry = data["TEST"]["iocontrol"]["BB01"]
        assert entry["label"] == "Fan speed"
        assert entry["verified"] is False
        # YAML parses bare on/off keys as booleans
        assert entry[True] == "2FBB01030100"
        assert entry[False] == "2FBB0100"
        # Removed from discoveries
        assert "BB01" not in (data["TEST"].get("iocontrol_discoveries") or {})

    def test_infers_from_longer_response(self, tmp_pids_dir: Path):
        """BB02 response 6FBB02010000 → tail=010000 → on=2FBB0203010000."""
        promote_discovery("TEST", "BB02", "Three-byte actuator", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["BB02"][True] == "2FBB0203010000"

    def test_falls_back_on_short_response(self, tmp_pids_dir: Path):
        """BB03 response 6FBB03 (3 bytes, no tail) → fallback payload 00."""
        promote_discovery("TEST", "BB03", "Fallback default", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        assert data["TEST"]["iocontrol"]["BB03"][True] == "2FBB030300"

    def test_removes_discoveries_section_when_emptied(self, tmp_pids_dir: Path):
        promote_discovery("TEST", "BB01", "a", pids_dir=tmp_pids_dir)
        promote_discovery("TEST", "BB02", "b", pids_dir=tmp_pids_dir)
        promote_discovery("TEST", "BB03", "c", pids_dir=tmp_pids_dir)
        raw = (tmp_pids_dir / "test.yaml").read_text()
        assert "iocontrol_discoveries:" not in raw
        data = _reload(tmp_pids_dir / "test.yaml")
        assert set(data["TEST"]["iocontrol"]) >= {"AA01", "AA02", "AA03", "BB01", "BB02", "BB03"}

    def test_keeps_other_discoveries_intact(self, tmp_pids_dir: Path):
        promote_discovery("TEST", "BB01", "a", pids_dir=tmp_pids_dir)
        data = _reload(tmp_pids_dir / "test.yaml")
        disc = data["TEST"]["iocontrol_discoveries"]
        assert set(disc) == {"BB02", "BB03"}

    def test_refuses_duplicate_did(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError, match="already exists"):
            promote_discovery("TEST", "AA01", "dup", pids_dir=tmp_pids_dir)

    def test_unknown_did(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            promote_discovery("TEST", "FFFF", "nope", pids_dir=tmp_pids_dir)

    def test_rejects_invalid_did_format(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError, match="4 hex digits"):
            promote_discovery("TEST", "ZZZZ", "bad", pids_dir=tmp_pids_dir)

    def test_rejects_empty_label(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError, match="label"):
            promote_discovery("TEST", "BB01", "   ", pids_dir=tmp_pids_dir)

    def test_unknown_ecu(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            promote_discovery("NOPE", "BB01", "x", pids_dir=tmp_pids_dir)


class TestSetPidVariableLength:
    def test_sets_true(self, tmp_pids_dir: Path):
        p = set_pid_variable_length("TEST", "2200", True, pids_dir=tmp_pids_dir)
        pid = _reload(p)["TEST"]["pids"][2200]
        assert pid["variable_length"] is True
        # Existing content preserved.
        assert pid["parameters"]["DUMMY"]["expression"] == "B:0"

    def test_clears_when_false(self, tmp_pids_dir: Path):
        set_pid_variable_length("TEST", "2200", True, pids_dir=tmp_pids_dir)
        p = set_pid_variable_length("TEST", "2200", False, pids_dir=tmp_pids_dir)
        assert "variable_length" not in _reload(p)["TEST"]["pids"][2200]

    def test_idempotent_true(self, tmp_pids_dir: Path):
        set_pid_variable_length("TEST", "2200", True, pids_dir=tmp_pids_dir)
        p = set_pid_variable_length("TEST", "2200", True, pids_dir=tmp_pids_dir)
        text = p.read_text()
        # No duplicate line.
        assert text.count("variable_length: true") == 1

    def test_unknown_pid(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError, match="not found"):
            set_pid_variable_length("TEST", "9999", True, pids_dir=tmp_pids_dir)

    def test_unknown_ecu(self, tmp_pids_dir: Path):
        with pytest.raises(PidsEditError):
            set_pid_variable_length("NOPE", "2200", True, pids_dir=tmp_pids_dir)

    def test_mixed_case_ecu_key(self, tmp_path: Path):
        # ECU file keys may be mixed-case (e.g. `Unknown-746`) while callers pass
        # the upper-cased name. The post-edit check must resolve case-insensitively.
        d = tmp_path / "pids"
        d.mkdir()
        (d / "unknown-746.yaml").write_text(
            "Unknown-746:\n"
            "  tx_id: 0x746\n"
            "  pids:\n"
            "    21F2:\n"
            "      status: static\n"
            "      parameters:\n"
            "        DUMMY:\n"
            '          expression: "B:0"\n'
        )
        p = set_pid_variable_length("Unknown-746", "21F2", True, pids_dir=d)
        assert _reload(p)["Unknown-746"]["pids"]["21F2"]["variable_length"] is True


_NOTES_FIXTURE = """\
TEST:
  tx_id: 0x7E0
  pids:
    "2200":
      status: draft
      vehicle_states: [SLEEP, READY]
      notes: >
        An existing folded note
        spanning several lines.
      parameters:
        DUMMY:
          expression: "B:0"

    "2201":
      status: draft
      parameters:
        OTHER:
          expression: "B:1"
"""


class TestSetPidNotes:
    """PID-level free-text notes — the editor that closes the hand-edit gap.

    A PID's header note records what the page *is*, so it goes stale as decoding
    progresses; correcting it previously had no surgical editor at all.
    """

    @pytest.fixture
    def d(self, tmp_path: Path) -> Path:
        p = tmp_path / "pids"
        p.mkdir()
        (p / "test.yaml").write_text(_NOTES_FIXTURE)
        return p

    def test_replaces_existing_block_scalar_in_place(self, d: Path):
        p = set_pid_notes("TEST", "2200", "Corrected: the tail bytes are a counter.", pids_dir=d)
        pid = _reload(p)["TEST"]["pids"]["2200"]
        assert pid["notes"] == "Corrected: the tail bytes are a counter."
        # Siblings and the parameter block survive.
        assert pid["status"] == "draft"
        assert pid["parameters"]["DUMMY"]["expression"] == "B:0"
        # Position preserved: the note stays above parameters:.
        text = p.read_text()
        assert text.index("notes:") < text.index("parameters:")

    def test_inserts_above_parameters_when_absent(self, d: Path):
        p = set_pid_notes("TEST", "2201", "A brand new note.", pids_dir=d)
        assert _reload(p)["TEST"]["pids"]["2201"]["notes"] == "A brand new note."
        block = p.read_text().split('"2201":')[1]
        assert block.index("notes:") < block.index("parameters:"), (
            "a new note belongs above parameters:, not appended after it"
        )

    def test_long_note_becomes_wrapped_folded_block(self, d: Path):
        long = " ".join(f"word{i}" for i in range(60))
        p = set_pid_notes("TEST", "2201", long, pids_dir=d)
        text = p.read_text()
        assert "notes: >-" in text
        assert _reload(p)["TEST"]["pids"]["2201"]["notes"] == long
        assert max(len(ln) for ln in text.splitlines()) <= 100

    def test_short_note_stays_inline(self, d: Path):
        p = set_pid_notes("TEST", "2201", "13 bytes.", pids_dir=d)
        assert "notes: 13 bytes." in p.read_text()

    def test_clears_a_block_scalar_without_orphaning_its_body(self, d: Path):
        # Regression: removing only the `notes:` header line leaves the folded
        # body indented under nothing, which is invalid YAML.
        p = set_pid_notes("TEST", "2200", None, pids_dir=d)
        pid = _reload(p)["TEST"]["pids"]["2200"]
        assert "notes" not in pid
        assert pid["parameters"]["DUMMY"]["expression"] == "B:0"
        assert "spanning several lines" not in p.read_text()

    def test_blank_value_clears(self, d: Path):
        p = set_pid_notes("TEST", "2200", "   ", pids_dir=d)
        assert "notes" not in _reload(p)["TEST"]["pids"]["2200"]

    def test_clearing_absent_note_errors(self, d: Path):
        with pytest.raises(PidsEditError, match="no notes"):
            set_pid_notes("TEST", "2201", None, pids_dir=d)

    def test_preserves_blank_line_between_pid_blocks(self, d: Path):
        # The blank separator before the next PID must survive, and must keep
        # surviving across repeated edits (it used to be eaten one line at a time).
        for i in range(3):
            p = set_pid_notes("TEST", "2200", f"note {i}", pids_dir=d)
        assert '\n\n    "2201":' in p.read_text()

    def test_round_trips_repeated_edits(self, d: Path):
        for i in range(3):
            p = set_pid_notes("TEST", "2200", f"revision {i} of the note", pids_dir=d)
        text = p.read_text()
        assert text.count("notes:") == 1, "no duplicate notes: field"
        assert _reload(p)["TEST"]["pids"]["2200"]["notes"] == "revision 2 of the note"

    def test_unknown_pid(self, d: Path):
        with pytest.raises(PidsEditError, match="not found"):
            set_pid_notes("TEST", "9999", "x", pids_dir=d)

    def test_unknown_ecu(self, d: Path):
        with pytest.raises(PidsEditError):
            set_pid_notes("NOPE", "2200", "x", pids_dir=d)

    def test_ecu_without_pids_section(self, tmp_path: Path):
        p = tmp_path / "pids"
        p.mkdir()
        (p / "bare.yaml").write_text("BARE:\n  tx_id: 0x700\n")
        with pytest.raises(PidsEditError, match="no pids:"):
            set_pid_notes("BARE", "2200", "x", pids_dir=p)


class TestRemoveFieldLine:
    """Unit tests for the _remove_field_line block-scalar trap (Part C)."""

    def test_removes_a_plain_scalar_line_exactly(self):
        from canlib.pids_edit._text import _remove_field_line

        block = "      status: draft\n      verified: true\n"
        assert _remove_field_line(block, "status", indent=6) == "      verified: true\n"

    def test_absent_field_returns_block_unchanged(self):
        from canlib.pids_edit._text import _remove_field_line

        block = "      verified: true\n      notes: hi\n"
        assert _remove_field_line(block, "status", indent=6) == block

    def test_removing_a_folded_block_leaves_valid_yaml(self):
        # A folded `>-` field's indented body must go with its header, or the
        # orphaned continuation lines produce invalid YAML (the trap this fixes).
        block = (
            "    22B002:\n"
            "      status: draft\n"
            "      notes: >-\n"
            "        a long folded note that wraps\n"
            "        across two physical lines\n"
            "      verified: true\n"
        )
        from canlib.pids_edit._text import _remove_field_line

        out = _remove_field_line(block, "notes", indent=6)
        assert "folded note" not in out, "the block body must be removed with its header"
        assert "verified: true" in out
        data = yaml.safe_load(out)  # raises on invalid YAML
        assert data == {"22B002": {"status": "draft", "verified": True}}

    def test_removing_a_nested_map_leaves_valid_yaml(self):
        block = (
            "      status: draft\n"
            "      values:\n"
            "        40: fan1\n"
            "        45: fanMAX\n"
            "      verified: true\n"
        )
        from canlib.pids_edit._text import _remove_field_line

        out = _remove_field_line(block, "values", indent=6)
        assert "fan1" not in out
        assert yaml.safe_load(out) == {"status": "draft", "verified": True}
