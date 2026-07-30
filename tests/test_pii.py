"""Tests for the PII pre-flight scanner (:mod:`canlib.pii`)."""

from __future__ import annotations

import json

from canlib import pii
from canlib.profile import Profile


def _write_profile(root, *, car_model="Test Car 2020", captures=None):
    (root / "ecus").mkdir(parents=True, exist_ok=True)
    (root / "profile.yaml").write_text(f"car_model: {car_model}\ninit: ATSP6;\n")
    caps_dir = root / "captures"
    caps_dir.mkdir(exist_ok=True)
    if captures is not None:
        (caps_dir / "2026-01-01.json").write_text(json.dumps({"sessions": [captures]}))
    return Profile("testcar", root)


def _vin_hex() -> str:
    # 17-char VIN -> ASCII -> hex payload (prefixed with a SID/DID echo).
    vin = "1HGCM82633A004352"
    return "62F190" + vin.encode().hex()


class TestIdentityDids:
    def test_flags_vin_did(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "read",
                "captures": [{"rx": "0x7EC", "pid": "22F190", "payload": _vin_hex()}],
            },
        )
        findings = pii.scan_profile(prof)
        kinds = {f.kind for f in findings}
        assert "identity-did" in kinds
        # The VIN-shaped payload is also caught independently.
        assert "vin-payload" in kinds

    def test_flags_kwp_vin_record(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [{"rx": "0x7EC", "pid": "1A90", "payload": "5A90"}],
            },
        )
        assert any(f.kind == "identity-did" for f in pii.scan_profile(prof))

    def test_part_number_did_not_flagged(self, tmp_path):
        # F188 (part number) is the same across a model — not identifying.
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [{"rx": "0x7EC", "pid": "22F188", "payload": "62F18841"}],
            },
        )
        assert not any(f.kind == "identity-did" for f in pii.scan_profile(prof))


class TestFreeText:
    def test_flags_email_in_label(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={"label": "sent by me@example.com", "captures": []},
        )
        assert any(f.kind == "email" for f in pii.scan_profile(prof))

    def test_flags_email_in_car_model(self, tmp_path):
        prof = _write_profile(tmp_path, car_model="owner me@example.com Car")
        assert any(f.kind == "email" and "car_model" in f.location for f in pii.scan_profile(prof))

    def test_clean_profile_has_no_findings(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "idle read",
                "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101ABCD"}],
            },
        )
        assert pii.scan_profile(prof) == []


class TestCaptureToggle:
    def test_no_captures_skips_capture_scan(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [{"rx": "0x7EC", "pid": "22F190", "payload": _vin_hex()}],
            },
        )
        assert pii.scan_profile(prof, include_captures=False) == []


class TestPrefixStrip:
    def test_strip_service_prefix(self):
        assert pii._strip_service_prefix("22F190") == "F190"
        assert pii._strip_service_prefix("1A90") == "90"
        assert pii._strip_service_prefix("F190") == "F190"
