"""``--wican`` resolution for the device commands (``canair wican …``).

``canair wican`` used to resolve ``--wican`` as "the alias, else the *default
device*", so a plain IP or a typo silently became the default WiCAN — and
``wican mode set`` then rewrote the config of, and rebooted, a device the user
never named. It must resolve exactly like the live commands' ``--wican``.
"""

from __future__ import annotations

import argparse

import pytest

from canlib.wican.api import resolve_wican_url

_CONFIG = {
    "devices": {"home": {"host": "10.0.0.5"}, "vpn": {"host": "10.8.0.2", "port": 3333}},
    "default_wican": "home",
}


@pytest.fixture
def config(monkeypatch):
    import canlib.config as cfg

    def install(data: dict):
        monkeypatch.setattr(cfg, "load_config", lambda: data)

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
        from canlib.transport.config import resolve_transport_candidates

        live = resolve_transport_candidates(argparse.Namespace(wican=value, no_fallback=True))
        assert resolve_wican_url(value) == f"http://{live[0].host}"


class TestWicanCommands:
    def test_parser_default_is_unset(self):
        from canlib.cli import build_parser

        args = build_parser().parse_args(["wican", "mode", "show"])
        assert args.wican is None

    @pytest.mark.parametrize(
        ("value", "url"),
        [("192.0.2.7", "http://192.0.2.7"), ("vpn", "http://10.8.0.2"), (None, "http://10.0.0.5")],
    )
    def test_mode_set_targets_the_named_device(self, config, monkeypatch, value, url):
        import canlib.config as cfg
        import canlib.wican.firmware as wf
        from canlib.commands import wican
        from canlib.wican import mode as wican_mode

        monkeypatch.setattr(cfg, "wican_model", lambda: "pro")
        monkeypatch.setattr(wf, "detect", lambda *a, **k: wf.FirmwareInfo("v4", version="4.52"))
        seen: list[str] = []
        monkeypatch.setattr(
            wican_mode, "current_protocol", lambda base, **k: seen.append(base) or "slcan"
        )
        monkeypatch.setattr(
            wican_mode, "set_protocol", lambda *a, **k: pytest.fail("already in the mode")
        )
        args = argparse.Namespace(wican=value, protocol="slcan", yes=True, no_transport=True)
        assert wican._cmd_mode_set(args) == 0
        assert seen == [url]
