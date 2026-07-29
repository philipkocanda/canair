"""Tests for validate-pids.py PCI-byte detection (check_pci_bytes)."""

import pytest

from canlib.commands import validate as validate_pids

check_pci_bytes = validate_pids.check_pci_bytes


def _warn(expr):
    return check_pci_bytes(expr, "P", "2101", "ECU")


class TestPciByteDetection:
    @pytest.mark.parametrize("expr", ["B8*0.02", "B16", "S24", "B32/2", "B0", "B1"])
    def test_flags_single_pci_byte(self, expr):
        assert _warn(expr), f"{expr} should be flagged as PCI"

    @pytest.mark.parametrize("expr", ["B9*0.02", "B7", "S10", "B44*0.02", "B17:0"])
    def test_passes_valid_single_byte(self, expr):
        assert not _warn(expr), f"{expr} should NOT be flagged"

    def test_flags_range_spanning_pci(self):
        # [B7:B9] includes B8 (PCI); [B15:B17] includes B16
        assert _warn("[B7:B9]/10")
        assert _warn("[B15:B17]/10")

    def test_passes_clean_range(self):
        assert not _warn("[B18:B19]/10")
        assert not _warn("[S12:S13]/100")
        assert not _warn("[B45:B46]/100")

    def test_message_is_clear(self):
        msg = _warn("B8*0.02")[0]
        assert "PCI" in msg and "B8" in msg


class TestRealPidsHaveNoPciBytes:
    """The shipped ecus/ must not read PCI bytes."""

    def test_no_pci_in_any_pid(self):
        import glob

        import yaml

        offenders = []
        from canlib.profile import active

        for path in glob.glob(str(active().ecus_dir / "*.yaml")):
            data = yaml.safe_load(open(path))
            if not isinstance(data, dict):
                continue
            for ecu, ecud in data.items():
                if not isinstance(ecud, dict) or "pids" not in ecud:
                    continue
                for pid, pidd in (ecud["pids"] or {}).items():
                    for pname, pmeta in ((pidd or {}).get("parameters") or {}).items():
                        if not isinstance(pmeta, dict):
                            continue
                        expr = pmeta.get("expression", "") or ""
                        offenders += check_pci_bytes(expr, pname, str(pid), ecu)
        assert not offenders, "PCI bytes referenced:\n" + "\n".join(offenders)


class TestDuplicateParamNames:
    """_duplicate_param_errors flags a shipped signal name used by >1 PID."""

    def _write(self, tmp_path, second_status="active", second_enabled=True):
        import textwrap

        (tmp_path / "a.yaml").write_text(
            textwrap.dedent(
                """\
                ECUA:
                  tx_id: 0x7E0
                  pids:
                    2101:
                      status: active
                      parameters:
                        SHARED:
                          expression: "B3"
                          verified: true
                """
            )
        )
        (tmp_path / "b.yaml").write_text(
            textwrap.dedent(
                f"""\
                ECUB:
                  tx_id: 0x7E1
                  pids:
                    2102:
                      status: {second_status}
                      parameters:
                        SHARED:
                          expression: "B3"
                          verified: true
                          enabled: {"true" if second_enabled else "false"}
                """
            )
        )
        return sorted(tmp_path.glob("*.yaml"))

    def test_flags_duplicate_shipped_name(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path))
        assert any("SHARED" in e and "ECUB 2102" in e for e in errs)

    def test_ignores_when_second_pid_not_active(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path, second_status="draft"))
        assert not errs

    def test_ignores_when_second_param_disabled(self, tmp_path):
        errs = validate_pids._duplicate_param_errors(self._write(tmp_path, second_enabled=False))
        assert not errs


