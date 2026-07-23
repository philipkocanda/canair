"""Tests for canlib.wican_mode — protocol switching with mocked HTTP."""

import pytest

from canlib import wican_mode
from canlib.wican_mode import ModeError, require_protocol, set_protocol


class FakeDevice:
    """In-memory stand-in for the WiCAN HTTP config API."""

    def __init__(self, protocol="elm327"):
        self.config = {"protocol": protocol, "port": "3333", "port_type": "tcp"}
        self.stores = []  # history of stored configs
        self.reboots = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "load_config", lambda base, timeout=10.0: dict(self.config))

        def _store(base, cfg, timeout=10):
            self.config = dict(cfg)
            self.stores.append(dict(cfg))
            self.reboots += 1

        monkeypatch.setattr(wican_mode, "store_config", _store)
        monkeypatch.setattr(
            wican_mode, "wait_until_ready", lambda host, port=80, timeout=45.0: True
        )
        monkeypatch.setattr(wican_mode.time, "sleep", lambda *_a: None)
        return self


class TestSetProtocol:
    def test_switch_returns_previous_and_stores(self, monkeypatch):
        dev = FakeDevice("elm327").install(monkeypatch)
        prev = set_protocol("http://d", "slcan")
        assert prev == "elm327"
        assert dev.config["protocol"] == "slcan"
        assert dev.reboots == 1

    def test_noop_when_already_target(self, monkeypatch):
        dev = FakeDevice("slcan").install(monkeypatch)
        prev = set_protocol("http://d", "slcan")
        assert prev == "slcan"
        assert dev.reboots == 0  # no store/reboot

    def test_preserves_other_config_keys(self, monkeypatch):
        dev = FakeDevice("elm327").install(monkeypatch)
        dev.config["mqtt_url"] = "10.0.0.5"
        set_protocol("http://d", "slcan")
        assert dev.config["mqtt_url"] == "10.0.0.5"  # full config round-tripped

    def test_raises_if_device_never_returns(self, monkeypatch):
        FakeDevice("elm327").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "wait_until_ready", lambda *a, **k: False)
        with pytest.raises(ModeError):
            set_protocol("http://d", "slcan")


class TestRequireProtocol:
    def test_ok_when_matching(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")
        monkeypatch.setattr(wican_mode, "current_protocol", lambda base, timeout=6.0: "slcan")
        require_protocol("vpn", "slcan")  # no raise

    def test_raises_on_mismatch(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")
        monkeypatch.setattr(wican_mode, "current_protocol", lambda base, timeout=6.0: "auto_pid")
        with pytest.raises(ModeError, match="mode set slcan"):
            require_protocol("vpn", "slcan")

    def test_noop_when_unreachable(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")

        def boom(base, timeout=6.0):
            raise OSError("unreachable")

        monkeypatch.setattr(wican_mode, "current_protocol", boom)
        require_protocol("vpn", "slcan")  # no raise (connect will surface it)
