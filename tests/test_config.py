"""Tests for the `canair config` command and canlib.config edit helpers."""

from __future__ import annotations

import argparse

import yaml

from canlib.commands import config as config_cmd


def _reset():
    from canlib import config

    config.load_config.cache_clear()


class TestCoerceScalar:
    def test_int(self):
        from canlib.config import coerce_scalar

        assert coerce_scalar("35000") == 35000
        assert coerce_scalar("-1") == -1

    def test_bool_and_null(self):
        from canlib.config import coerce_scalar

        assert coerce_scalar("true") is True
        assert coerce_scalar("False") is False
        assert coerce_scalar("null") is None

    def test_strings_stay_strings(self):
        from canlib.config import coerce_scalar

        assert coerce_scalar("10.0.2.86") == "10.0.2.86"  # IP: not an int
        assert coerce_scalar("slcan-tcp") == "slcan-tcp"


class TestSetConfigKey:
    def test_nested_key_creates_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.type", "slcan-tcp")
        config.set_config_key("transport.port", 35000)
        _reset()
        cfg = config.load_config()
        assert cfg["transport"] == {"type": "slcan-tcp", "port": 35000}

    def test_map_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_addresses.home", "10.0.2.86")
        _reset()
        assert config.load_config()["wican_addresses"]["home"] == "10.0.2.86"

    def test_preserves_starter_comments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        path = config.set_config_key("default_wican", "home")
        text = path.read_text()
        assert "# canair configuration" in text  # seeded comment block survives
        assert "default_wican: home" in text

    def test_updates_existing_nested_in_place(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.type", "wican-ws")
        config.set_config_key("transport.type", "slcan-tcp")
        _reset()
        assert config.load_config()["transport"]["type"] == "slcan-tcp"


class TestUnsetConfigKey:
    def test_removes_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.type", "slcan-tcp")
        config.set_config_key("transport.port", 35000)
        _path, removed = config.unset_config_key("transport.port")
        assert removed
        _reset()
        cfg = config.load_config()
        assert "port" not in cfg["transport"]
        assert cfg["transport"]["type"] == "slcan-tcp"

    def test_absent_key_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        _path, removed = config.unset_config_key("nope")
        assert removed is False


class TestGetConfigKey:
    def test_dotted_lookup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.port", 35000)
        _reset()
        assert config.get_config_key("transport.port") == 35000
        assert config.get_config_key("transport.missing") is None
        assert config.get_config_key("missing") is None


class TestConfigCommand:
    def test_show_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_show(argparse.Namespace(json=True))
        assert rc == 0
        import json

        out = json.loads(capsys.readouterr().out)
        assert "files" in out and "wican" in out

    def test_set_then_get(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(
            argparse.Namespace(key="transport.port", value="35000", string=False)
        )
        assert rc == 0
        _reset()
        capsys.readouterr()
        rc = config_cmd._cmd_get(argparse.Namespace(key="transport.port"))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "35000"

    def test_set_string_flag_skips_coercion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config_cmd._cmd_set(
            argparse.Namespace(key="some_id", value="007", string=True)
        )
        _reset()
        assert config.load_config()["some_id"] == "007"

    def test_get_missing_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_get(argparse.Namespace(key="nope"))
        assert rc == 1

    def test_unset_missing_returns_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_unset(argparse.Namespace(key="nope"))
        assert rc == 1

    def test_path_prints_user_config(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_path(argparse.Namespace())
        assert rc == 0
        assert str(tmp_path) in capsys.readouterr().out

    def test_written_config_roundtrips_with_pyyaml(self, tmp_path, monkeypatch):
        # Guard: config readers use PyYAML; ensure ruamel output parses cleanly.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.type", "slcan-tcp")
        config.set_config_key("wican_addresses.home", "10.0.2.86")
        data = yaml.safe_load(config.user_config_file().read_text())
        assert data["transport"]["type"] == "slcan-tcp"
        assert data["wican_addresses"]["home"] == "10.0.2.86"
