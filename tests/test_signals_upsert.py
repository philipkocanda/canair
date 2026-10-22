"""`signals upsert` / `signals_edit.upsert_signal` merge semantics, and `merge_bus`.

An upsert on an existing signal changes only what it is given: everything else a signal
carries (provenance, verification, notes, typed decoding, a big-endian byte order) must
survive an edit that doesn't mention it.
"""

from __future__ import annotations

import argparse

import pytest
import yaml

import canlib.profile as profile
from canlib.commands import validate
from canlib.profile import Profile
from canlib.signals_edit import SignalsEditError, merge_bus, upsert_signal


@pytest.fixture
def temp_profile(tmp_path):
    profile._active = Profile(name="test", root=tmp_path)
    return Profile(name="test", root=tmp_path)


def _load(prof, bus="b") -> dict:
    return yaml.safe_load((prof.signals_dir / f"{bus}.yaml").read_text())


def _sig(prof, mid, name, bus="b") -> dict:
    return _load(prof, bus)["messages"][mid]["signals"][name]


RICH = {
    "start_bit": 39,
    "length": 16,
    "byte_order": "big",
    "signed": True,
    "scale": 0.5,
    "min": -100.0,
    "max": 100.0,
    "unit": "rpm",
    "type": "enum",
    "values": {1: "P"},
    "verified": True,
    "source": "dbc:car.dbc",
    "notes": "evidence",
}


def _seed_rich(prof):
    merge_bus("b", {"0x1DA": {"signals": {"RPM": dict(RICH)}}}, profile=prof)


