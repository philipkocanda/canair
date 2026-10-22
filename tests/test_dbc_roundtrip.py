"""DBC round trips through `import dbc` / `export dbc` for what the linear model carries.

Float signals, the frame format (extended / CAN FD), value tables and declared lengths
must survive DBC → signals/ → DBC. DBCs are built in-test with cantools.
"""

from __future__ import annotations

import argparse

import cantools
import pytest
import yaml
from cantools.database import Database, Message, Signal
from cantools.database.conversion import BaseConversion

import canlib.profile as profile
from canlib.commands.import_ import DbcImportError, _dbc_to_signals
from canlib.profile import Profile
from canlib.signals_edit import merge_bus, upsert_signal


@pytest.fixture
def temp_profile(tmp_path):
    profile._active = Profile(name="test", root=tmp_path)
    return Profile(name="test", root=tmp_path)


def _sig(name, start=0, length=8, **conv):
    choices = conv.pop("choices", None)
    return Signal(
        name=name,
        start=start,
        length=length,
        byte_order=conv.pop("byte_order", "little_endian"),
        is_signed=conv.pop("is_signed", False),
        conversion=BaseConversion.factory(choices=choices, **conv),
    )


def _write(tmp_path, *messages, name="car.dbc"):
    from canlib.commands.export import _dbc_specifics

    path = tmp_path / name
    db = Database(messages=list(messages), strict=False, dbc_specifics=_dbc_specifics(messages))
    path.write_text(db.as_dbc_string())
    return path


def _msg(frame_id, *signals, length=8, **kw):
    return Message(
        frame_id=frame_id, name=f"M{frame_id:X}", length=length, signals=list(signals),
        strict=False, **kw,
    )  # fmt: skip


def _import(argv):
    from canlib.commands import import_ as imp

    p = imp.add_parser(argparse.ArgumentParser().add_subparsers())
    return imp.run(p.parse_args(argv))


def _export(argv):
    from canlib.commands import export

    p = export.add_parser(argparse.ArgumentParser().add_subparsers())
    return export.run(p.parse_args(argv))


def _roundtrip(tmp_path, dbc, bus="pt"):
    assert _import(["dbc", str(dbc), "--bus", bus]) == 0
    out = tmp_path / f"{bus}.exported.dbc"
    assert _export(["dbc", "--bus", bus, "-o", str(out)]) == 0
    return cantools.database.load_file(str(out), strict=False)


def _stored(prof, bus="pt"):
    return yaml.safe_load((prof.signals_dir / f"{bus}.yaml").read_text())["messages"]


class TestFloat:
    def test_float_signal_round_trips(self, temp_profile, tmp_path):
        dbc = _write(tmp_path, _msg(0x100, _sig("F", 0, 32, is_float=True)))
        src = cantools.database.load_file(str(dbc), strict=False)
        rt = _roundtrip(tmp_path, dbc)
        assert _stored(temp_profile)["0x100"]["signals"]["F"]["float"] is True
        frame = bytes.fromhex("0000803f00000000")
        want = src.get_message_by_frame_id(0x100).decode(frame)
        assert want == {"F": 1.0}
        assert rt.get_message_by_frame_id(0x100).decode(frame) == want
        assert rt.get_message_by_frame_id(0x100).signals[0].is_float

    def test_integer_signal_has_no_float_key(self, temp_profile, tmp_path):
        _roundtrip(tmp_path, _write(tmp_path, _msg(0x100, _sig("I", 0, 32))))
        assert "float" not in _stored(temp_profile)["0x100"]["signals"]["I"]


