"""Tests for `canair profile create` scaffolding and config.set_config_value."""

from __future__ import annotations

import argparse

import yaml

from canlib.commands.profile import DEFAULT_INIT, _cmd_create
from canlib.commands.validate import validate_ecus_registry


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
        assert (root / "pids").is_dir()
        assert (root / "captures").is_dir()
        assert (root / "out").is_dir()
        assert (root / "ecus.yaml").exists()
        assert (root / "pids" / "_meta.yaml").exists()

    def test_meta_contents(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root, init="ATSP0;"))
        meta = yaml.safe_load((root / "pids" / "_meta.yaml").read_text())
        assert meta["car_model"] == "VW e-Golf 2019"
        assert meta["init"] == "ATSP0;"

    def test_default_init(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        meta = yaml.safe_load((root / "pids" / "_meta.yaml").read_text())
        assert meta["init"] == DEFAULT_INIT

    def test_created_ecus_validates(self, tmp_path):
        root = tmp_path / "prof"
        _cmd_create(_args(path=root))
        errors, _w, stats = validate_ecus_registry(root / "ecus.yaml")
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
        assert (root / "ecus.yaml").exists()

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
