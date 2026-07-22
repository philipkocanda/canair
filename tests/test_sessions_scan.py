"""Tests for canlib.modes.sessions_scan, the sessions: YAML writer, and schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.commands.validate import collect_pids_validation, load_schema, validate_ecu_file
from canlib.modes.sessions_scan import (
    KWP_SESSION_MODES,
    UDS_SESSION_MODES,
    SessionHit,
    classify,
    mode_sessions_scan,
    scan_ecu_sessions,
)
from canlib.pids_edit import append_sessions_block


# ── classify() ───────────────────────────────────────────────────────────────


def test_classify_supported():
    resp = {"ok": True, "hex": "50 81 00 32 01 F4", "bytes": [0x50, 0x81]}
    assert classify(resp) == ("supported", None)


def test_classify_not_supported_subFunctionNotSupported():
    resp = {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}
    assert classify(resp) == ("not-supported", 0x12)


def test_classify_service_absent_aborts():
    resp = {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"}
    assert classify(resp) == ("service-absent", 0x11)


def test_classify_wrong_session():
    resp = {"ok": False, "nrc": 0x7F, "nrc_desc": "serviceNotSupportedInActiveSession"}
    assert classify(resp) == ("wrong-session", 0x7F)


def test_classify_unknown_nrc_is_not_supported():
    resp = {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect"}
    assert classify(resp) == ("not-supported", 0x22)


def test_classify_error_no_nrc():
    resp = {"ok": False, "error": "timeout"}
    assert classify(resp) == ("error", None)


def test_safe_modes_exclude_programming_sessions():
    """Programming sessions (UDS 0x02, KWP2000 0x85) must NEVER be probed."""
    assert 0x02 not in UDS_SESSION_MODES
    assert 0x85 not in KWP_SESSION_MODES
    # Only the well-known safe modes.
    assert set(UDS_SESSION_MODES) <= {0x01, 0x03}
    assert set(KWP_SESSION_MODES) <= {0x81, 0x82, 0x83}


# ── append_sessions_block() ──────────────────────────────────────────────────


FIXTURE_YAML = """\
TEST:
  tx_id: 0x7E4
  identity:
    description: Test ECU
    id_protocol: KWP2000
  availability:
    - ready
  pids:
    2101:
      parameters:
        TEST_PARAM:
          expression: B4
          verified: false

  research:
    - type: scan
      target: "10 81 session"
      status: pending
