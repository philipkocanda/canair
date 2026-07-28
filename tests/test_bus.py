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
        BusDef("B", "Body CAN", "Comfort/body electronics."),
        BusDef("P", "Powertrain CAN", "Drivetrain."),
        BusDef("All", "All segments", "Gateway bridges all."),
    ]
    ecus = {
        0x7A0: {"name": "BCM", "can_bus": ["B"]},
        0x7E4: {"name": "BMS", "can_bus": ["P"]},
        0x7E3: {"name": "MCU", "can_bus": ["P", "H"]},  # spans P + undeclared H
        0x7D2: {"name": "SRS"},  # unbussed
    }
    monkeypatch.setattr(bus, "_use_color", lambda: False)
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


def test_undeclared_and_unbussed(_patched, capsys):
    _run()
    out = capsys.readouterr().out
    assert "Undeclared codes" in out  # H used by MCU but not declared
    assert "1 ECU(s) have no can_bus set." in out  # SRS


def test_json(_patched, capsys):
    rc = _run(json=True)
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    counts = {b["code"]: b["ecus"] for b in data["buses"]}
    assert counts == {"B": 1, "P": 2, "All": 0}
    assert data["unbussed_ecus"] == 1
    assert [u["code"] for u in data["undeclared"]] == ["H"]


def test_no_color_when_piped(_patched, capsys):
    _run()
    out = capsys.readouterr().out
    assert "\033[" not in out  # _use_color() False → no ANSI escapes
