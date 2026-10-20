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
        assert len(seen) == 1

    def test_foreign_401_is_unknown(self, device):
        device({"/api/info": FakeResponse(401, headers={"WWW-Authenticate": 'Basic realm="x"'})})
        assert probe("192.0.2.1", timeout=1.0).generation == "unknown"

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
        monkeypatch.setattr(cfg, "firmware_check", lambda: False)
        assert wf.detect("192.0.2.1").generation == "unknown"
        assert seen == []

    def test_uses_the_connect_timeout(self, monkeypatch):
        import canlib.config as cfg

        got = {}

        def fetch(url, timeout):
            got["timeout"] = timeout
            raise requests.ConnectionError

        monkeypatch.setattr(wf, "_fetch", fetch)
        monkeypatch.setattr(cfg, "firmware_check", lambda: True)
        monkeypatch.setattr(cfg, "fallback_settings", lambda: (True, 7.5, None))
        wf.detect("192.0.2.1")
        assert got["timeout"] == 7.5


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

    def test_autopid_refusal_explains_the_data_loss(self):
        assert "wipe" in compatibility(V6, "autopid-sync").message

    @pytest.mark.parametrize(
        ("version", "level", "word"),
        [
            ("4.50", "ok", None),
            ("4.51", "ok", None),
            ("4.52", "ok", None),
            ("4.53", "warn", "newer"),
            ("5.00", "warn", "newer"),
            ("4.49", "warn", "older"),
            ("0.00", "warn", "older"),
        ],
    )
    def test_v4_supported_range(self, version, level, word):
        verdict = compatibility(FirmwareInfo("v4", version=version), "slcan-tcp")
        assert verdict.level == level
        if word:
            assert word in verdict.message and "4.50–4.52" in verdict.message

    def test_v4_range_warning_never_blocks_a_reboot(self):
        assert compatibility(FirmwareInfo("v4", version="4.60"), "reboot").level == "ok"

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

    def test_is_a_known_config_key(self):
        from canlib.commands.config import _KNOWN_KEYS

        assert "transport.firmware_check" in _KNOWN_KEYS


# --- the surfaces --------------------------------------------------------------


def _v6(monkeypatch, info: FirmwareInfo = V6):
    monkeypatch.setattr(wf, "detect", lambda host, *, timeout=None: info)


class TestRebootRegression:
    def test_reboot_on_405_reports_failure(self, monkeypatch, capsys):
        from canlib import terminal

        monkeypatch.setattr(
            terminal._requests_mod, "post", lambda *a, **k: FakeResponse(405), raising=True
        )
        assert terminal.reboot_wican("192.0.2.1") is False
        captured = capsys.readouterr()
        assert "FAILED" in captured.err and "405" in captured.err
        assert "Rebooting" not in captured.out

    def test_reboot_on_200_succeeds(self, monkeypatch, capsys):
        from canlib import terminal

        monkeypatch.setattr(terminal._requests_mod, "post", lambda *a, **k: FakeResponse(200))
        assert terminal.reboot_wican("192.0.2.1") is True
        assert "Rebooting" in capsys.readouterr().out

    def test_reboot_skipped_on_v6(self, monkeypatch, capsys):
        from canlib import terminal

        _v6(monkeypatch)
        monkeypatch.setattr(
            terminal._requests_mod, "post", lambda *a, **k: pytest.fail("must not POST on v6")
        )
        assert terminal.reboot_wican("192.0.2.1") is False
        assert "skipping --reboot" in capsys.readouterr().err

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


