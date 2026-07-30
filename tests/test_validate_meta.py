"""Tests for profile.yaml (profile-wide settings) validation — validate_meta."""

from __future__ import annotations

from canlib.commands.validate import validate_meta

REQUIRED = {"car_model", "init"}


def _write(tmp_path, text: str):
    p = tmp_path / "profile.yaml"
    p.write_text(text)
    return p


class TestRequiredFields:
    def test_minimal_valid_profile(self, tmp_path):
        p = _write(tmp_path, 'car_model: "Test Car"\ninit: "ATSP6;ATS0;ATAL;"\n')
        assert validate_meta(p, REQUIRED) == []

    def test_missing_required(self, tmp_path):
        p = _write(tmp_path, 'init: "ATSP6;"\n')
        errors = validate_meta(p, REQUIRED)
        assert any("car_model" in e for e in errors)

    def test_empty_file(self, tmp_path):
        p = _write(tmp_path, "")
        assert validate_meta(p, REQUIRED) == ["profile.yaml: empty or invalid"]


class TestTypedFields:
    def _base(self, extra: str) -> str:
        return 'car_model: "C"\ninit: "ATSP6;"\n' + extra

    def test_can_bitrate_accepts_positive_int(self, tmp_path):
        p = _write(tmp_path, self._base("can_bitrate: 250000\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_can_bitrate_rejects_non_int(self, tmp_path):
        p = _write(tmp_path, self._base('can_bitrate: "fast"\n'))
        assert any("can_bitrate" in e for e in validate_meta(p, REQUIRED))

    def test_response_timeout_rejects_zero(self, tmp_path):
        p = _write(tmp_path, self._base("response_timeout_ms: 0\n"))
        assert any("response_timeout_ms" in e for e in validate_meta(p, REQUIRED))

    def test_multi_did_batching_must_be_bool(self, tmp_path):
        p = _write(tmp_path, self._base("multi_did_batching: 1\n"))
        assert any("multi_did_batching" in e for e in validate_meta(p, REQUIRED))

    def test_failure_types_must_be_mapping(self, tmp_path):
        p = _write(tmp_path, self._base("failure_types: [1, 2]\n"))
        assert any("failure_types" in e for e in validate_meta(p, REQUIRED))

    def test_init_must_be_string(self, tmp_path):
        p = _write(tmp_path, 'car_model: "C"\ninit: 6\n')
        assert any("'init'" in e for e in validate_meta(p, REQUIRED))


class TestIsotpBlock:
    def _base(self, extra: str) -> str:
        return 'car_model: "C"\ninit: "ATSP6;"\n' + extra

    def test_valid_isotp(self, tmp_path):
        p = _write(tmp_path, self._base("isotp:\n  tx_padding: 0x00\n  can_fd: true\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_unknown_isotp_key(self, tmp_path):
        p = _write(tmp_path, self._base("isotp:\n  bogus: 1\n"))
        assert any("unknown isotp field 'bogus'" in e for e in validate_meta(p, REQUIRED))

    def test_tx_padding_out_of_range(self, tmp_path):
        p = _write(tmp_path, self._base("isotp:\n  tx_padding: 999\n"))
        assert any("tx_padding" in e for e in validate_meta(p, REQUIRED))

    def test_can_fd_must_be_bool(self, tmp_path):
        p = _write(tmp_path, self._base("isotp:\n  can_fd: yes_please\n"))
        assert any("can_fd" in e for e in validate_meta(p, REQUIRED))

    def test_isotp_must_be_mapping(self, tmp_path):
        p = _write(tmp_path, self._base("isotp: 5\n"))
        assert any("'isotp' must be a mapping" in e for e in validate_meta(p, REQUIRED))

    def test_negative_stmin(self, tmp_path):
        p = _write(tmp_path, self._base("isotp:\n  stmin: -1\n"))
        assert any("stmin" in e for e in validate_meta(p, REQUIRED))


class TestAddressingBlock:
    def _base(self, extra: str) -> str:
        return 'car_model: "C"\ninit: "ATSP6;"\n' + extra

    def test_valid_rx_offset(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  rx_offset: 0x80\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_unknown_addressing_key(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  bogus: 1\n"))
        assert any("unknown addressing field 'bogus'" in e for e in validate_meta(p, REQUIRED))

    def test_rx_offset_must_be_int(self, tmp_path):
        p = _write(tmp_path, self._base('addressing:\n  rx_offset: "0x80"\n'))
        assert any("addressing.rx_offset" in e for e in validate_meta(p, REQUIRED))

    def test_addressing_must_be_mapping(self, tmp_path):
        p = _write(tmp_path, self._base("addressing: 8\n"))
        assert any("'addressing' must be a mapping" in e for e in validate_meta(p, REQUIRED))

    def test_valid_addressing_mode(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  mode: normal_fixed_29bit\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_invalid_addressing_mode(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  mode: bogus_mode\n"))
        assert any("addressing.mode" in e for e in validate_meta(p, REQUIRED))

    def test_negative_rx_offset_allowed(self, tmp_path):
        # PSA/Stellantis use a negative offset (0x6B4 -> 0x694 = -0x20). Gap G-L.
        p = _write(tmp_path, self._base("addressing:\n  rx_offset: -32\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_extended_11bit_mode_valid(self, tmp_path):
        # BMW/PSA extended (mixed) 11-bit. Gap G-I.
        p = _write(tmp_path, self._base("addressing:\n  mode: normal_extended_11bit\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_target_address_byte_range(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  target_address: 300\n"))
        assert any("addressing.target_address" in e for e in validate_meta(p, REQUIRED))

    def test_source_address_valid(self, tmp_path):
        p = _write(tmp_path, self._base("addressing:\n  source_address: 0xF1\n"))
        assert validate_meta(p, REQUIRED) == []


class TestQuirks:
    def _base(self, extra: str) -> str:
        return 'car_model: "C"\ninit: "ATSP6;"\n' + extra

    def test_valid_quirk(self, tmp_path):
        p = _write(tmp_path, self._base("quirks:\n  - hk_f1xx_minus_one\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_unknown_quirk(self, tmp_path):
        p = _write(tmp_path, self._base("quirks:\n  - not_a_quirk\n"))
        assert any("unknown quirk 'not_a_quirk'" in e for e in validate_meta(p, REQUIRED))

    def test_quirks_must_be_list(self, tmp_path):
        p = _write(tmp_path, self._base("quirks: hk_f1xx_minus_one\n"))
        assert any("'quirks' must be a list" in e for e in validate_meta(p, REQUIRED))


class TestPhysicalBands:
    def _base(self, extra: str) -> str:
        return 'car_model: "C"\ninit: "ATSP6;"\n' + extra

    def test_valid_override(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands:\n  hv_pack: [450, 850]\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_custom_band_allowed(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands:\n  hv_pack_peak: [600, 900]\n"))
        assert validate_meta(p, REQUIRED) == []

    def test_must_be_mapping(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands: [1, 2]\n"))
        assert any("'physical_bands' must be a mapping" in e for e in validate_meta(p, REQUIRED))

    def test_range_must_be_two_elements(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands:\n  hv_pack: [450]\n"))
        assert any("physical_bands.hv_pack" in e for e in validate_meta(p, REQUIRED))

    def test_range_must_be_numeric(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands:\n  hv_pack: [low, high]\n"))
        assert any("must be numbers" in e for e in validate_meta(p, REQUIRED))

    def test_low_must_be_less_than_high(self, tmp_path):
        p = _write(tmp_path, self._base("physical_bands:\n  hv_pack: [850, 450]\n"))
        assert any("must be less than high" in e for e in validate_meta(p, REQUIRED))
