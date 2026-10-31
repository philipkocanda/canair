"""Every hand-edited profile file reports a key written twice, which YAML loads as the later one.

The ecus/ files are covered in ``test_validate_pids.py``; this drives each other ``validate``
target over a copy of the synthetic ``variants`` fixture profile.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from canlib import config
from canlib.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "profiles" / "variants"

SIGNALS = """messages:
  '0x5C5':
    signals:
      ODOMETER: {start_bit: 15, length: 24, byte_order: big}
      ODOMETER: {start_bit: 15, length: 24, byte_order: little}
"""

ACTIONS = """actions:
  sleep:
    description: Sleep.
    description: Sleep again.
"""

CAN_INDEX = "logs: []\nlogs: []\n"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    config.load_config.cache_clear()
    yield
    config.load_config.cache_clear()


@pytest.fixture
def copy(tmp_path) -> Path:
    dest = tmp_path / "variants"
    shutil.copytree(FIXTURE, dest)
    return dest


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize(
    ("target", "edit", "key"),
    [
        ("pids", lambda r: _append(r / "profile.yaml", 'car_model: "again"\n'), "car_model"),
        ("states", lambda r: _append(r / "vehicle_states.yaml", "states: []\n"), "states"),
        ("groups", lambda r: _append(r / "groups.yaml", "groups: {}\n"), "groups"),
        ("can-buses", lambda r: _append(r / "can_buses.yaml", "can_buses: {}\n"), "can_buses"),
        ("variants", lambda r: _append(r / "variants.yaml", "variants: {}\n"), "variants"),
        ("signals", lambda r: _write(r / "signals" / "CAR-CAN.yaml", SIGNALS), "ODOMETER"),
        ("actions", lambda r: _write(r / "actions.yaml", ACTIONS), "description"),
        ("can", lambda r: _write(r / "captures" / "can" / "index.yaml", CAN_INDEX), "logs"),
    ],
)
def test_a_repeated_key_fails_validation(copy, capsys, target, edit, key):
    assert main(["--profile", str(copy), "validate", target]) == 0
    capsys.readouterr()
    edit(copy)
    assert main(["--profile", str(copy), "validate", target]) == 1
    assert f"duplicate key '{key}'" in capsys.readouterr().out
