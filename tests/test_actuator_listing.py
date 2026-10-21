"""`canair io` / `canair routines` --json listings read the profile only.

Regression: the listing ran inside the live runtime, after transport selection,
so it failed with "can't reach the WiCAN" when no device was online — and took
the device lock — even though it never talks to the bus.
"""

from __future__ import annotations

import argparse
import json

import pytest

import canlib
from canlib.commands import io as io_cmd
from canlib.commands import routines as routines_cmd

PROFILE = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "iocontrol": {"6900": {"label": "Mirrors", "on": "30690001", "off": "30690000"}},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(canlib, "load_pids", lambda *a, **k: PROFILE)

    def _no_device(_args):
        raise AssertionError("a --json listing must not start a live session")

    monkeypatch.setattr(io_cmd, "run_live", _no_device)
    monkeypatch.setattr(routines_cmd, "run_live", _no_device)


def test_io_json_lists_without_the_device(capsys):
    rc = io_cmd.run(argparse.Namespace(iocontrol="BCM", did=None, json=True))
    assert rc == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["iocontrol"]["6900"]["session_mode"] == "03"


def test_routines_json_lists_without_the_device(capsys):
    rc = routines_cmd.run(argparse.Namespace(routines="BCM", rid=None, json=True))
    assert rc == 0
    assert "12A1" in capsys.readouterr().out
