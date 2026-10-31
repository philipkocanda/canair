"""AutoPID, the explorer, research and validation per variant and ECU revision.

Plan: ``plans/2026-10-30-profile-variants-9-consumers.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from canlib import config
from canlib.cli import main
from canlib.pids import load_pids
from canlib.profile import set_active

FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "variants"
REPO = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


@pytest.fixture
def copy(tmp_path) -> Path:
    dest = tmp_path / "variants"
    shutil.copytree(FIXTURE, dest)
    return dest


class TestAutoPid:
    def test_each_car_gets_its_own_file(self):
        from canlib.commands.wican import _profile_out

        set_active(str(FIXTURE), variant="40kwh")
        assert _profile_out("4") == FIXTURE / "out" / "40kwh" / "autopid-v4.json"
        assert _profile_out("6") == FIXTURE / "out" / "40kwh" / "autopid-v6.json"
        set_active("hyundai-kona-os-ev")  # no car named: unchanged
        assert _profile_out("4").parent.name == "out"

    @pytest.mark.parametrize("firmware", ["4", "6"])
    def test_the_28kwh_ioniq_build_is_the_committed_one(self, tmp_path, capsys, firmware):
        out = tmp_path / "autopid.json"
        argv = ["--profile", "hyundai-ioniq-ae-ev", "--variant", "28kwh"]
        write = ["wican", "autopid", "write", "--firmware", firmware, "--out", str(out)]
        assert main([*argv, *write]) == 0
        name = f"autopid-v{firmware}.json"
        committed = REPO / "profiles" / "hyundai-ioniq-ae-ev" / "out" / "28kwh" / name
        assert out.read_bytes() == committed.read_bytes()

    def test_every_bundled_car_carries_both_firmware_files(self):
        from canlib.profile import resolve_profile
        from canlib.variants import load_variants

        missing = []
        for root in sorted((REPO / "profiles").glob("*/profile.yaml")):
            prof = resolve_profile(str(root.parent))
            for variant in load_variants(prof) or {"": None}:
                folder = prof.root / "out" / variant
                missing += [
                    str(folder / f"autopid-v{fw}.json")
                    for fw in ("4", "6")
                    if not (folder / f"autopid-v{fw}.json").exists()
                ]
        assert not missing, "regenerate with `canair wican autopid write`:\n" + "\n".join(missing)

    def test_the_38kwh_build_ships_none_of_the_unverified_port(self, tmp_path, capsys):
        out = tmp_path / "a.json"
        argv = ["--profile", "hyundai-ioniq-ae-ev", "--variant", "38kwh", "wican", "autopid"]
        write = ["write", "--firmware", "4", "--out", str(out)]
        assert main([*argv, *write]) == 0
        names = {n for g in json.loads(out.read_text())["pids"] for n in g["parameters"]}
        assert "CELL_VOLTAGE_DEVIATION" not in names  # the uds port is unverified
        assert "CUMULATIVE_ENERGY_CHARGED" not in names  # a 28 kWh (kwp) page
        assert main([*argv, *write, "--include-unverified"]) == 0
        names = {n for g in json.loads(out.read_text())["pids"] for n in g["parameters"]}
        assert "CELL_VOLTAGE_DEVIATION" in names
        assert "CUMULATIVE_ENERGY_CHARGED" not in names

    def test_refused_while_a_revision_is_undetermined(self, tmp_path, capsys):
        argv = ["--profile", str(FIXTURE), "--variant", "60kwh", "wican", "autopid", "write"]
        assert main([*argv, "--firmware", "4", "--out", str(tmp_path / "a.json")]) == 2
        err = capsys.readouterr().err
        assert "VCU is undetermined" in err and "--pin VCU=kwp" in err
        assert not (tmp_path / "a.json").exists()
        pinned = ["--profile", str(FIXTURE), "--variant", "60kwh", "--revision", "VCU=uds"]
        out = tmp_path / "b.json"
        assert main([*pinned, "wican", "autopid", "write", "--firmware", "4", "--out", str(out),
                     "--include-unverified"]) == 0  # fmt: skip
        exprs = {
            (n, e) for g in json.loads(out.read_text())["pids"] for n, e in g["parameters"].items()
        }
        assert ("VEHICLE_SPEED", "B6") in exprs and ("VEHICLE_SPEED", "B4") not in exprs
        assert ("DRIVE_MODE", "B5") in exprs

    def test_send_to_may_repeat_across_disjoint_revisions(self, copy):
        from canlib.commands.validate.send_to import send_to_findings

        vcu = copy / "ecus" / "vcu.yaml"
        text = vcu.read_text()
        text = text.replace(
            "          expression: B4\n          unit: km/h\n",
            "          expression: B4\n          unit: km/h\n          send_to: car/speed\n",
        ).replace(
            "          expression: B6\n          unit: km/h\n",
            "          expression: B6\n          unit: km/h\n          send_to: car/speed\n",
        )
        vcu.write_text(text)
        errors, _ = send_to_findings([vcu])
        assert errors == []
        vcu.write_text(text.replace("      revisions: [uds]\n", "", 1))
        errors, _ = send_to_findings([vcu])
        assert any("car/speed" in e for e in errors)


class TestExplorer:
    def test_gates_overrides_and_presence_are_exported(self):
        from canlib.explorer.export import export_profile

        data = export_profile(FIXTURE)
        ecus = {e["name"]: e for e in data["ecus"]}
        assert [r["id"] for r in ecus["VCU"]["revisions"]] == ["kwp", "uds"]
        assert ecus["VCU"]["revisions"][1]["protocol"] == "UDS"
        assert ecus["HEATER"]["variants"] == ["60kwh"]
        assert ecus["BMS"]["variants"] is None
        pids = {p["id"]: p for p in ecus["VCU"]["pids"]}
        assert pids["22E001"]["revisions"] == ["uds"]
        sigs = {s["name"]: s for s in pids["21F0"]["signals"]}
        assert sigs["DRIVE_MODE"]["revisions"] == ["uds"] and "revisions" not in sigs["GEAR"]
        variants = {v["id"]: v for v in data["variants"]}
        assert variants["40kwh"]["revisions"] == {"VCU": ["kwp"]}
        assert "fingerprints" not in json.dumps(data)

    def test_the_ioniq_38kwh_bms_is_unverified_uds(self):
        from canlib.explorer.export import export_profile

        data = export_profile(REPO / "profiles" / "hyundai-ioniq-ae-ev")
        bms = next(e for e in data["ecus"] if e["name"] == "BMS")
        uds = [p for p in bms["pids"] if p.get("revisions") == ["uds"]]
        assert {p["id"] for p in uds} == {"220101", "220105"}
        assert not any(s["verified"] for p in uds for s in p["signals"])


class TestResearch:
    def test_a_task_about_another_revision_is_not_listed(self, copy):
        assert main(["--profile", str(copy), "pids", "add-research", "VCU", "--type", "verify",
                     "--target", "22E001", "--status", "pending", "--revision", "uds"]) == 0  # fmt: skip
        doc = yaml.safe_load((copy / "ecus" / "vcu.yaml").read_text())["VCU"]
        assert doc["research"][0]["revisions"] == ["uds"]
        set_active(str(copy), variant="40kwh")
        assert load_pids()["ecus"]["VCU"].get("research") == []
        set_active(str(copy), variant="60kwh", revisions=["VCU=uds"])
        assert len(load_pids()["ecus"]["VCU"]["research"]) == 1

    def test_a_research_gate_must_name_a_revision(self):
        from canlib.variants.revisions import check_ecu

        vcu = yaml.safe_load((FIXTURE / "ecus" / "vcu.yaml").read_text())["VCU"]
        vcu["research"] = [{"type": "verify", "target": "x", "status": "pending",
                            "revisions": ["can"]}]  # fmt: skip
        assert any("research[0]" in e and "'can'" in e for e in check_ecu("VCU", vcu))
