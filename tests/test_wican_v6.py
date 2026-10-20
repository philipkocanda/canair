"""WiCAN firmware v6: live data-path checks, device status, and the surfaces using them.

Driven against a stubbed HTTP seam (``wican_firmware._fetch``) serving the v6
API's JSON shapes, so every test is device-free. Plan:
``plans/2026-10-20-wican-fw-v6-compatibility.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy

import pytest
import requests

import canlib.wican_firmware as wf
from canlib import wican_v6
from canlib.wican_firmware import FirmwareInfo

HOST = "192.0.2.1"
V6 = FirmwareInfo("v6", version="6.0.0-beta.1", api_level=6)
V4 = FirmwareInfo("v4", version="4.52")

# A stock v6 device: ELM327 on TCP 35000 and /ws/obd, SLCAN parked, CAN bus off.
STOCK = {
    "/api/ws": {
        "channels": [
            {
                "name": "ws_obd",
                "path": "/ws/obd",
                "mode": "binary",
                "max_clients": 2,
                "enabled": True,
                "up": True,
                "stats": {"clients": 0},
                "bridge": "br_obd",
            },
            {"name": "ws_can", "path": "/ws/can", "enabled": False, "up": False, "bridge": None},
        ]
    },
    "/api/sockets": {
        "servers": [
            {
                "name": "obd0",
                "proto": "tcp",
                "port": 35000,
                "enabled": True,
                "up": True,
                "bridge": "br_tcp_obd",
            },
            {
                "name": "slcan0",
                "proto": "tcp",
                "port": 3333,
                "enabled": False,
                "up": False,
                "bridge": None,
            },
        ]
    },
    "/api/bridges": {
        "bridges": [
            {"name": "br_obd", "a": "obd", "b": "ws_obd", "translator": "raw", "enabled": True},
            {
                "name": "br_tcp_obd",
                "a": "obd0",
                "b": "obd",
                "translator": "raw",
                "enabled": True,
            },
        ]
    },
    "/api/can": {"enabled": False, "silent": False, "baud_auto": False, "baud_kbps": 500},
    "/api/sleep": {"enabled": True, "state": "normal", "voltage": 12.61, "sleep_v": 12.2},
    "/api/battery": {"voltage": 12.58},
    "/api/obd_chip": {"ready": True, "claim": "none"},
}


def _slcan_ready(routes: dict) -> dict:
    """The stock device with SLCAN set up the way the plan's three steps describe."""
    routes = copy.deepcopy(routes)
    slcan = routes["/api/sockets"]["servers"][1]
    slcan.update(enabled=True, up=True, bridge="br_slcan")
    routes["/api/bridges"]["bridges"].append(
        {"name": "br_slcan", "a": "can", "b": "slcan0", "translator": "slcan", "enabled": True}
    )
    routes["/api/can"]["enabled"] = True
    return routes


class FakeResponse:
    def __init__(self, status: int, body=None):
        self.status_code = status
        self._body = body
        self.headers: dict = {}

    def json(self):
        if self._body is None:
            raise requests.JSONDecodeError("not json", "", 0)
        return self._body


@pytest.fixture
def device(monkeypatch):
    """Serve ``routes`` (path -> JSON body) from the HTTP seam; returns the URL log."""
    seen: list[str] = []

    def install(routes: dict):
        def fetch(url, timeout):
            seen.append(url)
            for path, body in routes.items():
                if url.endswith(path):
                    return FakeResponse(200, copy.deepcopy(body))
            return FakeResponse(404)

        monkeypatch.setattr(wf, "_fetch", fetch)
        return seen

    return install


