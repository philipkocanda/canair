"""Tests for canlib.pids_edit.set_can_bus (top-level CAN-bus segment editing)."""

import textwrap

import pytest
import yaml

from canlib.pids_edit import PidsEditError, set_can_bus


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
                id_protocol: UDS
              pids:
                2101:
                  status: active
                  parameters: {}
            """
        )
    )
    return tmp_path


def _ecu(pids_dir):
    return yaml.safe_load((pids_dir / "test.yaml").read_text())["TESTECU"]


def test_adds_single_code(pids_dir):
    set_can_bus("TESTECU", ["B-CAN"], pids_dir=pids_dir)
    ecu = _ecu(pids_dir)
    assert ecu["can_bus"] == ["B-CAN"]
    # Sibling fields and header comment survive; placed before identity.
    text = (pids_dir / "test.yaml").read_text()
    assert "Header comment that must survive edits" in text
    assert ecu["identity"]["description"] == "Test ECU"
    assert text.index("can_bus") < text.index("identity")
    # Rendered as a flow (inline) list, not a block list.
    assert "can_bus: [B-CAN]" in text


def test_adds_multiple_codes(pids_dir):
    set_can_bus("TESTECU", ["H-CAN", "P-CAN"], pids_dir=pids_dir)
    assert _ecu(pids_dir)["can_bus"] == ["H-CAN", "P-CAN"]
    assert "can_bus: [H-CAN, P-CAN]" in (pids_dir / "test.yaml").read_text()


def test_replaces_existing_in_place(pids_dir):
    set_can_bus("TESTECU", ["B-CAN"], pids_dir=pids_dir)
    set_can_bus("TESTECU", ["C-CAN", "M-CAN"], pids_dir=pids_dir)
    ecu = _ecu(pids_dir)
    assert ecu["can_bus"] == ["C-CAN", "M-CAN"]
    # No duplicate can_bus key left behind.
    assert (pids_dir / "test.yaml").read_text().count("can_bus:") == 1


def test_replaces_block_style_with_flow(pids_dir):
    # A pre-existing block-style can_bus: is fully replaced by the inline form
    # (no leftover `  - X` continuation lines).
    (pids_dir / "test.yaml").write_text(
        textwrap.dedent(
            """\
            TESTECU:
              tx_id: 0x7E9
              can_bus:
                - H
                - P
              identity:
                description: Test ECU
            """
        )
    )
    set_can_bus("TESTECU", ["H-CAN", "P-CAN"], pids_dir=pids_dir)
    text = (pids_dir / "test.yaml").read_text()
    assert _ecu(pids_dir)["can_bus"] == ["H-CAN", "P-CAN"]
    assert "can_bus: [H-CAN, P-CAN]" in text
    assert "  - H" not in text and "  - P" not in text
    assert text.count("can_bus") == 1


def test_strips_and_dedups_blanks(pids_dir):
    set_can_bus("TESTECU", [" B-CAN ", ""], pids_dir=pids_dir)
    assert _ecu(pids_dir)["can_bus"] == ["B-CAN"]


def test_rejects_empty(pids_dir):
    with pytest.raises(PidsEditError):
        set_can_bus("TESTECU", ["  "], pids_dir=pids_dir)


def test_missing_ecu(tmp_path):
    (tmp_path / "_meta.yaml").write_text('car_model: "T"\ninit: "x"\n')
    with pytest.raises(PidsEditError):
        set_can_bus("NOPE", ["B-CAN"], pids_dir=tmp_path)
