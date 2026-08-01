"""Tests for the one-time wican_addresses -> devices migration."""

from __future__ import annotations

import yaml

from canlib import config, devices_migrate


def _reset():
    config.load_config.cache_clear()


def _write(tmp_path, text):
    cfg_dir = tmp_path / "canair"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(text)
    return cfg_dir / "config.yaml"


LEGACY = """\
# leading comment
default_profile: ioniq-2017

# WiCAN device addresses.
wican_addresses:
  home: "10.0.2.86"   # home LAN
  vpn: "192.168.3.2"  # via VPN
default_wican: vpn

check_for_updates: true
"""


class TestMigrate:
    def test_migrates_and_removes_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = _write(tmp_path, LEGACY)
        _reset()
        result = devices_migrate.migrate_config()
        assert result.migrated is True
        data = yaml.safe_load(path.read_text())
        assert "wican_addresses" not in data
        assert data["devices"]["home"]["host"] == "10.0.2.86"
        assert data["devices"]["vpn"]["host"] == "192.168.3.2"
        assert data["default_wican"] == "vpn"

    def test_preserves_comments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = _write(tmp_path, LEGACY)
        _reset()
        devices_migrate.migrate_config()
        text = path.read_text()
        assert "# leading comment" in text
        assert "home LAN" in text and "via VPN" in text
        # no stray blank line inside the block
        assert "\n\n    host:" not in text

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _write(tmp_path, LEGACY)
        _reset()
        devices_migrate.migrate_config()
        _reset()
        second = devices_migrate.migrate_config()
        assert second.migrated is False
        assert "no legacy" in (second.reason or "")

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = _write(tmp_path, LEGACY)
        _reset()
        result = devices_migrate.migrate_config(dry_run=True)
        assert result.migrated is True
        assert "wican_addresses" in path.read_text()  # unchanged on disk

    def test_no_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        result = devices_migrate.migrate_config()
        assert result.migrated is False

    def test_skips_when_devices_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _write(
            tmp_path,
            "devices:\n  home:\n    host: 10.0.0.9\nwican_addresses:\n  vpn: 1.1.1.1\n",
        )
        _reset()
        result = devices_migrate.migrate_config()
        assert result.migrated is False

    def test_roundtrips_and_resolves(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _write(tmp_path, LEGACY)
        _reset()
        devices_migrate.migrate_config()
        _reset()
        devices, default = config.wican_devices()
        assert default == "vpn"
        assert devices["home"].host == "10.0.2.86"

    def test_auto_migrate_notice(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _write(tmp_path, LEGACY)
        _reset()
        devices_migrate.maybe_auto_migrate()
        assert "migrated wican_addresses" in capsys.readouterr().err

    def test_auto_migrate_swallows_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _write(tmp_path, LEGACY)
        _reset()
        monkeypatch.setattr(
            devices_migrate, "migrate_config", lambda **_: (_ for _ in ()).throw(OSError("boom"))
        )
        # Must not raise.
        devices_migrate.maybe_auto_migrate()
