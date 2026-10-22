"""WiCAN firmware generation probe and the per-surface refuse/warn policy.

The probe is driven against a stubbed HTTP seam (``wican_firmware._fetch``), so
every test is device-free. Plan: ``plans/2026-10-20-wican-fw-v6-compatibility.md``.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest
import requests

import canlib.wican_firmware as wf
from canlib.wican_firmware import FirmwareInfo, compatibility, probe

# A v4 /check_status body is full of secrets; these must never survive the probe.
_V4_SECRETS = {
    "sta_pass": "hunter2-wifi",
    "home_password": "hunter2-home",
    "drive_password": "hunter2-drive",
    "mqtt_pass": "hunter2-mqtt",
    "sta_ip": "192.0.2.10",
}
_V4_STATUS = {
    "fw_version": "4.52",
    "hw_version": "WiCAN-OBD-PRO",
    "git_version": "v4.52p",
    "protocol": "slcan",
    **_V4_SECRETS,
}
_V6_INFO = {
    "device_type": "wican_pro",
    "model": "WiCAN Pro",
    "hw_version": "WiCAN-PRO",
    "fw_version": "6.0.0-beta.1",
    "fw_build": "v6.0.0p-beta.1",
    "device_id": "000000000000",
    "mac": "00:00:5E:00:53:00",
    "api_level": 6,
}


class FakeResponse:
    def __init__(self, status: int, body=None, *, headers=None, text: str | None = None):
        self.status_code = status
        self._body = body
        self._text = text
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        if self._text is not None:
            raise requests.JSONDecodeError("not json", self._text, 0)
        return self._body

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} Client Error")


@pytest.fixture
def device(monkeypatch):
    """Install a fake device: ``device({"/api/info": resp, ...})``; returns the URL log."""
    seen: list[str] = []

    def install(routes: dict):
        def fetch(url, timeout):
            seen.append(url)
            for path, resp in routes.items():
                if url.endswith(path):
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            return FakeResponse(404, text="Not Found")

        monkeypatch.setattr(wf, "_fetch", fetch)
        return seen

    return install


def _assert_no_secrets(info: FirmwareInfo) -> None:
    dumped = repr(info) + repr(info.as_dict()) + info.label()
    for value in _V4_SECRETS.values():
        assert value not in dumped
    for key in _V4_SECRETS:
        assert not hasattr(info, key)


class TestProbe:
    def test_v4_from_check_status(self, device):
        seen = device({"/check_status": FakeResponse(200, dict(_V4_STATUS))})
        info = probe("192.0.2.1", timeout=1.0)
        assert info == FirmwareInfo(
            "v4", version="4.52", build="v4.52p", hw_version="WiCAN-OBD-PRO"
        )
        assert seen == ["http://192.0.2.1/api/info", "http://192.0.2.1/check_status"]
        _assert_no_secrets(info)

    def test_v6_from_api_info(self, device):
        seen = device({"/api/info": FakeResponse(200, dict(_V6_INFO))})
        info = probe("192.0.2.1", timeout=1.0)
        assert info.generation == "v6"
        assert info.version == "6.0.0-beta.1"
        assert info.build == "v6.0.0p-beta.1"
        assert info.api_level == 6
        assert info.gated is None
        assert seen == ["http://192.0.2.1/api/info"]  # never asks the 4.x route
        # Device identity (MAC, device id) is not kept either.
        assert "00:00:5E:00:53:00" not in repr(info)

    def test_v6_password_gate(self, device):
        device(
            {
                "/api/info": FakeResponse(
                    401,
                    {"error": "password required"},
                    headers={"WWW-Authenticate": 'Basic realm="WiCAN"'},
                )
            }
        )
        info = probe("192.0.2.1", timeout=1.0)
        assert info == FirmwareInfo("v6", gated="auth")
        assert "admin password" in info.label()

    def test_v6_untrusted_network_gate(self, device):
        device(
            {"/api/info": FakeResponse(403, {"error": "configuration disabled on this network"})}
        )
        info = probe("192.0.2.1", timeout=1.0)
        assert info == FirmwareInfo("v6", gated="untrusted")

    def test_both_routes_missing_is_unknown(self, device):
        seen = device({})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"
        assert len(seen) == 2

    def test_non_json_200_is_unknown(self, device):
        device({"/api/info": FakeResponse(200, text="<html>router login</html>")})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"

    def test_non_json_check_status_is_unknown(self, device):
        device({"/check_status": FakeResponse(200, text="<html></html>")})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"

    def test_api_info_without_integer_level_is_unknown(self, device):
        seen = device({"/api/info": FakeResponse(200, {"api_level": True})})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"
        assert len(seen) == 2  # not positively v6, so the 4.x route was asked too

    def test_foreign_401_is_unknown(self, device):
        device({"/api/info": FakeResponse(401, headers={"WWW-Authenticate": 'Basic realm="x"'})})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"

    @pytest.mark.parametrize(
        "api_info",
        [
            FakeResponse(200, {"api_level": "6"}),  # not an integer
            FakeResponse(200, ["api_level", 6]),  # JSON, but not an object
            FakeResponse(200, text="<html>router</html>"),
            FakeResponse(401, headers={"WWW-Authenticate": 'Basic realm="router"'}),
            FakeResponse(403, {"error": "forbidden"}),  # a 403 that isn't v6's gate
            FakeResponse(500, text="Internal Server Error"),
            FakeResponse(503, text="busy"),
        ],
        ids=["string-level", "json-list", "html", "foreign-401", "foreign-403", "500", "503"],
    )
    def test_anything_not_positively_v6_still_tries_the_4x_route(self, device, api_info):
        """Only a 404 used to fall through, so a v4 behind an odd /api/info read unknown."""
        seen = device({"/api/info": api_info, "/check_status": FakeResponse(200, dict(_V4_STATUS))})
        info = probe("192.0.2.1", timeout=1.0)
        assert info.generation == "v4" and info.version == "4.52"
        assert seen == ["http://192.0.2.1/api/info", "http://192.0.2.1/check_status"]

    def test_unparsed_v4_tag_is_v4_without_a_version(self, device):
        """The firmware reports "0.00" for a tag it couldn't parse: no version, no warning."""
        status = {**_V4_STATUS, "fw_version": "0.00", "git_version": "custom-build"}
        device({"/check_status": FakeResponse(200, status)})
        info = probe("192.0.2.1", timeout=1.0)
        assert info == FirmwareInfo(
            "v4", version=None, build="custom-build", hw_version="WiCAN-OBD-PRO"
        )
        assert compatibility(info, "slcan-tcp").level == "ok"
        assert info.label() == "4.x (custom-build)"

    def test_v4_version_must_match_the_firmware_format(self, device):
        device({"/check_status": FakeResponse(200, {"fw_version": "4.5"})})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"

    def test_unreachable_host_is_unknown_after_one_request(self, device):
        seen = device({"/api/info": requests.ConnectionError("refused")})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"
        assert len(seen) == 1

    def test_memoised_per_host(self, device):
        seen = device({"/api/info": FakeResponse(200, dict(_V6_INFO))})
        probe("192.0.2.1", timeout=1.0)
        probe("http://192.0.2.1/", timeout=1.0)  # same host, URL form
        assert len(seen) == 1
        probe("192.0.2.2", timeout=1.0)
        assert len(seen) == 2


