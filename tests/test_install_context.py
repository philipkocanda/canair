"""Tests for install-context detection (canlib.install_context).

These verify that canair can tell *how* it was invoked — from the repo working
tree (``uv run`` / dev checkout) vs. the ``uv tool install`` snapshot copy — and
that it flags when the installed tool copy has drifted out of sync with the
source clone's ``pyproject.toml`` version.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from canlib import install_context as ic


def _make_clone(tmp_path, version: str):
    """Create a fake source clone with a git dir + pyproject at ``version``."""
    clone = tmp_path / "clone"
    (clone / "canlib").mkdir(parents=True)
    (clone / ".git").mkdir()
    (clone / "pyproject.toml").write_text(f'[project]\nname = "canair"\nversion = "{version}"\n')
    return clone


def _make_tool_venv(tmp_path, version: str):
    """Create a fake uv-tool venv with a canlib copy + dist-info at ``version``."""
    root = tmp_path / "data" / "uv" / "tools" / "canair"
    sp = root / "lib" / "python3.13" / "site-packages"
    (sp / "canlib").mkdir(parents=True)
    (sp / "canlib" / "__init__.py").write_text("")
    (sp / f"canair-{version}.dist-info").mkdir()
    return tmp_path / "data", sp


class TestCloneVersion:
    def test_reads_pyproject_version(self, tmp_path):
        clone = _make_clone(tmp_path, "3.4.5")
        assert ic.clone_version(clone) == "3.4.5"

    def test_none_when_no_clone(self):
        assert ic.clone_version(None) is None

    def test_none_when_no_pyproject(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert ic.clone_version(tmp_path / "empty") is None


class TestInstalledToolVersion:
    def test_reads_dist_info(self, tmp_path, monkeypatch):
        data_home, _ = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        assert ic.installed_tool_version() == "0.1.0"

    def test_none_when_no_tool_venv(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
        assert ic.installed_tool_version() is None


class TestRunningOrigin:
    def test_repo_when_package_in_git_clone(self, tmp_path, monkeypatch):
        clone = _make_clone(tmp_path, "1.0.0")
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")
        # No uv-tool venv on this XDG path.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
        assert ic.running_origin() == "repo"

    def test_uv_tool_when_package_is_the_copy(self, tmp_path, monkeypatch):
        data_home, sp = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(ic, "running_package_dir", lambda: sp / "canlib")
        assert ic.running_origin() == "uv-tool"

    def test_other_when_neither(self, tmp_path, monkeypatch):
        pkg = tmp_path / "site-packages" / "canlib"
        pkg.mkdir(parents=True)
        monkeypatch.setattr(ic, "running_package_dir", lambda: pkg)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
        assert ic.running_origin() == "other"


class TestBundledProfilesAreSnapshot:
    def test_true_when_running_uv_tool_copy(self, tmp_path, monkeypatch):
        data_home, sp = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(ic, "running_package_dir", lambda: sp / "canlib")
        assert ic.bundled_profiles_are_snapshot() is True

    def test_false_when_running_from_repo(self, tmp_path, monkeypatch):
        clone = _make_clone(tmp_path, "1.0.0")
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
        assert ic.bundled_profiles_are_snapshot() is False


class TestDescribeSyncVerdict:
    def test_out_of_sync_when_versions_differ(self, tmp_path, monkeypatch):
        clone = _make_clone(tmp_path, "1.2.0")
        data_home, _sp = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")

        result = ic.describe(clone)
        assert result["running_origin"] == "repo"
        assert result["clone_version"] == "1.2.0"
        assert result["tool_version"] == "0.1.0"
        assert result["out_of_sync"] is True

    def test_in_sync_when_versions_match(self, tmp_path, monkeypatch):
        clone = _make_clone(tmp_path, "1.2.0")
        data_home, _sp = _make_tool_venv(tmp_path, "1.2.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")

        result = ic.describe(clone)
        assert result["out_of_sync"] is False

    def test_not_out_of_sync_without_tool_copy(self, tmp_path, monkeypatch):
        clone = _make_clone(tmp_path, "1.2.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nothing"))
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")

        result = ic.describe(clone)
        assert result["tool_version"] is None
        assert result["out_of_sync"] is False


class TestUpdateCommandReportsInstall:
    def _args(self, **kw):
        base = {"check": True, "yes": False, "json": True}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_json_includes_install_block(self, tmp_path, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        clone = _make_clone(tmp_path, "1.2.0")
        data_home, _sp = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(update_cmd, "source_clone", lambda: clone)
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")
        monkeypatch.setattr(update_cmd, "fetch_latest_release", lambda *a, **k: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.2.0")

        rc = update_cmd.run(self._args())
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["install"]["out_of_sync"] is True
        assert out["install"]["tool_version"] == "0.1.0"
        assert out["install"]["clone_version"] == "1.2.0"

    def test_text_warns_on_out_of_sync(self, tmp_path, monkeypatch, capsys):
        from canlib.commands import update as update_cmd

        clone = _make_clone(tmp_path, "1.2.0")
        data_home, _sp = _make_tool_venv(tmp_path, "0.1.0")
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        monkeypatch.setattr(update_cmd, "source_clone", lambda: clone)
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")
        monkeypatch.setattr(update_cmd, "fetch_latest_release", lambda *a, **k: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.2.0")

        rc = update_cmd.run(self._args(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "out of sync" in out


class TestInstalledSnapshotKind:
    """Is a profile path inside a frozen install snapshot?"""

    def test_working_checkout_is_none(self, tmp_path):
        assert (
            ic.installed_snapshot_kind(tmp_path / "projects" / "canair" / "profiles" / "x") is None
        )

    def test_uv_tool_snapshot(self):
        p = Path(
            "/home/u/.local/share/uv/tools/canair/lib/python3.12/site-packages/profiles/ioniq-2017"
        )
        assert ic.installed_snapshot_kind(p) == "uv tool"

    def test_pipx_snapshot(self):
        p = Path("/home/u/.local/pipx/venvs/canair/lib/python3.12/site-packages/profiles/x")
        assert ic.installed_snapshot_kind(p) == "pipx"

    def test_generic_site_packages(self):
        p = Path("/usr/lib/python3.12/site-packages/profiles/ioniq-2017")
        assert ic.installed_snapshot_kind(p) == "installed package"


class TestSnapshotWriteNote:
    """The warning every profile write emits when it lands in a snapshot."""

    def test_a_writable_location_says_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ic, "source_clone", lambda: None)
        assert ic.snapshot_write_note(tmp_path / "profiles" / "mycar" / "captures") is None

    def test_names_the_path_the_cost_and_the_fix(self, monkeypatch, tmp_path):
        clone = tmp_path / "projects" / "canair"
        monkeypatch.setattr(ic, "source_clone", lambda: clone)
        p = Path("/home/u/.local/share/uv/tools/canair/lib/python3.12/site-packages/profiles/car")

        note = ic.snapshot_write_note(p)

        assert note is not None
        assert "uv tool" in note
        assert str(p) in note
        # The remedy is a runnable command naming the clone we actually found.
        assert f"canair config set profiles_dir {clone / 'profiles'}" in note

    def test_without_a_clone_it_suggests_adopting(self, monkeypatch):
        monkeypatch.setattr(ic, "source_clone", lambda: None)
        p = Path("/usr/lib/python3.12/site-packages/profiles/car")

        note = ic.snapshot_write_note(p)

        assert note is not None
        assert "canair profile adopt" in note
        assert "profiles_dir" not in note


class TestSnapshotProfileRisks:
    """What a reinstall would delete: snapshot profile data absent from the clone."""

    def _snapshot(self, tmp_path):
        """A bundled-profiles dir inside a uv-tool site-packages, with one profile."""
        sp = tmp_path / "data" / "uv" / "tools" / "canair" / "lib" / "python3.12" / "site-packages"
        root = sp / "profiles" / "ioniq-2017"
        (root / "ecus").mkdir(parents=True)
        (root / "captures").mkdir()
        (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')
        (root / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n")
        return sp / "profiles", root

    def _clone_copy(self, tmp_path, snapshot_root):
        """A clone whose profiles/ mirrors the snapshot profile exactly."""
        import shutil

        clone = tmp_path / "clone"
        dest = clone / "profiles" / snapshot_root.name
        dest.parent.mkdir(parents=True)
        shutil.copytree(snapshot_root, dest)
        return clone

    def test_no_clone_makes_no_claim(self, tmp_path, monkeypatch):
        bundled, _root = self._snapshot(tmp_path)
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)
        assert ic.snapshot_profile_risks(None) == []

    def test_a_dev_checkout_is_never_at_risk(self, tmp_path, monkeypatch):
        """Bundled profiles outside site-packages *are* the clone — nothing to lose."""
        bundled = tmp_path / "repo" / "profiles"
        root = bundled / "ioniq-2017"
        (root / "ecus").mkdir(parents=True)
        (root / "profile.yaml").write_text('car_model: "T"\ninit: "x"\n')
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)
        assert ic.snapshot_profile_risks(tmp_path / "clone") == []

    def test_an_identical_copy_is_not_a_risk(self, tmp_path, monkeypatch):
        bundled, root = self._snapshot(tmp_path)
        clone = self._clone_copy(tmp_path, root)
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)
        assert ic.snapshot_profile_risks(clone) == []

    def test_reports_a_capture_written_only_into_the_snapshot(self, tmp_path, monkeypatch):
        bundled, root = self._snapshot(tmp_path)
        clone = self._clone_copy(tmp_path, root)
        (root / "captures" / "2026-08-05.json").write_text('{"sessions": []}')
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)

        (risk,) = ic.snapshot_profile_risks(clone)

        assert risk.name == "ioniq-2017"
        assert risk.missing == [Path("captures/2026-08-05.json")]
        assert risk.differing == []

    def test_reports_a_definition_edited_in_the_snapshot(self, tmp_path, monkeypatch):
        bundled, root = self._snapshot(tmp_path)
        clone = self._clone_copy(tmp_path, root)
        (root / "ecus" / "bms.yaml").write_text("BMS:\n  tx_id: 0x7E4\n  # edited\n")
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)

        (risk,) = ic.snapshot_profile_risks(clone)

        assert risk.differing == [Path("ecus/bms.yaml")]
        assert risk.files == [Path("ecus/bms.yaml")]

    def test_generated_output_is_not_a_loss(self, tmp_path, monkeypatch):
        """``out/`` is regenerated by `canair wican autopid write` — not data."""
        bundled, root = self._snapshot(tmp_path)
        clone = self._clone_copy(tmp_path, root)
        (root / "out").mkdir()
        (root / "out" / "autopid.json").write_text("[]")
        monkeypatch.setattr("canlib.profile.BUNDLED_PROFILES_DIR", bundled)
        assert ic.snapshot_profile_risks(clone) == []


class TestSourceClone:
    def test_prefers_the_uv_receipt_directory(self, tmp_path, monkeypatch):
        clone = tmp_path / "from-receipt"
        (clone / ".git").mkdir(parents=True)
        root = tmp_path / "data" / "uv" / "tools" / "canair"
        root.mkdir(parents=True)
        (root / "uv-receipt.toml").write_text(
            f'[tool]\nrequirements = [{{ name = "canair", directory = "{clone}" }}]\n'
        )
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        assert ic.source_clone() == clone

    def test_falls_back_to_the_packages_own_repo_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "empty"))
        # The running tree is a git clone, so this resolves to the repo root.
        from canlib.constants import SCRIPT_DIR

        assert ic.source_clone() == Path(SCRIPT_DIR)
