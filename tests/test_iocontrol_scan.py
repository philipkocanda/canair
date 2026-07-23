"""Tests for canlib.modes.iocontrol_scan and the iocontrol_discoveries: YAML writer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.modes.iocontrol_scan import (
    NRC_ABSENT,
    NRC_EXISTS_HINTS,
    NRC_SF_UNSUPPORTED,
    NRC_WRONG_SESSION,
    SF_FREEZE,
    SF_RESET_TO_DEFAULT,
    SF_RETURN_CONTROL,
    SF_SHORT_TERM_ADJ,
    IOControlHit,
    classify,
    probe_iocontrol,
)
from canlib.pids_edit import append_iocontrol_discoveries_block


# ── classify() ───────────────────────────────────────────────────────────────


def test_classify_positive():
    resp = {"ok": True, "hex": "6F B0 01 00", "bytes": [0x6F, 0xB0, 0x01, 0x00]}
    assert classify(resp) == ("positive", None)


def test_classify_absent_requestOutOfRange():
    resp = {"ok": False, "nrc": 0x31, "nrc_desc": "requestOutOfRange"}
    assert classify(resp) == ("absent", 0x31)


def test_classify_service_absent():
    """NRC 0x11 means the whole ECU doesn't do 0x2F — scanner should abort."""
    resp = {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"}
    assert classify(resp) == ("service-absent", 0x11)


def test_classify_exists_subFunctionNotSupported():
    """Unlike routines scanner: SF 0x12 means DID is IOControl-capable but
    rejects SF 00 — still a hit."""
    resp = {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x12


def test_classify_exists_conditionsNotCorrect():
    resp = {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x22


def test_classify_exists_securityAccessDenied():
    resp = {"ok": False, "nrc": 0x33, "nrc_desc": "securityAccessDenied"}
    cat, nrc = classify(resp)
    assert cat == "exists"
    assert nrc == 0x33


def test_classify_wrong_session():
    resp = {"ok": False, "nrc": 0x7F, "nrc_desc": "serviceNotSupportedInActiveSession"}
    cat, nrc = classify(resp)
    assert cat == "wrong-session"
    assert nrc == 0x7F


def test_classify_unknown_nrc_treated_as_exists():
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
    assert not (NRC_ABSENT & NRC_EXISTS_HINTS)
    assert NRC_WRONG_SESSION not in NRC_ABSENT
    assert NRC_WRONG_SESSION not in NRC_EXISTS_HINTS
    assert NRC_SF_UNSUPPORTED not in NRC_ABSENT


def test_sub_function_constants():
    """Guard against accidental reordering of SF constants — critical for safety."""
    assert SF_RETURN_CONTROL == 0x00
    assert SF_RESET_TO_DEFAULT == 0x01
    assert SF_FREEZE == 0x02
    assert SF_SHORT_TERM_ADJ == 0x03


# ── probe_iocontrol() safety guard ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_refuses_unsafe_sub_functions():
    """probe_iocontrol must refuse any SF other than 0x00."""
    for unsafe_sf in (SF_RESET_TO_DEFAULT, SF_FREEZE, SF_SHORT_TERM_ADJ, 0x04, 0xFF):
        with pytest.raises(ValueError, match="safe"):
            await probe_iocontrol(terminal=None, did=0xB001, sub_function=unsafe_sf)


# ── append_iocontrol_discoveries_block() ─────────────────────────────────────


FIXTURE_YAML = """\
TEST:
  tx_id: 0x770
  vehicle_states:
    - acc2
  pids:
    BC03:
      label: TEST_PID
      verified: false
  iocontrol:
    B001:
      label: EXISTING_ACTUATOR
      on: "2F B0 01 03 01"
      off: "2F B0 01 00"
      verified: true

  research:
    - type: scan
      target: 22BC00-22BCFF
      status: pending
"""


@pytest.fixture
def pids_dir(tmp_path: Path) -> Path:
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    return tmp_path


def test_append_iocontrol_discoveries_adds_new_section(pids_dir):
    hits = [
        IOControlHit(did=0xB010, session="extended",
                     response_hex="6F B0 10 00", nrc=None, nrc_desc=None),
        IOControlHit(did=0xB02A, session="default",
                     response_hex="", nrc=0x33, nrc_desc="securityAccessDenied"),
    ]
    path = append_iocontrol_discoveries_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load(path.read_text())

    disc = data["TEST"]["iocontrol_discoveries"]
    assert "B010" in disc
    assert "B02A" in disc

    assert disc["B010"]["response"] == "6F B0 10 00"
    assert disc["B010"]["notes"] == ""

    assert disc["B02A"]["nrc"] == 0x33
    assert disc["B02A"]["nrc_desc"] == "securityAccessDenied"


def test_append_iocontrol_discoveries_preserves_curated_iocontrol(pids_dir):
    """Critical: the discovery writer MUST NOT touch the curated iocontrol: block."""
    hits = [
        IOControlHit(did=0xB099, session="default",
                     response_hex="", nrc=0x22, nrc_desc="conditionsNotCorrect"),
    ]
    path = append_iocontrol_discoveries_block("TEST", hits, pids_dir=pids_dir)
    # Check raw text: PyYAML parses `on:`/`off:` as boolean keys (YAML 1.1
    # quirk) so we can't rely on yaml.safe_load to preserve those field names.
    text = path.read_text()
    assert 'label: EXISTING_ACTUATOR' in text
    assert 'on: "2F B0 01 03 01"' in text
    assert 'off: "2F B0 01 00"' in text
    assert 'verified: true' in text
    # New discoveries in separate section
    data = yaml.safe_load(text)
    assert "B099" in data["TEST"]["iocontrol_discoveries"]


def test_append_iocontrol_discoveries_preserves_other_sections(pids_dir):
    hits = [
        IOControlHit(did=0xB050, session="default",
                     response_hex="", nrc=0x33, nrc_desc="securityAccessDenied"),
    ]
    append_iocontrol_discoveries_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())

    assert data["TEST"]["tx_id"] == 0x770
    assert data["TEST"]["vehicle_states"] == ["acc2"]
    assert "BC03" in data["TEST"]["pids"]
    assert data["TEST"]["research"][0]["target"] == "22BC00-22BCFF"


def test_append_iocontrol_discoveries_merges_with_existing(pids_dir):
    """Prior discoveries outside the new hit set must be preserved (merge)."""
    first = [IOControlHit(did=0xB001, session="default", response_hex="",
                          nrc=0x33, nrc_desc="securityAccessDenied")]
    append_iocontrol_discoveries_block("TEST", first, pids_dir=pids_dir)

    second = [IOControlHit(did=0xB002, session="default", response_hex="",
                           nrc=0x22, nrc_desc="conditionsNotCorrect")]
    append_iocontrol_discoveries_block("TEST", second, pids_dir=pids_dir)

    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    disc = data["TEST"]["iocontrol_discoveries"]
    # Both hits are present; second scan did not wipe the first.
    assert "B001" in disc
    assert "B002" in disc
    # Entries are sorted ascending by DID
    assert list(disc.keys()) == ["B001", "B002"]


def test_append_iocontrol_discoveries_upserts_same_did(pids_dir):
    """Re-scanning a DID overwrites its prior entry (latest result wins)."""
    first = [IOControlHit(did=0xB001, session="default", response_hex="",
                          nrc=0x33, nrc_desc="securityAccessDenied")]
    append_iocontrol_discoveries_block("TEST", first, pids_dir=pids_dir)

    # Same DID, different response (e.g., session-state changed between runs)
    second = [IOControlHit(did=0xB001, session="extended",
                           response_hex="6FB00100", nrc=None, nrc_desc=None)]
    append_iocontrol_discoveries_block("TEST", second, pids_dir=pids_dir)

    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    entry = data["TEST"]["iocontrol_discoveries"]["B001"]
    # The second (positive) result won; NRC fields are gone.
    assert entry["response"] == "6FB00100"
    assert entry["response"] == "6FB00100"
    assert "nrc" not in entry


def test_append_iocontrol_discoveries_empty_is_noop(pids_dir):
    original = (pids_dir / "test.yaml").read_text()
    append_iocontrol_discoveries_block("TEST", [], pids_dir=pids_dir)
    assert (pids_dir / "test.yaml").read_text() == original


def test_append_iocontrol_discoveries_unknown_ecu_raises(pids_dir):
    hits = [IOControlHit(did=0xB010, session="default", response_hex="",
                         nrc=0x22, nrc_desc="conditionsNotCorrect")]
    with pytest.raises(Exception):
        append_iocontrol_discoveries_block("NONEXISTENT", hits, pids_dir=pids_dir)


def test_append_iocontrol_discoveries_yaml_round_trip(pids_dir):
    hits = [
        IOControlHit(did=i, session="default", response_hex="",
                     nrc=0x33, nrc_desc="securityAccessDenied")
        for i in (0xB000, 0xB001, 0xB050, 0xBFFF)
    ]
    path = append_iocontrol_discoveries_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load(path.read_text())
    assert len(data["TEST"]["iocontrol_discoveries"]) == 4
    assert "BFFF" in data["TEST"]["iocontrol_discoveries"]


def test_routines_and_discoveries_coexist(pids_dir):
    """Both scanner sections can live side-by-side in the same ECU block."""
    from canlib.modes.routines_scan import RoutineHit
    from canlib.pids_edit import append_routines_block

    r_hits = [RoutineHit(rid=0xF010, session="default", response_hex="",
                         nrc=0x24, nrc_desc="requestSequenceError")]
    d_hits = [IOControlHit(did=0xB020, session="default", response_hex="",
                           nrc=0x33, nrc_desc="securityAccessDenied")]

    append_routines_block("TEST", r_hits, pids_dir=pids_dir)
    append_iocontrol_discoveries_block("TEST", d_hits, pids_dir=pids_dir)

    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    assert "F010" in data["TEST"]["routines"]
    assert "B020" in data["TEST"]["iocontrol_discoveries"]
    # Curated iocontrol still intact
    assert data["TEST"]["iocontrol"]["B001"]["label"] == "EXISTING_ACTUATOR"
