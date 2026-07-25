"""Stage-4 tests: signals/ editor, `canair signals`, and DBC import/export.

Broadcast signal definitions (domain B): the DBC-compatible linear `signals/`
model, its surgical validated editor, and cantools-backed DBC interop. DBC
fixtures are built in-test via cantools (no committed binary/DBC fixture).
"""

from __future__ import annotations

import argparse

import pytest

import canlib.profile as profile
from canlib import signals_edit
from canlib.commands import validate
from canlib.profile import Profile


@pytest.fixture
def temp_profile(tmp_path):
    profile._active = Profile(name="test", root=tmp_path)
    return Profile(name="test", root=tmp_path)


# ── signals_edit ──────────────────────────────────────────────────────────


class TestSignalsEdit:
    def test_upsert_creates_and_orders(self, temp_profile):
        p = signals_edit.upsert_signal(
            "powertrain",
            "0x386",
            "WHL_SPD_FL",
            start_bit=0,
            length=14,
            byte_order="little",
            scale=0.03125,
            unit="km/h",
            msg_name="WHL_SPD11",
            tx_ecu="ESC",
            verified=False,
            profile=temp_profile,
        )
        assert p.exists()
        import yaml

        data = yaml.safe_load(p.read_text())
        sig = data["messages"]["0x386"]["signals"]["WHL_SPD_FL"]
        assert sig["start_bit"] == 0 and sig["length"] == 14
        assert sig["scale"] == 0.03125 and sig["unit"] == "km/h"
        assert data["messages"]["0x386"]["name"] == "WHL_SPD11"
        assert validate._run_signals() == 0

    def test_int_arb_id_normalized(self, temp_profile):
        signals_edit.upsert_signal("b", 0x220, "S", start_bit=0, length=8, profile=temp_profile)
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "b.yaml").read_text())
        assert "0x220" in data["messages"]

    def test_second_upsert_same_message(self, temp_profile):
        signals_edit.upsert_signal("b", "0x386", "FL", start_bit=0, length=14, profile=temp_profile)
        signals_edit.upsert_signal(
            "b", "0x386", "FR", start_bit=16, length=14, profile=temp_profile
        )
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "b.yaml").read_text())
        assert set(data["messages"]["0x386"]["signals"]) == {"FL", "FR"}

    def test_remove(self, temp_profile):
        signals_edit.upsert_signal("b", "0x386", "FL", start_bit=0, length=14, profile=temp_profile)
        signals_edit.upsert_signal(
            "b", "0x386", "FR", start_bit=16, length=14, profile=temp_profile
        )
        signals_edit.remove_signal("b", "0x386", "FL", profile=temp_profile)
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "b.yaml").read_text())
        assert set(data["messages"]["0x386"]["signals"]) == {"FR"}

    def test_remove_last_drops_message(self, temp_profile):
        signals_edit.upsert_signal("b", "0x386", "FL", start_bit=0, length=14, profile=temp_profile)
        signals_edit.remove_signal("b", "0x386", "FL", profile=temp_profile)
        # Removing the last signal (and message) leaves no empty stub — file gone.
        assert not (temp_profile.signals_dir / "b.yaml").exists()

    def test_invalid_rejected(self, temp_profile):
        with pytest.raises(signals_edit.SignalsEditError):
            signals_edit.upsert_signal("b", "0x1", "X", start_bit=0, length=0, profile=temp_profile)
        with pytest.raises(signals_edit.SignalsEditError):
            signals_edit.upsert_signal(
                "b", "0x1", "X", start_bit=0, length=8, byte_order="sideways", profile=temp_profile
            )

    def test_merge_bus(self, temp_profile):
        imported = {
            "0x386": {
                "name": "WHL_SPD11",
                "tx_ecu": "GW",
                "signals": {
                    "FL": {"start_bit": 0, "length": 14},
                    "FR": {"start_bit": 16, "length": 14},
                },
            }
        }
        path, n = signals_edit.merge_bus("pt", imported, profile=temp_profile)
        assert n == 2 and path.exists()
        assert validate._run_signals() == 0


