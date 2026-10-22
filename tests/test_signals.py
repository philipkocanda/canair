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
from canlib.signals import signal_byte_span
from canlib.validation.signals import check_signals_doc


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

    def test_source_provenance_field(self, temp_profile):
        signals_edit.upsert_signal(
            "b",
            "0x354",
            "GEAR",
            start_bit=40,
            length=4,
            source="uhi22 drive.csv (fetch: scripts/fetch_can_corpus.py)",
            notes="P=1/R=2/N=3/D=4",
            profile=temp_profile,
        )
        import yaml

        sig = yaml.safe_load((temp_profile.signals_dir / "b.yaml").read_text())["messages"][
            "0x354"
        ]["signals"]["GEAR"]
        assert sig["source"].startswith("uhi22")
        assert validate._run_signals() == 0  # source is an accepted field

    def test_signed_persists_after_byte_order(self, temp_profile):
        p = signals_edit.upsert_signal(
            "b",
            "0x1DA",
            "RPM",
            start_bit=39,
            length=16,
            byte_order="big",
            signed=True,
            scale=0.5,
            profile=temp_profile,
        )
        import yaml

        sig = yaml.safe_load(p.read_text())["messages"]["0x1DA"]["signals"]["RPM"]
        assert sig["signed"] is True
        assert list(sig) == ["start_bit", "length", "byte_order", "signed", "scale"]
        assert validate._run_signals() == 0

    def test_signed_omitted_by_default(self, temp_profile):
        p = signals_edit.upsert_signal("b", "0x1", "X", start_bit=0, length=8, profile=temp_profile)
        import yaml

        assert "signed" not in yaml.safe_load(p.read_text())["messages"]["0x1"]["signals"]["X"]

    def test_merge_bus_keeps_typed_fields(self, temp_profile):
        # The schema documents type/values/bits; the editor used to drop them and the
        # validator used to reject them.
        imported = {
            "0x354": {
                "signals": {
                    "GEAR": {
                        "start_bit": 40,
                        "length": 4,
                        "type": "enum",
                        "values": {1: "P", 2: "R"},
                    }
                }
            }
        }
        path, _ = signals_edit.merge_bus("b", imported, profile=temp_profile)
        import yaml

        sig = yaml.safe_load(path.read_text())["messages"]["0x354"]["signals"]["GEAR"]
        assert sig["type"] == "enum" and sig["values"] == {1: "P", 2: "R"}
        assert validate._run_signals() == 0

    def test_msg_length_lands_before_existing_signals(self, temp_profile):
        signals_edit.upsert_signal("b", "0x284", "A", start_bit=0, length=8, profile=temp_profile)
        p = signals_edit.upsert_signal(
            "b",
            "0x284",
            "B",
            start_bit=8,
            length=8,
            msg_name="X284",
            msg_length=8,
            profile=temp_profile,
        )
        import yaml

        msg = yaml.safe_load(p.read_text())["messages"]["0x284"]
        assert list(msg) == ["name", "length", "signals"] and msg["length"] == 8
        assert validate._run_signals() == 0

    def test_msg_length_out_of_range(self, temp_profile):
        with pytest.raises(signals_edit.SignalsEditError, match="CAN FD size"):
            signals_edit.upsert_signal(
                "b", "0x1", "X", start_bit=0, length=8, msg_length=65, profile=temp_profile
            )

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


# ── check_signals_doc ─────────────────────────────────────────────────────


def _doc(**sig):
    return {"messages": {"0x1": {"signals": {"X": {"start_bit": 0, "length": 8, **sig}}}}}


class TestCheckSignalsDoc:
    def test_signed_must_be_bool(self):
        errors, _ = check_signals_doc(_doc(signed="yes"))
        assert errors == ["0x1/X: signed must be true|false (got 'yes')"]

    def test_signed_bool_accepted(self):
        assert check_signals_doc(_doc(signed=True)) == ([], 1)

    def test_typed_fields_accepted(self):
        assert check_signals_doc(_doc(type="bitmask", bits={0: "a"})) == ([], 1)

    def test_unknown_type_rejected(self):
        errors, _ = check_signals_doc(_doc(type="float"))
        assert len(errors) == 1 and "type must be one of" in errors[0]

    def test_unknown_field_rejected(self):
        errors, _ = check_signals_doc(_doc(is_signed=True))
        assert errors == ["0x1/X: unknown field 'is_signed'"]

    @pytest.mark.parametrize("length", [1, 8, 12, 64])
    def test_message_length_accepted(self, length):
        doc = _doc()
        doc["messages"]["0x1"]["length"] = length
        assert check_signals_doc(doc) == ([], 1)

    @pytest.mark.parametrize("length", [-1, 9, 11, 13, 65, "8", True])
    def test_message_length_rejected(self, length):
        doc = _doc()
        doc["messages"]["0x1"]["length"] = length
        errors, _ = check_signals_doc(doc)
        assert len(errors) == 1 and "length must be 0-8 bytes, or a CAN FD size" in errors[0]