class TestDetect:
    def test_disabled_check_sends_nothing(self, device, monkeypatch):
        import canlib.config as cfg

        seen = device({"/api/info": FakeResponse(200, dict(_V6_INFO))})
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: "off")
        for surface in ("wican-ws", "slcan-tcp", "elm327-tcp", "autopid-sync"):
            assert wf.detect("192.0.2.1", surface).generation == "unknown"
        assert seen == []

    def test_uses_the_connect_timeout(self, monkeypatch):
        import canlib.config as cfg

        got = {}

        def fetch(url, timeout):
            got["timeout"] = timeout
            raise requests.ConnectionError

        monkeypatch.setattr(wf, "_fetch", fetch)
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: "on")
        monkeypatch.setattr(cfg, "fallback_settings", lambda: (True, 7.5, None))
        wf.detect("192.0.2.1", "slcan-tcp")
        assert got["timeout"] == 7.5

    @pytest.mark.parametrize(("mode", "probed"), [("on", False), ("always", True), ("off", False)])
    def test_elm327_tcp_is_probed_only_when_asked(self, device, monkeypatch, mode, probed):
        """It is usually a clone with no HTTP server: a default probe cost a timeout."""
        import canlib.config as cfg

        seen = device({"/api/info": FakeResponse(200, dict(_V6_INFO))})
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: mode)
        info = wf.detect("192.0.2.1", "elm327-tcp")
        assert (info.generation == "v6") is probed
        assert bool(seen) is probed

    @pytest.mark.parametrize("surface", ["wican-ws", "slcan-tcp", "wican-mode", "reboot"])
    def test_wican_surfaces_are_probed_by_default(self, device, monkeypatch, surface):
        import canlib.config as cfg

        device({"/api/info": FakeResponse(200, dict(_V6_INFO))})
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: "on")
        assert wf.detect("192.0.2.1", surface).generation == "v6"


V4 = FirmwareInfo("v4", version="4.52", build="v4.52p")
V6 = FirmwareInfo("v6", version="6.0.0-beta.1", api_level=6)
V6_GATED = FirmwareInfo("v6", gated="auth")


