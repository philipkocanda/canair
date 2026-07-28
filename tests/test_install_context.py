"""Tests for install-context detection (canlib.install_context).

These verify that canair can tell *how* it was invoked — from the repo working
tree (``uv run`` / dev checkout) vs. the ``uv tool install`` snapshot copy — and
that it flags when the installed tool copy has drifted out of sync with the
source clone's ``pyproject.toml`` version.
"""

from __future__ import annotations

import argparse
import json

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
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: clone)
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
        monkeypatch.setattr(update_cmd, "_find_clone_dir", lambda: clone)
        monkeypatch.setattr(ic, "running_package_dir", lambda: clone / "canlib")
        monkeypatch.setattr(update_cmd, "fetch_latest_release", lambda *a, **k: None)
        import canlib

        monkeypatch.setattr(canlib, "__version__", "1.2.0")

        rc = update_cmd.run(self._args(json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "out of sync" in out
