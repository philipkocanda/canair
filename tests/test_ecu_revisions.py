"""ECU revisions: declaring them, gating definitions to them, and resolving them.

Driven by the synthetic fixture ``tests/fixtures/profiles/variants/``: two cars (40 kWh,
60 kWh), a VCU with ``kwp``/``uds`` revisions (the 40 kWh car only ever had ``kwp``, the
60 kWh car may have either), a BMS with none, and a HEATER only the 60 kWh car has.

Plan: ``plans/2026-10-30-profile-variants-5-ecu-revisions.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from canlib import config
from canlib import profile as profile_mod
from canlib.cli import main
from canlib.pids import clear_cache, load_pids, load_raw_pids, revision_resolutions
from canlib.profile import ProfileError, set_active
from canlib.variants import check_variants_doc, load_variants
from canlib.variants import revisions as rv
from canlib.variants.tags import capture_tag, narrow_to_captured, revisions_in_scope

FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "variants"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    """An empty user config, and nothing remembered between tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    config.load_config.cache_clear()
    import canlib.pids as pids_mod

    pids_mod._noted_skips.clear()
    yield
    config.load_config.cache_clear()


@pytest.fixture
def copy(tmp_path) -> Path:
    """A writable copy of the fixture, for the editors."""
    dest = tmp_path / "variants"
    shutil.copytree(FIXTURE, dest)
    return dest


def _activate(root=FIXTURE, variant=None, pins=None):
    return set_active(str(root), variant=variant, revisions=pins)


def _ecus():
    return load_pids()["ecus"]


def _pids(ecu: str) -> dict:
    return {str(k): v for k, v in (_ecus()[ecu].get("pids") or {}).items()}


# ── the load seam ───────────────────────────────────────────────────────────


