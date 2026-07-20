"""Tests for research.py — aggregating pids/ research: sections."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "research", Path(__file__).resolve().parent.parent / "research.py"
)
research = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(research)


@pytest.fixture
def pids_dir(tmp_path):
    """A minimal pids/ dir with two ECUs carrying research entries."""
    (tmp_path / "mcu.yaml").write_text(
        """\
MCU:
  tx_id: 0x7E3
  pids: {}
  research:
    - type: decode
      target: "2102"
      status: captured
      priority: P1
      prerequisite: [charging]
      notes: "motor torque candidate"
      what_to_test: ["a", "b"]
    - type: scan
      target: "22 E001-E010"
      status: pending
      priority: P2
      prerequisite: [acc]
    - type: verify
      target: "OLD"
      status: done
      priority: P1
"""
    )
    (tmp_path / "bms.yaml").write_text(
        """\
BMS:
  tx_id: 0x7E4
  pids: {}
  research:
    - type: iocontrol_scan
      target: "2F E000-E0FF"
      status: pending
      priority: P1
      prerequisite: [acc]
"""
    )
    # A file with no research: section must be tolerated.
    (tmp_path / "clu.yaml").write_text("CLU:\n  tx_id: 0x7C6\n  pids: {}\n")
    return tmp_path


class TestLoadResearch:
    def test_flattens_all_ecus_with_context(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert len(recs) == 4  # 3 MCU + 1 BMS
        mcu = [r for r in recs if r["ecu"] == "MCU"]
        assert len(mcu) == 3
        assert all(r["tx_id"] == 0x7E3 for r in mcu)

    def test_ignores_files_without_research(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert not any(r["ecu"] == "CLU" for r in recs)


class TestFilters:
    def test_done_hidden_by_default(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs)
        assert all(r["status"] != "done" for r in out)
        assert len(out) == 3

    def test_include_done(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, include_done=True)
        assert len(out) == 4

    def test_explicit_done_status_shows_done(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, status="done")
        assert len(out) == 1
        assert out[0]["status"] == "done"

    def test_filter_by_ecu_case_insensitive(self, pids_dir):
        recs = research.load_research(pids_dir)
        assert len(research.filter_records(recs, ecu="mcu")) == 2  # done hidden

    def test_filter_by_type(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, rtype="scan")
        assert len(out) == 1 and out[0]["target"] == "22 E001-E010"

    def test_filter_by_priority(self, pids_dir):
        recs = research.load_research(pids_dir)
        # P1 open items: MCU decode 2102 + BMS iocontrol_scan (done P1 hidden)
        out = research.filter_records(recs, priority="P1")
        assert len(out) == 2

    def test_filter_by_prerequisite(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, prerequisite="acc")
        assert {r["ecu"] for r in out} == {"MCU", "BMS"}
        assert all("acc" in r["prerequisite"] for r in out)

    def test_filters_combine_and(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = research.filter_records(recs, ecu="MCU", prerequisite="acc")
        assert len(out) == 1 and out[0]["type"] == "scan"


class TestSort:
    def test_priority_orders_first(self, pids_dir):
        recs = research.load_research(pids_dir)
        out = sorted(research.filter_records(recs), key=research._sort_key)
        prios = [r.get("priority") for r in out]
        assert prios == sorted(prios, key=lambda p: research._PRIO_RANK.get(p, 99))
        assert out[0]["priority"] == "P1"
