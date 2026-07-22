"""Tests for the WiCAN Pro-only guards in `canair wican`.

AutoPID vehicle-profile sync (--upload/--download/--diff) and --set-protocol
require a WiCAN Pro; on a classic (non-Pro) WiCAN they must be refused up front
with a clear message, without touching the device or the profile.
"""

from __future__ import annotations

import argparse

import pytest

import canlib.config as cfg_mod
from canlib.commands import wican


def _classic(monkeypatch):
    monkeypatch.setattr(cfg_mod, "wican_model", lambda: "classic")


def _pro(monkeypatch):
    monkeypatch.setattr(cfg_mod, "wican_model", lambda: "pro")


class TestRequirePro:
    def test_pro_allows(self, monkeypatch):
        _pro(monkeypatch)
        assert wican._require_pro("--upload") is None

    def test_classic_blocks_with_message(self, monkeypatch, capsys):
        _classic(monkeypatch)
        rc = wican._require_pro("--upload")
        assert rc == 2
        err = capsys.readouterr().err
        assert "WiCAN Pro" in err
        assert "wican_model" in err


def _args(**kw):
    base = {
        "set_protocol": None,
        "download": False,
        "diff": False,
        "upload": False,
        "stats": False,
        "verified_only": False,
        "no_write": False,
        "reboot": False,
        "wican": "ap",
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestRunGuards:
    @pytest.mark.parametrize("flag", ["download", "diff", "upload"])
    def test_device_ops_refused_on_classic(self, monkeypatch, flag):
        _classic(monkeypatch)
        # If the guard fails to short-circuit, active()/load_yaml() would run and
        # likely blow up — a clean exit code 2 proves we bailed out first.
        called = {"profile": False}
        monkeypatch.setattr(
            wican, "_profile_out", lambda: called.__setitem__("profile", True)
        )
        rc = wican.run(_args(**{flag: True}))
        assert rc == 2
        assert called["profile"] is False

    def test_set_protocol_refused_on_classic(self, monkeypatch):
        _classic(monkeypatch)
        rc = wican.run(_args(set_protocol="slcan"))
        assert rc == 2