class TestResolution:
    def test_the_40kwh_car_has_only_the_kwp_vcu(self):
        _activate(variant="40kwh")
        assert revision_resolutions()["VCU"].revision == "kwp"
        assert revision_resolutions()["VCU"].source == "only"
        pids = _pids("VCU")
        assert set(pids) == {"2101", "21F0"}
        assert set(pids["21F0"]["parameters"]) == {"GEAR"}
        assert _ecus()["VCU"]["identity"]["id_protocol"] == "KWP2000"

    def test_an_ecu_fitted_to_another_car_is_left_out(self):
        _activate(variant="40kwh")
        assert "HEATER" not in _ecus()
        _activate(variant="60kwh")
        assert "HEATER" in _ecus()

    def test_undetermined_skips_gated_definitions_and_says_so(self, capsys):
        _activate(variant="60kwh")
        assert revision_resolutions()["VCU"].revision is None
        pids = _pids("VCU")
        assert set(pids) == {"21F0"}
        assert set(pids["21F0"]["parameters"]) == {"GEAR"}
        err = capsys.readouterr().err
        assert "VCU could be any of kwp, uds" in err
        assert "--pin VCU=kwp" in err
        load_pids()
        _activate(variant="60kwh")
        load_pids()
        assert "could be any" not in capsys.readouterr().err  # once per process

    def test_with_no_car_chosen_the_note_says_to_name_it(self, capsys):
        _activate()
        load_pids()
        err = capsys.readouterr().err
        assert "No car is chosen (40kwh, 60kwh)" in err
        assert "--variant 40kwh" in err
        assert "--pin" not in err

    def test_a_pin_applies_the_revisions_overrides_and_gates(self):
        _activate(variant="60kwh", pins=["VCU=uds"])
        res = revision_resolutions()["VCU"]
        assert (res.revision, res.source) == ("uds", "flag")
        pids = _pids("VCU")
        assert set(pids) == {"22E001", "21F0"}
        assert set(pids["21F0"]["parameters"]) == {"GEAR", "DRIVE_MODE"}
        vcu = _ecus()["VCU"]
        assert vcu["identity"]["id_protocol"] == "UDS"
        assert vcu["identity"]["description"] == "Synthetic VCU with a protocol change"
        assert vcu["response_timeout_ms"] == 300
        assert "revisions" not in vcu and "revisions" not in pids["22E001"]

    def test_recorded_identity_belongs_to_the_reference_revision_only(self):
        _activate(variant="60kwh", pins=["VCU=kwp"])
        kwp = _ecus()["VCU"]["identity"]
        assert kwp["part_number"] == "SYN-0001" and kwp["calibration"] == "1234"
        _activate(variant="60kwh", pins=["VCU=uds"])
        assert _ecus()["VCU"]["identity"] == {
            "description": "Synthetic VCU with a protocol change",
            "id_protocol": "UDS",
        }
        _activate(variant="60kwh")  # undetermined: could be either, nothing is withheld
        assert _ecus()["VCU"]["identity"]["part_number"] == "SYN-0001"

    def test_ecu_detail_says_why_identity_is_missing_on_another_revision(self, capsys):
        from canlib.commands.ecu import _detail_record, cmd_detail
        from canlib.ecus import load_ecus, resolve_tx

        for pins, expected in (
            (["VCU=uds"], "None recorded for the uds revision (the recorded values belong to kwp)"),
            (None, "this car's revision is undetermined"),
            (["VCU=kwp"], None),
        ):
            _activate(variant="60kwh", pins=pins)
            tx = resolve_tx("VCU")
            rec = _detail_record(load_ecus()[tx], tx, "VCU", _ecus()["VCU"])
            assert cmd_detail(rec, False) == 0
            out = capsys.readouterr().out
            assert (expected in out) if expected else ("None recorded" not in out)

    def test_a_config_pin_and_the_flag_outranking_it(self):
        config.set_config_key("profile_settings.variants.revisions.VCU", "kwp")
        config.load_config.cache_clear()
        _activate(variant="60kwh")
        assert revision_resolutions()["VCU"].source == "config"
        assert "2101" in _pids("VCU")
        _activate(variant="60kwh", pins=["VCU=uds"])
        assert revision_resolutions()["VCU"].revision == "uds"

    def test_no_car_selected_keeps_every_ecu(self):
        _activate()
        assert "HEATER" in _ecus()
        assert revision_resolutions()["VCU"].possible == ("kwp", "uds")

    @pytest.mark.parametrize(
        ("variant", "pin", "message"),
        [
            ("60kwh", "NOPE=kwp", "has no ECU 'NOPE'"),
            ("60kwh", "BMS=kwp", "BMS declares no revisions"),
            ("60kwh", "VCU=can", "does not declare"),
            ("40kwh", "VCU=uds", "can only carry kwp"),
        ],
    )
    def test_a_pin_that_cannot_be_is_an_error(self, variant, pin, message):
        _activate(variant=variant, pins=[pin])
        with pytest.raises(ProfileError, match=message):
            load_pids()

    def test_a_malformed_pin_is_refused_up_front(self):
        with pytest.raises(ProfileError, match="expected ECU=REVISION"):
            _activate(pins=["VCU"])

    def test_profile_overrides_reach_meta(self):
        assert _activate(variant="60kwh").meta["car_model"].startswith("Variants fixture, 60")
        assert _activate(variant="40kwh").meta["car_model"] == "Variants fixture (synthetic)"
        assert load_raw_pids()["car_model"] == "Variants fixture (synthetic)"

    def test_the_raw_loader_sees_every_revision(self):
        _activate(variant="40kwh")
        raw = load_raw_pids()["ecus"]
        assert set(map(str, raw["VCU"]["pids"])) == {"2101", "22E001", "21F0"}
        assert "HEATER" in raw


class TestCacheKey:
    def test_the_variant_and_pins_are_part_of_it(self):
        keys = {
            _activate(variant="60kwh").cache_key,
            _activate(variant="60kwh", pins=["VCU=uds"]).cache_key,
            _activate(variant="60kwh", pins=["VCU=kwp"]).cache_key,
            _activate(variant="40kwh").cache_key,
        }
        assert len(keys) == 4

    def test_switching_car_or_pin_in_one_process_never_serves_a_stale_load(self):
        _activate(variant="60kwh", pins=["VCU=uds"])
        assert "22E001" in _pids("VCU")
        _activate(variant="60kwh", pins=["VCU=kwp"])
        assert "22E001" not in _pids("VCU") and "2101" in _pids("VCU")
        _activate(variant="40kwh")
        assert "HEATER" not in _ecus()

    def test_a_bundled_profile_without_revisions_is_untouched(self):
        set_active("hyundai-kona-os-ev")
        assert load_pids()["ecus"] is load_raw_pids()["ecus"]