"""


@pytest.fixture
def pids_dir(tmp_path: Path) -> Path:
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    return tmp_path


def test_append_sessions_adds_section(pids_dir):
    hits = [
        SessionHit(mode=0x81, name="standardDiagnosticSession", supported=True,
                   nrc=None, nrc_desc=None),
        SessionHit(mode=0x83, name="extendedDiagnosticSession", supported=False,
                   nrc=0x12, nrc_desc="subFunctionNotSupported"),
    ]
    path = append_sessions_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load(path.read_text())

    sessions = data["TEST"]["sessions"]
    assert sessions["81"]["supported"] is True
    assert sessions["81"]["name"] == "standardDiagnosticSession"
    assert "nrc" not in sessions["81"]

    assert sessions["83"]["supported"] is False
    assert sessions["83"]["nrc"] == 0x12
    assert sessions["83"]["nrc_desc"] == "subFunctionNotSupported"


def test_append_sessions_keys_stay_hex_strings(pids_dir):
    """All-digit modes like '03' must not be read as YAML ints/octal."""
    hits = [
        SessionHit(mode=0x01, name="defaultSession", supported=True, nrc=None, nrc_desc=None),
        SessionHit(mode=0x03, name="extendedDiagnosticSession", supported=True,
                   nrc=None, nrc_desc=None),
    ]
    path = append_sessions_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load(path.read_text())
    assert set(data["TEST"]["sessions"].keys()) == {"01", "03"}


def test_append_sessions_preserves_existing_sections(pids_dir):
    hits = [SessionHit(mode=0x81, name=None, supported=True, nrc=None, nrc_desc=None)]
    append_sessions_block("TEST", hits, pids_dir=pids_dir)
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())

    assert data["TEST"]["tx_id"] == 0x7E4
    assert data["TEST"]["availability"] == ["ready"]
    assert 2101 in data["TEST"]["pids"]
    assert data["TEST"]["research"][0]["target"] == "10 81 session"


def test_append_sessions_merges_and_sorts(pids_dir):
    append_sessions_block(
        "TEST",
        [SessionHit(mode=0x83, name=None, supported=False, nrc=0x12, nrc_desc="x")],
        pids_dir=pids_dir,
    )
    append_sessions_block(
        "TEST",
        [SessionHit(mode=0x81, name=None, supported=True, nrc=None, nrc_desc=None)],
        pids_dir=pids_dir,
    )
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    sessions = data["TEST"]["sessions"]
    # Both coexist and are sorted ascending by mode.
    assert list(sessions.keys()) == ["81", "83"]


def test_append_sessions_upserts_on_conflict(pids_dir):
    append_sessions_block(
        "TEST",
        [SessionHit(mode=0x81, name=None, supported=False, nrc=0x12, nrc_desc="old")],
        pids_dir=pids_dir,
    )
    append_sessions_block(
        "TEST",
        [SessionHit(mode=0x81, name="standardDiagnosticSession", supported=True,
                    nrc=None, nrc_desc=None)],
        pids_dir=pids_dir,
    )
    data = yaml.safe_load((pids_dir / "test.yaml").read_text())
    assert data["TEST"]["sessions"]["81"]["supported"] is True
    assert "nrc" not in data["TEST"]["sessions"]["81"]


def test_append_empty_hits_is_noop(pids_dir):
    original = (pids_dir / "test.yaml").read_text()
    append_sessions_block("TEST", [], pids_dir=pids_dir)
    assert (pids_dir / "test.yaml").read_text() == original


def test_written_sessions_pass_schema_validation(pids_dir):
    hits = [
        SessionHit(mode=0x81, name="standardDiagnosticSession", supported=True,
                   nrc=None, nrc_desc=None),
        SessionHit(mode=0x83, name="extendedDiagnosticSession", supported=False,
                   nrc=0x12, nrc_desc="subFunctionNotSupported"),
    ]
    path = append_sessions_block("TEST", hits, pids_dir=pids_dir)
    errors, _warnings, stats = collect_pids_validation([path])
    assert errors == [], errors
    assert stats["sessions"] == 2


# ── schema validation of sessions: ───────────────────────────────────────────


def _write_ecu(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ecu.yaml"
    p.write_text(body)
    return p


def test_schema_rejects_supported_with_nrc(tmp_path):
    p = _write_ecu(
        tmp_path,
        "E:\n  tx_id: 0x7E0\n  sessions:\n    \"81\":\n      supported: true\n      nrc: 0x12\n",
    )
    errors, _w, _s = collect_pids_validation([p])
    assert any("must not also carry an 'nrc'" in e for e in errors)


def test_schema_rejects_bad_session_key(tmp_path):
    p = _write_ecu(
        tmp_path,
        "E:\n  tx_id: 0x7E0\n  sessions:\n    \"XYZ\":\n      supported: true\n",
    )
    errors, _w, _s = collect_pids_validation([p])
    assert any("session key must be a 1-2 digit hex" in e for e in errors)


def test_schema_warns_unsupported_without_nrc(tmp_path):
    p = _write_ecu(
        tmp_path,
        "E:\n  tx_id: 0x7E0\n  sessions:\n    \"83\":\n      supported: false\n",
    )
    _e, warnings, _s = collect_pids_validation([p])
    assert any("unsupported session has no 'nrc'" in w for w in warnings)


def test_schema_accepts_valid_sessions(tmp_path):
    p = _write_ecu(
        tmp_path,
        'E:\n  tx_id: 0x7E0\n  sessions:\n'
        '    "01":\n      supported: true\n'
        '    "03":\n      supported: false\n      nrc: 0x12\n      nrc_desc: subFunctionNotSupported\n',
    )
    schema = load_schema()
    errors, _w, stats = validate_ecu_file(p, schema)
    assert errors == [], errors
    assert stats["sessions"] == 2


def test_sessions_field_declared_in_schema():
    schema = load_schema()
    assert "sessions" in set(schema.get("optional_ecu_fields", []))
    assert "sessions_fields" in schema


# ── scanner probing (fake terminal) ──────────────────────────────────────────


class _FakeTerminal:
    def __init__(self, responses=None):
        self.headers: list[int] = []
        self.reqs: list[str] = []
        self._responses = responses or {}

    async def set_header(self, tx_id):
        self.headers.append(tx_id)

    async def send_uds(self, req, **kw):
        self.reqs.append(req)
        # Default: subFunctionNotSupported.
        return self._responses.get(req, {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"})


@pytest.mark.asyncio
async def test_scan_ecu_sessions_records_supported_and_unsupported():
    term = _FakeTerminal(responses={
        "1081": {"ok": True, "hex": "50 81", "bytes": [0x50, 0x81]},
    })
    hits = await scan_ecu_sessions(
        term, "BMS", 0x7E4, KWP_SESSION_MODES, throttle_ms=0, write_yaml=False,
    )
    by_mode = {h.mode: h for h in hits}
    assert by_mode[0x81].supported is True
    assert by_mode[0x82].supported is False and by_mode[0x82].nrc == 0x12
    assert by_mode[0x83].supported is False
    # Probes exactly the KWP safe modes, in order — never 0x85.
    assert term.reqs == ["1081", "1082", "1083"]


@pytest.mark.asyncio
async def test_scan_ecu_sessions_aborts_on_service_absent():
    term = _FakeTerminal(responses={
        "1001": {"ok": False, "nrc": 0x11, "nrc_desc": "serviceNotSupported"},
    })
    hits = await scan_ecu_sessions(
        term, "X", 0x7E0, UDS_SESSION_MODES, throttle_ms=0, write_yaml=False,
    )
    assert hits == []
    # Aborted after the first probe returned 0x11.
    assert term.reqs == ["1001"]


@pytest.mark.asyncio
async def test_mode_sessions_scan_autoselects_by_protocol(tmp_path):
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    term = _FakeTerminal(responses={
        "1081": {"ok": True, "hex": "50 81", "bytes": [0x50, 0x81]},
    })
    pids_data = {"ecus": {"test": yaml.safe_load(FIXTURE_YAML)["TEST"]}}
    # Give the ECU a tx_id/identity so protocol auto-select picks KWP modes.
    pids_data["ecus"]["test"]["tx_id"] = 0x7E4

    results = await mode_sessions_scan(
        term, pids_data, ecus=["TEST"], throttle_ms=0, write_yaml=False,
    )
    # KWP2000 id_protocol → probes 81/82/83, not the UDS 01/03.
    assert term.reqs == ["1081", "1082", "1083"]
    assert 0x81 in {h.mode for h in results["TEST"]}


@pytest.mark.asyncio
async def test_mode_sessions_scan_write_yaml_false_does_not_write(tmp_path):
    """With write_yaml=False the scanner must not touch any YAML file."""
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    original = (tmp_path / "test.yaml").read_text()
    term = _FakeTerminal(responses={
        "1081": {"ok": True, "hex": "50 81", "bytes": [0x50, 0x81]},
    })
    ecu_def = yaml.safe_load(FIXTURE_YAML)["TEST"]
    ecu_def["tx_id"] = 0x7E4
    pids_data = {"ecus": {"test": ecu_def}}

    await mode_sessions_scan(
        term, pids_data, ecus=["TEST"], throttle_ms=0, write_yaml=False,
    )
    assert (tmp_path / "test.yaml").read_text() == original


def test_sessions_writeback_round_trips_through_writer(tmp_path):
    """The scanner's writeback (append_sessions_block) persists a hit to YAML."""
    (tmp_path / "test.yaml").write_text(FIXTURE_YAML)
    append_sessions_block(
        "TEST",
        [SessionHit(mode=0x81, name="standardDiagnosticSession", supported=True,
                    nrc=None, nrc_desc=None)],
        pids_dir=tmp_path,
    )
    data = yaml.safe_load((tmp_path / "test.yaml").read_text())
    assert data["TEST"]["sessions"]["81"]["supported"] is True

