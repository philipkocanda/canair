"""Tests for the WiCAN Pro-only guards and command wiring in `canair wican`.

AutoPID profile sync (`autopid upload/download/diff`) and `mode set` require a
WiCAN Pro; on a classic (non-Pro) WiCAN they must be refused up front with a
clear message, without touching the device or the profile. `autopid write` and
`autopid stats` are local-only and work on any model.
"""

from __future__ import annotations

import pytest

import canlib.config as cfg_mod
from canlib.cli import build_parser
from canlib.commands import wican


def _classic(monkeypatch):
    monkeypatch.setattr(cfg_mod, "wican_model", lambda: "classic")


def _pro(monkeypatch):
    monkeypatch.setattr(cfg_mod, "wican_model", lambda: "pro")


def _parse(*argv):
    return build_parser().parse_args(list(argv))


class TestRequirePro:
    def test_pro_allows(self, monkeypatch):
        _pro(monkeypatch)
        assert wican._require_pro("autopid upload") is None

    def test_classic_blocks_with_message(self, monkeypatch, capsys):
        _classic(monkeypatch)
        rc = wican._require_pro("autopid upload")
        assert rc == 2
        err = capsys.readouterr().err
        assert "WiCAN Pro" in err
        assert "wican_model" in err
        assert "autopid write" in err  # points at the local-only escape hatch


class TestParserWiring:
    """The nested subcommands must dispatch to the right handler via run()."""

    def test_bare_wican_is_group_help(self):
        args = _parse("wican")
        assert args.func is wican.run
        assert args._wican_func is wican._group_help

    def test_bare_autopid_is_group_help(self):
        args = _parse("wican", "autopid")
        assert args._wican_func is wican._group_help

    def test_bare_mode_is_group_help(self):
        args = _parse("wican", "mode")
        assert args._wican_func is wican._group_help

    def test_autopid_write_dispatch(self):
        args = _parse("wican", "autopid", "write", "--verified-only")
        assert args.func is wican.run
        assert args._wican_func is wican._cmd_autopid_write
        assert args.verified_only is True

    def test_autopid_upload_dispatch(self):
        args = _parse("wican", "autopid", "upload", "--reboot")
        assert args._wican_func is wican._cmd_autopid_upload
        assert args.reboot is True

    def test_autopid_download_dispatch(self):
        args = _parse("wican", "autopid", "download")
        assert args._wican_func is wican._cmd_autopid_download

    def test_autopid_diff_dispatch(self):
        args = _parse("wican", "autopid", "diff")
        assert args._wican_func is wican._cmd_autopid_diff

    def test_autopid_stats_dispatch(self):
        args = _parse("wican", "autopid", "stats")
        assert args._wican_func is wican._cmd_autopid_stats

    def test_mode_show_dispatch(self):
        args = _parse("wican", "mode", "show")
        assert args._wican_func is wican._cmd_mode_show

    def test_mode_set_dispatch(self):
        args = _parse("wican", "mode", "set", "slcan")
        assert args._wican_func is wican._cmd_mode_set
        assert args.protocol == "slcan"

    def test_mode_set_rejects_unknown_protocol(self):
        with pytest.raises(SystemExit):
            _parse("wican", "mode", "set", "bogus")


class TestProGuards:
    """Device ops must bail out on a classic WiCAN before doing any work."""

    @pytest.mark.parametrize(
        "argv",
        [
            ("wican", "autopid", "upload"),
            ("wican", "autopid", "download"),
            ("wican", "autopid", "diff"),
        ],
    )
    def test_device_ops_refused_on_classic(self, monkeypatch, argv):
        _classic(monkeypatch)
        # If the guard fails to short-circuit, _generate()/download would run and
        # likely blow up — a clean exit code 2 proves we bailed out first.
        called = {"work": False}
        monkeypatch.setattr(wican, "_generate", lambda a: called.__setitem__("work", True))
        monkeypatch.setattr(
            wican, "download_profile", lambda *a, **k: called.__setitem__("work", True)
        )
        rc = wican.run(_parse(*argv))
        assert rc == 2
        assert called["work"] is False

    def test_mode_set_refused_on_classic(self, monkeypatch):
        _classic(monkeypatch)
        rc = wican.run(_parse("wican", "mode", "set", "slcan"))
        assert rc == 2


class TestLocalOps:
    """Local-only ops don't require a Pro and don't touch the device."""

    def test_write_generates_and_writes(self, monkeypatch, tmp_path):
        _classic(monkeypatch)  # local ops work regardless of model
        data = {"car_model": "X", "init": "ATZ", "ecus": {}}
        monkeypatch.setattr(wican, "load_yaml", lambda: data)
        monkeypatch.setattr(wican, "_profile_out", lambda: tmp_path / "autopid.json")

        class _Prof:
            ecus_dir = tmp_path

        monkeypatch.setattr("canlib.profile.active", lambda: _Prof())
        rc = wican.run(_parse("wican", "autopid", "write"))
        assert rc == 0
        assert (tmp_path / "autopid.json").exists()

    def test_write_honors_out_flag(self, monkeypatch, tmp_path):
        data = {"car_model": "X", "init": "ATZ", "ecus": {}}
        monkeypatch.setattr(wican, "load_yaml", lambda: data)

        class _Prof:
            ecus_dir = tmp_path

        monkeypatch.setattr("canlib.profile.active", lambda: _Prof())
        out = tmp_path / "custom.json"
        rc = wican.run(_parse("wican", "autopid", "write", "--out", str(out)))
        assert rc == 0
        assert out.exists()
