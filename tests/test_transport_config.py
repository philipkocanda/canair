"""Tests for transport resolution + `canair status` gathering (mocked)."""

import pytest

import canlib.config as cfg_mod
from canlib.transport import config as tc
from canlib.transport.config import TransportError, resolve_transport


class Args:
    def __init__(self, **kw):
        self.__dict__.update(
            {
                "transport": None,
                "wican": None,
                "port": None,
                "bitrate": None,
                "timeout": 4.0,
                "no_fallback": False,
            }
        )
        self.__dict__.update(kw)


@pytest.fixture
def env(monkeypatch):
    from canlib.config import DeviceEntry

    monkeypatch.setattr(tc, "_wican_addresses", lambda: {"vpn": "1.2.3.4", "home": "10.0.0.9"})

    devices = {"vpn": DeviceEntry(host="1.2.3.4"), "home": DeviceEntry(host="10.0.0.9")}
    monkeypatch.setattr(tc, "_wican_devices", lambda: (dict(devices), "vpn"))
    # Fallback off by default so these focused tests see a single candidate;
    # dedicated fallback tests re-enable it.
    monkeypatch.setattr(tc, "_fallback_settings", lambda: (False, 2.0, None))

    def set_block(block):
        monkeypatch.setattr(cfg_mod, "load_config", lambda: {"transport": block} if block else {})

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


class TestResolveCandidates:
    """Ordered candidate resolution + per-device transport + fallback ordering."""

    @pytest.fixture
    def devenv(self, monkeypatch):
        from canlib.config import DeviceEntry

        devices = {
            "home": DeviceEntry(host="10.0.0.9", transport="slcan-tcp", port=3333),
            "vpn": DeviceEntry(host="1.2.3.4", transport="wican-ws"),
            "ap": DeviceEntry(host="192.168.80.1"),
        }
        monkeypatch.setattr(tc, "_wican_devices", lambda: (dict(devices), "home"))
        monkeypatch.setattr(tc, "_wican_addresses", lambda: {a: d.host for a, d in devices.items()})
        monkeypatch.setattr(tc, "_is_pro", lambda: True)
        monkeypatch.setattr(cfg_mod, "load_config", lambda: {})

        def set_fallback(enabled=True, timeout=2.0, order=None):
            monkeypatch.setattr(tc, "_fallback_settings", lambda: (enabled, timeout, order))

        set_fallback()
        return set_fallback

    def _hosts(self, cands):
        return [(c.type, c.host) for c in cands]

    def test_primary_then_others(self, devenv):
        cands = tc.resolve_transport_candidates(Args())
        assert self._hosts(cands) == [
            ("slcan-tcp", "10.0.0.9"),
            ("wican-ws", "1.2.3.4"),
            ("slcan-tcp", "192.168.80.1"),
        ]

    def test_explicit_wican_goes_first(self, devenv):
        cands = tc.resolve_transport_candidates(Args(wican="vpn"))
        assert cands[0].host == "1.2.3.4" and cands[0].type == "wican-ws"
        assert {c.host for c in cands[1:]} == {"10.0.0.9", "192.168.80.1"}

    def test_no_fallback_single_candidate(self, devenv):
        cands = tc.resolve_transport_candidates(Args(no_fallback=True))
        assert len(cands) == 1 and cands[0].host == "10.0.0.9"

    def test_fallback_disabled_in_config(self, devenv):
        devenv(enabled=False)
        cands = tc.resolve_transport_candidates(Args())
        assert len(cands) == 1

    def test_per_device_transport_applied(self, devenv):
        cands = tc.resolve_transport_candidates(Args())
        assert cands[0].type == "slcan-tcp"  # home's own transport
        assert cands[1].type == "wican-ws"  # vpn's own transport

    def test_cli_transport_forces_all(self, devenv):
        cands = tc.resolve_transport_candidates(Args(transport="slcan-tcp"))
        assert all(c.type == "slcan-tcp" for c in cands)

    def test_explicit_order_sequences_the_rest(self, devenv):
        devenv(order=["ap", "vpn"])
        cands = tc.resolve_transport_candidates(Args())
        assert cands[0].host == "10.0.0.9"  # primary still first
        assert [c.host for c in cands[1:]] == ["192.168.80.1", "1.2.3.4"]

    def test_classic_filters_wican_ws_fallback(self, devenv, monkeypatch):
        monkeypatch.setattr(tc, "_is_pro", lambda: False)
        cands = tc.resolve_transport_candidates(Args())
        # vpn (wican-ws) filtered; home (slcan-tcp) primary + ap remain.
        assert all(c.type != "wican-ws" for c in cands)
        assert {c.host for c in cands} == {"10.0.0.9", "192.168.80.1"}


