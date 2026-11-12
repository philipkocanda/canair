"""``--wican`` resolution for the device commands (``canair wican …``).

``canair wican`` used to resolve ``--wican`` as "the alias, else the *default
device*", so a plain IP or a typo silently became the default WiCAN — and
``wican mode set`` then rewrote the config of, and rebooted, a device the user
never named. It must resolve exactly like the live commands' ``--wican``.
"""

from __future__ import annotations

import argparse

import pytest

from canair.device.wican import api as wican_api
from canair.device.wican.api import resolve_wican_url

_CONFIG = {
    "devices": {"home": {"host": "10.0.0.5"}, "vpn": {"host": "10.8.0.2", "port": 3333}},
    "default_wican": "home",
}


@pytest.fixture
def config(monkeypatch):
    from canair.config import devices as cfg_devices
    from canair.config import file as cfg_file

    def install(data: dict):
        monkeypatch.setattr(cfg_file, "load_config", lambda: data)
        monkeypatch.setattr(wican_api, "load_config", lambda: data)
        monkeypatch.setattr(cfg_devices, "load_config", lambda: data)

    install(_CONFIG)
    return install


class TestResolveWicanUrl:
    def test_alias_maps_to_its_host(self, config):
        assert resolve_wican_url("vpn") == "http://10.8.0.2"

    def test_a_plain_ip_is_itself_not_the_default_device(self, config):
        assert resolve_wican_url("192.0.2.7") == "http://192.0.2.7"

    def test_a_typo_is_taken_as_a_host_not_swapped_for_the_default(self, config):
        assert resolve_wican_url("hmoe") == "http://hmoe"

    def test_a_url_passes_through(self, config):
        assert resolve_wican_url("http://192.0.2.7:8080/") == "http://192.0.2.7:8080"

    def test_no_value_is_the_default_device(self, config):
        assert resolve_wican_url(None) == "http://10.0.0.5"

    def test_no_value_prefers_transport_host(self, config):
        config({**_CONFIG, "transport": {"host": "192.0.2.9"}})
        assert resolve_wican_url(None) == "http://192.0.2.9"

    @pytest.mark.parametrize("value", ["vpn", "192.0.2.7", "hmoe", None])
    def test_matches_the_live_commands_host(self, config, value):
        """One semantics: the host a live command would connect to is the one we call."""
        from canair.transport.config import TransportOverrides, resolve_transport_candidates

        live = resolve_transport_candidates(TransportOverrides(wican=value, no_fallback=True))
        assert resolve_wican_url(value) == f"http://{live[0].host}"


class TestWicanCommands:
    def test_parser_default_is_unset(self):
        from canair.cli import build_parser

        args = build_parser().parse_args(["wican", "mode", "show"])
        assert args.wican is None

    @pytest.mark.parametrize(
        ("value", "url"),
        [("192.0.2.7", "http://192.0.2.7"), ("vpn", "http://10.8.0.2"), (None, "http://10.0.0.5")],
    )
    def test_mode_set_targets_the_named_device(self, config, monkeypatch, value, url):
        import canair.device.wican.firmware as wf
        from canair.cli.commands.wican import mode as mode_cmd
        from canair.config import devices as cfg_devices

        monkeypatch.setattr(cfg_devices, "wican_model", lambda: "pro")
        monkeypatch.setattr(wf, "detect", lambda *a, **k: wf.FirmwareInfo("v4", version="4.52"))
        monkeypatch.setattr(
            "canair.cli.commands.wican.common.detect",
            lambda *a, **k: wf.FirmwareInfo("v4", version="4.52"),
        )
        seen: list[str] = []
        monkeypatch.setattr(
            mode_cmd, "current_protocol", lambda base, **k: seen.append(base) or "slcan"
        )
        monkeypatch.setattr(
            mode_cmd, "set_protocol", lambda *a, **k: pytest.fail("already in the mode")
        )
        monkeypatch.setattr("canair.transport.fallback.tcp_open", lambda h, p, t: True)
        args = argparse.Namespace(
            wican=value, no_fallback=False, protocol="slcan", yes=True, no_transport=True
        )
        assert mode_cmd.cmd_mode_set(args) == 0
        assert seen == [url]


