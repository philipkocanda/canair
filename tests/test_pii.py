"""Tests for the PII pre-flight scanner (:mod:`canlib.pii`)."""

from __future__ import annotations

import json

import pytest

from canlib import pii
from canlib.profile import Profile


def _write_profile(root, *, car_model="Test Car 2020", captures=None, ecus=None):
    ecus_dir = root / "ecus"
    ecus_dir.mkdir(parents=True, exist_ok=True)
    (root / "profile.yaml").write_text(f"car_model: {car_model}\ninit: ATSP6;\n")
    if ecus:
        for name, body in ecus.items():
            (ecus_dir / f"{name.lower()}.yaml").write_text(body)
    caps_dir = root / "captures"
    caps_dir.mkdir(exist_ok=True)
    if captures is not None:
        (caps_dir / "2026-01-01.json").write_text(json.dumps({"sessions": [captures]}))
    return Profile("testcar", root)


def _ecu_yaml(name: str, **identity: str) -> str:
    lines = [f"{name}:", "  tx_id: 0x7E2", "  identity:"]
    lines += [f"    {k}: {v!r}" for k, v in identity.items()]
    return "\n".join(lines) + "\n"


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

    def test_serial_did_not_flagged(self, tmp_path):
        # F18C/F18B return a per-unit ECU serial. That names a MODULE, not a
        # person, and the project treats it as shareable diagnostic data — the same
        # call as leaving identity.serial unscanned. Kept symmetric on purpose.
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [
                    {
                        "rx": "0x7EC",
                        "pid": "22F18C",
                        "payload": "62F18C" + b"GWLK7F01A986826".hex(),
                    },
                    {"rx": "0x7EC", "pid": "22F18B", "payload": "62F18B" + b"1705304705".hex()},
                ],
            },
        )
        assert pii.scan_profile(prof) == []

    def test_serial_response_that_is_vin_shaped_is_still_caught(self, tmp_path):
        # The value check is keyed on the payload, not the identifier, so a serial
        # DID whose response really does decode to a VIN doesn't get a free pass.
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [
                    {
                        "rx": "0x7EC",
                        "pid": "22F18C",
                        "payload": "62F18C" + b"KMHC851HFHU012435".hex(),
                    }
                ],
            },
        )
        kinds = {f.kind for f in pii.scan_profile(prof)}
        assert kinds == {"vin-payload"}


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


class TestLooksRedacted:
    """A value already scrubbed is not PII — re-flagging it trains reviewers to skip."""

    def test_masked_vin(self):
        assert pii.looks_redacted("KMHCXXXXXXXXXXXXX")

    def test_other_mask_chars(self):
        assert pii.looks_redacted("WBA****1234")
        assert pii.looks_redacted("1HG####33A004352")
        assert pii.looks_redacted("VF1????1234")

    def test_real_vin_is_not_redacted(self):
        assert not pii.looks_redacted("KMHC851HFHU012435")
        assert not pii.looks_redacted("1HGCM82633A004352")

    def test_short_run_is_not_a_mask(self):
        # 3 X's could plausibly occur; 4 is the threshold.
        assert not pii.looks_redacted("ABCXXX123")
        assert pii.looks_redacted("ABCXXXX123")


class TestEcuIdentityScan:
    """ecus/<ecu>.yaml identity: — the DEFINITIONS leak path.

    `canair identity` writes a live VIN read straight into the ECU file, which a
    --no-captures contribution still ships. The capture-only scan never saw it.
    """

    def test_flags_unredacted_vin(self, tmp_path):
        prof = _write_profile(tmp_path, ecus={"VCU": _ecu_yaml("VCU", vin="KMHC851HFHU012435")})
        findings = pii.scan_profile(prof)
        vin = [f for f in findings if f.kind == "identity-vin"]
        assert len(vin) == 1
        assert "identity.vin" in vin[0].location
        # The value itself is not reproduced in full — only a locating prefix.
        assert "KMHC851HFHU012435" not in vin[0].detail
        assert "KMHC" in vin[0].detail

    def test_redacted_vin_not_flagged(self, tmp_path):
        prof = _write_profile(tmp_path, ecus={"VCU": _ecu_yaml("VCU", vin="KMHCXXXXXXXXXXXXX")})
        assert not [f for f in pii.scan_profile(prof) if f.kind == "identity-vin"]

    def test_scanned_even_without_captures(self, tmp_path):
        # The whole point: a definitions-only PR still ships the VIN.
        prof = _write_profile(tmp_path, ecus={"VCU": _ecu_yaml("VCU", vin="KMHC851HFHU012435")})
        assert any(f.kind == "identity-vin" for f in pii.scan_profile(prof, include_captures=False))

    def test_ecu_serial_not_flagged(self, tmp_path):
        # Per-unit module serials are shareable diagnostic data by project policy,
        # and 17-digit ones would otherwise false-positive as VIN-shaped.
        prof = _write_profile(
            tmp_path,
            ecus={
                "BSD": _ecu_yaml("BSD", serial="31114851712211429"),
                "ESC": _ecu_yaml("ESC", serial="GWLK7F01A986826"),
            },
        )
        assert pii.scan_profile(prof) == []

    def test_part_number_and_versions_not_flagged(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            ecus={
                "VCU": _ecu_yaml(
                    "VCU",
                    part_number="36601-0E250",
                    sw_id="EAEEO5L-NS7-D060",
                    calibration="C5210617",
                )
            },
        )
        assert pii.scan_profile(prof) == []

    def test_vin_token_in_identity_notes(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            ecus={"VCU": _ecu_yaml("VCU", notes="read the VIN KMHC851HFHU012435 here")},
        )
        assert any(f.kind == "vin-text" for f in pii.scan_profile(prof))

    def test_email_in_identity_notes(self, tmp_path):
        prof = _write_profile(
            tmp_path, ecus={"VCU": _ecu_yaml("VCU", notes="reported by me@example.com")}
        )
        assert any(f.kind == "email" for f in pii.scan_profile(prof))

    def test_did_range_in_notes_is_not_a_phone_number(self, tmp_path):
        # Identity prose is technical. There is no digit-run heuristic to trip
        # (see TestNoDigitRunHeuristic) — this guards it staying that way.
        prof = _write_profile(
            tmp_path,
            ecus={"SCC": _ecu_yaml("SCC", notes="Live data via 220100-0103/0105, undecoded")},
        )
        assert pii.scan_profile(prof) == []