class TestPolicyTable:
    @pytest.mark.parametrize(
        ("surface", "level"),
        [
            ("wican-ws", "refuse"),
            ("slcan-tcp", "warn"),
            ("elm327-tcp", "note"),
            ("sniff-listen-only", "refuse"),
            ("wican-mode", "refuse"),
            ("autopid-sync", "refuse"),
            ("reboot", "skip"),
        ],
    )
    @pytest.mark.parametrize("info", [V6, V6_GATED], ids=["v6", "v6-gated"])
    def test_v6(self, info, surface, level):
        assert compatibility(info, surface).level == level

    @pytest.mark.parametrize(
        "surface",
        [
            "wican-ws",
            "slcan-tcp",
            "elm327-tcp",
            "sniff-listen-only",
            "wican-mode",
            "autopid-sync",
            "reboot",
        ],
    )
    @pytest.mark.parametrize("info", [V4, wf.UNKNOWN], ids=["v4", "unknown"])
    def test_v4_and_unknown_are_untouched(self, info, surface):
        assert compatibility(info, surface).level == "ok"

    def test_wican_ws_refusal_points_at_elm327_tcp(self):
        msg = compatibility(V6, "wican-ws").message
        assert "--transport elm327-tcp" in msg and "35000" in msg

    def test_slcan_warning_names_the_three_settings_and_the_trap(self):
        msg = compatibility(V6, "slcan-tcp").message
        assert "slcan0" in msg and "bridge" in msg and "can_manager" in msg
        assert "discards" in msg

    def test_autopid_refusal_states_the_actual_risk(self):
        """canair POSTs /store_car_data, which v6 answers 405: a loud failure, not a wipe."""
        msg = compatibility(V6, "autopid-sync").message
        assert "fail" in msg
        assert "wipe" not in msg

    @pytest.mark.parametrize(
        ("version", "level", "word"),
        [
            ("4.50", "ok", None),
            ("4.51", "ok", None),
            ("4.52", "ok", None),
            ("4.53", "warn", "newer"),
            ("5.00", "warn", "newer"),
            ("4.49", "warn", "older"),
            ("3.10", "warn", "older"),  # pre-4 releases share the 4.x API family
        ],
    )
    def test_v4_supported_range(self, version, level, word):
        verdict = compatibility(FirmwareInfo("v4", version=version), "slcan-tcp")
        assert verdict.level == level
        if word:
            assert word in verdict.message and "4.50–4.52" in verdict.message

    def test_v4_range_warning_never_blocks_a_reboot(self):
        assert compatibility(FirmwareInfo("v4", version="4.60"), "reboot").level == "ok"

    @pytest.mark.parametrize(
        ("info", "label"),
        [
            (FirmwareInfo("v4", version="4.52", build="v4.52p"), "4.52 (v4.52p)"),
            (FirmwareInfo("v4", version="4.52"), "4.52"),
            (FirmwareInfo("v4"), "4.x"),
            (FirmwareInfo("v6", version="6.0.0-beta.1"), "v6 6.0.0-beta.1"),
            (FirmwareInfo("v6"), "v6"),
            (wf.UNKNOWN, "unknown"),
        ],
    )
    def test_label(self, info, label):
        assert info.label() == label

    def test_unconfirmed_listen_only_and_autopid_warn(self):
        for why in ("check-off", "unidentified"):
            assert wf.unconfirmed("sniff-listen-only", why).level == "warn"
            assert wf.unconfirmed("autopid-sync", why).level == "warn"
        assert "firmware_check is off" in wf.unconfirmed("sniff-listen-only", "check-off").message
        assert wf.unconfirmed("slcan-tcp", "unidentified").level == "ok"

    def test_supported_table_is_well_formed(self):
        for rng in wf.SUPPORTED:
            assert wf._version_key(rng.low) is not None
            assert wf._version_key(rng.high) is not None
            assert wf._version_key(rng.low) <= wf._version_key(rng.high)


class TestFirmwareCheckConfig:
    def _with(self, monkeypatch, block):
        import canlib.config as cfg

        monkeypatch.setattr(cfg, "load_config", lambda: {"transport": block})
        return cfg.firmware_check()

    def test_default_on(self, monkeypatch):
        assert self._with(monkeypatch, {}) is True

    @pytest.mark.parametrize("value", [False, "false", "off", "no", "0"])
    def test_off(self, monkeypatch, value):
        assert self._with(monkeypatch, {"firmware_check": value}) is False

    def test_null_falls_back_to_default(self, monkeypatch):
        assert self._with(monkeypatch, {"firmware_check": None}) is True

    @pytest.mark.parametrize(
        ("value", "mode"),
        [
            (None, "on"),
            (True, "on"),
            ("true", "on"),
            (False, "off"),
            ("off", "off"),
            ("always", "always"),
            ("ALWAYS", "always"),
        ],
    )
    def test_mode(self, monkeypatch, value, mode):
        import canlib.config as cfg

        block = {} if value is None else {"firmware_check": value}
        monkeypatch.setattr(cfg, "load_config", lambda: {"transport": block})
        assert cfg.firmware_check_mode() == mode
        assert cfg.firmware_check() is (mode != "off")

    def test_is_a_known_config_key(self):
        from canlib.commands.config import _KNOWN_KEYS

        assert "transport.firmware_check" in _KNOWN_KEYS


# --- the surfaces --------------------------------------------------------------


def _v6(monkeypatch, info: FirmwareInfo = V6):
    monkeypatch.setattr(wf, "detect", lambda host, surface, *, timeout=None: info)