class TestIoniqFaceliftBms:
    """The bundled Ioniq's BMS: ``kwp`` on the 28 kWh car, ``uds`` (ported, unverified) on 38."""

    def test_the_28kwh_car_reads_exactly_the_kwp_pages(self):
        set_active("hyundai-ioniq-ae-ev", variant="28kwh")
        bms = load_pids()["ecus"]["BMS"]
        assert {str(p) for p in bms["pids"]} == {"2101", "2102", "2103", "2104", "2105", "21F2"}
        assert bms["identity"]["id_protocol"] == "KWP2000"
        assert "revisions" not in bms and "revisions" not in bms["pids"][2101]

    def test_the_38kwh_car_reads_only_the_ported_uds_pages_unverified(self):
        set_active("hyundai-ioniq-ae-ev", variant="38kwh")
        bms = load_pids()["ecus"]["BMS"]
        assert {str(p) for p in bms["pids"]} == {"220101", "220105"}
        assert bms["identity"]["id_protocol"] == "UDS"
        params = [p for pid in bms["pids"].values() for p in pid["parameters"].values()]
        assert params and not any(p.get("verified") for p in params)


# ── checks ──────────────────────────────────────────────────────────────────


def _vcu(**changes) -> dict:
    data = yaml.safe_load((FIXTURE / "ecus" / "vcu.yaml").read_text())["VCU"]
    data.update(changes)
    return data


class TestChecks:
    def test_the_fixture_is_valid(self):
        _activate()
        assert main(["--profile", str(FIXTURE), "validate", "all"]) == 0

    def test_a_signal_must_stay_within_its_pids_revisions(self):
        vcu = _vcu()
        vcu["pids"]["2101"]["parameters"]["VEHICLE_SPEED"]["revisions"] = ["uds"]
        errors = rv.check_ecu("VCU", vcu)
        assert any("not among its PID's [kwp]" in e for e in errors)

    def test_a_gate_must_name_a_declared_revision(self):
        vcu = _vcu()
        vcu["pids"]["21F0"]["revisions"] = ["can"]
        assert any("names 'can'" in e for e in rv.check_ecu("VCU", vcu))

    def test_a_gate_needs_its_ecu_to_declare_revisions(self):
        bms = {"tx_id": 1, "pids": {"2101": {"revisions": ["kwp"], "parameters": {}}}}
        assert any("declares no revisions" in e for e in rv.check_ecu("BMS", bms))

    def test_presence_must_name_declared_variants(self):
        errors = rv.check_ecu("X", {"variants": ["99kwh"]}, variant_ids=["40kwh", "60kwh"])
        assert any("'99kwh'" in e for e in errors)

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ({"pids": {}}, "'pids' cannot differ by revision"),
            ({"identity": {"part_number": "x"}}, "identity.part_number cannot differ"),
            ({"fingerprints": [{"responds": "1A9"}]}, "whole bytes"),
            ({"fingerprints": [{"smell": "x"}]}, "unknown fingerprint kind"),
            ({"fingerprints": [{"identity": {"colour": "x*"}}]}, "unknown identity field"),
        ],
    )
    def test_a_revision_entry_is_whitelisted(self, entry, message):
        vcu = _vcu(revisions={"kwp": entry})
        for pid in vcu["pids"].values():
            pid.pop("revisions", None)
            for p in pid["parameters"].values():
                p.pop("revisions", None)
        errors = rv.check_ecu("VCU", vcu, identity_fields={"part_number"})
        assert any(message in e for e in errors), errors

    def test_variant_references_must_exist(self):
        _activate()
        variants = load_variants()
        ecus = load_raw_pids()["ecus"]
        assert rv.variant_reference_errors(variants, ecus) == []
        doc = yaml.safe_load((FIXTURE / "variants.yaml").read_text())
        doc["variants"]["40kwh"]["revisions"] = {"VCU": ["can"], "BMS": ["kwp"], "vcu": ["kwp"]}
        assert check_variants_doc(doc) == []
        from canlib.variants import _variant_from

        bad = {"40kwh": _variant_from("40kwh", doc["variants"]["40kwh"])}
        errors = rv.variant_reference_errors(bad, ecus)
        assert any("no revision 'can'" in e for e in errors)
        assert any("BMS declares no revisions" in e for e in errors)
        assert any("did you mean VCU" in e for e in errors)

    def test_one_signal_name_across_disjoint_revisions_is_allowed(self, copy):
        from canlib.commands.validate.pids import _duplicate_param_errors

        files = sorted((copy / "ecus").glob("*.yaml"))
        assert _duplicate_param_errors(files) == []
        vcu = copy / "ecus" / "vcu.yaml"
        vcu.write_text(vcu.read_text().replace("      revisions: [uds]\n", "", 1))
        assert any("VEHICLE_SPEED" in e for e in _duplicate_param_errors(files))

    def test_a_predicate_must_resolve_on_every_car(self, copy):
        from canlib.states.refs import check_references_on_every_car

        _activate(copy)
        assert check_references_on_every_car([("DRIVING", "VCU.VEHICLE_SPEED > 0")]) == []
        issues = check_references_on_every_car([("DRIVING", "VCU.DRIVE_MODE > 0")])
        assert len(issues) == 1
        assert "variant 40kwh, VCU=kwp" in issues[0].message
        assert "variant 60kwh, VCU=kwp" in issues[0].message
        assert "VCU=uds" not in issues[0].message

    def test_validate_states_fails_on_a_dark_revision(self, copy, capsys):
        states = copy / "vehicle_states.yaml"
        states.write_text(states.read_text().replace("VEHICLE_SPEED", "DRIVE_MODE"))
        assert main(["--profile", str(copy), "validate", "states"]) == 1
        assert "VCU=kwp" in capsys.readouterr().out

    def test_validate_captures_flags_an_impossible_revision_tag(self, copy, capsys):
        cap = copy / "captures" / "2026-01-01.json"
        cap.write_text(cap.read_text().replace('"VCU": "kwp"', '"VCU": "can"', 1))
        main(["--profile", str(copy), "validate", "captures"])
        assert "VCU revision 'can'" in capsys.readouterr().out


