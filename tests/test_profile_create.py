"""Tests for `canair profile create` scaffolding and config.set_config_value."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.commands.profile import DEFAULT_INIT, _cmd_create, _cmd_list, _cmd_use
from canlib.commands.validate import collect_pids_validation


@pytest.fixture(autouse=True)
def _isolate_profile_state():
    """Reset the memoized active profile and config cache around each test.

    `_cmd_list`/`active()` memoize a Profile into a module global; when the test
    points it at a tmp_path that's later deleted, it would pollute unrelated
    tests that resolve the active profile.
    """
    from canlib import config, profile

    profile._active = None
    config.load_config.cache_clear()
    yield
    profile._active = None
    config.load_config.cache_clear()


def _args(**kw) -> argparse.Namespace:
    base = {
        "name": "testcar",
        "car_model": "VW e-Golf 2019",
        "init": None,
        "path": None,
        "set_default": False,
        "force": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestProfileCreate:
    def test_scaffolds_bundle(self, tmp_path):
        root = tmp_path / "prof"
        rc = _cmd_create(_args(path=root))
        assert rc == 0
        assert (root / "ecus").is_dir()
        assert (root / "captures").is_dir()
        assert (root / "out").is_dir()
        assert (root / "profile.yaml").exists()

    def test_scaffolds_can_buses(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        cb = root / "can_buses.yaml"
        assert cb.exists()
        data = yaml.safe_load(cb.read_text())
        assert "All" in data["can_buses"]
        assert data["can_buses"]["All"]["name"]

    def test_meta_contents(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root, init="ATSP0;"))
        meta = yaml.safe_load((root / "profile.yaml").read_text())
        assert meta["car_model"] == "VW e-Golf 2019"
        assert meta["init"] == "ATSP0;"

    def test_default_init(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        meta = yaml.safe_load((root / "profile.yaml").read_text())
        assert meta["init"] == DEFAULT_INIT

    def test_created_ecus_dir_validates_empty(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        files = sorted((root / "ecus").glob("*.yaml"))
        errors, _w, stats = collect_pids_validation(files)
        assert errors == []
        assert stats["ecus"] == 0

    def test_rejects_nonempty_dir(self, tmp_path):
        root = tmp_path / "prof"
        root.mkdir()
        (root / "junk.txt").write_text("x")
        assert _cmd_create(_args(path=root)) == 1

    def test_force_allows_nonempty_dir(self, tmp_path):
        root = tmp_path / "prof"
        root.mkdir()
        (root / "junk.txt").write_text("x")
        assert _cmd_create(_args(path=root, force=True)) == 0
        assert (root / "profile.yaml").exists()

    def test_missing_car_model_noninteractive(self, tmp_path, monkeypatch):
        # No car_model + non-tty stdin → error, no scaffolding.
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        root = tmp_path / "prof"
        assert _cmd_create(_args(path=root, car_model=None)) == 2
        assert not root.exists()

    def test_set_default_writes_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        from canlib.config import load_config

        load_config.cache_clear()
        root = tmp_path / "prof"
        _cmd_create(_args(name="mycar", path=root, set_default=True))
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == "mycar"


class TestSetConfigValue:
    def test_appends_new_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        path = config.set_config_value("default_profile", "foo")
        assert "default_profile: foo" in path.read_text()
        config.load_config.cache_clear()
        assert config.load_config()["default_profile"] == "foo"

    def test_replaces_existing_uncommented_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        config.set_config_value("default_profile", "foo")
        config.set_config_value("default_profile", "bar")
        text = (tmp_path / "canair" / "config.yaml").read_text()
        assert "default_profile: bar" in text
        assert "default_profile: foo" not in text

    def test_leaves_commented_line_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        config.load_config.cache_clear()
        # Starter config has a commented `# default_profile: ...` line.
        path = config.set_config_value("default_profile", "foo")
        text = path.read_text()
        assert "# default_profile:" in text  # commented example preserved
        assert "default_profile: foo" in text


def _scaffold(tmp_path, name):
    """Create a minimal discoverable profile bundle under a profiles dir."""
    root = tmp_path / "profiles" / name
    (root / "ecus").mkdir(parents=True)
    (root / "profile.yaml").write_text(f'car_model: "{name}"\n')
    return root


class TestProfileUse:
    def test_sets_default_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config

        config.load_config.cache_clear()
        _scaffold(tmp_path, "ev6")
        args = argparse.Namespace(name="ev6", profiles_dir=None)
        assert _cmd_use(args) == 0
        cfg = yaml.safe_load((tmp_path / "cfg" / "canair" / "config.yaml").read_text())
        assert cfg["default_profile"] == "ev6"

    def test_rejects_unknown_profile(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config

        config.load_config.cache_clear()
        _scaffold(tmp_path, "ev6")
        args = argparse.Namespace(name="nope", profiles_dir=None)
        assert _cmd_use(args) == 1
        err = capsys.readouterr().err
        assert "not found" in err
        # Nothing written.
        cfg_file = tmp_path / "cfg" / "canair" / "config.yaml"
        if cfg_file.exists():
            cfg = yaml.safe_load(cfg_file.read_text()) or {}
            assert cfg.get("default_profile") is None


class TestProfileListHint:
    def test_hint_when_no_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        _scaffold(tmp_path, "leaf")
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "No default_profile set" in out
        assert "canair profile use" in out

    def test_no_hint_when_default_set(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("CANAIR_PROFILES_DIR", str(tmp_path / "profiles"))
        from canlib import config, profile

        config.load_config.cache_clear()
        profile._active = None
        _scaffold(tmp_path, "ev6")
        config.set_config_value("default_profile", "ev6")
        config.load_config.cache_clear()
        _cmd_list(argparse.Namespace(profiles_dir=None))
        out = capsys.readouterr().out
        assert "No default_profile set" not in out
