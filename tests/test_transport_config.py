"""Tests for transport resolution + `canair status` gathering (mocked)."""

import pytest

import canlib.config as cfg_mod
import canlib.constants as const_mod
from canlib.transport import config as tc
from canlib.transport.config import TransportError, resolve_transport


class Args:
    def __init__(self, **kw):
        self.__dict__.update(
            {"transport": None, "wican": None, "port": None, "bitrate": None, "timeout": 4.0}
        )
        self.__dict__.update(kw)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(tc, "_wican_addresses", lambda: {"vpn": "1.2.3.4", "home": "10.0.0.9"})
    monkeypatch.setattr(const_mod, "DEFAULT_WICAN", "vpn", raising=False)

    def set_block(block):
        monkeypatch.setattr(cfg_mod, "load_config", lambda: ({"transport": block} if block else {}))

    set_block(None)
    return set_block


class TestResolveTransport:
    def test_default_is_slcan_tcp_via_default_alias(self, env):
        t = resolve_transport(Args())
        assert t.type == "slcan-tcp" and t.is_raw
        assert t.host == "1.2.3.4"  # default_wican=vpn -> IP
        assert t.port is None and t.bitrate is None

    def test_config_block_used(self, env):
        env({"type": "slcan-tcp", "host": "5.6.7.8", "port": 3333, "bitrate": 250000})
        t = resolve_transport(Args())
        assert t.type == "slcan-tcp" and t.is_raw
        assert (t.host, t.port, t.bitrate) == ("5.6.7.8", 3333, 250000)

    def test_cli_overrides_block(self, env):
        env({"type": "slcan-tcp", "host": "5.6.7.8", "port": 3333})
        t = resolve_transport(Args(transport="wican-ws", wican="home", port=9000))
        assert t.type == "wican-ws"
        assert t.host == "10.0.0.9"  # --wican home -> IP
        assert t.port == 9000

    def test_wican_alias_resolves_and_ip_passthrough(self, env):
        assert resolve_transport(Args(wican="home")).host == "10.0.0.9"
        assert resolve_transport(Args(wican="192.168.9.9")).host == "192.168.9.9"

    def test_bad_transport_raises(self, env):
        with pytest.raises(TransportError):
            resolve_transport(Args(transport="bogus"))

    def test_no_args_object(self, env):
        # resolve_transport(None) must work (uses config only).
        assert resolve_transport(None).type == "slcan-tcp"

    def test_wican_ws_refused_on_classic(self, env, monkeypatch):
        env({"type": "slcan-tcp"})
        monkeypatch.setattr(cfg_mod, "wican_model", lambda: "classic")
        with pytest.raises(TransportError, match="Pro-only"):
            resolve_transport(Args(transport="wican-ws"))

    def test_wican_ws_allowed_on_pro(self, env, monkeypatch):
        env({"type": "slcan-tcp"})
        monkeypatch.setattr(cfg_mod, "wican_model", lambda: "pro")
        assert resolve_transport(Args(transport="wican-ws")).type == "wican-ws"


class TestTransportConfigProps:
    def test_is_raw_elm(self):
        from canlib.transport.config import TransportConfig

        assert TransportConfig("wican-ws").is_elm
        assert not TransportConfig("wican-ws").is_raw
        assert TransportConfig("slcan-tcp").is_raw
        assert not TransportConfig("slcan-tcp").is_elm

    def test_describe(self):
        from canlib.transport.config import TransportConfig

        assert TransportConfig("slcan-tcp", "1.2.3.4", 3333).describe() == "slcan-tcp (1.2.3.4:3333)"


class TestStatusGather:
    @pytest.fixture
    def patch_status(self, monkeypatch, env):
        from canlib.commands import status

        def setup(*, cfg=None, st=None, tcp=True):
            monkeypatch.setattr(status, "_load_device_config", lambda h, t: cfg)
            monkeypatch.setattr(status, "_device_status", lambda h, t: st)
            monkeypatch.setattr(status, "_tcp_open", lambda h, p, t: tcp)

        return setup, status

    def test_wican_ws_ready(self, patch_status):
        setup, status = patch_status
        setup(cfg={"protocol": "auto_pid", "port": "35000"}, st={"batt_voltage": "14.6V"})
        info = status._gather(Args(transport="wican-ws"))
        assert info["exit"] == 0
        assert info["transport"]["usable"] is True
        assert info["device"]["protocol"] == "auto_pid"

    def test_wican_ws_unreachable(self, patch_status):
        setup, status = patch_status
        setup(cfg=None)
        info = status._gather(Args(transport="wican-ws"))
        assert info["exit"] == 1
        assert info["transport"]["usable"] is False

    def test_slcan_mode_mismatch(self, patch_status):
        setup, status = patch_status
        setup(cfg={"protocol": "auto_pid", "port": "35000"}, tcp=True)
        info = status._gather(Args(transport="slcan-tcp"))
        assert info["exit"] == 2
        assert info["transport"]["usable"] is False  # open port but wrong mode
        assert any("slcan" in w for w in info["warnings"])

    def test_slcan_ready(self, patch_status):
        setup, status = patch_status
        setup(cfg={"protocol": "slcan", "port": "3333"}, tcp=True)
        info = status._gather(Args(transport="slcan-tcp"))
        assert info["exit"] == 0
        assert info["transport"]["usable"] is True
