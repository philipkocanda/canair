"""Fields a ``dtcs:`` section states once for its codes (canlib/dtc/definitions.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from canlib.commands.validate import load_schema, validate_ecu_file
from canlib.dtc.definitions import effective_entries, is_dtc_code, split_section
from canlib.explorer.export import _dtc_records
from canlib.pids import load_pids

ECU = """\
ESC:
  tx_id: 0x7D1
  dtcs:
    source: Service manual, ESC diagnosis
    source_links:
      - https://example.invalid/esc
    verified: false
    C1101:
      description: Battery voltage high
    C1102:
      description: Battery voltage low
      source: Shop manual table
    C1604:
      description: ECU hardware error
      source_links: []
      verified: true
"""


@pytest.fixture
def ecus_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ecus"
    d.mkdir()
    (d / "esc.yaml").write_text(ECU)
    return d


class TestIsDtcCode:
    @pytest.mark.parametrize("key", ["C1101", "b16a8", "U0111", "P0A0D", "C160E"])
    def test_codes(self, key):
        assert is_dtc_code(key)

    @pytest.mark.parametrize("key", ["source", "source_links", "verified", "C1101-08", "C110"])
    def test_not_codes(self, key):
        assert not is_dtc_code(key)


class TestEffectiveEntries:
    def test_entries_inherit_what_they_omit_and_keep_what_they_set(self):
        got = effective_entries(
            {
                "source": "S",
                "source_links": ["u"],
                "verified": False,
                "C1101": {"description": "a"},
                "C1102": {"description": "b", "source": "own"},
                "C1604": {"description": "c", "source_links": [], "verified": True},
            }
        )
        assert set(got) == {"C1101", "C1102", "C1604"}
        assert got["C1101"] == {
            "description": "a",
            "source": "S",
            "source_links": ["u"],
            "verified": False,
        }
        assert got["C1102"]["source"] == "own"
        assert got["C1102"]["source_links"] == ["u"]
        # An explicit empty list / true is an override, not a gap.
        assert got["C1604"]["source_links"] == []
        assert got["C1604"]["verified"] is True

    def test_section_without_shared_fields_is_returned_as_written(self):
        dtcs = {"C1101": {"description": "a"}, "C1102": "bare string"}
        assert effective_entries(dtcs) == dtcs

    def test_bare_string_entry_gets_nothing(self):
        assert effective_entries({"source": "S", "C1101": "text"}) == {"C1101": "text"}

    def test_definition_on_file_is_not_modified(self):
        entry = {"description": "a"}
        effective_entries({"source": "S", "C1101": entry})
        assert entry == {"description": "a"}

    def test_split_section(self):
        shared, entries = split_section({"source": "S", "C1101": {}})
        assert shared == {"source": "S"}
        assert entries == {"C1101": {}}


def test_loaded_definitions_carry_resolved_entries_only(ecus_dir):
    dtcs = load_pids(ecus_dir)["ecus"]["ESC"]["dtcs"]
    assert set(dtcs) == {"C1101", "C1102", "C1604"}
    assert dtcs["C1101"]["source"] == "Service manual, ESC diagnosis"
    assert dtcs["C1101"]["verified"] is False
    assert dtcs["C1102"]["source"] == "Shop manual table"
    assert dtcs["C1604"]["verified"] is True


class TestValidation:
    def _check(self, ecus_dir, text):
        (ecus_dir / "esc.yaml").write_text(text)
        return validate_ecu_file(ecus_dir / "esc.yaml", load_schema())

    def test_section_fields_validate_and_are_not_counted_as_codes(self, ecus_dir):
        errors, warnings, stats = self._check(ecus_dir, ECU)
        assert not errors
        assert not [w for w in warnings if "dtcs" in w]
        assert stats["dtcs"] == 3

    def test_section_field_of_the_wrong_type_is_an_error(self, ecus_dir):
        errors, _, _ = self._check(ecus_dir, ECU.replace("verified: false", "verified: maybe"))
        assert any("ESC/dtcs: verified must be a boolean" in e for e in errors)

    def test_entry_field_of_the_wrong_type_is_an_error(self, ecus_dir):
        errors, _, _ = self._check(
            ecus_dir, ECU.replace("      source_links: []", "      source_links: nope")
        )
        assert any("dtcs/C1604: source_links must be a list of strings" in e for e in errors)

    def test_key_that_is_neither_a_code_nor_a_section_field_warns(self, ecus_dir):
        _, warnings, _ = self._check(
            ecus_dir, ECU + "    C1616-08:\n      description: C-CAN bus off\n"
        )
        assert any("dtcs/C1616-08: not a DTC code" in w for w in warnings)


def test_explorer_exports_codes_only(ecus_dir):
    from canlib.yaml_rt import round_trip_yaml

    doc = round_trip_yaml().load(ECU)
    records = _dtc_records(doc["ESC"], "esc.yaml")
    assert [r["code"] for r in records] == ["C1101", "C1102", "C1604"]
