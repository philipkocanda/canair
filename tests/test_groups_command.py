"""Tests for `canair groups` — group listing (human + JSON) and edit wiring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import canlib.commands.groups as groups_cmd
from canlib.ecu_groups import Group


class _FakeProfile:
    name = "testcar"
    groups_file = Path("/tmp/testcar/groups.yaml")


@pytest.fixture
def _patched(monkeypatch):
    groups = {
        "charging": Group("charging", "SoC while plugged", ("BMS:2101", "OBC")),
        "driving": Group("driving", "", ("BMS", "VCU", "MCU")),
    }
    monkeypatch.setattr(groups_cmd, "_use_color", lambda: False)
    monkeypatch.setattr("canlib.ecu_groups.load_groups", lambda profile=None: groups)
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())


def _run(**kw):
    base = {"json": False, "_groups_func": None}
    base.update(kw)
    return groups_cmd.run(argparse.Namespace(**base))


def test_human_output(_patched, capsys):
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "@charging" in out
    assert "SoC while plugged" in out
    assert "BMS:2101 OBC" in out
    assert "groups.yaml" in out


def test_json_output(_patched, capsys):
    rc = _run(json=True)
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    names = {g["name"]: g["members"] for g in data["groups"]}
    assert names == {"charging": ["BMS:2101", "OBC"], "driving": ["BMS", "VCU", "MCU"]}


def test_no_color_when_piped(_patched, capsys):
    _run()
    assert "\033[" not in capsys.readouterr().out


def test_empty_profile_message(monkeypatch, capsys):
    monkeypatch.setattr(groups_cmd, "_use_color", lambda: False)
    monkeypatch.setattr("canlib.ecu_groups.load_groups", lambda profile=None: {})
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "No selector groups" in out
