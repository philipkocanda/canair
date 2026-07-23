"""Tests for canlib.pids_edit.set_identity_field (curated identity editing)."""

import textwrap

import pytest
import yaml

from canlib.pids_edit import PidsEditError, set_identity_field


@pytest.fixture
def pids_dir(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "Test"\ninit: "ATSP6;"\n')
    (tmp_path / "test.yaml").write_text(
        textwrap.dedent(
            """\
            # Header comment that must survive edits
            TESTECU:
              tx_id: 0x7E9
              identity:
                description: Test ECU
                part_number: "123"
                id_protocol: UDS
                notes: >
                  original multi-line
                  note body
              pids:
                2101:
                  status: active
                  parameters: {}
            """
        )
    )
    return tmp_path


def _identity(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TESTECU"]["identity"]


def test_replaces_existing_notes(pids_dir):
    set_identity_field("TESTECU", "notes", "brand new note", pids_dir=pids_dir)
    ident = _identity(pids_dir)
    assert ident["notes"].strip() == "brand new note"
    # Sibling fields and header comment survive.
    text = (pids_dir / "test.yaml").read_text()
    assert "Header comment that must survive edits" in text
    assert ident["part_number"] == "123"
    assert ident["description"] == "Test ECU"


def test_replaces_scalar_description(pids_dir):
    set_identity_field("TESTECU", "description", "Updated ECU", pids_dir=pids_dir)
    assert _identity(pids_dir)["description"] == "Updated ECU"


def test_adds_missing_field(pids_dir):
    set_identity_field("TESTECU", "alias", "TEC", pids_dir=pids_dir)
    ident = _identity(pids_dir)
    assert ident["alias"] == "TEC"
    # Existing fields untouched.
    assert ident["notes"].strip().startswith("original multi-line")


def test_multiline_notes_roundtrip(pids_dir):
    set_identity_field("TESTECU", "notes", "line one\nline two", pids_dir=pids_dir)
    # Folded block scalar collapses the newline to a space.
    assert _identity(pids_dir)["notes"].strip() == "line one line two"


def test_rejects_empty_value(pids_dir):
    with pytest.raises(PidsEditError):
        set_identity_field("TESTECU", "notes", "   ", pids_dir=pids_dir)


def test_rejects_bad_field_name(pids_dir):
    with pytest.raises(PidsEditError):
        set_identity_field("TESTECU", "bad field!", "x", pids_dir=pids_dir)


def test_missing_identity_section(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
    (tmp_path / "n.yaml").write_text("NOID:\n  tx_id: 0x700\n  pids: {}\n")
    with pytest.raises(PidsEditError):
        set_identity_field("NOID", "notes", "x", pids_dir=tmp_path)