class TestSelectReachable:
    """Fallback connect-probe selection (fake liveness probe)."""

    def _cands(self):
        from canlib.transport.config import TransportConfig

        return [
            TransportConfig("slcan-tcp", "10.0.0.9"),
            TransportConfig("wican-ws", "1.2.3.4"),
        ]

    def test_single_candidate_no_probe(self):
        from canlib.transport.config import TransportConfig
        from canlib.transport.fallback import select_reachable_transport

        chosen = select_reachable_transport(
            [TransportConfig("slcan-tcp", "10.0.0.9")], connect_timeout=2.0
        )
        assert chosen.host == "10.0.0.9"

    def test_primary_reachable(self, monkeypatch):
        from canlib import wican_mode
        from canlib.transport.fallback import select_reachable_transport

        monkeypatch.setattr(wican_mode, "_tcp_open", lambda h, p, t: True)
        chosen = select_reachable_transport(
            self._cands(), connect_timeout=2.0, notice=lambda m: None
        )
        assert chosen.host == "10.0.0.9"

    def test_falls_back_to_second(self, monkeypatch):
        from canlib import wican_mode
        from canlib.transport.fallback import select_reachable_transport

        monkeypatch.setattr(wican_mode, "_tcp_open", lambda h, p, t: h == "1.2.3.4")
        msgs: list[str] = []
        chosen = select_reachable_transport(self._cands(), connect_timeout=2.0, notice=msgs.append)
        assert chosen.host == "1.2.3.4"
        assert any("falling back" in m for m in msgs)

    def test_all_down_returns_primary(self, monkeypatch):
        from canlib import wican_mode
        from canlib.transport.fallback import select_reachable_transport

        monkeypatch.setattr(wican_mode, "_tcp_open", lambda h, p, t: False)
        chosen = select_reachable_transport(
            self._cands(), connect_timeout=2.0, notice=lambda m: None
        )
        assert chosen.host == "10.0.0.9"  # primary, so the normal error path fires

    def test_probes_port_80_for_wican(self, monkeypatch):
        from canlib import wican_mode
        from canlib.transport.fallback import select_reachable_transport

        seen: list[tuple] = []

        def probe(h, p, t):
            seen.append((h, p))
            return True

        monkeypatch.setattr(wican_mode, "_tcp_open", probe)
        select_reachable_transport(self._cands(), connect_timeout=2.0, notice=lambda m: None)
        assert seen[0] == ("10.0.0.9", 80)