class TestRebootRegression:
    def test_reboot_on_405_reports_failure(self, monkeypatch, capsys):
        from canlib import terminal, wican_api

        monkeypatch.setattr(wican_api.requests, "post", lambda *a, **k: FakeResponse(405))
        assert terminal.reboot_wican("192.0.2.1") is False
        captured = capsys.readouterr()
        assert "FAILED" in captured.err and "405" in captured.err
        assert "Rebooting" not in captured.out

    def test_reboot_on_200_succeeds(self, monkeypatch, capsys):
        from canlib import terminal, wican_api

        monkeypatch.setattr(wican_api.requests, "post", lambda *a, **k: FakeResponse(200))
        assert terminal.reboot_wican("192.0.2.1") is True
        assert "Rebooting" in capsys.readouterr().out

    def test_reboot_skipped_on_v6(self, monkeypatch, capsys):
        from canlib import terminal, wican_api

        _v6(monkeypatch)
        monkeypatch.setattr(
            wican_api.requests, "post", lambda *a, **k: pytest.fail("must not POST on v6")
        )
        assert terminal.reboot_wican("192.0.2.1") is False
        assert "skipping --reboot" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("info", "post", "outcome"),
        [
            (V6, None, "skipped"),
            (V4, FakeResponse(200), "rebooted"),
            (wf.UNKNOWN, FakeResponse(200), "rebooted"),
            (V4, FakeResponse(405), "failed"),
            (wf.UNKNOWN, requests.ConnectionError("down"), "failed"),
        ],
        ids=["v6", "v4-ok", "unknown-ok", "v4-405", "unreachable"],
    )
    def test_reboot_device_tells_skipped_from_failed(self, monkeypatch, info, post, outcome):
        from canlib import wican_api

        _v6(monkeypatch, info)

        def _post(*a, **k):
            if post is None:
                pytest.fail("must not POST")
            if isinstance(post, Exception):
                raise post
            return post

        monkeypatch.setattr(wican_api.requests, "post", _post)
        assert wican_api.reboot_device("192.0.2.1") == outcome

    def test_autopid_upload_reboot_on_405_reports_failure(self, monkeypatch, capsys):
        from canlib.commands import wican

        def post(url, **kw):
            return FakeResponse(405 if url.endswith("/system_reboot") else 200)

        assert wican.requests is not None
        monkeypatch.setattr(wican.requests, "post", post)
        monkeypatch.setattr(wican.time, "sleep", lambda *_a: None)
        payload = {"cars": [{"pids": []}]}
        wican.upload_profile("http://192.0.2.1", payload, reboot=True)
        err = capsys.readouterr().err
        assert "FAILED to reboot" in err and "405" in err


def _elm_session(monkeypatch, ttype: str, *, calls: list):
    """Drive ``async_main`` for one ELM candidate with every device seam stubbed."""
    from canlib.commands._live import connect, runtime
    from canlib.transport.config import TransportConfig

    cand = TransportConfig(type=ttype, host="192.0.2.1")
    monkeypatch.setattr("canlib.transport.resolve_transport_candidates", lambda args: [cand])
    monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
    monkeypatch.setattr(runtime, "load_pids", lambda: {"init": "ATZ;"})
    monkeypatch.setattr(
        "canlib.wican_mode.require_ws_reachable", lambda *a, **k: calls.append("reach")
    )
    monkeypatch.setattr(
        "canlib.wican_mode.require_elm327_tcp_reachable", lambda *a, **k: calls.append("reach")
    )

    class _Term:
        async def close(self):
            calls.append("close")

    async def _connect(*a, **k):
        calls.append("connect")
        return _Term()

    async def _session(*a, **k):
        calls.append("session")
        return 0

    monkeypatch.setattr(runtime, "connect_elm_terminal", _connect)
    monkeypatch.setattr(runtime, "run_session_guarded", _session)
    monkeypatch.setattr(runtime, "_print_sleep_banner", lambda host: None)
    monkeypatch.setattr(connect, "_print_sleep_banner", lambda host: None)


def _live_args(**kw):
    from canlib.commands._live import CANAIR_DEFAULTS

    return argparse.Namespace(**{**CANAIR_DEFAULTS, **kw})


def _recording_detect(monkeypatch, info: FirmwareInfo, calls: list):
    def detect(host, surface, *, timeout=None):
        calls.append(f"probe:{surface}")
        return info

    monkeypatch.setattr(wf, "detect", detect)