class TestLiveSurfaces:
    def test_wican_ws_connect_refused_on_v6(self, monkeypatch, capsys):
        from canlib.commands._live import CANAIR_DEFAULTS, runtime
        from canlib.transport.config import TransportConfig

        cand = TransportConfig(type="wican-ws", host="192.0.2.1")
        monkeypatch.setattr("canlib.transport.resolve_transport_candidates", lambda args: [cand])
        monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
        monkeypatch.setattr(runtime, "load_pids", lambda: {"init": "ATZ;"})
        monkeypatch.setattr(
            "canlib.wican_mode.require_ws_reachable",
            lambda *a, **k: pytest.fail("refusal must come before the reachability check"),
        )
        _v6(monkeypatch)

        args = argparse.Namespace(**CANAIR_DEFAULTS)
        rc = asyncio.run(runtime.async_main(args))
        assert rc == 2
        err = capsys.readouterr().err
        assert "no /ws ELM327 terminal" in err and "elm327-tcp" in err

    def _raw(self, monkeypatch):
        from canlib import wican_mode
        from canlib.modes import raw_monitor

        calls: list = []
        monkeypatch.setattr(
            wican_mode, "require_protocol", lambda *a, **k: calls.append("require_protocol")
        )
        monkeypatch.setattr(wican_mode, "require_slcan_reachable", lambda *a, **k: None)

        async def _mon(*a, **k):
            calls.append("monitor")
            return 0

        monkeypatch.setattr(raw_monitor, "run_raw_monitor", _mon)
        return calls

    class _T:
        host = "192.0.2.1"

        def resolve_device_defaults(self, profile_bitrate=None):
            return 3333, 500000

    def test_slcan_on_v6_warns_and_skips_the_mode_check(self, monkeypatch, capsys):
        from canlib.modes import raw_ops

        calls = self._raw(monkeypatch)
        _v6(monkeypatch)
        args = argparse.Namespace(multi=["query BMS"], monitor=1.0, verbose=False, unsafe=False)
        rc = asyncio.run(raw_ops.run_raw(args, self._T(), {}))
        assert rc == 0
        assert calls == ["monitor"]  # require_protocol skipped
        assert "slcan0" in capsys.readouterr().err

    def test_slcan_on_v4_still_checks_the_mode(self, monkeypatch, capsys):
        from canlib.modes import raw_ops

        calls = self._raw(monkeypatch)
        _v6(monkeypatch, V4)
        args = argparse.Namespace(multi=["query BMS"], monitor=1.0, verbose=False, unsafe=False)
        asyncio.run(raw_ops.run_raw(args, self._T(), {}))
        assert calls == ["require_protocol", "monitor"]
        assert capsys.readouterr().err == ""

    def test_sniff_listen_only_refused_on_v6(self, monkeypatch, capsys):
        from canlib.commands import sniff

        monkeypatch.setattr("canlib.transport.resolve_transport", lambda args: self._T())
        monkeypatch.setattr(
            "canlib.lock.WiCANLock.acquire",
            lambda *a, **k: pytest.fail("must refuse before taking the device"),
        )
        _v6(monkeypatch)
        args = argparse.Namespace(
            wican=None, listen_only=True, filter=None, duration=None, save=None, force=False
        )
        assert sniff.run(args) == 2
        assert "can_manager.silent" in capsys.readouterr().err


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
        assert "wipe" in capsys.readouterr().err


class TestStatusSurface:
    @pytest.fixture
    def gather(self, monkeypatch):
        from canlib.commands import status
        from canlib.transport.config import TransportConfig

        def run(ttype: str, info: FirmwareInfo, *, tcp: bool = True):
            cand = TransportConfig(type=ttype, host="192.0.2.1")
            monkeypatch.setattr(
                "canlib.transport.resolve_transport_candidates", lambda args: [cand]
            )
            monkeypatch.setattr("canlib.transport.select_reachable_transport", lambda c, **k: c[0])
            monkeypatch.setattr(wf, "probe", lambda host, *, timeout: info)
            monkeypatch.setattr(
                status,
                "_load_device_config",
                lambda *a: pytest.fail("no 4.x routes on v6") if info is V6 else None,
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

    def test_slcan_on_v6_warns(self, gather):
        info = gather("slcan-tcp", V6)
        assert info["exit"] == 0
        assert info["transport"]["usable"] is True
        assert any("slcan0" in w for w in info["warnings"])

    def test_elm327_tcp_on_v6_is_a_note(self, gather):
        info = gather("elm327-tcp", V6)
        assert info["exit"] == 0
        assert any("AutoPID" in n for n in info["notes"])

    def test_render_shows_the_firmware_line(self, gather, capsys):
        from canlib.commands import status

        status._render(gather("wican-ws", V6))
        out = capsys.readouterr().out
        assert "6.0.0-beta.1" in out and "api_level 6" in out
