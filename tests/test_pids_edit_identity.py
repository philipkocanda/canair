"""Tests for the curated identity editors (set_identity_field/remove_identity_field)."""

import textwrap

import pytest
import yaml

from canlib.pids_edit import PidsEditError, remove_identity_field, set_identity_field


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


class TestRemoveIdentityField:
    def test_removes_scalar_field(self, pids_dir):
        remove_identity_field("TESTECU", "part_number", pids_dir=pids_dir)
        ident = _identity(pids_dir)
        assert "part_number" not in ident
        # Siblings and the header comment survive.
        assert ident["description"] == "Test ECU"
        assert ident["notes"].strip().startswith("original multi-line")
        assert "Header comment that must survive edits" in (pids_dir / "test.yaml").read_text()

    def test_removes_block_scalar_field(self, pids_dir):
        remove_identity_field("TESTECU", "notes", pids_dir=pids_dir)
        ident = _identity(pids_dir)
        assert "notes" not in ident
        # The folded body went with it — no orphaned continuation lines.
        assert "original multi-line" not in (pids_dir / "test.yaml").read_text()
        assert ident["id_protocol"] == "UDS"

    def test_absent_field_is_an_error(self, pids_dir):
        with pytest.raises(PidsEditError, match=r"no identity\.alias"):
            remove_identity_field("TESTECU", "alias", pids_dir=pids_dir)

    def test_rejects_bad_field_name(self, pids_dir):
        with pytest.raises(PidsEditError):
            remove_identity_field("TESTECU", "bad field!", pids_dir=pids_dir)

    def test_missing_identity_section(self, tmp_path):
        (tmp_path / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
        (tmp_path / "n.yaml").write_text("NOID:\n  tx_id: 0x700\n  pids: {}\n")
        with pytest.raises(PidsEditError):
            remove_identity_field("NOID", "notes", pids_dir=tmp_path)

    def test_removing_last_field_drops_the_block(self, tmp_path):
        # An empty `identity:` key parses to None, so the whole block goes.
        (tmp_path / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
        (tmp_path / "o.yaml").write_text(
            "ONE:\n  tx_id: 0x700\n  identity:\n    alias: X\n  pids: {}\n"
        )
        remove_identity_field("ONE", "alias", pids_dir=tmp_path)
        doc = yaml.safe_load((tmp_path / "o.yaml").read_text())["ONE"]
        assert "identity" not in doc
        assert doc["tx_id"] == 0x700
