"""Tests for canlib.modes.routines_scan and the routines: YAML writer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.modes.routines_scan import (
    NRC_ABSENT,
    NRC_EXISTS_HINTS,
    NRC_WRONG_SESSION,
    RoutineHit,
    classify,
)
from canlib.pids_edit import append_routines_block


# ── classify() ───────────────────────────────────────────────────────────────


def test_classify_positive():
    resp = {"ok": True, "hex": "71 03 F0 10 00", "bytes": [0x71, 0x03, 0xF0, 0x10, 0x00]}
    assert classify(resp) == ("positive", None)


def test_classify_absent_requestOutOfRange():
    resp = {"ok": False, "nrc": 0x31, "nrc_desc": "requestOutOfRange"}
    assert classify(resp) == ("absent", 0x31)


def test_classify_absent_serviceNotSupported():
    resp = {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"}
    assert classify(resp) == ("absent", 0x11)


def test_classify_absent_subFunctionNotSupported():
    resp = {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}
    assert classify(resp) == ("absent", 0x12)


def test_classify_exists_requestSequenceError():
    resp = {"ok": False, "nrc": 0x24, "nrc_desc": "requestSequenceError"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x24


def test_classify_exists_securityAccessDenied():
    resp = {"ok": False, "nrc": 0x33, "nrc_desc": "securityAccessDenied"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x33


def test_classify_exists_conditionsNotCorrect():
    resp = {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x22


def test_classify_wrong_session():
    resp = {"ok": False, "nrc": 0x7F, "nrc_desc": "serviceNotSupportedInActiveSession"}
    cat, nrc = classify(resp)
    assert cat == "wrong-session"
    assert nrc == 0x7F


def test_classify_unknown_nrc_treated_as_exists():
    """Unknown NRCs should be treated as hits — don't miss anything."""
    resp = {"ok": False, "nrc": 0x55, "nrc_desc": "reserved"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x55


def test_classify_error_no_nrc():
    resp = {"ok": False, "error": "timeout"}
    cat, nrc = classify(resp)
    assert cat == "error"
    assert nrc is None


def test_nrc_sets_dont_overlap():
    """A given NRC must belong to exactly one category."""
    assert not (NRC_ABSENT & NRC_EXISTS_HINTS)
    assert NRC_WRONG_SESSION not in NRC_ABSENT
    assert NRC_WRONG_SESSION not in NRC_EXISTS_HINTS


# ── append_routines_block() ──────────────────────────────────────────────────


FIXTURE_YAML = """\
TEST:
  tx_id: 0x770
  availability:
    - ign
  pids:
    BC03:
      label: TEST_PID
      verified: false

  research:
    - type: scan
      target: 22BC00-22BCFF
      status: pending
"""


@pytest.fixture
def pids_dir(tmp_path: Path) -> Path:
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    return tmp_path


def test_append_routines_block_adds_new_section(pids_dir):
    hits = [
        RoutineHit(rid=0xF010, session="extended", response_hex="71 03 F0 10 00",
                   nrc=None, nrc_desc=None),
        RoutineHit(rid=0xF02A, session="default", response_hex="",
                   nrc=0x24, nrc_desc="requestSequenceError"),
    ]
    path = append_routines_block("TEST", hits, pids_dir=pids_dir)
    text = path.read_text()
    data = yaml.safe_load(text)

    routines = data["TEST"]["routines"]
    assert "F010" in routines
    assert "F02A" in routines

    assert routines["F010"]["session"] == "extended"
    assert routines["F010"]["response"] == "71 03 F0 10 00"
    assert routines["F010"]["notes"] == ""

    assert routines["F02A"]["session"] == "default"
    assert routines["F02A"]["nrc"] == 0x24
    assert routines["F02A"]["nrc_desc"] == "requestSequenceError"


def test_append_routines_preserves_existing_sections(pids_dir):
    hits = [
        RoutineHit(rid=0xF010, session="default", response_hex="",
                   nrc=0x33, nrc_desc="securityAccessDenied"),
    ]
    append_routines_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())

    # Original sections intact
    assert data["TEST"]["tx_id"] == 0x770
    assert data["TEST"]["availability"] == ["ign"]
    assert "BC03" in data["TEST"]["pids"]
    assert data["TEST"]["pids"]["BC03"]["label"] == "TEST_PID"
    assert data["TEST"]["research"][0]["target"] == "22BC00-22BCFF"


def test_append_routines_replaces_existing_block(pids_dir):
    """Running the scanner twice should overwrite, not append duplicates."""
    first = [
        RoutineHit(rid=0xF001, session="default", response_hex="",
                   nrc=0x24, nrc_desc="requestSequenceError"),
    ]
    append_routines_block("TEST", first, pids_dir=pids_dir)

    second = [
        RoutineHit(rid=0xF002, session="default", response_hex="",
                   nrc=0x33, nrc_desc="securityAccessDenied"),
    ]
    append_routines_block("TEST", second, pids_dir=pids_dir)

    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    routines = data["TEST"]["routines"]
    assert "F001" not in routines
    assert "F002" in routines


def test_append_empty_hits_is_noop(pids_dir):
    original = (pids_dir / "test.yaml").read_text()
    append_routines_block("TEST", [], pids_dir=pids_dir)
    assert (pids_dir / "test.yaml").read_text() == original


def test_append_unknown_ecu_raises(pids_dir):
    hits = [RoutineHit(rid=0xF010, session="default", response_hex="",
                       nrc=0x24, nrc_desc="requestSequenceError")]
    with pytest.raises(Exception):
        append_routines_block("NONEXISTENT", hits, pids_dir=pids_dir)


def test_append_yaml_is_still_valid_after_write(pids_dir):
    hits = [
        RoutineHit(rid=i, session="default", response_hex="",
                   nrc=0x24, nrc_desc="requestSequenceError")
        for i in (0xF000, 0xF001, 0xF010, 0xF0FF)
    ]
    path = append_routines_block("TEST", hits, pids_dir=pids_dir)
    # Must round-trip cleanly
    data = yaml.safe_load(path.read_text())
    assert len(data["TEST"]["routines"]) == 4
    assert "F0FF" in data["TEST"]["routines"]