class TestRxIdValidation:
    """Per-ECU rx_id (CAN response-address override) is range-checked like tx_id."""

    def _validate(self, tmp_path, body: str):
        import textwrap

        p = tmp_path / "e.yaml"
        p.write_text(textwrap.dedent(body))
        errors, _warnings, _stats = validate_pids.validate_ecu_file(p, validate_pids.load_schema())
        return errors

    def test_valid_rx_id(self, tmp_path):
        errs = self._validate(tmp_path, "ECU:\n  tx_id: 0x704\n  rx_id: 0x784\n")
        assert errs == []

    def test_rx_id_out_of_range(self, tmp_path):
        errs = self._validate(tmp_path, "ECU:\n  tx_id: 0x704\n  rx_id: 0x1234\n")
        assert any("rx_id" in e for e in errs)

    def test_rx_id_must_be_int(self, tmp_path):
        errs = self._validate(tmp_path, 'ECU:\n  tx_id: 0x704\n  rx_id: "0x784"\n')
        assert any("rx_id" in e for e in errs)


class TestAddressingModeValidation:
    """29-bit addressing widens the accepted tx_id/rx_id range; per-ECU mode is validated."""

    def _validate(self, tmp_path, body: str):
        import textwrap

        p = tmp_path / "e.yaml"
        p.write_text(textwrap.dedent(body))
        errors, _warnings, _stats = validate_pids.validate_ecu_file(p, validate_pids.load_schema())
        return errors

    def test_29bit_tx_id_rejected_without_mode(self, tmp_path):
        # Default 11-bit: a full 29-bit id is out of the 0x7FF range.
        errs = self._validate(tmp_path, "ECU:\n  tx_id: 0x18DA10F1\n")
        assert any("tx_id" in e for e in errs)

    def test_29bit_tx_id_accepted_with_per_ecu_mode(self, tmp_path):
        errs = self._validate(
            tmp_path,
            "ECU:\n  tx_id: 0x18DA10F1\n  addressing:\n    mode: normal_fixed_29bit\n",
        )
        assert errs == []

    def test_invalid_per_ecu_mode(self, tmp_path):
        errs = self._validate(tmp_path, "ECU:\n  tx_id: 0x704\n  addressing:\n    mode: bogus\n")
        assert any("addressing.mode" in e for e in errs)

    def test_unknown_per_ecu_addressing_key(self, tmp_path):
        errs = self._validate(tmp_path, "ECU:\n  tx_id: 0x704\n  addressing:\n    rx_offset: 8\n")
        assert any("unknown addressing field" in e for e in errs)

    def test_extended_11bit_target_source(self, tmp_path):
        # BMW/PSA extended-11-bit: mode + target/source bytes. Gap G-I.
        errs = self._validate(
            tmp_path,
            "DME:\n  tx_id: 0x6F1\n  rx_id: 0x612\n  addressing:\n"
            "    mode: normal_extended_11bit\n    target_address: 0x12\n    source_address: 0xF1\n",
        )
        assert errs == []

    def test_target_address_out_of_range(self, tmp_path):
        errs = self._validate(
            tmp_path, "ECU:\n  tx_id: 0x6F1\n  addressing:\n    target_address: 300\n"
        )
        assert any("addressing.target_address" in e for e in errs)

    def test_source_address_out_of_range(self, tmp_path):
        errs = self._validate(
            tmp_path, "ECU:\n  tx_id: 0x6F1\n  addressing:\n    source_address: 300\n"
        )
        assert any("addressing.source_address" in e for e in errs)

    def test_fc_id_override_accepted(self, tmp_path):
        # Functional-TX / physical-RX (Renault/Mitsubishi): fc_id override. Gap G-J.
        errs = self._validate(
            tmp_path,
            "EVC:\n  tx_id: 0x18DB33F1\n  rx_id: 0x18DAF1DB\n  addressing:\n"
            "    mode: normal_29bit\n    fc_id: 0x18DADBF1\n",
        )
        assert errs == []

    def test_fc_id_must_be_positive(self, tmp_path):
        errs = self._validate(
            tmp_path,
            "EVC:\n  tx_id: 0x18DB33F1\n  addressing:\n    mode: normal_29bit\n    fc_id: 0\n",
        )
        assert any("addressing.fc_id" in e for e in errs)


