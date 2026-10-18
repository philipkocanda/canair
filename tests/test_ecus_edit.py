"""Tests for canlib.ecus_edit (per-ECU file writer) and identity validation.

All writes are directed at a tmp_path-backed ``ecus/`` directory via the
``ecus_dir=`` kwarg so the real profile is never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from canlib.commands.validate import collect_pids_validation
from canlib.ecus_edit import (
    EcusEditError,
    append_scan_log,
    register_ecu,
    rename_ecu,
    set_addressing,
    set_ecu_fields,
    tx_key,
)

SEED_IGPM = """\
# hand-authored header comment.
IGPM:
  tx_id: 0x770
  identity:
    description: Integrated Gateway & Power Module
    id_protocol: UDS
"""


@pytest.fixture
def ecus_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ecus"
    d.mkdir()
    (d / "igpm.yaml").write_text(SEED_IGPM)
    return d


def _load_ecu(ecus_dir: Path, filename: str) -> dict:
    return yaml.safe_load((ecus_dir / filename).read_text())


class TestTxKey:
    def test_formats_uppercase_3_digit(self):
        assert tx_key(0x7E0) == "0x7E0"
        assert tx_key(0x770) == "0x770"

    def test_formats_29bit_8_digit(self):
        assert tx_key(0x18DA10F1) == "0x18DA10F1"
        assert tx_key(0x800) == "0x00000800"

    @pytest.mark.parametrize("bad", [-1, 0x20000000, "770", 1.0])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(EcusEditError):
            tx_key(bad)


class TestRegisterEcu:
    def test_adds_new_file(self, ecus_dir):
        changed = register_ecu(0x7E0, "VCU", id_protocol="KWP2000", ecus_dir=ecus_dir)
        assert changed is True
        data = _load_ecu(ecus_dir, "vcu.yaml")
        assert data["VCU"]["tx_id"] == 0x7E0
        assert data["VCU"]["identity"] == {"id_protocol": "KWP2000"}

    def test_default_unknown_name(self, ecus_dir):
        register_ecu(0x7C0, ecus_dir=ecus_dir)
        data = _load_ecu(ecus_dir, "unknown-7c0.yaml")
        assert "Unknown-7C0" in data

    def test_creates_dir_when_absent(self, tmp_path):
        d = tmp_path / "ecus"
        register_ecu(0x7E2, "MCU", ecus_dir=d)
        assert (d / "mcu.yaml").exists()
        assert _load_ecu(d, "mcu.yaml")["MCU"]["tx_id"] == 0x7E2

    def test_merge_fills_blank_identity(self, ecus_dir):
        # Existing IGPM (found by tx) — merge fills its identity, keeps the key.
        changed = register_ecu(0x770, "SHOULD_NOT_WIN", part_number="91950G7510", ecus_dir=ecus_dir)
        assert changed is True
        entry = _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]
        assert entry["identity"]["part_number"] == "91950G7510"
        assert entry["identity"]["description"] == "Integrated Gateway & Power Module"

    def test_merge_noop_returns_false(self, ecus_dir):
        assert register_ecu(0x770, id_protocol="UDS", ecus_dir=ecus_dir) is False

    def test_overwrite_replaces_identity_field(self, ecus_dir):
        register_ecu(0x770, description="New desc", overwrite=True, ecus_dir=ecus_dir)
        entry = _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]
        assert entry["identity"]["description"] == "New desc"

    def test_rejects_unknown_field(self, ecus_dir):
        with pytest.raises(EcusEditError, match="unknown identity field"):
            register_ecu(0x7E0, "VCU", bogus_field="x", ecus_dir=ecus_dir)

    def test_preserves_header_comment(self, ecus_dir):
        register_ecu(0x770, part_number="91950G7510", ecus_dir=ecus_dir)
        assert "hand-authored header comment" in (ecus_dir / "igpm.yaml").read_text()


class TestRenameEcu:
    def test_renames_key_and_file(self, ecus_dir):
        register_ecu(0x7D5, "Unknown-7D5", ecus_dir=ecus_dir)
        new_path = rename_ecu(0x7D5, "EPB", ecus_dir=ecus_dir)
        assert new_path == ecus_dir / "epb.yaml"
        assert new_path.exists()
        assert not (ecus_dir / "unknown-7d5.yaml").exists()  # old file gone
        data = _load_ecu(ecus_dir, "epb.yaml")
        assert "EPB" in data
        assert "Unknown-7D5" not in data
        assert data["EPB"]["tx_id"] == 0x7D5

    def test_preserves_header_comment(self, ecus_dir):
        # The IGPM seed carries a leading document comment; renaming keeps it.
        rename_ecu(0x770, "Gateway", ecus_dir=ecus_dir)
        assert "hand-authored header comment" in (ecus_dir / "gateway.yaml").read_text()

    def test_resolves_old_by_tx(self, ecus_dir):
        # The caller resolves the selector to a tx_id; rename finds the file by it.
        rename_ecu(0x770, "Gateway", ecus_dir=ecus_dir)
        assert _load_ecu(ecus_dir, "gateway.yaml")["Gateway"]["tx_id"] == 0x770

    def test_empty_name_rejected(self, ecus_dir):
        with pytest.raises(EcusEditError, match="non-empty"):
            rename_ecu(0x770, "   ", ecus_dir=ecus_dir)

    def test_same_name_rejected(self, ecus_dir):
        with pytest.raises(EcusEditError, match="already named"):
            rename_ecu(0x770, "IGPM", ecus_dir=ecus_dir)

    def test_unregistered_rejected(self, ecus_dir):
        with pytest.raises(EcusEditError, match="not registered"):
            rename_ecu(0x7E0, "VCU", ecus_dir=ecus_dir)

    def test_collision_with_other_ecu_rejected(self, ecus_dir):
        # A second ECU already owns the target slug — refuse rather than clobber.
        register_ecu(0x7E0, "VCU", ecus_dir=ecus_dir)
        with pytest.raises(EcusEditError, match="already exists"):
            rename_ecu(0x770, "VCU", ecus_dir=ecus_dir)
        # The would-be victim and source both survive untouched.
        assert _load_ecu(ecus_dir, "vcu.yaml")["VCU"]["tx_id"] == 0x7E0
        assert "IGPM" in _load_ecu(ecus_dir, "igpm.yaml")


class TestSetEcuFields:
    def test_updates_existing(self, ecus_dir):
        changed = set_ecu_fields(0x770, part_number="91950G7510", ecus_dir=ecus_dir)
        assert changed is True
        entry = _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]
        assert entry["identity"]["part_number"] == "91950G7510"

    def test_no_clobber_without_overwrite(self, ecus_dir):
        assert set_ecu_fields(0x770, id_protocol="KWP2000", ecus_dir=ecus_dir) is False
        entry = _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]
        assert entry["identity"]["id_protocol"] == "UDS"

    def test_raises_for_unregistered(self, ecus_dir):
        with pytest.raises(EcusEditError, match="not registered"):
            set_ecu_fields(0x7E0, description="VCU", ecus_dir=ecus_dir)

    def test_invalid_value_is_reverted(self, ecus_dir):
        # id_protocol passes the field-name check but fails schema validation,
        # so the write must be rolled back and the file left untouched.
        before = (ecus_dir / "igpm.yaml").read_text()
        with pytest.raises(EcusEditError, match="invalid after edit"):
            set_ecu_fields(0x770, id_protocol="BOGUS", overwrite=True, ecus_dir=ecus_dir)
        assert (ecus_dir / "igpm.yaml").read_text() == before


class TestSetAddressing:
    def test_set_rx_only(self, ecus_dir):
        register_ecu(0x704, "VCU", ecus_dir=ecus_dir)
        changed = set_addressing(0x704, rx_id=0x784, ecus_dir=ecus_dir)
        assert changed is True
        entry = _load_ecu(ecus_dir, "vcu.yaml")["VCU"]
        assert entry["rx_id"] == 0x784

    def test_extended_11bit(self, ecus_dir):
        # BMW/PSA extended-11-bit: mode + target byte. Gap G-I.
        register_ecu(0x6F1, "DME", ecus_dir=ecus_dir)
        set_addressing(
            0x6F1, mode="normal_extended_11bit", target_address=0x12, rx_id=0x612, ecus_dir=ecus_dir
        )
        entry = _load_ecu(ecus_dir, "dme.yaml")["DME"]
        assert entry["addressing"]["mode"] == "normal_extended_11bit"
        assert entry["addressing"]["target_address"] == 0x12
        assert entry["rx_id"] == 0x612

    def test_register_29bit_functional_tx_with_fc_id(self, ecus_dir):
        # Gap G-J: functional-TX 29-bit ECU created atomically (mode present so the
        # 29-bit tx_id passes the width check), with a physical FC override.
        register_ecu(
            0x18DB33F1,
            "EVC",
            ecus_dir=ecus_dir,
            mode="normal_29bit",
            rx_id=0x18DAF1DB,
            fc_id=0x18DADBF1,
        )
        entry = _load_ecu(ecus_dir, "evc.yaml")["EVC"]
        assert entry["tx_id"] == 0x18DB33F1
        assert entry["addressing"]["mode"] == "normal_29bit"
        assert entry["addressing"]["fc_id"] == 0x18DADBF1

    def test_unknown_mode_rejected(self, ecus_dir):
        register_ecu(0x704, "VCU", ecus_dir=ecus_dir)
        with pytest.raises(EcusEditError, match="unknown addressing mode"):
            set_addressing(0x704, mode="bogus", ecus_dir=ecus_dir)

    def test_noop_returns_false_and_leaves_file_untouched(self, ecus_dir):
        # Applying the same values twice is a no-op: the second call reports False
        # ("nothing to change") and must not rewrite the file.
        register_ecu(0x6F1, "DME", ecus_dir=ecus_dir)
        assert (
            set_addressing(
                0x6F1,
                mode="normal_extended_11bit",
                target_address=0x12,
                rx_id=0x612,
                ecus_dir=ecus_dir,
            )
            is True
        )
        fpath = ecus_dir / "dme.yaml"
        before = fpath.read_text()
        before_mtime = fpath.stat().st_mtime_ns
        # Re-apply the identical values — no change expected.
        assert (
            set_addressing(
                0x6F1,
                mode="normal_extended_11bit",
                target_address=0x12,
                rx_id=0x612,
                ecus_dir=ecus_dir,
            )
            is False
        )
        assert fpath.read_text() == before
        assert fpath.stat().st_mtime_ns == before_mtime

    def test_byte_out_of_range_rejected(self, ecus_dir):
        register_ecu(0x6F1, "DME", ecus_dir=ecus_dir)
        with pytest.raises(EcusEditError, match="target_address"):
            set_addressing(0x6F1, target_address=0x100, ecus_dir=ecus_dir)

    def test_raises_for_unregistered(self, ecus_dir):
        with pytest.raises(EcusEditError, match="not registered"):
            set_addressing(0x7E0, rx_id=0x7E8, ecus_dir=ecus_dir)


class TestAppendScanLog:
    def test_appends_entry_with_date_default(self, ecus_dir):
        append_scan_log(
            0x770,
            service=0x22,
            range="F100-F1FF",
            hits=3,
            vehicle_states=["acc"],
            ecus_dir=ecus_dir,
        )
        entry = _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]
        entries = entry["scan_log"]
        assert len(entries) == 1
        assert entries[0]["hits"] == 3
        assert entries[0]["range"] == "F100-F1FF"
        assert entries[0]["vehicle_states"] == ["ACC"]  # normalized to UPPERCASE
        assert "date" in entries[0]  # defaulted to today

    def test_appends_to_existing_list(self, ecus_dir):
        append_scan_log(0x770, service=0x21, ecus_dir=ecus_dir)
        append_scan_log(0x770, service=0x22, ecus_dir=ecus_dir)
        assert len(_load_ecu(ecus_dir, "igpm.yaml")["IGPM"]["scan_log"]) == 2

    def test_raises_for_unregistered(self, ecus_dir):
        with pytest.raises(EcusEditError, match="not registered"):
            append_scan_log(0x7E0, service=0x22, ecus_dir=ecus_dir)


_LONG_NOTE = (
    "Seeded device-free from the upstream WiCAN community profile "
    "meatpiHQ/wican-fw vehicle_profiles/xpeng/xpeng_g6.json. The upstream routes "
    "every DID through a single responder at request 0x704 / response 0x784 "
    "(rx = tx + 0x80). Named BMS from the battery-centric DIDs; unverified."
)


class TestNoteRendering:
    """Free-text notes render per the shared policy: inline when short, else a
    wrapped folded (``>-``) block — and folding never reflows the rest of the file."""

    def test_long_note_folds_and_wraps(self, ecus_dir):
        register_ecu(0x7E0, "VCU", notes=_LONG_NOTE, ecus_dir=ecus_dir)
        text = (ecus_dir / "vcu.yaml").read_text()
        assert "notes: >-" in text  # folded block, not inline
        # Wrapped for readability, and value-preserving on round-trip.
        assert max(len(ln) for ln in text.splitlines()) <= 100
        assert _load_ecu(ecus_dir, "vcu.yaml")["VCU"]["identity"]["notes"] == _LONG_NOTE

    def test_short_note_stays_inline(self, ecus_dir):
        register_ecu(0x7E0, "VCU", notes="Short battery ECU note.", ecus_dir=ecus_dir)
        text = (ecus_dir / "vcu.yaml").read_text()
        assert "notes: >" not in text
        assert "notes: Short battery ECU note." in text

    def test_scan_log_note_folds(self, ecus_dir):
        append_scan_log(0x770, service=0x22, notes=_LONG_NOTE, ecus_dir=ecus_dir)
        text = (ecus_dir / "igpm.yaml").read_text()
        assert "notes: >-" in text
        assert _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]["scan_log"][0]["notes"] == _LONG_NOTE

    def test_set_ecu_fields_note_folds(self, ecus_dir):
        set_ecu_fields(0x770, notes=_LONG_NOTE, overwrite=True, ecus_dir=ecus_dir)
        text = (ecus_dir / "igpm.yaml").read_text()
        assert "notes: >-" in text
        assert _load_ecu(ecus_dir, "igpm.yaml")["IGPM"]["identity"]["notes"] == _LONG_NOTE

    def test_write_does_not_reflow_other_content(self, tmp_path):
        # A note-setting write must fold ONLY the note — a long pre-existing line
        # elsewhere in the file (here a long expression) stays byte-identical.
        d = tmp_path / "ecus"
        d.mkdir()
        long_expr = "(" + "+".join(f"B{i}*{i}" for i in range(1, 30)) + ")/1000"
        seed = (
            "BMS:\n"
            "  tx_id: 0x7E4\n"
            "  pids:\n"
            "    2101:\n"
            "      status: active\n"
            "      parameters:\n"
            "        WIDE:\n"
            f'          expression: "{long_expr}"\n'
        )
        (d / "bms.yaml").write_text(seed)
        set_ecu_fields(0x7E4, notes=_LONG_NOTE, overwrite=True, ecus_dir=d)
        text = (d / "bms.yaml").read_text()
        assert f'expression: "{long_expr}"' in text  # untouched, not wrapped
        assert "notes: >-" in text


class TestIdentityValidation:
    def _write(self, tmp_path, text):
        d = tmp_path / "ecus"
        d.mkdir(exist_ok=True)
        p = d / "x.yaml"
        p.write_text(text)
        return p

    def test_valid_file_has_no_errors(self, ecus_dir):
        errors, _warnings, stats = collect_pids_validation([ecus_dir / "igpm.yaml"])
        assert errors == []
        assert stats["ecus"] == 1

    def test_missing_tx_id_is_error(self, tmp_path):
        p = self._write(tmp_path, "X:\n  identity:\n    description: no tx\n")
        errors, _, _ = collect_pids_validation([p])
        assert any("missing required field 'tx_id'" in e for e in errors)

    def test_invalid_protocol_is_error(self, tmp_path):
        p = self._write(tmp_path, "X:\n  tx_id: 0x770\n  identity:\n    id_protocol: FOO\n")
        errors, _, _ = collect_pids_validation([p])
        assert any("invalid id_protocol" in e for e in errors)

    def test_unknown_identity_field_is_error(self, tmp_path):
        p = self._write(tmp_path, "X:\n  tx_id: 0x770\n  identity:\n    wat: 1\n")
        errors, _warnings, _ = collect_pids_validation([p])
        assert any("unknown field 'wat'" in e for e in errors)

    def test_duplicate_synonym_identity_values_warn(self, tmp_path):
        # The HVAC case: one DID reading mirrored into a second, synonymous field.
        p = self._write(
            tmp_path,
            'X:\n  tx_id: 0x770\n  identity:\n    firmware: "AE 1.0"\n    fw_version: "ae 1.0"\n',
        )
        errors, warnings, _ = collect_pids_validation([p])
        assert errors == []
        assert any("hold the same value" in w and "firmware" in w for w in warnings)

    def test_duplicate_across_synonym_groups_is_not_flagged(self, tmp_path):
        # hw_version and sw_version describe different axes — both reading "100"
        # is a coincidence (real on ADM/DDM/PSM), not a mirrored value.
        p = self._write(
            tmp_path,
            'X:\n  tx_id: 0x770\n  identity:\n    hw_version: "100"\n    sw_version: "100"\n',
        )
        errors, warnings, _ = collect_pids_validation([p])
        assert errors == []
        assert not any("hold the same value" in w for w in warnings)

    def test_identity_only_ecu_is_valid(self, tmp_path):
        # No pids: block — an identity-only module (AMP/SRS) must validate.
        p = self._write(tmp_path, "AMP:\n  tx_id: 0x783\n  identity:\n    id_protocol: none\n")
        errors, _, _ = collect_pids_validation([p])
        assert errors == []


# A per-ECU file with an *indented* block sequence (dash at +2 past the key) —
# the hand-authored scan_log style. A field edit must not reflow it.
SEED_INDENTED_SEQ = """\
IGPM:
  tx_id: 0x770
  identity:
    id_protocol: UDS
  scan_log:
    - service: 0x31
      range: "0000-FFFF"
      hits: 0
      notes: >
        All NRC 0x12 — sub-function not supported.