# ── captures ────────────────────────────────────────────────────────────────


def _labels() -> set[str]:
    from canlib.captures.store import load_all_captures

    return {e["session_label"] for e in load_all_captures()}


class TestCaptures:
    def test_scope_follows_the_resolved_revision(self):
        _activate(variant="60kwh", pins=["VCU=uds"])
        assert _labels() == {"60 kWh car, UDS VCU"}
        _activate(variant="60kwh")
        assert _labels() == {"60 kWh car, UDS VCU", "60 kWh car, KWP2000 VCU"}
        _activate(variant="40kwh")
        assert _labels() == {"40 kWh car, KWP2000 VCU"}

    def test_revisions_in_scope(self):
        assert revisions_in_scope({}, {"VCU": "kwp"})  # untagged: read as resolved
        assert revisions_in_scope({"revisions": {"VCU": "kwp"}}, {"VCU": "kwp"})
        assert not revisions_in_scope({"revisions": {"VCU": "uds"}}, {"VCU": "kwp"})
        assert revisions_in_scope({"revisions": {"VCU": "uds"}}, {})

    def test_a_recording_is_tagged_with_the_revisions_it_captured(self):
        prof = _activate(variant="60kwh", pins=["VCU=uds"])
        tag = capture_tag(prof)
        assert tag == {
            "variant": "60kwh",
            "revisions": {"VCU": "uds"},
            "attribution": {"variant": "selected", "revisions": {"VCU": "pinned"}},
        }
        assert narrow_to_captured(tag, {"BMS"}) == {
            "variant": "60kwh",
            "attribution": {"variant": "selected"},
        }
        tag40 = capture_tag(_activate(variant="40kwh"))
        assert tag40["revisions"] == {"VCU": "kwp"}
        assert tag40["attribution"]["revisions"] == {"VCU": "only"}
        assert "revisions" not in capture_tag(_activate(variant="60kwh"))

    def test_save_session_narrows_to_captured_ecus(self, copy):
        from canlib.captures import apply_capture_tag

        prof = _activate(copy, variant="60kwh", pins=["VCU=uds"])
        bms_only = {
            "date": "2026-02-01",
            "label": "x",
            "captures": [{"rx": "0x7EC", "pid": "2101"}],
        }
        assert "revisions" not in apply_capture_tag(bms_only, capture_tag(prof))
        vcu = {"date": "2026-02-01", "label": "x", "captures": [{"rx": "0x7EA", "pid": "21F0"}]}
        assert apply_capture_tag(vcu, capture_tag(prof))["revisions"] == {"VCU": "uds"}

    def test_an_import_ignores_a_config_pin(self, copy):
        from canlib.variants.selection import require_named_car

        config.set_config_key("profile_settings.variants.revisions.VCU", "uds")
        config.load_config.cache_clear()
        prof = _activate(copy, variant="60kwh")
        assert prof.revision_pins == (("VCU", "uds", "config"),)
        require_named_car(prof, "import uds")
        assert profile_mod.active().revision_pins == ()

    @pytest.mark.parametrize(
        "argv",
        [
            ["investigate", "VCU", "21F0"],
            ["hunt", "VCU", "21F0", "--physical"],
            ["decode", "VCU", "21F0", "--dump-bytes"],
            ["coverage"],
        ],
    )
    def test_byte_level_analysis_refuses_two_revisions(self, argv, capsys):
        rc = main(["--profile", str(FIXTURE), "--variant", "60kwh", *argv])
        assert rc == 2
        assert "span VCU revisions" in capsys.readouterr().err

    def test_a_pin_makes_the_byte_level_scope_one_revision(self, capsys):
        rc = main(["--profile", str(FIXTURE), "--variant", "60kwh", "--revision", "VCU=uds",
                   "investigate", "VCU", "21F0"])  # fmt: skip
        assert rc in (0, 1)
        assert "span VCU revisions" not in capsys.readouterr().err