class TestTypedParamValidation:
    """validate._validate_param_type — typed-decoding field rules."""

    VALID = frozenset({"numeric", "enum", "bitmask", "ascii", "date", "bcd", "struct"})

    def _v(self, param):
        return validate_pids._validate_param_type(param, "P", "2101", "ECU", self.VALID)

    def test_numeric_untyped_ok(self):
        assert self._v({"expression": "B3"}) == []

    def test_companion_map_without_type_errors(self):
        errs = self._v({"expression": "B3", "values": {1: "a"}})
        assert errs and "requires a 'type:'" in errs[0]

    def test_unknown_type_errors(self):
        errs = self._v({"expression": "B3", "type": "bogus"})
        assert any("invalid type" in e for e in errs)

    def test_enum_requires_values(self):
        assert any(
            "requires a 'values:'" in e for e in self._v({"expression": "B3", "type": "enum"})
        )

    def test_enum_non_int_key_errors(self):
        errs = self._v({"expression": "B3", "type": "enum", "values": {"x": "a"}})
        assert any("must be an integer" in e for e in errs)

    def test_enum_ok(self):
        assert self._v({"expression": "B3", "type": "enum", "values": {40: "a"}}) == []

    def test_bitmask_index_range(self):
        errs = self._v({"expression": "B3", "type": "bitmask", "bits": {64: "x"}})
        assert any("out of range" in e for e in errs)

    def test_values_only_with_enum(self):
        errs = self._v(
            {"expression": "B3", "type": "bitmask", "bits": {0: "a"}, "values": {1: "b"}}
        )
        assert any("only valid with type 'enum'" in e for e in errs)

    def test_struct_requires_named_fields(self):
        errs = self._v({"expression": "0", "type": "struct", "fields": [{"expression": "B3"}]})
        assert any("missing 'name'" in e for e in errs)

    def test_struct_ok(self):
        param = {
            "expression": "0",
            "type": "struct",
            "fields": [{"name": "h", "expression": "B4"}],
        }
        assert self._v(param) == []


class TestCanBusValidation:
    """validate_ecu_file — top-level can_bus list vs the profile's can_buses.yaml."""

    def _errors(self, tmp_path, can_bus_yaml, *, vocab="[B-CAN, P-CAN, C-CAN, M-CAN, H-CAN, All]"):
        import textwrap

        from canlib.profile import Profile

        ecus = tmp_path / "ecus"
        ecus.mkdir(exist_ok=True)
        if vocab is not None:
            (tmp_path / "can_buses.yaml").write_text(f"can_buses: {vocab}\n")
        p = ecus / "x.yaml"
        p.write_text(
            textwrap.dedent(
                f"""\
                ECUX:
                  tx_id: 0x7E0
                  can_bus: {can_bus_yaml}
                  pids:
                    2101:
                      status: active
                      parameters: {{}}
                """
            )
        )
        schema = validate_pids.load_schema()
        profile = Profile(tmp_path.name, tmp_path)
        errors, _warnings, _stats = validate_pids.validate_ecu_file(p, schema, profile)
        return errors

    def test_valid_codes_ok(self, tmp_path):
        assert self._errors(tmp_path, "[H-CAN, P-CAN]") == []

    def test_invalid_code_errors(self, tmp_path):
        errs = self._errors(tmp_path, "[X]")
        assert any("can_bus" in e and "invalid" in e for e in errs)

    def test_non_list_errors(self, tmp_path):
        errs = self._errors(tmp_path, "B-CAN")
        assert any("can_bus must be a list" in e for e in errs)

    def test_duplicate_codes_error(self, tmp_path):
        errs = self._errors(tmp_path, "[B-CAN, B-CAN]")
        assert any("duplicate can_bus" in e for e in errs)

    def test_no_vocabulary_skips_membership(self, tmp_path):
        # No can_buses.yaml declared → any code accepted (shape/dup still checked).
        assert self._errors(tmp_path, "[ZZ]", vocab=None) == []