class TestSignalByteSpan:
    @pytest.mark.parametrize(
        ("start_bit", "length", "byte_order", "span"),
        [
            (0, 8, "little", 1),
            (0, 14, "little", 2),
            (56, 8, "little", 8),
            (7, 8, "big", 1),  # byte 0
            (15, 8, "big", 2),  # byte 1
            (7, 16, "big", 2),  # bytes 0-1
            (39, 16, "big", 6),  # bytes 4-5
            (15, 24, "big", 4),  # bytes 1-3
            (4, 13, "big", 2),  # starts mid-byte, runs into byte 1
            (0, 1, "big", 1),
        ],
    )
    def test_span(self, start_bit, length, byte_order, span):
        assert signal_byte_span(start_bit, length, byte_order) == span


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

    def test_upsert_signed_flag(self, temp_profile, capsys):
        argv = ["upsert", "pt", "0x1DA", "RPM", "--start-bit", "39", "--length", "16"]
        assert self._run([*argv, "--byte-order", "big", "--signed"]) == 0
        import yaml

        data = yaml.safe_load((temp_profile.signals_dir / "pt.yaml").read_text())
        assert data["messages"]["0x1DA"]["signals"]["RPM"]["signed"] is True
        assert self._run(["list"]) == 0
        assert "big signed" in capsys.readouterr().out

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
    rpm = Signal(
        name="MOTOR_RPM",
        start=39,
        length=16,
        byte_order="big_endian",
        is_signed=True,
        conversion=BaseConversion.factory(scale=0.5, offset=0.0, is_float=False),
    )
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
    inv = Message(frame_id=0x1DA, name="INV", length=8, signals=[rpm], strict=False)
    p = tmp_path / "car.dbc"
    p.write_text(Database(messages=[msg, inv], strict=False).as_dbc_string())
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
        assert "signed" not in sig
        rpm = data["messages"]["0x1DA"]["signals"]["MOTOR_RPM"]
        assert rpm["signed"] is True and rpm["byte_order"] == "big" and rpm["start_bit"] == 39
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
        assert "signed" not in sig
        assert data["messages"]["0x1DA"]["signals"]["MOTOR_RPM"]["signed"] is True

    def test_import_records_message_length(self, temp_profile, tmp_path):
        dbc = _write_dbc(tmp_path)
        assert self._import(["dbc", str(dbc), "--bus", "pt"]) == 0
        import yaml

        msgs = yaml.safe_load((temp_profile.signals_dir / "pt.yaml").read_text())["messages"]
        assert msgs["0x386"]["length"] == 8 and msgs["0x1DA"]["length"] == 8

    def test_export_keeps_declared_length(self, temp_profile, tmp_path):
        # Regression: export derived the length from start_bit + length, which for a
        # big-endian signal (start_bit = its MSB) gave 7 bytes here instead of 8.
        import cantools

        dbc = _write_dbc(tmp_path)
        self._import(["dbc", str(dbc), "--bus", "pt"])
        out = tmp_path / "exported.dbc"
        assert self._export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        db = cantools.database.load_file(str(out), strict=False)
        assert db.get_message_by_frame_id(0x1DA).length == 8

    def test_export_computes_big_endian_span(self, temp_profile, tmp_path):
        import cantools

        signals_edit.upsert_signal(
            "pt", "0x300", "A", start_bit=7, length=8, byte_order="big", profile=temp_profile
        )
        signals_edit.upsert_signal(
            "pt", "0x300", "B", start_bit=15, length=8, byte_order="big", profile=temp_profile
        )
        out = tmp_path / "exported.dbc"
        assert self._export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        msg = cantools.database.load_file(str(out), strict=False).get_message_by_frame_id(0x300)
        assert msg.length == 2  # was 3: max(start_bit + length) = 23 bits
        assert msg.decode(bytes([0x12, 0x34])) == {"A": 0x12, "B": 0x34}

    def test_export_29bit_id_is_extended(self, temp_profile, tmp_path):
        # Regression: export crashed on any ID above 0x7FF (never marked extended).
        import cantools

        signals_edit.upsert_signal(
            "pt", "0x18DAF110", "X", start_bit=0, length=8, profile=temp_profile
        )
        out = tmp_path / "exported.dbc"
        assert self._export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        msg = cantools.database.load_file(str(out), strict=False).messages[0]
        assert msg.frame_id == 0x18DAF110 and msg.is_extended_frame

    def test_export_writes_dbc_sign_marker(self, temp_profile, tmp_path):
        dbc = _write_dbc(tmp_path)
        self._import(["dbc", str(dbc), "--bus", "pt"])
        out = tmp_path / "exported.dbc"
        assert self._export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        text = out.read_text()
        assert "MOTOR_RPM : 39|16@0-" in text and "WHL_SPD_FL : 0|14@1+" in text