# ── editors ─────────────────────────────────────────────────────────────────


def _run(root: Path, *argv: str) -> int:
    clear_cache()
    return main(["--profile", str(root), *argv])


def _vcu_doc(root: Path) -> dict:
    return yaml.safe_load((root / "ecus" / "vcu.yaml").read_text())["VCU"]


class TestEditors:
    def test_add_and_fingerprint_a_revision(self, copy):
        assert _run(copy, "ecu", "revisions", "VCU", "add", "cmb", "--protocol", "UDS") == 0
        assert _run(copy, "ecu", "revisions", "VCU", "set-fingerprint", "cmb",
                    "--identity", "part_number=X*") == 0  # fmt: skip
        assert _run(copy, "ecu", "revisions", "VCU", "set-fingerprint", "cmb",
                    "--responds", "22f190") == 0  # fmt: skip
        entry = _vcu_doc(copy)["revisions"]["cmb"]
        assert entry["identity"] == {"id_protocol": "UDS"}
        assert entry["fingerprints"] == [
            {"identity": {"part_number": "X*"}},
            {"responds": "22F190"},
        ]
        assert list(_vcu_doc(copy)["revisions"]) == ["kwp", "uds", "cmb"]

    def test_a_bad_revision_id_is_refused(self, copy):
        assert _run(copy, "ecu", "revisions", "VCU", "add", "UDS 2") == 1

    def test_rename_rewrites_every_reference(self, copy, capsys):
        assert _run(copy, "ecu", "revisions", "VCU", "rename", "uds", "uds2") == 0
        vcu = _vcu_doc(copy)
        assert list(vcu["revisions"]) == ["kwp", "uds2"]
        assert vcu["pids"]["22E001"]["revisions"] == ["uds2"]
        assert vcu["pids"]["21F0"]["parameters"]["DRIVE_MODE"]["revisions"] == ["uds2"]
        variants = yaml.safe_load((copy / "variants.yaml").read_text())["variants"]
        assert variants["60kwh"]["revisions"]["VCU"] == ["kwp", "uds2"]
        sessions = json.loads((copy / "captures" / "2026-01-01.json").read_text())["sessions"]
        assert [s["revisions"]["VCU"] for s in sessions] == ["kwp", "uds2", "kwp"]
        assert _run(copy, "validate", "all") == 0

    def test_remove_is_refused_while_referenced(self, copy, capsys):
        assert _run(copy, "ecu", "revisions", "VCU", "rm", "uds") == 1
        assert "still used by" in capsys.readouterr().err
        assert _run(copy, "ecu", "revisions", "VCU", "add", "spare") == 0
        assert _run(copy, "ecu", "revisions", "VCU", "rm", "spare") == 0
        assert "spare" not in _vcu_doc(copy)["revisions"]

    def test_gate_a_pid_and_a_signal(self, copy):
        assert _run(copy, "pids", "set-revisions", "VCU:21F0:GEAR", "kwp") == 0
        assert _vcu_doc(copy)["pids"]["21F0"]["parameters"]["GEAR"]["revisions"] == ["kwp"]
        # A PID gate the signal falls outside is refused (and reverted).
        before = (copy / "ecus" / "vcu.yaml").read_text()
        with pytest.raises(SystemExit):
            _run(copy, "pids", "set-revisions", "VCU:21F0", "uds")
        assert (copy / "ecus" / "vcu.yaml").read_text() == before
        assert _run(copy, "pids", "set-revisions", "VCU:21F0:GEAR", "--clear") == 0
        assert "revisions" not in _vcu_doc(copy)["pids"]["21F0"]["parameters"]["GEAR"]

    def test_set_variants_for_presence(self, copy):
        assert _run(copy, "ecu", "set-variants", "BMS", "40kwh") == 0
        bms = yaml.safe_load((copy / "ecus" / "bms.yaml").read_text())["BMS"]
        assert bms["variants"] == ["40kwh"]
        assert _run(copy, "ecu", "set-variants", "BMS", "99kwh") == 1
        assert _run(copy, "ecu", "set-variants", "BMS", "--clear") == 0
        assert "variants" not in yaml.safe_load((copy / "ecus" / "bms.yaml").read_text())["BMS"]

    def test_variants_set_revisions(self, copy):
        assert _run(copy, "variants", "set-revisions", "60kwh", "VCU", "uds") == 0
        doc = yaml.safe_load((copy / "variants.yaml").read_text())
        assert doc["variants"]["60kwh"]["revisions"]["VCU"] == ["uds"]
        with pytest.raises(SystemExit):
            _run(copy, "variants", "set-revisions", "60kwh", "VCU", "can")
        assert _run(copy, "variants", "set-revisions", "40kwh", "VCU", "--clear") == 0
        assert (
            "revisions"
            not in yaml.safe_load((copy / "variants.yaml").read_text())["variants"]["40kwh"]
        )

    def test_variant_rename_rewrites_ecu_presence(self, copy):
        assert _run(copy, "variants", "rename", "60kwh", "60kwh-eu") == 0
        heater = yaml.safe_load((copy / "ecus" / "heater.yaml").read_text())["HEATER"]
        assert heater["variants"] == ["60kwh-eu"]
        assert _run(copy, "validate", "all") == 0

    def test_variant_rm_is_refused_while_an_ecu_is_fitted_to_it(self, copy, capsys):
        cap = copy / "captures" / "2026-01-01.json"
        data = json.loads(cap.read_text())
        data["sessions"] = [s for s in data["sessions"] if s["variant"] != "60kwh"]
        from canlib.captures import io as capture_io

        cap.write_text(capture_io.capture_text(data))
        with pytest.raises(SystemExit, match="ECU\\(s\\) HEATER"):
            _run(copy, "variants", "rm", "60kwh")

    def test_ecu_rename_follows_into_variants_and_capture_tags(self, copy):
        assert _run(copy, "ecu", "rename", "VCU", "PCU") == 0
        variants = yaml.safe_load((copy / "variants.yaml").read_text())["variants"]
        assert variants["40kwh"]["revisions"] == {"PCU": ["kwp"]}
        session = json.loads((copy / "captures" / "2026-01-01.json").read_text())["sessions"][0]
        assert session["revisions"] == {"PCU": "kwp"}
        assert session["attribution"]["revisions"] == {"PCU": "only"}

    def test_profile_use_pin(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(FIXTURE.parent))
        assert main(["profile", "use", "variants", "--variant", "60kwh", "--pin", "vcu=uds"]) == 0
        config.load_config.cache_clear()
        cfg = config.load_config()
        assert cfg["profile_settings"]["variants"] == {
            "variant": "60kwh",
            "revisions": {"VCU": "uds"},
        }

    def test_profile_use_bad_pin_writes_nothing(self, monkeypatch, capsys):
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(FIXTURE.parent))
        assert main(["profile", "use", "variants", "--variant", "40kwh", "--pin", "VCU=uds"]) == 2
        assert "can only carry kwp" in capsys.readouterr().err
        config.load_config.cache_clear()
        assert "default_profile" not in config.load_config()