class TestTransportConfigProps:
    def test_is_raw_elm(self):
        from canlib.transport.config import TransportConfig

        assert TransportConfig("wican-ws").is_elm
        assert not TransportConfig("wican-ws").is_raw
        assert TransportConfig("slcan-tcp").is_raw
        assert not TransportConfig("slcan-tcp").is_elm

    def test_elm327_tcp_is_elm_not_raw_not_wican_http(self):
        from canlib.transport.config import TransportConfig

        t = TransportConfig("elm327-tcp", "1.2.3.4", port=35000)
        assert t.is_elm
        assert not t.is_raw
        # A direct ELM327 adapter has no HTTP config API even with a host set.
        assert not t.is_wican_http

    def test_wican_transports_are_wican_http_with_host(self):
        from canlib.transport.config import TransportConfig

        assert TransportConfig("slcan-tcp", "1.2.3.4").is_wican_http
        assert TransportConfig("wican-ws", "1.2.3.4").is_wican_http
        # No host -> never wican-http.
        assert not TransportConfig("wican-ws", host=None).is_wican_http

    def test_elm327_tcp_probe_port_default(self):
        from canlib.transport.config import DEFAULT_ELM327_TCP_PORT, TransportConfig
        from canlib.transport.fallback import _probe_port

        # No HTTP -> probe the ELM socket port, defaulting to 35000.
        assert _probe_port(TransportConfig("elm327-tcp", "1.2.3.4")) == DEFAULT_ELM327_TCP_PORT
        assert _probe_port(TransportConfig("elm327-tcp", "1.2.3.4", port=3333)) == 3333
        # WiCAN transports probe HTTP port 80.
        assert _probe_port(TransportConfig("slcan-tcp", "1.2.3.4")) == 80

    def test_describe(self):
        from canlib.transport.config import TransportConfig

        assert (
            TransportConfig("slcan-tcp", "1.2.3.4", 3333).describe() == "slcan-tcp (1.2.3.4:3333)"
        )

    def test_resolve_device_defaults_uses_explicit(self):
        from canlib.transport.config import TransportConfig

        # Both explicit -> no device probe, values passed through.
        t = TransportConfig("slcan-tcp", "1.2.3.4", port=35000, bitrate=250000)
        assert t.resolve_device_defaults() == (35000, 250000)

    def test_resolve_device_defaults_no_host_falls_back(self):
        from canlib.transport.config import TransportConfig

        # No host -> not is_wican_http -> conventional SLCAN defaults, no probe.
        t = TransportConfig("slcan-tcp", host=None)
        assert t.resolve_device_defaults() == (3333, 500000)

    def test_resolve_device_defaults_probes_wican(self, monkeypatch):
        from canlib.transport.config import TransportConfig

        # Gaps + a WiCAN host -> query the live device config for port/bitrate.
        monkeypatch.setattr(
            TransportConfig,
            "_wican_device_config",
            lambda self: {"port": 3333, "can_datarate": "500K"},
        )
        t = TransportConfig("slcan-tcp", "1.2.3.4")
        assert t.resolve_device_defaults() == (3333, 500000)

    def test_profile_bitrate_used_when_config_silent(self):
        from canlib.transport.config import TransportConfig

        # No config bitrate, no host -> the profile's can_bitrate wins over 500k.
        t = TransportConfig("slcan-tcp", host=None)
        assert t.resolve_device_defaults(250000) == (3333, 250000)

    def test_config_bitrate_beats_profile(self):
        from canlib.transport.config import TransportConfig

        # Explicit config transport.bitrate outranks the profile can_bitrate.
        t = TransportConfig("slcan-tcp", "1.2.3.4", port=35000, bitrate=500000)
        assert t.resolve_device_defaults(250000) == (35000, 500000)

    def test_profile_bitrate_beats_device_probe(self, monkeypatch):
        from canlib.transport.config import TransportConfig

        # A profile can_bitrate is preferred over the device's live config; with
        # bitrate resolved, only the port gap triggers a probe.
        monkeypatch.setattr(
            TransportConfig,
            "_wican_device_config",
            lambda self: {"port": 3333, "can_datarate": "500K"},
        )
        t = TransportConfig("slcan-tcp", "1.2.3.4")
        assert t.resolve_device_defaults(250000) == (3333, 250000)


class TestTransportTypeVocabulary:
    """`TransportType` is the single source of truth for the backend vocabulary.

    Two ways to name a transport exist at runtime — the `TRANSPORTS` registry keys
    and `VALID_TRANSPORTS` (which feeds argparse `choices=`) — and both must stay
    equal to `get_args(TransportType)`, or a `.type == "…"` gate could compare
    against a name the registry no longer knows.
    """

    def test_valid_transports_derives_from_the_literal(self):
        from typing import get_args

        assert tc.VALID_TRANSPORTS == get_args(tc.TransportType)

    def test_registry_keys_match_the_literal(self):
        from typing import get_args

        assert set(tc.TRANSPORTS) == set(get_args(tc.TransportType))

    def test_each_spec_type_matches_its_registry_key(self):
        assert all(key == spec.type for key, spec in tc.TRANSPORTS.items())

    def test_default_transport_is_a_known_type(self):
        assert tc.DEFAULT_TRANSPORT in tc.VALID_TRANSPORTS

    def test_checked_type_narrows_known_names(self):
        for name in tc.VALID_TRANSPORTS:
            assert tc._checked_type(name) == name

    def test_checked_type_rejects_a_near_miss(self):
        # The exact drift a plain `str` would have swallowed silently.
        with pytest.raises(TransportError, match="Unknown transport 'wican_ws'"):
            tc._checked_type("wican_ws")


class TestParseDatarate:
    def test_suffixes_and_garbage(self):
        from canlib.transport.config import _parse_datarate

        assert _parse_datarate("500K") == 500_000
        assert _parse_datarate("1M") == 1_000_000
        assert _parse_datarate("250000") == 250_000
        assert _parse_datarate(None) is None
        assert _parse_datarate("fast") is None


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