# ── canair signals command ────────────────────────────────────────────────


class TestSignalsCommand:
    def _run(self, argv):
        from canlib.commands import signals

        p = signals.add_parser(argparse.ArgumentParser().add_subparsers())
        return signals.run(p.parse_args(argv))

    def test_list_empty(self, temp_profile, capsys):
        assert self._run(["list"]) == 0
        assert "No broadcast signals" in capsys.readouterr().out

    def test_upsert_then_list_and_rm(self, temp_profile, capsys):
        assert (
            self._run(
                [
                    "upsert",
                    "pt",
                    "0x386",
                    "WHL_SPD_FL",
                    "--start-bit",
                    "0",
                    "--length",
                    "14",
                    "--scale",
                    "0.03125",
                    "--unit",
                    "km/h",
                ]
            )
            == 0
        )
        assert self._run(["list"]) == 0
        out = capsys.readouterr().out
        assert "WHL_SPD_FL" in out and "0x386" in out
        assert self._run(["rm", "pt", "0x386", "WHL_SPD_FL"]) == 0

    def test_bare_signals_lists(self, temp_profile, capsys):
        # `canair signals` with no subcommand defaults to list.
        from canlib.commands import signals

        p = signals.add_parser(argparse.ArgumentParser().add_subparsers())
        assert signals.run(p.parse_args([])) == 0


# ── import dbc / export dbc (round-trip via cantools) ─────────────────────


def _write_dbc(tmp_path):
    """A tiny valid DBC built via cantools (wheel-speed style linear signal)."""
    from cantools.database import Database, Message, Signal
    from cantools.database.conversion import BaseConversion

    conv = BaseConversion.factory(scale=0.03125, offset=0.0, is_float=False)
    sig = Signal(
        name="WHL_SPD_FL",
        start=0,
        length=14,
        byte_order="little_endian",
        conversion=conv,
        minimum=0,
        maximum=511.969,
        unit="km/h",
    )
    msg = Message(frame_id=0x386, name="WHL_SPD11", length=8, signals=[sig], strict=False)
    p = tmp_path / "car.dbc"
    p.write_text(Database(messages=[msg], strict=False).as_dbc_string())
    return p


class TestDbcInterop:
    def _import(self, argv):
        from canlib.commands import import_ as imp

        p = imp.add_parser(argparse.ArgumentParser().add_subparsers())
        return imp.run(p.parse_args(argv))

    def _export(self, argv):
        from canlib.commands import export

        p = export.add_parser(argparse.ArgumentParser().add_subparsers())
        return export.run(p.parse_args(argv))

    def test_import_dbc(self, temp_profile, tmp_path):
        dbc = _write_dbc(tmp_path)
        assert self._import(["dbc", str(dbc), "--bus", "pt"]) == 0
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "pt.yaml").read_text())
        sig = data["messages"]["0x386"]["signals"]["WHL_SPD_FL"]
        assert sig["length"] == 14 and sig["scale"] == 0.03125 and sig["unit"] == "km/h"
        assert validate._run_signals() == 0

    def test_import_dbc_dry_run_writes_nothing(self, temp_profile, tmp_path, capsys):
        dbc = _write_dbc(tmp_path)
        assert self._import(["dbc", str(dbc), "--bus", "pt", "--dry-run"]) == 0
        assert "dry run" in capsys.readouterr().out
        assert not (temp_profile.signals_dir / "pt.yaml").exists()

    def test_import_dbc_missing_file(self, temp_profile, capsys):
        assert self._import(["dbc", "/nonexistent/x.dbc"]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_export_roundtrip(self, temp_profile, tmp_path):
        dbc = _write_dbc(tmp_path)
        self._import(["dbc", str(dbc), "--bus", "pt"])
        out = tmp_path / "exported.dbc"
        assert self._export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        # Re-import the exported DBC → the signal survives.
        assert self._import(["dbc", str(out), "--bus", "rt"]) == 0
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "rt.yaml").read_text())
        sig = data["messages"]["0x386"]["signals"]["WHL_SPD_FL"]
        assert sig["length"] == 14 and sig["scale"] == 0.03125