# ── phase 7: per-revision overrides and evidence ────────────────────────────


def _ecu_with_overrides() -> dict:
    return {
        "tx_id": 0x7E2,
        "revisions": {"kwp": {}, "uds": {}},
        "pids": {
            "21F0": {
                "status": "active",
                "response_frames": 3,
                "verified": True,
                "per_revision": {"uds": {"response_frames": 5}},
                "parameters": {
                    "GEAR": {
                        "expression": "B4",
                        "verified": True,
                        "per_revision": {"uds": {"expression": "B5", "unit": "x"}},
                    },
                    "MODE": {"expression": "B6", "verified": True},
                    "UDS_ONLY": {"expression": "B7", "verified": True, "revisions": ["uds"]},
                },
            },
            "22E001": {
                "status": "active",
                "revisions": ["uds"],
                "response_frames": 2,
                "parameters": {"SPEED": {"expression": "B6", "verified": True}},
            },
        },
    }


def _resolve_at(rid):
    ecus = {"VCU": _ecu_with_overrides()}
    res = {"VCU": rv.Resolution("VCU", rid, "flag" if rid else None, ("kwp", "uds"))}
    return rv.apply(ecus, None, res)[0]["VCU"]["pids"]


class TestOverridesAndEvidence:
    def test_the_reference_revision_keeps_the_base_evidence(self):
        pids = _resolve_at("kwp")
        assert pids["21F0"]["response_frames"] == 3
        gear = pids["21F0"]["parameters"]["GEAR"]
        assert (gear["expression"], gear["verified"]) == ("B4", True)
        assert "per_revision" not in gear and "22E001" not in pids

    def test_another_revision_gets_only_its_own_evidence(self):
        pids = _resolve_at("uds")
        assert pids["21F0"]["response_frames"] == 5  # its own count, never kwp's 3
        gear = pids["21F0"]["parameters"]["GEAR"]
        assert (gear["expression"], gear["unit"], gear["verified"]) == ("B5", "x", False)
        assert pids["21F0"]["parameters"]["MODE"]["verified"] is False
        # A uds-only signal under an ungated PID: its own base speaks for uds, but a
        # verified equal to the PID's may be inherited from it, which spoke for kwp —
        # so it stays unverified until per_revision.uds says otherwise.
        assert pids["21F0"]["parameters"]["UDS_ONLY"]["verified"] is False
        # A uds-only PID's base values are uds's own.
        assert pids["22E001"]["response_frames"] == 2
        assert pids["22E001"]["parameters"]["SPEED"]["verified"] is True

    def test_an_undetermined_revision_counts_and_verifies_nothing(self):
        pids = _resolve_at(None)
        assert "response_frames" not in pids["21F0"]
        assert pids["21F0"]["parameters"]["MODE"]["verified"] is False

    def test_seeds_come_from_the_resolved_revision_only(self):
        from canlib.response_frames import seed_counts

        for rid, want in (("kwp", 3), ("uds", 5)):
            data = {"ecus": {"VCU": {**_ecu_with_overrides(), "pids": _resolve_at(rid)}}}
            assert seed_counts(data)[(0x7E2, "21F0")] == want
        data = {"ecus": {"VCU": {**_ecu_with_overrides(), "pids": _resolve_at(None)}}}
        assert (0x7E2, "21F0") not in seed_counts(data)

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ({"kwp": {"parameters": {}}}, "'parameters' cannot be overridden"),
            ({"kwp": {"revisions": ["kwp"]}}, "'revisions' cannot be overridden"),
            ({"kwp": {"status": "draft"}}, "'status' cannot differ"),
            ({"can": {"period": 5}}, "declares no revision 'can'"),
            ({"kwp": {"response_frames": True}}, "response_frames must be an int"),
        ],
    )
    def test_pid_overrides_are_whitelisted(self, entry, message):
        vcu = _vcu()
        vcu["pids"]["21F0"]["per_revision"] = entry
        assert any(message in e for e in rv.check_ecu("VCU", vcu)), rv.check_ecu("VCU", vcu)

    def test_an_override_outside_the_applicability_is_dead_data(self):
        vcu = _vcu()
        vcu["pids"]["2101"]["per_revision"] = {"uds": {"period": 5}}
        assert any("never read" in e for e in rv.check_ecu("VCU", vcu))

    def test_signal_overrides_are_whitelisted(self):
        vcu = _vcu()
        vcu["pids"]["21F0"]["parameters"]["GEAR"]["per_revision"] = {"uds": {"ha_class": "x"}}
        assert any("'ha_class' cannot differ" in e for e in rv.check_ecu("VCU", vcu))

    def test_a_count_learned_on_one_revision_is_written_to_that_revision(self, copy):
        from canlib.frame_counts import FrameCountLedger
        from canlib.response_frames import persist, seed_counts

        for rid, frames in (("uds", 4), ("kwp", 2)):
            _activate(copy, variant="60kwh", pins=[f"VCU={rid}"])
            ledger = FrameCountLedger()
            ledger.confirm((0x7E2, "21F0"), frames)
            assert persist(load_pids(), ledger) == 1
        pid = _vcu_doc(copy)["pids"]["21F0"]
        assert pid["response_frames"] == 2  # kwp is the reference revision
        assert pid["per_revision"] == {"uds": {"response_frames": 4}}
        _activate(copy, variant="60kwh", pins=["VCU=kwp"])
        assert seed_counts(load_pids())[(0x7E2, "21F0")] == 2
        _activate(copy, variant="60kwh", pins=["VCU=uds"])
        assert seed_counts(load_pids())[(0x7E2, "21F0")] == 4

    def test_an_undetermined_revision_persists_nothing(self, copy):
        from canlib.frame_counts import FrameCountLedger
        from canlib.response_frames import persist

        _activate(copy, variant="60kwh")
        ledger = FrameCountLedger()
        ledger.confirm((0x7E2, "21F0"), 3)
        before = (copy / "ecus" / "vcu.yaml").read_text()
        assert persist(load_pids(), ledger) == 0
        assert (copy / "ecus" / "vcu.yaml").read_text() == before

    def test_upsert_param_revision_on_an_existing_signal(self, copy):
        assert _run(copy, "pids", "upsert-param", "VCU", "21F0", "GEAR", "B5",
                    "--revision", "uds", "--verified") == 0  # fmt: skip
        gear = _vcu_doc(copy)["pids"]["21F0"]["parameters"]["GEAR"]
        assert gear["expression"] == "B4"
        assert gear["per_revision"] == {"uds": {"expression": "B5", "verified": True}}
        _activate(copy, variant="60kwh", pins=["VCU=uds"])
        assert _pids("VCU")["21F0"]["parameters"]["GEAR"]["expression"] == "B5"

    def test_rename_follows_into_overrides(self, copy):
        assert _run(copy, "pids", "upsert-param", "VCU", "21F0", "GEAR", "B5",
                    "--revision", "uds") == 0  # fmt: skip
        assert _run(copy, "ecu", "revisions", "VCU", "rename", "uds", "uds2") == 0
        gear = _vcu_doc(copy)["pids"]["21F0"]["parameters"]["GEAR"]
        assert list(gear["per_revision"]) == ["uds2"]

    def test_upsert_param_revision_on_a_new_signal_gates_it(self, copy):
        assert _run(copy, "pids", "upsert-param", "VCU", "21F0", "NEW", "B6",
                    "--revision", "uds") == 0  # fmt: skip
        assert _vcu_doc(copy)["pids"]["21F0"]["parameters"]["NEW"]["revisions"] == ["uds"]

    def test_the_28kwh_ioniq_keeps_its_verified_and_counts(self):
        set_active("hyundai-ioniq-ae-ev", variant="28kwh")
        resolved = load_pids()["ecus"]["BMS"]["pids"]
        raw = load_raw_pids()["ecus"]["BMS"]["pids"]
        for code, pid in resolved.items():
            assert pid.get("response_frames") == raw[code].get("response_frames")
            for name, param in pid["parameters"].items():
                assert param.get("verified") == raw[code]["parameters"][name].get("verified")
