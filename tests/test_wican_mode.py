"""Tests for canlib.wican_mode — protocol switching with mocked HTTP."""

import pytest

from canlib import wican_mode
from canlib.wican_mode import ModeError, protocol_mode, set_protocol


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


class TestProtocolMode:
    def test_switches_and_restores(self, monkeypatch):
        dev = FakeDevice("elm327").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")

        with protocol_mode("vpn", "slcan", assume_yes=True) as base:
            assert base == "http://d"
            assert dev.config["protocol"] == "slcan"
        # Restored on exit.
        assert dev.config["protocol"] == "elm327"
        assert [c["protocol"] for c in dev.stores] == ["slcan", "elm327"]

    def test_no_switch_when_already_in_mode(self, monkeypatch):
        dev = FakeDevice("slcan").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")
        with protocol_mode("vpn", "slcan", assume_yes=True):
            pass
        assert dev.reboots == 0  # never touched

    def test_restores_even_on_exception(self, monkeypatch):
        dev = FakeDevice("elm327").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")
        with pytest.raises(RuntimeError, match="boom"):
            with protocol_mode("vpn", "slcan", assume_yes=True):
                raise RuntimeError("boom")
        assert dev.config["protocol"] == "elm327"  # still restored

    def test_declined_without_consent(self, monkeypatch):
        dev = FakeDevice("elm327").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")
        monkeypatch.setattr(wican_mode, "_confirm", lambda prompt, yes: False)
        with pytest.raises(ModeError):
            with protocol_mode("vpn", "slcan", assume_yes=False):
                pass
        assert dev.reboots == 0

    def test_restore_failure_warns_not_raises(self, monkeypatch, capsys):
        FakeDevice("elm327").install(monkeypatch)
        monkeypatch.setattr(wican_mode, "resolve_wican_url", lambda w: "http://d")

        calls = {"n": 0}
        real_store = wican_mode.store_config

        def flaky_store(base, cfg, timeout=10):
            calls["n"] += 1
            if calls["n"] == 2:  # fail the restore store
                raise RuntimeError("network down")
            real_store(base, cfg, timeout)

        monkeypatch.setattr(wican_mode, "store_config", flaky_store)
        # Should not raise despite restore failing.
        with protocol_mode("vpn", "slcan", assume_yes=True):
            pass
        err = capsys.readouterr().err
        assert "failed to restore" in err