class TestLiveSurfaces:
    def test_wican_ws_connect_refused_on_v6_after_the_reachability_check(self, monkeypatch, capsys):
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "wican-ws", calls=calls)
        _recording_detect(monkeypatch, V6, calls)
        rc = asyncio.run(runtime.async_main(_live_args()))
        assert rc == 2
        # The cheap TCP pre-check first: an offline host must not also pay the probe.
        assert calls == ["reach", "probe:wican-ws"]
        err = capsys.readouterr().err
        assert "no /ws ELM327 terminal" in err and "elm327-tcp" in err

    def test_an_unreachable_host_fails_fast_without_the_probe(self, monkeypatch, capsys):
        from canlib.commands._live import runtime
        from canlib.wican_mode import ModeError

        calls: list = []
        _elm_session(monkeypatch, "wican-ws", calls=calls)
        _recording_detect(monkeypatch, V6, calls)

        def _down(*a, **k):
            raise ModeError("can't reach the WiCAN")

        monkeypatch.setattr("canlib.wican_mode.require_ws_reachable", _down)
        with pytest.raises(SystemExit):
            asyncio.run(runtime.async_main(_live_args()))
        assert calls == []

    def test_v4_out_of_range_warns_and_connects(self, monkeypatch, capsys):
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "wican-ws", calls=calls)
        _recording_detect(monkeypatch, FirmwareInfo("v4", version="4.60"), calls)
        assert asyncio.run(runtime.async_main(_live_args())) == 0
        assert "session" in calls
        assert "newer than canair has tested" in capsys.readouterr().err

    def test_elm327_tcp_is_not_probed_by_default(self, monkeypatch, capsys):
        import canlib.config as cfg
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "elm327-tcp", calls=calls)
        seen = []
        monkeypatch.setattr(wf, "probe", lambda host, *, timeout: seen.append(host) or V6)
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: "on")
        assert asyncio.run(runtime.async_main(_live_args())) == 0
        assert seen == []
        assert "AutoPID" not in capsys.readouterr().err

    def test_elm327_tcp_note_when_opted_in(self, monkeypatch, capsys):
        import canlib.config as cfg
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "elm327-tcp", calls=calls)
        monkeypatch.setattr(wf, "probe", lambda host, *, timeout: V6)
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: "always")
        assert asyncio.run(runtime.async_main(_live_args())) == 0
        err = capsys.readouterr().err
        assert err.startswith("note: ") and "AutoPID" in err
        assert "session" in calls

    @pytest.mark.parametrize(
        ("info", "posted", "text"),
        [(V6, False, "skipping --reboot"), (wf.UNKNOWN, True, "Rebooting")],
        ids=["v6-skips", "unknown-reboots"],
    )
    def test_reboot_after_an_elm_session(self, monkeypatch, capsys, info, posted, text):
        from canlib import wican_api
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "wican-ws", calls=calls)
        # Only the reboot sees v6 here: the connect itself must get through.
        monkeypatch.setattr(
            wf, "detect", lambda host, surface, **k: info if surface == "reboot" else V4
        )
        posts: list = []
        monkeypatch.setattr(
            wican_api.requests, "post", lambda *a, **k: posts.append(a) or FakeResponse(200)
        )
        assert asyncio.run(runtime.async_main(_live_args(reboot=True))) == 0
        assert bool(posts) is posted
        captured = capsys.readouterr()
        assert text in captured.out + captured.err

    def test_reboot_on_elm327_tcp_is_reported_not_dropped(self, monkeypatch, capsys):
        from canlib import wican_api
        from canlib.commands._live import runtime

        calls: list = []
        _elm_session(monkeypatch, "elm327-tcp", calls=calls)
        monkeypatch.setattr(wican_api.requests, "post", lambda *a, **k: pytest.fail("no API"))
        assert asyncio.run(runtime.async_main(_live_args(reboot=True))) == 0
        assert "skipping --reboot" in capsys.readouterr().err

    # --- slcan-tcp ---------------------------------------------------------------

    def _raw(self, monkeypatch, *, http_up: bool = True):
        from canlib import wican_mode
        from canlib.modes import raw_monitor

        calls: list = []
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: http_up)
        monkeypatch.setattr(
            wican_mode, "require_protocol", lambda *a, **k: calls.append("require_protocol")
        )
        monkeypatch.setattr(
            wican_mode,
            "require_slcan_reachable",
            lambda *a, **k: calls.append(("slcan_reachable", k.get("http_ok"))),
        )

        async def _mon(*a, **k):
            calls.append("monitor")
            return 0

        monkeypatch.setattr(raw_monitor, "run_raw_monitor", _mon)
        return calls

    def _cand(self, **kw):
        from canlib.transport.config import TransportConfig

        return TransportConfig(type="slcan-tcp", host="192.0.2.1", **kw)

    def _raw_args(self, **kw):
        return argparse.Namespace(
            multi=["query BMS"], monitor=1.0, verbose=False, unsafe=False, **kw
        )

    def test_slcan_on_v6_warns_and_skips_the_mode_check(self, monkeypatch, capsys):
        from canlib.modes import raw_ops

        calls = self._raw(monkeypatch)
        _v6(monkeypatch)
        rc = asyncio.run(raw_ops.run_raw(self._raw_args(), self._cand(), {}))
        assert rc == 0
        assert calls == [("slcan_reachable", True), "monitor"]  # require_protocol skipped
        assert "slcan0" in capsys.readouterr().err

    def test_slcan_on_v6_does_not_read_the_4x_device_config(self, monkeypatch):
        """No /load_config on v6: a gated one answered 401/403 and printed noise."""
        from canlib.modes import raw_ops
        from canlib.transport.config import TransportConfig

        self._raw(monkeypatch)
        _v6(monkeypatch, V6_GATED)
        monkeypatch.setattr(
            TransportConfig,
            "_wican_device_config",
            lambda self: pytest.fail("v6 has no /load_config"),
        )
        assert asyncio.run(raw_ops.run_raw(self._raw_args(), self._cand(), {})) == 0

    def test_slcan_on_v4_still_checks_the_mode(self, monkeypatch, capsys):
        from canlib.modes import raw_ops

        calls = self._raw(monkeypatch)
        _v6(monkeypatch, V4)
        asyncio.run(raw_ops.run_raw(self._raw_args(), self._cand(port=3333, bitrate=500000), {}))
        assert calls == ["require_protocol", ("slcan_reachable", True), "monitor"]
        assert capsys.readouterr().err == ""

    def test_slcan_with_the_http_port_down_skips_every_http_request(self, monkeypatch):
        """An offline host used to pay the probe, the mode check and the config read."""
        from canlib.modes import raw_ops
        from canlib.transport.config import TransportConfig

        calls = self._raw(monkeypatch, http_up=False)
        monkeypatch.setattr(wf, "detect", lambda *a, **k: pytest.fail("no probe when down"))
        monkeypatch.setattr(
            TransportConfig, "_wican_device_config", lambda self: pytest.fail("no config read")
        )
        asyncio.run(raw_ops.run_raw(self._raw_args(), self._cand(), {}))
        assert calls == [("slcan_reachable", False), "monitor"]

    @pytest.mark.parametrize(
        ("info", "posted"), [(V4, True), (V6, False)], ids=["v4-reboots", "v6-skips"]
    )
    def test_reboot_works_after_a_raw_session(self, monkeypatch, capsys, info, posted):
        """--reboot used to be ignored on slcan-tcp: run_raw returned before the reboot."""
        from canlib import wican_api
        from canlib.modes import raw_ops

        self._raw(monkeypatch)
        _v6(monkeypatch, info)
        posts: list = []
        monkeypatch.setattr(
            wican_api.requests, "post", lambda *a, **k: posts.append(a) or FakeResponse(200)
        )
        args = self._raw_args(reboot=True)
        assert asyncio.run(raw_ops.run_raw(args, self._cand(port=3333, bitrate=500000), {})) == 0
        assert bool(posts) is posted

    def test_no_reboot_when_the_raw_preflight_fails(self, monkeypatch):
        from canlib import wican_api, wican_mode
        from canlib.modes import raw_ops

        self._raw(monkeypatch)
        _v6(monkeypatch, V4)

        def _wrong_mode(*a, **k):
            raise wican_mode.ModeError("wrong mode")

        monkeypatch.setattr(wican_mode, "require_protocol", _wrong_mode)
        monkeypatch.setattr(wican_api, "reboot_device", lambda *a, **k: pytest.fail("no session"))
        args = self._raw_args(reboot=True)
        assert asyncio.run(raw_ops.run_raw(args, self._cand(port=3333, bitrate=500000), {})) == 2

    # --- sniff -------------------------------------------------------------------

    def _sniff(self, monkeypatch, *, http_up: bool = True):
        from canlib import wican_mode
        from canlib.commands import sniff

        ran: list = []
        monkeypatch.setattr("canlib.transport.resolve_transport", lambda args: self._cand())
        monkeypatch.setattr(wican_mode, "_tcp_open", lambda host, port, timeout: http_up)
        monkeypatch.setattr(wican_mode, "require_protocol", lambda *a, **k: None)
        monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda *a, **k: None)
        monkeypatch.setattr(
            "canlib.transport.config.TransportConfig._wican_device_config", lambda self: {}
        )
        monkeypatch.setattr("canlib.lock.WiCANLock.acquire", lambda *a, **k: None)
        monkeypatch.setattr("canlib.lock.WiCANLock.release", lambda *a, **k: None)
        monkeypatch.setattr(sniff, "_run_sniff", lambda *a, **k: ran.append(a))
        return ran

    def _sniff_args(self, **kw):
        base = {"wican": None, "listen_only": False, "filter": None, "duration": None, "save": None}
        return argparse.Namespace(**{**base, "force": False, **kw})

    def test_sniff_listen_only_refused_on_v6(self, monkeypatch, capsys):
        from canlib.commands import sniff

        self._sniff(monkeypatch)
        monkeypatch.setattr(
            "canlib.lock.WiCANLock.acquire",
            lambda *a, **k: pytest.fail("must refuse before taking the device"),
        )
        _v6(monkeypatch)
        assert sniff.run(self._sniff_args(listen_only=True)) == 2
        assert "can_manager.silent" in capsys.readouterr().err

    def test_sniff_on_v6_without_listen_only_warns_and_sniffs(self, monkeypatch, capsys):
        from canlib.commands import sniff

        ran = self._sniff(monkeypatch)
        _v6(monkeypatch)
        assert sniff.run(self._sniff_args()) == 0
        assert ran
        err = capsys.readouterr().err
        assert err.startswith("warning: ") and "slcan0" in err

    @pytest.mark.parametrize(
        ("mode", "word"), [("on", "did not identify"), ("off", "firmware_check is off")]
    )
    def test_listen_only_on_an_unidentified_http_device_warns(
        self, monkeypatch, capsys, mode, word
    ):
        """Unknown used to pass silently, though it may well be a v6 WiCAN."""
        import canlib.config as cfg
        from canlib.commands import sniff

        ran = self._sniff(monkeypatch)
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: mode)
        assert sniff.run(self._sniff_args(listen_only=True)) == 0
        assert ran  # a warning, not a refusal
        err = capsys.readouterr().err
        assert "--listen-only cannot be guaranteed" in err and word in err

    def test_listen_only_without_an_http_server_stays_quiet(self, monkeypatch, capsys):
        """No HTTP server: not a v6 WiCAN (v6 always serves one), so nothing to warn about."""
        from canlib.commands import sniff

        ran = self._sniff(monkeypatch, http_up=False)
        assert sniff.run(self._sniff_args(listen_only=True)) == 0
        assert ran
        assert "listen-only" not in capsys.readouterr().err

    def test_sniff_listen_only_on_v4_is_silent(self, monkeypatch, capsys):
        from canlib.commands import sniff

        self._sniff(monkeypatch)
        _v6(monkeypatch, V4)
        assert sniff.run(self._sniff_args(listen_only=True)) == 0
        assert capsys.readouterr().err == ""

    def test_sniff_fails_fast_when_the_slcan_port_is_closed(self, monkeypatch, capsys):
        from canlib.commands import sniff
        from canlib.wican_mode import ModeError

        self._sniff(monkeypatch)

        def _closed(host, port, **kw):
            raise ModeError(f"can't reach the WiCAN's SLCAN data port at {host}:{port}")

        monkeypatch.setattr("canlib.wican_mode.require_slcan_reachable", _closed)
        monkeypatch.setattr(
            "canlib.lock.WiCANLock.acquire",
            lambda *a, **k: pytest.fail("must fail before taking the device"),
        )
        _v6(monkeypatch, V4)
        assert sniff.run(self._sniff_args()) == 2
        assert "SLCAN data port" in capsys.readouterr().err


