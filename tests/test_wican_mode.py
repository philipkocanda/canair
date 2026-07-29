"""Tests for canlib.wican_mode — protocol switching with mocked HTTP."""

import pytest

from canlib import wican_mode
from canlib.transport.errors import connect_error_detail
from canlib.wican_mode import (
    ModeError,
    require_protocol,
    require_slcan_reachable,
    require_ws_reachable,
    set_protocol,
)


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


class TestRequireWsReachable:
    def test_ok_when_port_open(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: True)
        require_ws_reachable("10.0.2.86")  # no raise

    def test_raises_when_port_closed(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: False)
        monkeypatch.setattr(
            wican_mode, "_tcp_probe", lambda host, port, timeout: ConnectionRefusedError()
        )
        with pytest.raises(ModeError) as exc:
            require_ws_reachable("10.0.2.86")
        msg = str(exc.value)
        # The alert names the host and points at the diagnostic commands.
        assert "10.0.2.86" in msg
        assert "canair status" in msg
        assert "wican mode set" in msg
        # ...and reports what actually happened (the real OS-level reason).
        assert "refused" in msg

    def test_default_port_is_the_http_websocket_port(self, monkeypatch):
        seen: dict = {}

        def fake_open(host, port, timeout):
            seen["port"] = port
            return True

        monkeypatch.setattr(wican_mode, "_tcp_open", fake_open)
        require_ws_reachable("10.0.2.86")
        assert seen["port"] == 80


class TestRequireSlcanReachable:
    def test_ok_when_port_open(self, monkeypatch):
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: True)
        require_slcan_reachable("10.0.2.86", 35000)  # no raise

    def test_raises_when_slcan_closed_but_device_online(self, monkeypatch):
        # SLCAN port (35000) closed, but the HTTP config API (80) responds → the
        # device is online and the SLCAN socket itself is wedged (reboot hint).
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: port == 80)
        monkeypatch.setattr(wican_mode, "_tcp_probe", lambda host, port, timeout: TimeoutError())
        with pytest.raises(ModeError) as exc:
            require_slcan_reachable("10.0.2.86", 35000)
        msg = str(exc.value)
        assert "10.0.2.86:35000" in msg
        assert "canair status" in msg
        # ...reports what actually happened (the real OS-level reason)...
        assert "timed out" in msg
        # ...says the device is online (HTTP responded) and points at a reboot.
        assert "online" in msg.lower()
        assert "reboot" in msg.lower()

    def test_raises_when_device_offline(self, monkeypatch):
        # Neither port responds → the device looks offline; no false "it's online".
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: False)
        monkeypatch.setattr(wican_mode, "_tcp_probe", lambda host, port, timeout: TimeoutError())
        with pytest.raises(ModeError) as exc:
            require_slcan_reachable("10.0.2.86", 35000)
        msg = str(exc.value)
        assert "offline" in msg.lower()
        assert "online — its" not in msg.lower()

    def test_probes_the_given_slcan_port(self, monkeypatch):
        seen: dict = {}

        def fake_open(host, port, timeout):
            seen["port"] = port
            return True

        monkeypatch.setattr(wican_mode, "_tcp_open", fake_open)
        require_slcan_reachable("10.0.2.86", 35000)
        assert seen["port"] == 35000


class TestConnectErrorDetail:
    def test_timeout(self):
        assert "timed out" in connect_error_detail(TimeoutError())

    def test_refused(self):
        assert "refused" in connect_error_detail(ConnectionRefusedError())

    def test_name_resolution(self):
        import socket

        assert "resolution" in connect_error_detail(socket.gaierror("nope"))

    def test_no_route_to_host(self):
        import errno

        err = OSError(errno.EHOSTUNREACH, "No route to host")
        assert "no route to host" in connect_error_detail(err).lower()

    def test_network_unreachable(self):
        import errno

        err = OSError(errno.ENETUNREACH, "Network is unreachable")
        assert "network is unreachable" in connect_error_detail(err).lower()

    def test_unknown_falls_back_to_message(self):
        assert "weird" in connect_error_detail(OSError("weird failure"))
