"""Locating a profile's per-ECU definition files (canlib.ecu_files)."""

import pytest

from canlib import ecu_files
from canlib.profile import Profile

AAF = """\
AAF:
  tx_id: 0x7EA
  pids:
    2181:
      status: active
"""

BMS = """\
BMS:
  tx_id: 0x7E4
"""


@pytest.fixture
def ecus(tmp_path):
    d = tmp_path / "ecus"
    d.mkdir()
    (d / "aaf.yaml").write_text(AAF)
    (d / "bms.yaml").write_text(BMS)
    return d


class TestIterEcuFiles:
    def test_sorted_and_skips_disabled(self, ecus):
        (ecus / "_parked.yaml").write_text("OLD:\n  tx_id: 0x700\n")
        assert [p.name for p in ecu_files.iter_ecu_files(ecus)] == ["aaf.yaml", "bms.yaml"]

    def test_include_disabled_for_the_validator(self, ecus):
        (ecus / "_parked.yaml").write_text("OLD:\n  tx_id: 0x700\n")
        names = [p.name for p in ecu_files.iter_ecu_files(ecus, include_disabled=True)]
        assert names == ["_parked.yaml", "aaf.yaml", "bms.yaml"]

    def test_absent_dir_yields_nothing(self, tmp_path):
        assert list(ecu_files.iter_ecu_files(tmp_path / "nope")) == []


class TestFindByName:
    def test_finds_case_insensitively(self, ecus):
        assert ecu_files.find_by_name("aaf", ecus).name == "aaf.yaml"
        assert ecu_files.find_by_name("AAF", ecus).name == "aaf.yaml"

    def test_missing_returns_none(self, ecus):
        assert ecu_files.find_by_name("NOPE", ecus) is None

    def test_ignores_a_nested_key_of_the_same_name(self, ecus):
        # Only a *top-level* key defines an ECU; "2181:" is nested, and a param
        # named like an ECU must not claim the file.
        (ecus / "other.yaml").write_text("OTHER:\n  pids:\n    2181:\n      AAF: x\n")
        assert ecu_files.find_by_name("AAF", ecus).name == "aaf.yaml"

    def test_skips_disabled_files(self, ecus):
        (ecus / "_old.yaml").write_text("GHOST:\n  tx_id: 0x700\n")
        assert ecu_files.find_by_name("GHOST", ecus) is None


class TestFindByTx:
    def test_finds_path_and_name(self, ecus):
        path, name = ecu_files.find_by_tx(0x7E4, ecus)
        assert (path.name, name) == ("bms.yaml", "BMS")

    def test_missing_returns_none_pair(self, ecus):
        assert ecu_files.find_by_tx(0x123, ecus) == (None, None)

    def test_unparseable_file_does_not_abort_the_search(self, ecus):
        (ecus / "aaa_broken.yaml").write_text("{{{ not yaml\n")
        path, name = ecu_files.find_by_tx(0x7E4, ecus)
        assert (path.name, name) == ("bms.yaml", "BMS")


class TestDirResolution:
    def test_explicit_dir_wins(self, ecus):
        assert ecu_files.ecus_dir(ecus) == ecus

    def test_profile_argument(self, tmp_path):
        prof = Profile("p", tmp_path)
        assert ecu_files.ecus_dir(None, profile=prof) == tmp_path / "ecus"

    def test_defaults_to_the_active_profile(self, tmp_path, monkeypatch):
        from canlib import profile

        monkeypatch.setattr(profile, "_active", Profile("p", tmp_path))
        assert ecu_files.ecus_dir() == tmp_path / "ecus"