class TestWicanCommandSurfaces:
    @pytest.mark.parametrize(
        "handler", ["_cmd_mode_show", "_cmd_mode_set", "_cmd_autopid_download"]
    )
    def test_refused_on_v6_before_any_request(self, monkeypatch, capsys, handler):
        import canlib.config as cfg
        from canlib.commands import wican

        monkeypatch.setattr(cfg, "wican_model", lambda: "pro")
        monkeypatch.setattr(
            "canlib.wican_mode.current_protocol",
            lambda *a, **k: pytest.fail("must not talk to the 4.x API on v6"),
        )
        monkeypatch.setattr(
            wican, "download_profile", lambda *a: pytest.fail("must not download on v6")
        )
        _v6(monkeypatch)
        args = argparse.Namespace(
            wican="http://192.0.2.1", protocol="slcan", yes=True, no_transport=True
        )
        assert getattr(wican, handler)(args) == 2
        assert "v6" in capsys.readouterr().err

    @pytest.mark.parametrize("handler", ["_cmd_autopid_upload", "_cmd_autopid_diff"])
    def test_autopid_writes_refused_on_v6(self, monkeypatch, capsys, handler):
        import canlib.config as cfg
        from canlib.commands import wican

        monkeypatch.setattr(cfg, "wican_model", lambda: "pro")
        monkeypatch.setattr(wican, "upload_profile", lambda *a, **k: pytest.fail("no upload"))
        monkeypatch.setattr(wican, "_generate", lambda args: pytest.fail("refuse first"))
        _v6(monkeypatch)
        args = argparse.Namespace(wican="http://192.0.2.1", reboot=False)
        assert getattr(wican, handler)(args) == 2
        assert "cannot sync yet" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("info", "mode", "warned"),
        [
            (wf.UNKNOWN, "on", "did not identify"),
            (wf.UNKNOWN, "off", "firmware_check is off"),
            (V4, "on", None),
        ],
        ids=["unidentified", "check-off", "v4"],
    )
    def test_autopid_upload_warns_when_the_generation_is_unconfirmed(
        self, monkeypatch, capsys, info, mode, warned
    ):
        """Unknown used to pass silently; it warns, but still uploads (no lock-out)."""
        import canlib.config as cfg
        from canlib.commands import wican

        monkeypatch.setattr(cfg, "wican_model", lambda: "pro")
        monkeypatch.setattr(cfg, "firmware_check_mode", lambda: mode)
        monkeypatch.setattr(wf, "probe", lambda host, *, timeout: info)
        uploaded: list = []
        monkeypatch.setattr(wican, "upload_profile", lambda *a, **k: uploaded.append(a))
        monkeypatch.setattr(wican, "_generate", lambda args: ({}, {}))
        monkeypatch.setattr(wican, "to_device_format", lambda p, d: {"cars": [{"pids": []}]})
        args = argparse.Namespace(wican="http://192.0.2.1", reboot=False)
        assert wican._cmd_autopid_upload(args) == 0
        assert uploaded
        err = capsys.readouterr().err
        if warned:
            assert "could not confirm this WiCAN runs firmware 4.x" in err and warned in err
        else:
            assert err == ""