class TestResolveDeviceUrl:
    """``wican`` device commands: only the implicit default falls back, as live commands do."""

    @pytest.fixture
    def probe(self, config, monkeypatch):
        from canair.transport import config as transport_config
        from canair.transport import fallback

        config(_CONFIG)
        monkeypatch.setattr(transport_config, "load_config", lambda: _CONFIG)
        monkeypatch.setattr(transport_config, "_is_pro", lambda: True)
        up: set[str] = set()
        asked: list[str] = []

        def tcp_open(host, port, timeout):
            asked.append(host)
            return host in up

        monkeypatch.setattr(fallback, "tcp_open", tcp_open)
        return up, asked

    @staticmethod
    def _args(**kw):
        return argparse.Namespace(**{"wican": None, "no_fallback": False, **kw})

    def test_default_device_up_is_used_without_a_note(self, probe, capsys):
        from canair.cli.commands.wican.common import resolve_device_url

        probe[0].update({"10.0.0.5", "10.8.0.2"})
        assert resolve_device_url(self._args()) == "http://10.0.0.5"
        assert capsys.readouterr().err == ""

    def test_default_device_down_falls_back_with_a_note(self, probe, capsys):
        from canair.cli.commands.wican.common import resolve_device_url

        probe[0].add("10.8.0.2")
        assert resolve_device_url(self._args()) == "http://10.8.0.2"
        assert "falling back" in capsys.readouterr().err

    def test_nothing_answers_keeps_the_default_for_the_normal_error(self, probe):
        from canair.cli.commands.wican.common import resolve_device_url

        assert resolve_device_url(self._args()) == "http://10.0.0.5"

    def test_an_explicit_device_is_never_swapped_or_probed(self, probe):
        from canair.cli.commands.wican.common import resolve_device_url

        probe[0].add("10.8.0.2")
        assert resolve_device_url(self._args(wican="home")) == "http://10.0.0.5"
        assert probe[1] == []

    def test_no_fallback_keeps_the_default(self, probe):
        from canair.cli.commands.wican.common import resolve_device_url

        probe[0].add("10.8.0.2")
        assert resolve_device_url(self._args(no_fallback=True)) == "http://10.0.0.5"
        assert probe[1] == []

    def test_a_device_without_an_http_api_is_not_a_fallback(self, config, probe, monkeypatch):
        from canair.cli.commands.wican.common import resolve_device_url
        from canair.transport import config as transport_config

        cfg = {
            "devices": {
                "home": {"host": "10.0.0.5"},
                "adapter": {"host": "10.0.0.7", "transport": "elm327-tcp"},
            },
            "default_wican": "home",
        }
        config(cfg)
        monkeypatch.setattr(transport_config, "load_config", lambda: cfg)
        probe[0].add("10.0.0.7")
        assert resolve_device_url(self._args()) == "http://10.0.0.5"

    def test_a_transport_misconfiguration_does_not_block_an_http_command(
        self, probe, config, monkeypatch
    ):
        from canair.cli.commands.wican.common import resolve_device_url
        from canair.transport import config as transport_config

        cfg = {**_CONFIG, "transport": {"type": "bogus"}}
        config(cfg)
        monkeypatch.setattr(transport_config, "load_config", lambda: cfg)
        assert resolve_device_url(self._args()) == "http://10.0.0.5"

    def test_parser_offers_no_fallback(self):
        from canair.cli import build_parser

        for argv in (["wican", "mode", "set", "slcan"], ["wican", "autopid", "download"]):
            assert build_parser().parse_args([*argv, "--no-fallback"]).no_fallback is True
