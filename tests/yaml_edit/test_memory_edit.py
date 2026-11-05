"""Tests for canair.yaml_edit.memory — set-memory and the scan's region writer."""

from __future__ import annotations

import pytest
import yaml

from canair.ecus.memory import MemoryRegion
from canair.uds.memory import RegionResult, parse_format
from canair.yaml_edit.ecu import EcusEditError
from canair.yaml_edit.memory import record_scan, set_memory

BCM = """\
BCM:
  tx_id: 0x7A0
  identity:
    id_protocol: UDS  # keep me
  pids:
    22B003:
      status: active
      parameters:
        V:
          expression: B04
"""


@pytest.fixture
def ecus_dir(tmp_path):
    (tmp_path / "bcm.yaml").write_text(BCM)
    return tmp_path


def _bcm(ecus_dir) -> dict:
    return yaml.safe_load((ecus_dir / "bcm.yaml").read_text())["BCM"]


class TestEditor:
    def test_set_memory(self, ecus_dir):
        set_memory(0x7A0, fmt=parse_format("24"), max_block=128, session="03", ecus_dir=ecus_dir)
        assert _bcm(ecus_dir)["memory"] == {"format": "24", "max_block": 128, "session": "03"}
        assert "# keep me" in (ecus_dir / "bcm.yaml").read_text()

    def test_clear(self, ecus_dir):
        set_memory(0x7A0, fmt=parse_format("24"), session="03", ecus_dir=ecus_dir)
        set_memory(0x7A0, clear=["session"], ecus_dir=ecus_dir)
        assert _bcm(ecus_dir)["memory"] == {"format": "24"}

    def test_refused_value_reverts(self, ecus_dir):
        before = (ecus_dir / "bcm.yaml").read_text()
        with pytest.raises(EcusEditError):
            set_memory(0x7A0, max_block=9999, ecus_dir=ecus_dir)
        assert (ecus_dir / "bcm.yaml").read_text() == before

    def test_unknown_ecu(self, ecus_dir):
        with pytest.raises(EcusEditError, match="not registered"):
            set_memory(0x7E4, max_block=1, ecus_dir=ecus_dir)

    def test_record_scan_merges_by_overlap(self, ecus_dir):
        fmt = parse_format("24")
        first = [
            MemoryRegion(0x0, 0xFFF, RegionResult.OUT_OF_RANGE, 0x31),
            MemoryRegion(0x1000, 0x1FFF, RegionResult.READABLE, software="F195:1"),
        ]
        record_scan(0x7A0, first, fmt=fmt, max_block=64, ecus_dir=ecus_dir)
        second = [MemoryRegion(0x1800, 0x2FFF, RegionResult.LOCKED, 0x33)]
        record_scan(0x7A0, second, fmt=fmt, ecus_dir=ecus_dir)
        regions = _bcm(ecus_dir)["memory"]["regions"]
        assert [(r["start"], r["result"]) for r in regions] == [
            (0x0, "out_of_range"),
            (0x1800, "locked"),
        ]
        text = (ecus_dir / "bcm.yaml").read_text()
        assert "start: 0x00001800" in text


class TestMemoryPidKey:
    def test_token_becomes_the_request(self, ecus_dir):
        from canair.yaml_edit.memory import memory_pid_key

        set_memory(0x7A0, fmt=parse_format("24"), ecus_dir=ecus_dir)
        assert memory_pid_key("BCM", "23/FEDE1234+02", ecus_dir) == "2324FEDE12340002"
        assert memory_pid_key("BCM", "22B003", ecus_dir) == "22B003"

    def test_without_a_format_it_says_how_to_set_one(self, ecus_dir):
        from canair.yaml_edit.memory import memory_pid_key

        with pytest.raises(EcusEditError, match="canair memory set"):
            memory_pid_key("BCM", "23/FEDE1234+02", ecus_dir)

    def test_upsert_param_takes_the_token(self, ecus_dir):
        from canair.cli import main

        set_memory(0x7A0, fmt=parse_format("24"), ecus_dir=ecus_dir)
        argv = ["pids", "upsert-param", "BCM", "23/FEDE1234+02", "RAM_X", "B04"]
        assert main([*argv, "--dir", str(ecus_dir), "--no-validate"]) == 0
        assert "2324FEDE12340002" in {str(k) for k in _bcm(ecus_dir)["pids"]}