class TestStatusSurface:
    @pytest.fixture
    def gather(self, monkeypatch):
        import canlib.config as cfg
        from canlib.commands import status
        from canlib.transport.config import TransportConfig

        def run(
            ttype: str,
            info: FirmwareInfo,
            *,
            tcp: bool = True,
            mode: str = "on",
            cfg_body: dict | None = None,
            probed: list | None = None,
        ):
            cand = TransportConfig(type=ttype, host="192.0.2.1")
            monkeypatch.setattr(
                "canlib.transport.resolve_transport_candidates", lambda args: [cand]
            )
            monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
            monkeypatch.setattr(cfg, "firmware_check_mode", lambda: mode)

            def _probe(host, *, timeout):
                if probed is not None:
                    probed.append(host)
                return info

            monkeypatch.setattr(wf, "probe", _probe)
            monkeypatch.setattr(
                status,
                "_load_device_config",
                lambda *a: (
                    pytest.fail("no 4.x routes on v6") if info.generation == "v6" else cfg_body
                ),
            )
            monkeypatch.setattr(status, "_device_status", lambda *a: None)
            monkeypatch.setattr(status, "_tcp_open", lambda *a: tcp)
            return status._gather(argparse.Namespace(transport=None, wican=None))

        return run

    def test_wican_ws_on_v6_is_misconfigured(self, gather):
        info = gather("wican-ws", V6)
        assert info["exit"] == 2
        assert info["transport"]["usable"] is False
        assert info["firmware"]["generation"] == "v6"
        assert info["firmware"]["api_level"] == 6
        assert info["firmware"]["verdict"] == "refuse"
        assert info["device"] is None
        assert any("elm327-tcp" in e for e in info["errors"])

    def test_wican_ws_on_v6_with_the_check_off_still_says_why(self, gather):
        """It used to report "HTTP not reachable … on VPN?" although the probe got through."""
        info = gather("wican-ws", V6, mode="off")
        assert info["exit"] == 2
        assert info["firmware"]["checked"] is False
        assert info["firmware"]["verdict"] == "refuse"
        assert any("no /ws ELM327 terminal" in e for e in info["errors"])
        assert not any("not reachable" in e for e in info["errors"])
        assert any("firmware_check is off" in n for n in info["notes"])

    def test_slcan_on_v6_with_the_check_off_warns_too(self, gather):
        info = gather("slcan-tcp", V6, mode="off")
        assert info["exit"] == 0
        assert any("slcan0" in w for w in info["warnings"])

    def test_v4_config_route_failing_is_not_called_offline(self, gather):
        info = gather("wican-ws", V4, cfg_body=None)
        assert info["exit"] == 1
        assert any("answered the firmware probe but not /load_config" in e for e in info["errors"])

    def test_unknown_and_no_config_is_unreachable(self, gather):
        info = gather("wican-ws", wf.UNKNOWN, cfg_body=None)
        assert any("not reachable" in e for e in info["errors"])

    def test_slcan_on_v6_warns(self, gather):
        info = gather("slcan-tcp", V6)
        assert info["exit"] == 0
        assert info["transport"]["usable"] is True
        assert any("slcan0" in w for w in info["warnings"])

    def test_elm327_tcp_is_not_probed_by_default(self, gather):
        probed: list = []
        info = gather("elm327-tcp", V6, probed=probed)
        assert probed == []
        assert info["firmware"]["generation"] == "unknown"
        assert info["exit"] == 0

    def test_elm327_tcp_on_v6_is_a_note_when_opted_in(self, gather):
        info = gather("elm327-tcp", V6, mode="always")
        assert info["exit"] == 0
        assert any("AutoPID" in n for n in info["notes"])

    def test_render_shows_the_firmware_line(self, gather, capsys):
        from canlib.commands import status

        status._render(gather("wican-ws", V6))
        out = capsys.readouterr().out
        assert "6.0.0-beta.1" in out and "api_level 6" in out

    def test_render_shows_a_known_v4_firmware_line(self, gather, capsys):
        from canlib.commands import status

        body = {"protocol": "elm327", "port": 3333, "sleep_status": "disable"}
        info = gather(
            "wican-ws",
            FirmwareInfo("v4", version="4.52", build="v4.52p", hw_version="WiCAN-OBD-PRO"),
            cfg_body=body,
        )
        status._render(info)
        out = capsys.readouterr().out
        assert "WiCAN-OBD-PRO" in out and "4.52 (v4.52p)" in out
        assert "version    " not in out  # the firmware line replaces the legacy one
        assert "elm327" in out

    def test_render_known_firmware_without_config_says_config_unreachable(self, gather, capsys):
        from canlib.commands import status

        info = gather("slcan-tcp", V4, cfg_body=None)
        status._render(info)
        out = capsys.readouterr().out
        assert "4.52 (v4.52p)" in out
        assert "config" in out and "HTTP config API not reachable" in out
        assert "non-WiCAN gateway" not in out

    def test_render_marks_the_check_off(self, gather, capsys):
        from canlib.commands import status

        status._render(gather("slcan-tcp", V6, mode="off"))
        assert "firmware check off" in capsys.readouterr().out
