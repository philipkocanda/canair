"""Tests for `canair bus` — CAN bus listing, ECU counts, JSON, TTY coloring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import canlib.commands.bus as bus
from canlib.can_buses import BusDef


class _FakeProfile:
    name = "testcar"
    can_buses_file = Path("/tmp/testcar/can_buses.yaml")


@pytest.fixture
def _patched(monkeypatch):
    buses = [
        BusDef("B-CAN", "Body CAN", "Comfort/body electronics.", bitrate=100000),
        BusDef("P-CAN", "Powertrain CAN", "Drivetrain.", bitrate=500000),
        BusDef("ALL", "All segments", "Gateway bridges all."),
    ]
    ecus = {
        0x7A0: {"name": "BCM", "can_bus": ["B-CAN"]},
        0x7E4: {"name": "BMS", "can_bus": ["P-CAN"]},
        0x7E3: {"name": "MCU", "can_bus": ["P-CAN", "H-CAN"]},  # spans P + undeclared H
        0x7D2: {"name": "SRS"},  # unbussed
    }
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("canlib.can_buses.load_can_buses", lambda prof=None: buses)
    monkeypatch.setattr("canlib.ecus.load_ecus", lambda path=None: ecus)
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())


def _run(**kw):
    base = {"json": False}
    base.update(kw)
    return bus.run(argparse.Namespace(**base))


def test_human_output(_patched, capsys):
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Body CAN" in out
    assert "Comfort/body electronics." in out
    # ECU counts: B has 1 (BCM), P has 2 (BMS, MCU).
    assert "source:" in out
    assert "can_buses.yaml" in out
    # bitrate rendered as a human-readable bus speed; unset shows the em dash.
    assert "100 kbit/s" in out
    assert "500 kbit/s" in out


def test_undeclared_and_unbussed(_patched, capsys):
    _run()
    out = capsys.readouterr().out
    assert "Undeclared codes" in out  # H-CAN used by MCU but not declared
    assert "1 ECU(s) have no can_bus set." in out  # SRS


def test_json(_patched, capsys):
    rc = _run(json=True)
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    counts = {b["code"]: b["ecus"] for b in data["buses"]}
    assert counts == {"B-CAN": 1, "P-CAN": 2, "ALL": 0}
    rates = {b["code"]: b["bitrate"] for b in data["buses"]}
    assert rates == {"B-CAN": 100000, "P-CAN": 500000, "ALL": None}
    assert data["unbussed_ecus"] == 1
    assert [u["code"] for u in data["undeclared"]] == ["H-CAN"]


def test_gateway_all_counted_on_every_segment(monkeypatch, capsys):
    # An ECU tagged ALL is counted on every declared segment (incl. the
    # standalone ALL row), not just an ALL row.
    buses = [
        BusDef("ALL", "All segments", "Gateway."),
        BusDef("B-CAN", "Body CAN", "Body.", bitrate=100000),
        BusDef("D-CAN", "Diagnostic CAN", "Diag.", bitrate=500000),
    ]
    ecus = {
        0x770: {"name": "IGPM", "can_bus": ["ALL"]},  # gateway
        0x7A0: {"name": "BCM", "can_bus": ["B-CAN"]},
    }
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("canlib.can_buses.load_can_buses", lambda prof=None: buses)
    monkeypatch.setattr("canlib.ecus.load_ecus", lambda path=None: ecus)
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())

    rc = _run(json=True)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    counts = {b["code"]: b["ecus"] for b in data["buses"]}
    # ALL row = the gateway itself; B-CAN = BCM + gateway; D-CAN = just gateway.
    assert counts == {"ALL": 1, "B-CAN": 2, "D-CAN": 1}
    assert data["gateway_ecus"] == 1


def test_no_color_when_piped(_patched, capsys):
    _run()
    out = capsys.readouterr().out
    assert "\033[" not in out  # NO_COLOR set → no ANSI escapes
