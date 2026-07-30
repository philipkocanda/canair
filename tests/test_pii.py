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


class TestScanContribution:
    def test_skips_sessions_already_upstream(self, tmp_path):
        # captures/ in the (workspace) contribution has two sessions: one that
        # already exists upstream (base), one newly added — both contain a VIN.
        old = {
            "label": "old",
            "captures": [{"rx": "0x7EC", "pid": "22F190", "payload": _vin_hex()}],
        }
        new = {
            "label": "new",
            "captures": [{"rx": "0x7EC", "pid": "22F190", "payload": _vin_hex()}],
        }
        caps = tmp_path / "captures"
        caps.mkdir()
        (caps / "2026-01-01.json").write_text(json.dumps({"sessions": [old, new]}))
        prof = Profile("testcar", tmp_path)
        (tmp_path / "profile.yaml").write_text("car_model: Clean Car\n")

        # base_reader returns the committed file with ONLY the old session.
        base = json.dumps({"sessions": [old]})

        def base_reader(relpath):
            assert relpath == "profiles/testcar/captures/2026-01-01.json"
            return base

        findings = pii.scan_contribution(prof, caps, include_captures=True, base_reader=base_reader)
        # Only the NEW session's VIN is flagged; the already-upstream one is not.
        locs = [f.location for f in findings]
        assert any("sessions[1]" in loc for loc in locs)
        assert not any("sessions[0]" in loc for loc in locs)

    def test_new_file_scanned_fully(self, tmp_path):
        new = {"label": "new", "captures": [{"rx": "0x7EC", "pid": "1A90", "payload": "5A90"}]}
        caps = tmp_path / "captures"
        caps.mkdir()
        (caps / "2026-02-02.json").write_text(json.dumps({"sessions": [new]}))
        prof = Profile("testcar", tmp_path)
        (tmp_path / "profile.yaml").write_text("car_model: Clean Car\n")

        findings = pii.scan_contribution(
            prof, caps, include_captures=True, base_reader=lambda rel: None
        )
        assert any(f.kind == "identity-did" for f in findings)

    def test_no_captures_only_scans_car_model(self, tmp_path):
        caps = tmp_path / "captures"
        caps.mkdir()
        (caps / "x.json").write_text(json.dumps({"sessions": [{"label": "s", "captures": []}]}))
        prof = Profile("testcar", tmp_path)
        (tmp_path / "profile.yaml").write_text("car_model: contact me@example.com\n")
        findings = pii.scan_contribution(
            prof, caps, include_captures=False, base_reader=lambda rel: None
        )
        assert len(findings) == 1 and findings[0].kind == "email"