class TestFrameFormat:
    def test_extended_frame_with_small_id_round_trips(self, temp_profile, tmp_path):
        dbc = _write(tmp_path, _msg(0x123, _sig("A"), is_extended_frame=True))
        rt = _roundtrip(tmp_path, dbc)
        assert _stored(temp_profile)["0x123"]["extended"] is True
        assert rt.get_message_by_frame_id(0x123, force_extended_id=True).is_extended_frame

    def test_implied_formats_are_not_stored(self, temp_profile, tmp_path):
        dbc = _write(
            tmp_path,
            _msg(0x18DAF110, _sig("A"), is_extended_frame=True),
            _msg(0x200, _sig("B")),
        )
        rt = _roundtrip(tmp_path, dbc)
        stored = _stored(temp_profile)
        assert "extended" not in stored["0x18DAF110"] and "extended" not in stored["0x200"]
        assert rt.get_message_by_frame_id(0x18DAF110).is_extended_frame
        assert not rt.get_message_by_frame_id(0x200).is_extended_frame

    def test_standard_and_extended_with_one_number_refused(self, temp_profile, tmp_path, capsys):
        dbc = _write(
            tmp_path,
            _msg(0x123, _sig("A")),
            _msg(0x123, _sig("B"), is_extended_frame=True),
        )
        assert _import(["dbc", str(dbc), "--bus", "pt"]) == 1
        assert (
            "0x123: a standard and an extended frame share this number" in capsys.readouterr().err
        )
        assert not (temp_profile.signals_dir / "pt.yaml").exists()

    def test_clash_refused_on_dry_run_too(self, temp_profile, tmp_path):
        dbc = _write(tmp_path, _msg(0x5, _sig("A")), _msg(0x5, _sig("B"), is_extended_frame=True))
        assert _import(["dbc", str(dbc), "--bus", "pt", "--dry-run"]) == 1

    def test_fd_frame_round_trips(self, temp_profile, tmp_path):
        dbc = _write(
            tmp_path,
            _msg(0x300, _sig("BIG", 88, 8), length=12, is_fd=True),
            _msg(0x301, _sig("SMALL"), length=8, is_fd=True),
            _msg(0x302, _sig("CLASSIC"), length=8),
        )
        rt = _roundtrip(tmp_path, dbc)
        stored = _stored(temp_profile)
        assert "fd" not in stored["0x300"]  # implied by 12 bytes
        assert stored["0x301"]["fd"] is True
        assert "fd" not in stored["0x302"]
        assert rt.get_message_by_frame_id(0x300).is_fd
        assert rt.get_message_by_frame_id(0x301).is_fd
        assert not rt.get_message_by_frame_id(0x302).is_fd

    def test_long_frame_without_frame_format_exports_as_fd(self, temp_profile, tmp_path):
        # Many real DBCs declare a 32/64-byte BO_ without VFrameFormat: it can only be FD.
        rt = _roundtrip(tmp_path, _write(tmp_path, _msg(0x400, _sig("A"), length=32)))
        msg = rt.get_message_by_frame_id(0x400)
        assert msg.is_fd and msg.length == 32


class TestValueTables:
    def test_unscaled_choices_become_enum_and_round_trip(self, temp_profile, tmp_path):
        dbc = _write(tmp_path, _msg(0x354, _sig("GEAR", 0, 4, choices={1: "P", 2: "R"})))
        rt = _roundtrip(tmp_path, dbc)
        sig = _stored(temp_profile)["0x354"]["signals"]["GEAR"]
        assert sig["type"] == "enum" and sig["values"] == {1: "P", 2: "R"}
        choices = rt.get_message_by_frame_id(0x354).signals[0].choices
        assert {k: str(v) for k, v in choices.items()} == {1: "P", 2: "R"}

    def test_scaled_choices_are_reported_not_carried(self, temp_profile, tmp_path, capsys):
        dbc = _write(tmp_path, _msg(0x354, _sig("G", 0, 8, scale=2, choices={1: "P"})))
        assert _import(["dbc", str(dbc), "--bus", "pt"]) == 0
        assert "value tables not carried on 1 scaled or float signal(s): 0x354/G" in (
            capsys.readouterr().err
        )
        assert "type" not in _stored(temp_profile)["0x354"]["signals"]["G"]


class TestImportSkipsInvalid:
    def test_message_overrunning_its_length_is_skipped(self, temp_profile, tmp_path, capsys):
        dbc = _write(
            tmp_path,
            _msg(0x10, _sig("OVER", 8, 8), length=1),
            _msg(0x11, _sig("OK")),
        )
        assert _import(["dbc", str(dbc), "--bus", "pt"]) == 0
        err = capsys.readouterr().err
        assert "skipping 1 message(s)" in err and "0x10" in err
        assert set(_stored(temp_profile)) == {"0x11"}

    def test_zero_scale_is_skipped(self, temp_profile, tmp_path, capsys):
        dbc = _write(tmp_path, _msg(0x10, _sig("Z", scale=0)), _msg(0x11, _sig("OK")))
        assert _import(["dbc", str(dbc), "--bus", "pt"]) == 0
        assert "scale must not be 0" in capsys.readouterr().err

    def test_bad_ids_filter_reports(self, temp_profile, tmp_path, capsys):
        dbc = _write(tmp_path, _msg(0x10, _sig("A")))
        assert _import(["dbc", str(dbc), "--ids", "0xZZ"]) == 1
        assert "--ids must be hex" in capsys.readouterr().err