class TestUpsertMerges:
    def test_one_field_edit_keeps_everything_else(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal("b", "0x1DA", "RPM", unit="1/min", profile=temp_profile)
        assert _sig(temp_profile, "0x1DA", "RPM") == {**RICH, "unit": "1/min"}
        assert validate._run_signals() == 0

    def test_byte_order_default_does_not_reset_big_endian(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal("b", "0x1DA", "RPM", start_bit=47, profile=temp_profile)
        sig = _sig(temp_profile, "0x1DA", "RPM")
        assert sig["byte_order"] == "big" and sig["start_bit"] == 47

    def test_explicit_values_overwrite(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal(
            "b", "0x1DA", "RPM", byte_order="little", verified=False, profile=temp_profile
        )
        sig = _sig(temp_profile, "0x1DA", "RPM")
        assert sig["byte_order"] == "little" and sig["verified"] is False

    def test_unsigned_removes_the_flag(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal("b", "0x1DA", "RPM", signed=False, profile=temp_profile)
        assert "signed" not in _sig(temp_profile, "0x1DA", "RPM")

    def test_new_signal_unsigned_is_absent(self, temp_profile):
        upsert_signal("b", "0x1", "X", start_bit=0, length=8, signed=False, profile=temp_profile)
        assert "signed" not in _sig(temp_profile, "0x1", "X")

    def test_empty_text_clears(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal("b", "0x1DA", "RPM", notes="", source="", profile=temp_profile)
        sig = _sig(temp_profile, "0x1DA", "RPM")
        assert "notes" not in sig and "source" not in sig and sig["unit"] == "rpm"

    def test_clear_numeric_fields(self, temp_profile):
        _seed_rich(temp_profile)
        upsert_signal("b", "0x1DA", "RPM", clear=["min", "max", "scale"], profile=temp_profile)
        sig = _sig(temp_profile, "0x1DA", "RPM")
        assert not {"min", "max", "scale"} & set(sig)

    def test_clear_and_set_together_rejected(self, temp_profile):
        _seed_rich(temp_profile)
        with pytest.raises(SignalsEditError, match="both set and cleared"):
            upsert_signal("b", "0x1DA", "RPM", scale=2, clear=["scale"], profile=temp_profile)

    def test_clear_unknown_field_rejected(self, temp_profile):
        with pytest.raises(SignalsEditError, match="cannot clear start_bit"):
            upsert_signal(
                "b", "0x1", "X", start_bit=0, length=8, clear=["start_bit"], profile=temp_profile
            )

    def test_new_field_lands_in_canonical_order(self, temp_profile):
        upsert_signal("b", "0x1", "X", start_bit=0, length=8, notes="n", profile=temp_profile)
        upsert_signal("b", "0x1", "X", scale=0.5, signed=True, profile=temp_profile)
        assert list(_sig(temp_profile, "0x1", "X")) == [
            "start_bit",
            "length",
            "byte_order",
            "signed",
            "scale",
            "notes",
        ]

    def test_new_signal_needs_layout(self, temp_profile):
        with pytest.raises(SignalsEditError, match="new signal: start_bit and length required"):
            upsert_signal("b", "0x1", "X", unit="V", profile=temp_profile)

    def test_float_flag(self, temp_profile):
        upsert_signal("b", "0x1", "F", start_bit=0, length=32, is_float=True, profile=temp_profile)
        assert _sig(temp_profile, "0x1", "F")["float"] is True
        upsert_signal("b", "0x1", "F", is_float=False, profile=temp_profile)
        assert "float" not in _sig(temp_profile, "0x1", "F")

    def test_float_needs_32_or_64_bits(self, temp_profile):
        with pytest.raises(SignalsEditError, match="32 or 64 bits"):
            upsert_signal(
                "b", "0x1", "F", start_bit=0, length=16, is_float=True, profile=temp_profile
            )
        assert not (temp_profile.signals_dir / "b.yaml").exists()  # rolled back


class TestUpsertMessage:
    @pytest.mark.parametrize("bad", [0, -1, 9])
    def test_msg_length_invalid(self, temp_profile, bad):
        with pytest.raises(SignalsEditError):
            upsert_signal(
                "b", "0x1", "X", start_bit=0, length=8, msg_length=bad, profile=temp_profile
            )
        assert not (temp_profile.signals_dir / "b.yaml").exists()

    def test_msg_length_shorter_than_signals_rejected(self, temp_profile):
        with pytest.raises(SignalsEditError, match="shorter than its signals"):
            upsert_signal(
                "b", "0x1", "X", start_bit=8, length=8, msg_length=1, profile=temp_profile
            )

    @pytest.mark.parametrize("bad", ["0x20000000", "zz", -1])
    def test_bad_ids_rejected(self, temp_profile, bad):
        with pytest.raises(SignalsEditError):
            upsert_signal("b", bad, "X", start_bit=0, length=8, profile=temp_profile)

    def test_message_fields_inserted_between_existing_keys(self, temp_profile):
        upsert_signal("b", "0x284", "A", start_bit=0, length=8, msg_name="M", profile=temp_profile)
        upsert_signal(
            "b", "0x284", "A", tx_ecu="VCM", msg_length=8, extended=True, profile=temp_profile
        )
        msg = _load(temp_profile)["messages"]["0x284"]
        assert list(msg) == ["name", "tx_ecu", "length", "extended", "signals"]

    def test_explicit_msg_name_overwrites(self, temp_profile):
        upsert_signal("b", "0x1", "A", start_bit=0, length=8, msg_name="OLD", profile=temp_profile)
        upsert_signal("b", "0x1", "A", msg_name="NEW", profile=temp_profile)
        assert _load(temp_profile)["messages"]["0x1"]["name"] == "NEW"

    def test_extended_stored_only_when_not_implied(self, temp_profile):
        upsert_signal(
            "b", "0x18DAF110", "A", start_bit=0, length=8, extended=True, profile=temp_profile
        )
        assert "extended" not in _load(temp_profile)["messages"]["0x18DAF110"]
        upsert_signal("b", "0x123", "A", start_bit=0, length=8, extended=True, profile=temp_profile)
        assert _load(temp_profile)["messages"]["0x123"]["extended"] is True
        upsert_signal("b", "0x123", "A", extended=False, profile=temp_profile)
        assert "extended" not in _load(temp_profile)["messages"]["0x123"]

    def test_standard_above_11_bits_rejected(self, temp_profile):
        with pytest.raises(SignalsEditError, match="cannot be a standard frame"):
            upsert_signal(
                "b", "0x800", "A", start_bit=0, length=8, extended=False, profile=temp_profile
            )

    def test_fd_flag(self, temp_profile):
        upsert_signal(
            "b", "0x1", "A", start_bit=0, length=8, msg_length=8, fd=True, profile=temp_profile
        )
        assert _load(temp_profile)["messages"]["0x1"]["fd"] is True
        upsert_signal("b", "0x1", "A", msg_length=12, profile=temp_profile)
        assert "fd" not in _load(temp_profile)["messages"]["0x1"]  # implied by 12 bytes
        with pytest.raises(SignalsEditError, match="classic"):
            upsert_signal("b", "0x1", "A", fd=False, profile=temp_profile)


class TestMergeBus:
    def test_reimport_with_different_length_updates_it(self, temp_profile):
        merge_bus(
            "b",
            {"0x1": {"length": 8, "signals": {"A": {"start_bit": 0, "length": 8}}}},
            profile=temp_profile,
        )
        merge_bus(
            "b",
            {"0x1": {"length": 4, "signals": {"A": {"start_bit": 0, "length": 8}}}},
            profile=temp_profile,
        )
        assert _load(temp_profile)["messages"]["0x1"]["length"] == 4

    def test_frame_format_clash_refused(self, temp_profile):
        upsert_signal("b", "0x123", "A", start_bit=0, length=8, profile=temp_profile)
        before = (temp_profile.signals_dir / "b.yaml").read_text()
        spec = {"0x123": {"extended": True, "signals": {"B": {"start_bit": 0, "length": 8}}}}
        with pytest.raises(SignalsEditError, match=r"standard frame in b\.yaml but an extended"):
            merge_bus("b", spec, profile=temp_profile)
        assert (temp_profile.signals_dir / "b.yaml").read_text() == before

    def test_reimport_drops_a_stale_fd_flag(self, temp_profile):
        merge_bus(
            "b",
            {"0x1": {"length": 8, "fd": True, "signals": {"A": {"start_bit": 0, "length": 8}}}},
            profile=temp_profile,
        )
        assert _load(temp_profile)["messages"]["0x1"]["fd"] is True
        merge_bus(
            "b",
            {"0x1": {"length": 8, "signals": {"A": {"start_bit": 0, "length": 8}}}},
            profile=temp_profile,
        )
        assert "fd" not in _load(temp_profile)["messages"]["0x1"]


# ── through the CLI ─────────────────────────────────────────────────────────


def _cli(argv):
    from canlib.commands import signals

    p = signals.add_parser(argparse.ArgumentParser().add_subparsers())
    return signals.run(p.parse_args(argv))


class TestUpsertCli:
    BASE = ("upsert", "b", "0x1DA", "RPM")

    def test_msg_length_and_unsigned(self, temp_profile, capsys):
        argv = [*self.BASE, "--start-bit", "39", "--length", "16", "--byte-order", "big"]
        assert _cli([*argv, "--signed", "--msg-length", "8"]) == 0
        assert _load(temp_profile)["messages"]["0x1DA"]["length"] == 8
        assert _cli([*self.BASE, "--unsigned"]) == 0
        sig = _sig(temp_profile, "0x1DA", "RPM")
        assert "signed" not in sig and sig["byte_order"] == "big" and sig["start_bit"] == 39

    def test_signed_and_unsigned_are_exclusive(self, temp_profile, capsys):
        argv = [*self.BASE, "--start-bit", "0", "--length", "8", "--signed", "--unsigned"]
        with pytest.raises(SystemExit):
            _cli(argv)

    def test_new_signal_without_layout_fails_cleanly(self, temp_profile, capsys):
        assert _cli([*self.BASE, "--unit", "rpm"]) == 1
        assert "start_bit and length required" in capsys.readouterr().err

    def test_clear_flag(self, temp_profile):
        _seed_rich(temp_profile)
        assert _cli([*self.BASE, "--clear", "min", "--clear", "max"]) == 0
        assert not {"min", "max"} & set(_sig(temp_profile, "0x1DA", "RPM"))

    def test_bad_msg_length_reports(self, temp_profile, capsys):
        argv = [*self.BASE, "--start-bit", "0", "--length", "8", "--msg-length", "10"]
        assert _cli(argv) == 1
        assert "CAN FD size" in capsys.readouterr().err

    def test_list_shows_length_and_frame_format(self, temp_profile, capsys):
        upsert_signal(
            "b", "0x123", "A", start_bit=0, length=32, is_float=True, msg_length=8,
            extended=True, fd=True, msg_name="M", profile=temp_profile,
        )  # fmt: skip
        upsert_signal("b", "0x7", "B", start_bit=0, length=8, msg_length=2, profile=temp_profile)
        assert _cli(["list"]) == 0
        out = capsys.readouterr().out
        assert "(8 B, extended, FD)" in out and "(2 B)" in out
        assert "little float" in out
