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