class TestWsObd:
    def test_stock_channel_is_fine(self, device):
        device(STOCK)
        assert wican_v6.ws_obd_verdict(HOST, timeout=1.0) is None

    def test_unreadable_api_is_unknown(self, device):
        device({})
        assert wican_v6.ws_obd_verdict(HOST, timeout=1.0) is None

    def test_missing_channel_refused(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"].pop(0)
        device(routes)
        verdict = wican_v6.ws_obd_verdict(HOST, timeout=1.0)
        assert verdict is not None and verdict.refused
        assert "no /ws/obd" in verdict.message and "elm327-tcp" in verdict.message

    @pytest.mark.parametrize(
        ("change", "word"),
        [({"enabled": False}, "disabled"), ({"up": False}, "not running")],
    )
    def test_disabled_or_stopped_channel_refused(self, device, change, word):
        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"][0].update(change)
        device(routes)
        verdict = wican_v6.ws_obd_verdict(HOST, timeout=1.0)
        assert verdict is not None and verdict.refused and word in verdict.message

    def test_unbridged_channel_refused(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"][0]["bridge"] = None
        device(routes)
        verdict = wican_v6.ws_obd_verdict(HOST, timeout=1.0)
        assert verdict is not None and verdict.refused
        assert "discard" in verdict.message and "'raw'" in verdict.message

    def test_channel_bridged_to_something_else_refused(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/bridges"]["bridges"][0].update(a="can", translator="slcan")
        device(routes)
        verdict = wican_v6.ws_obd_verdict(HOST, timeout=1.0)
        assert verdict is not None and verdict.refused and "ELM327" in verdict.message

    def test_full_channel_warns(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"][0]["stats"]["clients"] = 2
        device(routes)
        verdict = wican_v6.ws_obd_verdict(HOST, timeout=1.0)
        assert verdict is not None and verdict.level == "warn" and "2 of 2" in verdict.message


class TestSlcan:
    def test_stock_device_serves_no_slcan(self, device):
        device(STOCK)
        verdict = wican_v6.slcan_verdict(HOST, 3333, 500000, timeout=1.0)
        assert verdict is not None and verdict.refused and "disabled" in verdict.message

    def test_enabled_server_without_a_bridge_is_the_silent_trap(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/sockets"]["servers"][1].update(enabled=True, up=True)
        device(routes)
        verdict = wican_v6.slcan_verdict(HOST, 3333, 500000, timeout=1.0)
        assert verdict is not None and verdict.refused
        assert "silently discards" in verdict.message and "look dead" in verdict.message

    def test_no_server_on_the_port(self, device):
        device(STOCK)
        verdict = wican_v6.slcan_verdict(HOST, 4444, None, timeout=1.0)
        assert verdict is not None and verdict.refused and "TCP 4444" in verdict.message

    def test_the_elm327_port_is_not_slcan(self, device):
        device(STOCK)
        verdict = wican_v6.slcan_verdict(HOST, 35000, None, timeout=1.0)
        assert verdict is not None and verdict.refused
        assert "ELM327 server" in verdict.message and "elm327-tcp" in verdict.message

    def test_can_manager_off_refused(self, device):
        routes = _slcan_ready(STOCK)
        routes["/api/can"]["enabled"] = False
        device(routes)
        verdict = wican_v6.slcan_verdict(HOST, 3333, 500000, timeout=1.0)
        assert verdict is not None and verdict.refused and "standby" in verdict.message

    def test_ready_path_is_a_note(self, device):
        device(_slcan_ready(STOCK))
        verdict = wican_v6.slcan_verdict(HOST, 3333, 500000, timeout=1.0)
        assert verdict is not None and verdict.level == "note"
        assert "bridged to the CAN bus" in verdict.message

    def test_fixed_bitrate_mismatch_warns(self, device):
        device(_slcan_ready(STOCK))
        verdict = wican_v6.slcan_verdict(HOST, 3333, 250000, timeout=1.0)
        assert verdict is not None and verdict.level == "warn"
        assert "500 kbit/s" in verdict.message and "250 kbit/s" in verdict.message

    def test_auto_bitrate_never_warns(self, device):
        routes = _slcan_ready(STOCK)
        routes["/api/can"]["baud_auto"] = True
        device(routes)
        verdict = wican_v6.slcan_verdict(HOST, 3333, 250000, timeout=1.0)
        assert verdict is not None and verdict.level == "note"

    def test_device_defaults_follow_the_slcan_bridge(self, device):
        routes = _slcan_ready(STOCK)
        routes["/api/sockets"]["servers"][1]["port"] = 4000
        routes["/api/can"]["baud_kbps"] = 250
        device(routes)
        assert wican_v6.device_defaults(HOST, timeout=1.0) == {
            "port": 4000,
            "can_datarate": "250K",
        }

    def test_device_defaults_empty_without_slcan(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/can"]["baud_auto"] = True
        device(routes)
        assert wican_v6.device_defaults(HOST, timeout=1.0) == {}

    def test_resolve_device_defaults_reads_v6_settings(self, device, monkeypatch):
        from canlib.transport.config import TransportConfig

        routes = _slcan_ready(STOCK)
        routes["/api/sockets"]["servers"][1]["port"] = 4000
        routes["/api/can"]["baud_kbps"] = 250
        device(routes)
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        t = TransportConfig(type="slcan-tcp", host=HOST)
        assert t.resolve_device_defaults(None) == (4000, 250000)
        # The profile's bitrate still wins over the device's (and is checked by assess).
        assert t.resolve_device_defaults(500000) == (4000, 500000)


class TestListenOnly:
    def test_silent_device_allows_a_passive_sniff(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/can"]["silent"] = True
        device(routes)
        verdict = wican_v6.assess(V6, "sniff-listen-only", HOST, timeout=1.0)
        assert verdict.level == "note" and "passive" in verdict.message

    def test_active_device_is_still_refused(self, device):
        device(STOCK)
        assert wican_v6.assess(V6, "sniff-listen-only", HOST, timeout=1.0).refused


class TestAssess:
    def test_never_asks_a_v4_or_gated_device(self, device):
        seen = device(STOCK)
        assert wican_v6.assess(V4, "wican-ws", HOST).level == "ok"
        assert wican_v6.assess(FirmwareInfo("v6", gated="auth"), "wican-ws", HOST).refused
        assert seen == []

    def test_static_verdict_when_the_api_is_silent(self, device):
        device({})
        assert wican_v6.assess(V6, "wican-ws", HOST, timeout=1.0).level == "note"
        assert wican_v6.assess(V6, "slcan-tcp", HOST, port=3333, timeout=1.0).level == "warn"

    def test_live_refusal_overrides_the_static_note(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"][0]["enabled"] = False
        device(routes)
        assert wican_v6.assess(V6, "wican-ws", HOST, timeout=1.0).refused

    def test_other_surfaces_stay_static(self, device):
        seen = device(STOCK)
        assert wican_v6.assess(V6, "autopid-sync", HOST).refused
        assert wican_v6.assess(V6, "reboot", HOST).level == "skip"
        assert seen == []


class TestDeviceStatus:
    def test_reads_power_and_chip_state(self, device):
        device(STOCK)
        st = wican_v6.device_status(HOST, timeout=1.0)
        assert st.battery_v == 12.58  # the dedicated route, not /api/sleep's copy
        assert st.sleep_enabled is True and st.sleep_v == 12.2 and st.sleep_state == "normal"
        assert st.obd_chip_ready is True and st.obd_chip_claim == "none"
        assert st.reachable

    def test_battery_falls_back_to_the_sleep_reading(self, device):
        routes = copy.deepcopy(STOCK)
        routes["/api/battery"] = {"voltage": None}
        device(routes)
        assert wican_v6.device_status(HOST, timeout=1.0).battery_v == 12.61

    def test_silent_device_is_all_unknown(self, device):
        device({})
        st = wican_v6.device_status(HOST, timeout=1.0)
        assert not st.reachable


class TestSleepBanner:
    def test_v6_banner_reads_api_sleep(self, device, monkeypatch, capsys):
        from canlib.commands._live import connect

        device(STOCK)
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        connect._print_sleep_banner(HOST)
        out = capsys.readouterr().out
        assert "Sleep: ON" in out and "12.61V" in out

    def test_v6_banner_warns_near_the_cutoff(self, device, monkeypatch, capsys):
        from canlib.commands._live import connect

        routes = copy.deepcopy(STOCK)
        routes["/api/sleep"]["voltage"] = 12.4
        device(routes)
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        connect._print_sleep_banner(HOST)
        assert "may shut down soon" in capsys.readouterr().out

    def test_v6_banner_skipped_when_silent(self, device, monkeypatch, capsys):
        from canlib.commands._live import connect

        device({})
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        connect._print_sleep_banner(HOST)
        assert capsys.readouterr().out == ""


class TestLiveConnect:
    def _args(self):
        from canlib.commands._live import CANAIR_DEFAULTS

        return argparse.Namespace(**CANAIR_DEFAULTS)

    @pytest.mark.parametrize(("info", "path"), [(V6, "/ws/obd"), (V4, "/ws")])
    def test_wican_ws_picks_the_channel_from_the_firmware(self, monkeypatch, info, path):
        from canlib.commands._live import connect
        from canlib.transport.config import TransportConfig

        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: info)
        built = {}

        async def fake_connect(self):
            built["url"] = self.url
            raise ConnectionError("stop here")

        monkeypatch.setattr(connect.WiCANTerminal, "connect", fake_connect)
        cand = TransportConfig(type="wican-ws", host=HOST)
        with pytest.raises(ConnectionError):
            asyncio.run(connect.connect_elm_terminal(cand, {"init": "ATZ;"}, self._args()))
        assert built["url"] == f"ws://{HOST}{path}"

    def test_unbridged_ws_obd_refused_before_connecting(self, device, monkeypatch, capsys):
        from canlib.commands._live import runtime
        from canlib.transport.config import TransportConfig

        routes = copy.deepcopy(STOCK)
        routes["/api/ws"]["channels"][0]["bridge"] = None
        device(routes)
        cand = TransportConfig(type="wican-ws", host=HOST)
        monkeypatch.setattr("canlib.transport.resolve_transport_candidates", lambda args: [cand])
        monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
        monkeypatch.setattr(runtime, "load_pids", lambda: {"init": "ATZ;"})
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        monkeypatch.setattr(
            "canlib.wican_mode.require_ws_reachable",
            lambda *a, **k: pytest.fail("refusal must come before the reachability check"),
        )
        assert asyncio.run(runtime.async_main(self._args())) == 2
        assert "nothing is bridged" in capsys.readouterr().err

    def test_raw_command_refused_on_the_silent_trap(self, device, monkeypatch, capsys):
        from canlib.modes import raw_ops

        routes = copy.deepcopy(STOCK)
        routes["/api/sockets"]["servers"][1].update(enabled=True, up=True)
        device(routes)
        monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
        monkeypatch.setattr(
            "canlib.wican_mode.require_slcan_reachable",
            lambda *a, **k: pytest.fail("must refuse before the port check"),
        )

        class _T:
            host = HOST

            def resolve_device_defaults(self, profile_bitrate=None):
                return 3333, 500000

        args = argparse.Namespace(multi=["query BMS"], monitor=1.0, verbose=False, unsafe=False)
        assert asyncio.run(raw_ops.run_raw(args, _T(), {})) == 2
        assert "silently discards" in capsys.readouterr().err


class TestStatus:
    @pytest.fixture
    def gather(self, monkeypatch):
        from canlib.commands import status
        from canlib.transport.config import TransportConfig

        def run(ttype: str, routes: dict):
            cand = TransportConfig(type=ttype, host=HOST)
            monkeypatch.setattr(
                "canlib.transport.resolve_transport_candidates", lambda args: [cand]
            )
            monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
            monkeypatch.setattr(wf, "probe", lambda host, *, timeout: V6)
            monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: V6)
            monkeypatch.setattr(status, "_load_device_config", lambda *a: pytest.fail("4.x"))
            monkeypatch.setattr(status, "_tcp_open", lambda *a: True)

            def fetch(url, timeout):
                for path, body in routes.items():
                    if url.endswith(path):
                        return FakeResponse(200, copy.deepcopy(body))
                return FakeResponse(404)

            monkeypatch.setattr(wf, "_fetch", fetch)
            return status._gather(argparse.Namespace(transport=None, wican=None))

        return run

    def test_reports_v6_power_and_chip_state(self, gather, capsys):
        from canlib.commands import status

        info = gather("wican-ws", STOCK)
        assert info["exit"] == 0
        assert info["device_v6"]["battery_v"] == 12.58
        status._render(info)
        out = capsys.readouterr().out
        assert "12.58V" in out and "threshold 12.20V" in out and "obd chip   ready" in out

    def test_slcan_silent_trap_is_misconfigured(self, gather):
        routes = copy.deepcopy(STOCK)
        routes["/api/sockets"]["servers"][1].update(enabled=True, up=True)
        info = gather("slcan-tcp", routes)
        assert info["exit"] == 2
        assert info["transport"]["usable"] is False
        assert any("silently discards" in e for e in info["errors"])

    def test_slcan_ready_is_usable(self, gather):
        info = gather("slcan-tcp", _slcan_ready(STOCK))
        assert info["exit"] == 0
        assert info["transport"]["usable"] is True
        assert info["transport"]["port"] == 3333
        assert any("bridged to the CAN bus" in n for n in info["notes"])
