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

    def test_float(self):
        from canlib.config import coerce_scalar

        assert coerce_scalar("2.0") == 2.0
        assert coerce_scalar("0.5") == 0.5

    def test_non_finite_floats_stay_strings(self):
        from canlib.config import coerce_scalar

        # float() accepts inf/nan; we must not silently store non-finite values.
        assert coerce_scalar("inf") == "inf"
        assert coerce_scalar("nan") == "nan"


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


class TestWicanModel:
    def test_defaults_to_pro(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        assert config.wican_model() == "pro"
        assert config.is_wican_pro() is True

    def test_classic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_model", "classic")
        _reset()
        assert config.wican_model() == "classic"
        assert config.is_wican_pro() is False

    def test_normalizes_case_and_whitespace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_model", "  Classic ")
        _reset()
        assert config.wican_model() == "classic"

    def test_unknown_value_falls_back_to_pro(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_model", "deluxe")
        _reset()
        assert config.wican_model() == "pro"


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
        config_cmd._cmd_set(argparse.Namespace(key="some_id", value="007", string=True))
        _reset()
        assert config.load_config()["some_id"] == "007"

    def test_set_reports_before_after(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        config_cmd._cmd_set(argparse.Namespace(key="default_wican", value="home", string=False))
        _reset()
        capsys.readouterr()
        rc = config_cmd._cmd_set(argparse.Namespace(key="default_wican", value="vpn", string=False))
        assert rc == 0
        assert "default_wican: 'home' -> 'vpn'" in capsys.readouterr().out

    def test_set_already_set_is_unchanged(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        config_cmd._cmd_set(argparse.Namespace(key="default_wican", value="home", string=False))
        _reset()
        capsys.readouterr()
        rc = config_cmd._cmd_set(
            argparse.Namespace(key="default_wican", value="home", string=False)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "already 'home' (unchanged)" in out
        assert "Saved" not in out

    def test_set_invalid_transport_type_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(
            argparse.Namespace(key="transport.type", value="bogus", string=False)
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "slcan-tcp" in err and "wican-ws" in err

    def test_set_invalid_wican_model_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(
            argparse.Namespace(key="wican_model", value="deluxe", string=False)
        )
        assert rc == 2
        assert "pro, classic" in capsys.readouterr().err

    def test_set_invalid_grid_region_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(argparse.Namespace(key="grid_region", value="mars", string=False))
        assert rc == 2
        err = capsys.readouterr().err
        assert "invalid grid_region" in err and "US" in err

    def test_set_valid_grid_region_writes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        # Case-insensitive: lower-case token accepted (enum check upper-cases).
        rc = config_cmd._cmd_set(argparse.Namespace(key="grid_region", value="us", string=False))
        assert rc == 0
        _reset()
        assert config.load_config()["grid_region"] == "us"

    def test_set_unknown_key_warns_but_writes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        rc = config_cmd._cmd_set(
            argparse.Namespace(key="defualt_wican", value="home", string=False)
        )
        assert rc == 0
        assert "not a recognized config key" in capsys.readouterr().err
        _reset()
        assert config.load_config()["defualt_wican"] == "home"

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

    def test_example_prints_the_example_file(self, capsys):
        rc = config_cmd._cmd_example(argparse.Namespace())
        assert rc == 0
        out = capsys.readouterr().out
        assert "canair configuration" in out
        assert "default_profile" in out

    def test_example_missing_file_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(config_cmd, "CONFIG_EXAMPLE_FILE", tmp_path / "nope.yaml")
        rc = config_cmd._cmd_example(argparse.Namespace())
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err
        assert "reference/config" in err

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


def _ns(**kw):
    kw.setdefault("string", False)
    return argparse.Namespace(**kw)


class TestDevicesAndFallbackConfig:
    def test_wican_devices_from_devices_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("devices.home.host", "10.0.0.9")
        config.set_config_key("devices.home.transport", "wican-ws")
        config.set_config_key("devices.home.port", 3333)
        _reset()
        devices, _ = config.wican_devices()
        assert devices["home"].host == "10.0.0.9"
        assert devices["home"].transport == "wican-ws"
        assert devices["home"].port == 3333

    def test_devices_supersede_wican_addresses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_addresses.legacy", "1.1.1.1")
        config.set_config_key("devices.home.host", "10.0.0.9")
        _reset()
        devices, _ = config.wican_devices()
        assert set(devices) == {"home"}  # wican_addresses ignored when devices present

    def test_wican_addresses_used_when_no_devices(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("wican_addresses.home", "10.0.0.9")
        _reset()
        devices, _ = config.wican_devices()
        assert devices["home"].host == "10.0.0.9" and devices["home"].transport is None

    def test_wican_settings_backcompat_shim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("devices.home.host", "10.0.0.9")
        config.set_config_key("devices.home.transport", "wican-ws")
        _reset()
        addresses, _ = config.wican_settings()
        assert addresses == {"home": "10.0.0.9"}  # flattened to alias->host

    def test_fallback_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        enabled, timeout, order = config.fallback_settings()
        assert enabled is True and timeout == config._DEFAULT_CONNECT_TIMEOUT
        assert order is None

    def test_fallback_settings_read(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.fallback", False)
        config.set_config_key("transport.connect_timeout", 1.5)
        _reset()
        enabled, timeout, _ = config.fallback_settings()
        assert enabled is False and timeout == 1.5

    def test_bad_connect_timeout_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.connect_timeout", -3)
        _reset()
        _, timeout, _ = config.fallback_settings()
        assert timeout == config._DEFAULT_CONNECT_TIMEOUT

    def test_ws_ping_interval_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        assert config.ws_ping_interval() == 20.0

    def test_ws_ping_interval_read(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.ws_ping_interval", 5.0)
        _reset()
        assert config.ws_ping_interval() == 5.0

    def test_ws_ping_interval_zero_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.ws_ping_interval", 0)
        _reset()
        assert config.ws_ping_interval() is None

    def test_bad_ws_ping_interval_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.ws_ping_interval", -1)
        _reset()
        assert config.ws_ping_interval() == 20.0

    def test_stale_cycles_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        assert config.stale_cycles_before_reconnect() == 3

    def test_stale_cycles_read_and_zero_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.stale_cycles_before_reconnect", 8)
        _reset()
        assert config.stale_cycles_before_reconnect() == 8
        config.set_config_key("transport.stale_cycles_before_reconnect", 0)
        _reset()
        assert config.stale_cycles_before_reconnect() == 0

    def test_bad_stale_cycles_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.stale_cycles_before_reconnect", -2)
        _reset()
        assert config.stale_cycles_before_reconnect() == 3

    def test_expected_responses_defaults_on(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        assert config.expected_responses() is True

    def test_expected_responses_can_be_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.expected_responses", False)
        _reset()
        assert config.expected_responses() is False

    def test_null_expected_responses_falls_back_to_default(self, tmp_path, monkeypatch):
        """An explicitly blank key means "unset", not "off".

        YAML turns a bare ``expected_responses:`` into None, which would otherwise
        silently disable the optimization for anyone who uncommented the line in
        ``config.example.yaml`` without filling in a value.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("transport.expected_responses", None)
        _reset()
        assert config.expected_responses() is True


class TestConfigCommandDevices:
    def test_set_device_transport_valid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        rc = config_cmd._cmd_set(_ns(key="devices.home.transport", value="wican-ws"))
        assert rc == 0
        _reset()
        assert config.load_config()["devices"]["home"]["transport"] == "wican-ws"

    def test_set_device_transport_invalid(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(_ns(key="devices.home.transport", value="bogus"))
        assert rc == 2
        err = capsys.readouterr().err
        assert "devices.home.transport" in err and "slcan-tcp" in err

    def test_fallback_order_comma_split(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        rc = config_cmd._cmd_set(_ns(key="transport.fallback_order", value="home, vpn ,ap"))
        assert rc == 0
        _reset()
        assert config.load_config()["transport"]["fallback_order"] == ["home", "vpn", "ap"]

    def test_wican_addresses_deprecation_warning(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _reset()
        rc = config_cmd._cmd_set(_ns(key="wican_addresses.home", value="10.0.0.9"))
        assert rc == 0
        err = capsys.readouterr().err
        assert "deprecated" in err and "devices.home.host" in err

    def test_wican_addresses_ignored_warning_when_devices_present(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("devices.home.host", "10.0.0.9")
        _reset()
        capsys.readouterr()
        config_cmd._cmd_set(_ns(key="wican_addresses.legacy", value="1.1.1.1"))
        assert "ignored at runtime" in capsys.readouterr().err

    def test_show_json_has_devices_and_fallback(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from canlib import config

        _reset()
        config.set_config_key("devices.home.host", "10.0.0.9")
        config.set_config_key("devices.home.transport", "wican-ws")
        _reset()
        rc = config_cmd._cmd_show(argparse.Namespace(json=True))
        assert rc == 0
        import json

        out = json.loads(capsys.readouterr().out)
        assert out["devices"]["home"]["host"] == "10.0.0.9"
        assert out["devices"]["home"]["transport"] == "wican-ws"
        assert out["fallback"]["enabled"] is True
        # back-compat: wican.addresses stays {alias: host}
        assert out["wican"]["addresses"]["home"] == "10.0.0.9"


class TestDefaultsAreSizedForAMobileLink:
    """These defaults exist to make a phone-hotspot session survivable.

    Exact values are a judgement call and may be retuned, but the *direction* is
    not: each was originally a LAN number that failed on a cellular link, so a
    change back below these floors would reintroduce a known failure.
    """

    def test_the_liveness_probe_outlasts_a_radio_wakeup(self):
        from canlib import config

        # A cellular radio's idle-to-connected transition can take a second or two
        # before the SYN leaves; probing for less declares a live device dead.
        assert config._DEFAULT_CONNECT_TIMEOUT >= 4.0

    def test_the_reconnect_window_outlasts_a_cell_handover(self):
        from canlib import config

        # A hotspot changing cells, or a VPN re-key, is routinely out for tens of
        # seconds. Giving up sooner loses the rest of a drive's recording.
        assert config._DEFAULT_RECONNECT_MAX_WAIT >= 30.0

    def test_the_ws_ping_outlasts_a_cellular_round_trip(self):
        from canlib import config

        # The ping has to detect a half-open link without severing a slow-but-live
        # one, so its timeout must comfortably exceed a cellular+VPN round trip.
        assert config._DEFAULT_WS_PING_INTERVAL >= 10.0
