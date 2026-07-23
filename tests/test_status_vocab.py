"""Consolidation coverage: PID `status:` lifecycle, `vehicle_states` vocabulary
single-source, generate_profile shipping gate, and legacy-field rejection.

These guard the ignored/static/enabled -> status refactor and the
availability/prerequisite/state -> vehicle_states rename.
"""

from pathlib import Path

import pytest

from canlib.commands.validate import collect_pids_validation
from canlib.commands.wican import generate_profile
from canlib.pids_edit import PidsEditError, set_pid_status
from canlib.states import POWER_STATES, allowed_states, join_states, parse_states

# ── vocabulary single source ───────────────────────────────────────────────

class TestVocabularySource:
    def test_power_states_base(self):
        assert POWER_STATES == ("sleep", "plugged", "acc", "acc2", "ready", "charging")

    def test_allowed_states_superset_of_base(self):
        assert set(POWER_STATES) <= allowed_states()

    def test_parse_states_from_comma_string(self):
        assert parse_states("Ready, Parked") == ["ready", "parked"]

    def test_parse_states_from_list(self):
        assert parse_states(["ACC2", " charging "]) == ["acc2", "charging"]

    def test_parse_states_none_and_empty(self):
        assert parse_states(None) == []
        assert parse_states("") == []

    def test_join_states_roundtrips(self):
        assert join_states(["ready", "parked"]) == "ready, parked"
        assert join_states([]) == ""
        assert join_states("ready") == "ready"


# ── generate_profile ships only `active` PIDs (the leak fix) ────────────────

class TestGenerateProfileShipping:
    def _data(self):
        return {
            "car_model": "Test",
            "init": "ATZ;",
            "ecus": {
                "TEST": {
                    "tx_id": 0x7E0,
                    "pids": {
                        "2101": {"status": "active",
                                 "parameters": {"A": {"expression": "B0", "verified": True}}},
                        "2102": {"status": "draft",
                                 "parameters": {"B": {"expression": "B1", "verified": True}}},
                        "21F2": {"status": "static",
                                 "parameters": {"C": {"expression": "B2", "verified": True}}},
                        "22DEAD": {"status": "ignored",
                                   "parameters": {"D": {"expression": "B3", "verified": True}}},
                    },
                }
            },
        }

    def test_only_active_pids_shipped(self):
        prof = generate_profile(self._data())
        shipped = {p["pid"].upper() for p in prof["pids"]}
        assert shipped == {"2101"}  # draft/static/ignored all excluded

    def test_no_leak_even_with_parameters(self):
        # draft/static/ignored carry decodable params but must NOT reach the device.
        prof = generate_profile(self._data())
        names = {n for p in prof["pids"] for n in p["parameters"]}
        assert names == {"A"}


# ── validate rejects legacy fields (hard cut-over) ──────────────────────────

def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ecu.yaml"
    p.write_text(body)
    return p


class TestLegacyFieldRejection:
    @pytest.mark.parametrize("field,value", [
        ("ignored", "true"),
        ("static", "true"),
        ("enabled", "false"),
    ])
    def test_legacy_pid_boolean_is_error(self, tmp_path, field, value):
        path = _write(tmp_path, f"""\
TEST:
  tx_id: 0x7E0
  pids:
    2101:
      {field}: {value}
      parameters:
        A:
          expression: B0
""")
        errors, _w, _s = collect_pids_validation([path])
        assert any(f"legacy field '{field}'" in e for e in errors), errors

    def test_legacy_availability_is_error(self, tmp_path):
        path = _write(tmp_path, """\
TEST:
  tx_id: 0x7E0
  availability: [ready]
  pids:
    2101:
      parameters:
        A:
          expression: B0
""")
        errors, _w, _s = collect_pids_validation([path])
        assert any("legacy field 'availability'" in e for e in errors), errors

    def test_legacy_prerequisite_is_error(self, tmp_path):
        path = _write(tmp_path, """\
TEST:
  tx_id: 0x7E0
  pids:
    2101:
      parameters:
        A:
          expression: B0
  research:
    - type: scan
      target: "22 E0"
      status: pending
      prerequisite: [acc]
""")
        errors, _w, _s = collect_pids_validation([path])
        assert any("legacy field 'prerequisite'" in e for e in errors), errors

    def test_invalid_status_value_is_error(self, tmp_path):
        path = _write(tmp_path, """\
TEST:
  tx_id: 0x7E0
  pids:
    2101:
      status: bogus
      parameters:
        A:
          expression: B0
""")
        errors, _w, _s = collect_pids_validation([path])
        assert any("invalid status 'bogus'" in e for e in errors), errors

    def test_clean_status_and_vehicle_states_pass(self, tmp_path):
        path = _write(tmp_path, """\
TEST:
  tx_id: 0x7E0
  vehicle_states: [ready, charging]
  pids:
    2101:
      status: draft
      vehicle_states: [ready]
      parameters:
        A:
          expression: B0
  research:
    - type: scan
      target: "22 E0"
      status: pending
      vehicle_states: [acc]
""")
        errors, _w, _s = collect_pids_validation([path])
        assert errors == [], errors


# ── set_pid_status surgical edit ────────────────────────────────────────────

FIXTURE = """\
TEST:
  tx_id: 0x7E0
  pids:
    2101:
      period: 5000
      parameters:
        A:
          expression: B0
    2102:
      status: draft
      parameters:
        B:
          expression: B1
"""


class TestSetPidStatus:
    def _load(self, d: Path):
        import yaml
        return yaml.safe_load((d / "test.yaml").read_text())

    @pytest.fixture
    def pids_dir(self, tmp_path):
        (tmp_path / "test.yaml").write_text(FIXTURE)
        return tmp_path

    def test_sets_status(self, pids_dir):
        set_pid_status("TEST", "2101", "static", pids_dir=pids_dir)
        assert self._load(pids_dir)["TEST"]["pids"][2101]["status"] == "static"

    def test_active_removes_key(self, pids_dir):
        set_pid_status("TEST", "2102", "active", pids_dir=pids_dir)
        assert "status" not in self._load(pids_dir)["TEST"]["pids"][2102]

    def test_rejects_bad_value(self, pids_dir):
        with pytest.raises(PidsEditError):
            set_pid_status("TEST", "2101", "bogus", pids_dir=pids_dir)

    def test_unknown_pid_raises(self, pids_dir):
        with pytest.raises(PidsEditError):
            set_pid_status("TEST", "9999", "draft", pids_dir=pids_dir)

    def test_preserves_other_fields(self, pids_dir):
        set_pid_status("TEST", "2101", "ignored", pids_dir=pids_dir)
        pid = self._load(pids_dir)["TEST"]["pids"][2101]
        assert pid["status"] == "ignored"
        assert pid["period"] == 5000
        assert pid["parameters"]["A"]["expression"] == "B0"