"""


class TestIndentationPreserved:
    def test_detect_sequence_indent(self):
        from canlib.yaml_rt import detect_sequence_indent

        assert detect_sequence_indent("a:\n  0x1:\n    - x: 1\n") == (4, 2)
        assert detect_sequence_indent("scan_log:\n  0x770:  # IGPM\n    - s: 1\n") == (4, 2)
        assert detect_sequence_indent("s:\n  captures:\n  - ecu: 1\n") == (2, 0)
        assert detect_sequence_indent("sessions:\n- date: 1\n") == (2, 0)
        assert detect_sequence_indent("a:\n  b: 1\n") is None

    def test_field_edit_does_not_reflow_indented_scan_log(self, tmp_path):
        d = tmp_path / "ecus"
        d.mkdir()
        p = d / "igpm.yaml"
        p.write_text(SEED_INDENTED_SEQ)
        before = p.read_text()
        set_ecu_fields(0x770, overwrite=True, ecus_dir=d, identity_confidence="confirmed")
        after = p.read_text()
        # The scan_log block (unrelated to the edit) must be byte-identical.
        before_scanlog = before[before.index("scan_log:") :]
        after_scanlog = after[after.index("scan_log:") :]
        assert before_scanlog == after_scanlog
        assert "identity_confidence: confirmed" in after
