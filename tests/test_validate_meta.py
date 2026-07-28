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
