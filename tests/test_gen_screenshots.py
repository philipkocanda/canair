"""Tests for scripts/gen_screenshots.py.

Covers the pure helpers, the manifest ↔ committed-asset invariant, and the
`--check` currency logic (asset presence, orphan detection, command validity).
Rendering (freeze/vhs) is not exercised — it needs external binaries and produces
non-reproducible output by design.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen_screenshots.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("gen_screenshots", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest(mod):
    return mod._load_manifest()


def test_manifest_entries_well_formed(mod, manifest):
    entries = mod._all_entries(manifest)
    assert entries, "manifest declares no shots"
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate shot id in manifest"
    for entry in entries:
        assert entry["kind"] in {"rich", "anim"}
        if entry["kind"] == "rich":
            assert isinstance(entry["command"], list) and entry["command"]
        else:
            assert (mod.SHOTS_DIR / entry["tape"]).exists(), f"missing tape for {entry['id']}"


def test_asset_path_extension(mod):
    assert mod._asset_path({"id": "x", "kind": "rich"}).suffix == ".svg"
    assert mod._asset_path({"id": "y", "kind": "anim"}).suffix == ".gif"


def test_checks_for(mod):
    rich = {"id": "x", "kind": "rich", "command": ["bus"]}
    assert mod._checks_for(rich) == [["bus"]]
    anim = {"id": "y", "kind": "anim", "checks": [["a", "b"], ["c"]]}
    assert mod._checks_for(anim) == [["a", "b"], ["c"]]
    assert mod._checks_for({"id": "z", "kind": "anim"}) == []


def test_base_argv_uses_relative_profiles_dir(mod, manifest):
    """The rendered command must use a relative --profiles-dir so no absolute
    (username-bearing) path is baked into the committed images."""
    argv = mod._base_argv(manifest)
    assert argv == ["--profiles-dir", "profiles", "--profile", "ioniq-2017"]


def test_committed_assets_present(mod, manifest):
    """Every manifest asset exists in the repo (guards against a deleted image)."""
    for entry in mod._all_entries(manifest):
        path = mod._asset_path(entry)
        assert path.exists(), f"committed asset missing: {path}"


def test_no_username_baked_into_svgs(mod, manifest):
    """No SVG may embed an absolute /Users/... path (a PII leak)."""
    for entry in mod._all_entries(manifest):
        if entry["kind"] != "rich":
            continue
        text = mod._asset_path(entry).read_text(errors="ignore")
        assert "/Users/" not in text, f"{entry['id']}.svg leaks an absolute home path"


def test_check_passes_when_assets_present_and_commands_ok(mod, manifest, monkeypatch):
    monkeypatch.setattr(mod, "_run_command", lambda *a: 0)
    assert mod.check(manifest) == 0


def test_check_fails_on_command_drift(mod, manifest, monkeypatch):
    """A renamed command / dropped flag surfaces as a non-zero exit code."""
    monkeypatch.setattr(mod, "_run_command", lambda *a: 2)
    assert mod.check(manifest) == 1


def _write_manifest(shots_dir: Path, body: str) -> None:
    (shots_dir / "shots.yaml").write_text(body)


def test_check_detects_missing_and_orphan_assets(mod, tmp_path, monkeypatch):
    shots = tmp_path / "screenshots"
    (shots / "tapes").mkdir(parents=True)
    _write_manifest(
        shots,
        "profile: ioniq-2017\nprofiles_dir: profiles\nfreeze_config: freeze.json\n"
        "shots:\n  - id: present\n    kind: rich\n    command: [bus]\n"
        "  - id: absent\n    kind: rich\n    command: [ecu]\n",
    )
    monkeypatch.setattr(mod, "SHOTS_DIR", shots)
    monkeypatch.setattr(mod, "MANIFEST", shots / "shots.yaml")
    monkeypatch.setattr(mod, "_run_command", lambda *a: 0)

    manifest = mod._load_manifest()

    # `absent.svg` is missing → fail.
    (shots / "present.svg").write_text("<svg/>")
    assert mod.check(manifest) == 1

    # Both present, plus an orphan not in the manifest → still fail.
    (shots / "absent.svg").write_text("<svg/>")
    (shots / "orphan.svg").write_text("<svg/>")
    assert mod.check(manifest) == 1

    # Remove the orphan → pass.
    (shots / "orphan.svg").unlink()
    assert mod.check(manifest) == 0
