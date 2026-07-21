"""Tests for canlib.ecus_edit (ecus.yaml writer) and validate ecus.

All writes are directed at a tmp_path-backed ecus.yaml via the ``path=`` kwarg
so the real profile registry is never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.commands.validate import validate_ecus_registry
from canlib.ecus_edit import (
    EcusEditError,
    append_scan_log,
    register_ecu,
    set_ecu_fields,
    tx_key,
)

SEED = """\
# Vehicle ECU registry — hand-authored header comment.
ecus:
  0x770:
    name: IGPM
    description: Integrated Gateway & Power Module
    id_protocol: UDS
"""


@pytest.fixture
def ecus_file(tmp_path: Path) -> Path:
    p = tmp_path / "ecus.yaml"
    p.write_text(SEED)
    return p


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


class TestTxKey:
    def test_formats_uppercase_3_digit(self):
        assert tx_key(0x7E0) == "0x7E0"
        assert tx_key(0x770) == "0x770"

    @pytest.mark.parametrize("bad", [-1, 0x800, "770", 1.0])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(EcusEditError):
            tx_key(bad)


class TestRegisterEcu:
    def test_adds_new_entry(self, ecus_file):
        changed = register_ecu(0x7E0, "VCU", id_protocol="KWP2000", path=ecus_file)
        assert changed is True
        data = _load(ecus_file)
        assert data["ecus"][0x7E0] == {"name": "VCU", "id_protocol": "KWP2000"}

    def test_default_unknown_name(self, ecus_file):
        register_ecu(0x7C0, path=ecus_file)
        assert _load(ecus_file)["ecus"][0x7C0]["name"] == "Unknown-7C0"

    def test_creates_file_when_absent(self, tmp_path):
        p = tmp_path / "new.yaml"
        register_ecu(0x7E2, "MCU", path=p)
        assert p.exists()
        assert _load(p)["ecus"][0x7E2]["name"] == "MCU"

    def test_merge_does_not_clobber_existing(self, ecus_file):
        # name already IGPM; a merge must not overwrite it, but fills blanks.
        changed = register_ecu(
            0x770, "SHOULD_NOT_WIN", part_number="91950G7510", path=ecus_file
        )
        assert changed is True
        entry = _load(ecus_file)["ecus"][0x770]
        assert entry["name"] == "IGPM"
        assert entry["part_number"] == "91950G7510"

    def test_merge_noop_returns_false(self, ecus_file):
        assert register_ecu(0x770, "IGPM", path=ecus_file) is False

    def test_overwrite_replaces(self, ecus_file):
        register_ecu(0x770, "NEWNAME", overwrite=True, path=ecus_file)
        assert _load(ecus_file)["ecus"][0x770]["name"] == "NEWNAME"

    def test_rejects_unknown_field(self, ecus_file):
        with pytest.raises(EcusEditError, match="unknown ECU field"):
            register_ecu(0x7E0, "VCU", bogus_field="x", path=ecus_file)

    def test_preserves_header_comment(self, ecus_file):
        register_ecu(0x7E0, "VCU", path=ecus_file)
        assert "hand-authored header comment" in ecus_file.read_text()


class TestSetEcuFields:
    def test_updates_existing(self, ecus_file):
        changed = set_ecu_fields(0x770, part_number="91950G7510", path=ecus_file)
        assert changed is True
        assert _load(ecus_file)["ecus"][0x770]["part_number"] == "91950G7510"

    def test_no_clobber_without_overwrite(self, ecus_file):
        assert set_ecu_fields(0x770, name="X", path=ecus_file) is False
        assert _load(ecus_file)["ecus"][0x770]["name"] == "IGPM"

    def test_raises_for_unregistered(self, ecus_file):
        with pytest.raises(EcusEditError, match="not registered"):
            set_ecu_fields(0x7E0, name="VCU", path=ecus_file)

    def test_invalid_value_is_reverted(self, ecus_file):
        # id_protocol passes the field-name check but fails schema validation,
        # so the write must be rolled back and the file left untouched.
        before = ecus_file.read_text()
        with pytest.raises(EcusEditError, match="invalid after edit"):
            set_ecu_fields(0x770, id_protocol="BOGUS", overwrite=True, path=ecus_file)
        assert ecus_file.read_text() == before


class TestAppendScanLog:
    def test_appends_entry_with_date_default(self, ecus_file):
        append_scan_log(
            0x770, service=0x22, range="F100-F1FF", hits=3, state="acc", path=ecus_file
        )
        data = _load(ecus_file)
        entries = data["scan_log"][0x770]
        assert len(entries) == 1
        assert entries[0]["hits"] == 3
        assert entries[0]["range"] == "F100-F1FF"
        assert "date" in entries[0]  # defaulted to today

    def test_appends_to_existing_list(self, ecus_file):
        append_scan_log(0x770, service=0x21, path=ecus_file)
        append_scan_log(0x770, service=0x22, path=ecus_file)
        assert len(_load(ecus_file)["scan_log"][0x770]) == 2


class TestValidateEcusRegistry:
    def _write(self, tmp_path, text):
        p = tmp_path / "ecus.yaml"
        p.write_text(text)
        return p

    def test_valid_file_has_no_errors(self, ecus_file):
        errors, _warnings, stats = validate_ecus_registry(ecus_file)
        assert errors == []
        assert stats["ecus"] == 1

    def test_missing_name_is_error(self, tmp_path):
        p = self._write(tmp_path, "ecus:\n  0x770:\n    description: no name\n")
        errors, _, _ = validate_ecus_registry(p)
        assert any("missing required field 'name'" in e for e in errors)

    def test_bad_tx_key_is_error(self, tmp_path):
        p = self._write(tmp_path, "ecus:\n  notahex:\n    name: X\n")
        errors, _, _ = validate_ecus_registry(p)
        assert any("hex TX id" in e for e in errors)

    def test_invalid_protocol_is_error(self, tmp_path):
        p = self._write(tmp_path, "ecus:\n  0x770:\n    name: X\n    id_protocol: FOO\n")
        errors, _, _ = validate_ecus_registry(p)
        assert any("invalid id_protocol" in e for e in errors)

    def test_unknown_field_is_warning(self, tmp_path):
        p = self._write(tmp_path, "ecus:\n  0x770:\n    name: X\n    wat: 1\n")
        errors, warnings, _ = validate_ecus_registry(p)
        assert errors == []
        assert any("unknown field 'wat'" in w for w in warnings)

    def test_duplicate_name_is_error(self, tmp_path):
        p = self._write(
            tmp_path,
            "ecus:\n  0x770:\n    name: DUP\n  0x7A0:\n    name: DUP\n",
        )
        errors, _, _ = validate_ecus_registry(p)
        assert any("duplicate ECU name" in e for e in errors)