class TestDbcToSignals:
    def _db(self, tmp_path, *messages):
        return cantools.database.load_file(str(_write(tmp_path, *messages)), strict=False)

    def test_shape(self, tmp_path):
        db = self._db(
            tmp_path,
            _msg(0x1, _sig("F", 0, 32, is_float=True), _sig("E", 32, 8, choices={0: "off"})),
            _msg(0x2, _sig("X"), is_extended_frame=True),
        )
        result = _dbc_to_signals(db, None, "ECU", "dbc:car.dbc")
        one = result.messages["0x1"]
        assert one["tx_ecu"] == "ECU" and one["length"] == 8 and "extended" not in one
        assert one["signals"]["F"] == {
            "start_bit": 0, "length": 32, "byte_order": "little", "float": True,
            "source": "dbc:car.dbc",
        }  # fmt: skip
        assert one["signals"]["E"]["values"] == {0: "off"}
        assert result.messages["0x2"]["extended"] is True
        assert result.skipped == {} and result.value_tables_dropped == []

    def test_no_tx_ecu_key_without_one(self, tmp_path):
        result = _dbc_to_signals(self._db(tmp_path, _msg(0x1, _sig("A"))), None, None, None)
        assert "tx_ecu" not in result.messages["0x1"]

    def test_clash_raises(self, tmp_path):
        db = self._db(tmp_path, _msg(0x7, _sig("A")), _msg(0x7, _sig("B"), is_extended_frame=True))
        with pytest.raises(DbcImportError):
            _dbc_to_signals(db, None, None, None)


class TestExport:
    def test_little_endian_without_length_gets_smallest_fit(self, temp_profile, tmp_path):
        upsert_signal("pt", "0x10", "A", start_bit=0, length=4, profile=temp_profile)
        upsert_signal("pt", "0x11", "B", start_bit=64, length=8, profile=temp_profile)
        out = tmp_path / "o.dbc"
        assert _export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        db = cantools.database.load_file(str(out), strict=False)
        assert db.get_message_by_frame_id(0x10).length == 1
        nine = db.get_message_by_frame_id(0x11)
        assert nine.length == 12 and nine.is_fd  # 9 bytes rounds up to the next FD size

    def test_verified_only_keeps_declared_length(self, temp_profile, tmp_path):
        upsert_signal(
            "pt", "0x10", "A", start_bit=0, length=8, verified=True, msg_length=8,
            profile=temp_profile,
        )  # fmt: skip
        upsert_signal("pt", "0x10", "B", start_bit=8, length=8, profile=temp_profile)
        out = tmp_path / "o.dbc"
        assert _export(["dbc", "--bus", "pt", "--verified-only", "-o", str(out)]) == 0
        msg = cantools.database.load_file(str(out), strict=False).get_message_by_frame_id(0x10)
        assert msg.length == 8 and [s.name for s in msg.signals] == ["A"]

    def test_verified_only_without_length_sizes_to_exported_signals(self, temp_profile, tmp_path):
        upsert_signal("pt", "0x10", "A", start_bit=0, length=8, verified=True, profile=temp_profile)
        upsert_signal("pt", "0x10", "B", start_bit=56, length=8, profile=temp_profile)
        out = tmp_path / "o.dbc"
        assert _export(["dbc", "--bus", "pt", "--verified-only", "-o", str(out)]) == 0
        msg = cantools.database.load_file(str(out), strict=False).get_message_by_frame_id(0x10)
        assert msg.length == 1

    def test_invalid_map_refused_not_crashed(self, temp_profile, tmp_path, capsys):
        # A hand-edited ID above 29 bits used to raise out of cantools.
        temp_profile.signals_dir.mkdir(parents=True)
        (temp_profile.signals_dir / "pt.yaml").write_text(
            "messages:\n  '0x20000000':\n    signals:\n      A: {start_bit: 0, length: 8}\n"
        )
        assert _export(["dbc", "--bus", "pt", "-o", str(tmp_path / "o.dbc")]) == 1
        err = capsys.readouterr().err
        assert "pt.yaml is invalid" in err and "29-bit maximum" in err

    def test_scale_and_offset_written_as_stored(self, temp_profile, tmp_path):
        merge_bus(
            "pt",
            {
                "0x10": {
                    "signals": {"T": {"start_bit": 0, "length": 8, "scale": 0.5, "offset": -40}}
                }
            },
            profile=temp_profile,
        )
        out = tmp_path / "o.dbc"
        assert _export(["dbc", "--bus", "pt", "-o", str(out)]) == 0
        sig = cantools.database.load_file(str(out), strict=False).messages[0].signals[0]
        assert (sig.scale, sig.offset) == (0.5, -40)
