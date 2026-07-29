"""Tests for `canair states` — vocabulary listing + edit dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import canlib.commands.states as states_cmd
from canlib.states import StateRule


class _FakeProfile:
    name = "testcar"
    states_file = Path("/tmp/testcar/vehicle_states.yaml")


@pytest.fixture
def _patched(monkeypatch):
    rules = [
        StateRule("CHARGING", "HV charging", predicate=object(), expr="BMS.CUR < -1"),
        StateRule("READY", "Driveable", predicate=object(), expr="VCU.RDY == 1"),
        StateRule("SLEEP", "Standby"),
        StateRule("ALL", "Every state"),
    ]
    usage = {"CHARGING": 12, "READY": 30, "SLEEP": 0, "ALL": 0, "FOB PRESENT": 1}
    monkeypatch.setattr(states_cmd, "_use_color", lambda: False)
    monkeypatch.setattr("canlib.states.load_states", lambda profile=None: rules)
    monkeypatch.setattr(states_cmd, "_load_usage", lambda: usage)
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())


def _run(**kw):
    base = {"json": False, "_states_func": None}
    base.update(kw)
    return states_cmd.run(argparse.Namespace(**base))


def test_human_output(_patched, capsys):
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Vehicle states" in out
    assert "CHARGING" in out and "HV charging" in out
    # predicate shown under an auto-suggested state
    assert "when: BMS.CUR < -1" in out
    assert "source:" in out


def test_undeclared_surfaced(_patched, capsys):
    _run()
    out = capsys.readouterr().out
    # FOB PRESENT is used in ecus/ but absent from the vocabulary.
    assert "Undeclared tokens" in out
    assert "FOB PRESENT" in out


def test_json(_patched, capsys):
    rc = _run(json=True)
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    uses = {s["name"]: s["uses"] for s in data["states"]}
    assert uses == {"CHARGING": 12, "READY": 30, "SLEEP": 0, "ALL": 0}
    whens = {s["name"]: s["when"] for s in data["states"]}
    assert whens["CHARGING"] == "BMS.CUR < -1"
    assert whens["SLEEP"] is None
    assert [u["name"] for u in data["undeclared"]] == ["FOB PRESENT"]


def test_no_color_when_piped(_patched, capsys):
    _run()
    assert "\033[" not in capsys.readouterr().out


def test_empty_vocabulary(monkeypatch, capsys):
    monkeypatch.setattr(states_cmd, "_use_color", lambda: False)
    monkeypatch.setattr("canlib.states.load_states", lambda profile=None: [])
    monkeypatch.setattr(states_cmd, "_load_usage", lambda: {})
    monkeypatch.setattr("canlib.profile.active", lambda: _FakeProfile())
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0
    assert "No vehicle states declared" in out


class TestEditDispatch:
    """The edit subcommands delegate to states_edit and print a confirmation."""

    def test_add_dispatch(self, monkeypatch, capsys, tmp_path):
        called = {}

        def _add(name, *, description=None, when=None, profile=None):
            called.update(name=name, description=description, when=when)
            return tmp_path / "vehicle_states.yaml"

        monkeypatch.setattr("canlib.states_edit.add_state", _add)
        monkeypatch.setattr(states_cmd, "_use_color", lambda: False)
        args = argparse.Namespace(
            name="precondition", description="d", when=None, _states_func=states_cmd.cmd_add
        )
        rc = states_cmd.run(args)
        assert rc == 0
        assert called == {"name": "precondition", "description": "d", "when": None}
        assert "added state PRECONDITION" in capsys.readouterr().out

    def test_add_error_is_clean(self, monkeypatch):
        from canlib.states_edit import StatesEditError

        def _add(*a, **k):
            raise StatesEditError("already exists")

        monkeypatch.setattr("canlib.states_edit.add_state", _add)
        monkeypatch.setattr(states_cmd, "_use_color", lambda: False)
        args = argparse.Namespace(
            name="READY", description=None, when=None, _states_func=states_cmd.cmd_add
        )
        with pytest.raises(SystemExit) as exc:
            states_cmd.run(args)
        assert "already exists" in str(exc.value)