class TestNoDigitRunHeuristic:
    """There is deliberately no "long run of digits" check.

    It could not tell a phone number from a part number, an ECU serial, a DID
    range or raw payload hex — which is what capture notes consist of. On the
    bundled Ioniq profile it fired 15 times and was wrong every time, burying the
    real findings.
    """

    def test_regex_is_gone(self):
        assert not hasattr(pii, "_PHONE_RE")

    @pytest.mark.parametrize(
        "note",
        [
            "Part 95821G2000 (HW version). Serial 31114851712211429.",
            "Scanned 22 0101-0130 on this ECU",
            "Live data via 220100-0103/0105",
            "payload 17060100003900000000",
            "AVN serial 00000076FE0085-003240",
            "call me on 555 123 4567",  # a real phone number is also not flagged
        ],
    )
    def test_digit_runs_produce_no_findings(self, tmp_path, note):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101AB", "notes": note}],
            },
        )
        assert pii.scan_profile(prof) == []


class TestRedactionSuppressesCaptureFindings:
    def test_masked_response_not_flagged_as_vin(self, tmp_path):
        # A `response` holding an already-redacted decoded VIN. The 1A90 identity
        # DID still fires (that is about the request, not the value).
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "identity",
                "captures": [{"rx": "0x7EA", "pid": "1A90", "response": "KMHCXXXXXXXXXXXXX"}],
            },
        )
        kinds = {f.kind for f in pii.scan_profile(prof)}
        assert "vin-payload" not in kinds
        assert "identity-did" in kinds

    def test_plain_ascii_vin_in_response_is_flagged(self, tmp_path):
        # `response` is a free-form summary that may hold DECODED text, so the
        # string is tested as written as well as hex-decoded.
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "identity",
                "captures": [{"rx": "0x7EA", "pid": "2101", "response": "KMHC851HFHU012435"}],
            },
        )
        assert any(f.kind == "vin-payload" for f in pii.scan_profile(prof))


class TestVinTokenRequiresALetter:
    """A 17-char all-digit token is an ECU serial, not a VIN.

    The VIN charset is a superset of the digits, so a 17-digit serial (exactly
    what the BSD modules report) matches the VIN *shape*. Flagging it contradicts
    the policy of not reporting ECU serials at all.
    """

    def test_all_digit_serial_in_notes_not_flagged(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "identity",
                "captures": [
                    {
                        "rx": "0x7EC",
                        "pid": "22F191",
                        "payload": "62F19141",
                        "notes": "Part 95821G2000 (HW version). Serial 31114851712211429.",
                    }
                ],
            },
        )
        assert not any(f.kind == "vin-text" for f in pii.scan_profile(prof))

    def test_real_vin_in_notes_still_flagged(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={"label": "read KMHC851HFHU012435 off the VCU", "captures": []},
        )
        assert any(f.kind == "vin-text" for f in pii.scan_profile(prof))

    def test_all_digit_payload_not_flagged(self, tmp_path):
        prof = _write_profile(
            tmp_path,
            captures={
                "label": "x",
                "captures": [
                    {
                        "rx": "0x7EC",
                        "pid": "22F18C",
                        "payload": "62F18C" + b"31114851712211429".hex(),
                    }
                ],
            },
        )
        assert pii.scan_profile(prof) == []

    def test_helper_directly(self):
        assert pii._has_vin_token("KMHC851HFHU012435")
        assert pii._has_vin_token("1HGCM82633A004352")
        assert not pii._has_vin_token("31114851712211429")  # all digits -> serial
        assert not pii._has_vin_token("too short")


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

    def test_ecus_identity_scanned_on_definitions_only_pr(self, tmp_path):
        # A --no-captures contribution still ships ecus/, so the VIN must be caught.
        prof = _write_profile(tmp_path, ecus={"VCU": _ecu_yaml("VCU", vin="KMHC851HFHU012435")})
        findings = pii.scan_contribution(
            prof, tmp_path / "captures", include_captures=False, base_reader=lambda rel: None
        )
        assert any(f.kind == "identity-vin" for f in findings)
