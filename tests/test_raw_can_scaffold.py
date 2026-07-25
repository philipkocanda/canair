"""Stage-0 scaffold tests for the raw-CAN broadcast domain.

Covers the `canair validate signals` / `validate can` wiring (graceful-absent,
valid, invalid) and the `canair import` command stub. See
plans/2026-07-24-raw-can-analysis.md.
"""

from __future__ import annotations

import argparse

import pytest

import canlib.profile as profile
from canlib.commands import import_ as import_cmd
from canlib.commands import validate
from canlib.profile import Profile


@pytest.fixture
def temp_profile(tmp_path):
    """Point the active profile at an empty temp dir (reset by conftest autouse)."""
    profile._active = Profile(name="test", root=tmp_path)
    return tmp_path


# ── validate signals ──────────────────────────────────────────────────────


class TestValidateSignals:
    def test_absent_skips_ok(self, temp_profile):
        assert validate._run_signals() == 0

    def test_empty_dir_skips_ok(self, temp_profile):
        (temp_profile / "signals").mkdir()
        assert validate._run_signals() == 0

    def test_valid_file_ok(self, temp_profile):
        sig = temp_profile / "signals"
        sig.mkdir()
        (sig / "powertrain.yaml").write_text(
            "bus: powertrain\n"
            "bitrate: 500000\n"
            "messages:\n"
            '  "0x220":\n'
            "    name: VCU_GEAR\n"
            "    tx_ecu: VCU\n"
            "    signals:\n"
            "      GEAR:\n"
            "        start_bit: 0\n"
            "        length: 4\n"
            "        byte_order: little\n"
            "        scale: 1\n"
            "        offset: 0\n"
            "        unit: ''\n"
            "        verified: false\n"
        )
        assert validate._run_signals() == 0

    def test_bad_arb_id_errors(self, temp_profile):
        sig = temp_profile / "signals"
        sig.mkdir()
        (sig / "bad.yaml").write_text(
            "messages:\n"
            "  not_hex:\n"
            "    signals:\n"
            "      X:\n"
            "        start_bit: 0\n"
            "        length: 8\n"
        )
        assert validate._run_signals() == 1

    def test_missing_required_signal_field_errors(self, temp_profile):
        sig = temp_profile / "signals"
        sig.mkdir()
        (sig / "bad.yaml").write_text(
            "messages:\n"
            '  "0x100":\n'
            "    signals:\n"
            "      X:\n"
            "        length: 8\n"  # missing start_bit
        )
        assert validate._run_signals() == 1

    def test_bad_byte_order_errors(self, temp_profile):
        sig = temp_profile / "signals"
        sig.mkdir()
        (sig / "bad.yaml").write_text(
            "messages:\n"
            '  "0x100":\n'
            "    signals:\n"
            "      X:\n"
            "        start_bit: 0\n"
            "        length: 8\n"
            "        byte_order: sideways\n"
        )
        assert validate._run_signals() == 1

    def test_unknown_field_errors(self, temp_profile):
        sig = temp_profile / "signals"
        sig.mkdir()
        (sig / "bad.yaml").write_text("bogus: 1\nmessages: {}\n")
        assert validate._run_signals() == 1


# ── validate can (raw-CAN log index) ──────────────────────────────────────


class TestValidateCan:
    def test_absent_skips_ok(self, temp_profile):
        assert validate._run_can() == 0

    def test_valid_index_ok(self, temp_profile):
        can = temp_profile / "captures" / "can"
        can.mkdir(parents=True)
        (can / "index.yaml").write_text(
            "logs:\n"
            "  - file: 2026-07-24-drive.blf\n"
            "    format: blf\n"
            "    source: https://example.com/log\n"
            '    date: "2026-07-24"\n'
            "    label: drive fwd/neutral/reverse\n"
            "    frame_count: 12345\n"
            "    id_set: ['0x220', '0x18DAF110']\n"
            "    bitrate: 500000\n"
        )
        assert validate._run_can() == 0

    def test_missing_required_field_errors(self, temp_profile):
        can = temp_profile / "captures" / "can"
        can.mkdir(parents=True)
        (can / "index.yaml").write_text("logs:\n  - file: x.blf\n")  # no format
        assert validate._run_can() == 1

    def test_bad_format_enum_errors(self, temp_profile):
        can = temp_profile / "captures" / "can"
        can.mkdir(parents=True)
        (can / "index.yaml").write_text("logs:\n  - file: x.foo\n    format: foo\n")
        assert validate._run_can() == 1

    def test_bad_id_pattern_errors(self, temp_profile):
        can = temp_profile / "captures" / "can"
        can.mkdir(parents=True)
        (can / "index.yaml").write_text(
            "logs:\n  - file: x.blf\n    format: blf\n    id_set: ['220']\n"  # not 0x-prefixed
        )
        assert validate._run_can() == 1


# ── validate parser wiring ────────────────────────────────────────────────


class TestValidateTargets:
    def test_signals_and_can_are_choices(self):
        p = validate.add_parser(argparse.ArgumentParser().add_subparsers())
        assert p.parse_args(["signals"]).target == "signals"
        assert p.parse_args(["can"]).target == "can"


# ── import command stub ───────────────────────────────────────────────────


class TestImportStub:
    def _parse(self, argv):
        p = import_cmd.add_parser(argparse.ArgumentParser().add_subparsers())
        return p.parse_args(argv)

    def test_name_and_registered(self):
        from canlib.commands import COMMAND_NAMES

        assert import_cmd.NAME == "import"
        assert "import_" in COMMAND_NAMES  # module filename; NAME drives the CLI name

    def test_can_subcommand_parses(self):
        args = self._parse(["can", "drive.blf", "--format", "gvret", "--bitrate", "500000"])
        assert args._import_kind == "can"
        assert args.file == "drive.blf"
        assert args.format == "gvret"
        assert args.bitrate == 500000

    def test_dbc_subcommand_parses(self):
        args = self._parse(["dbc", "car.dbc", "--dry-run"])
        assert args._import_kind == "dbc"
        assert args.dry_run is True

    def test_run_not_implemented_returns_2(self, capsys):
        args = self._parse(["can", "x.blf"])
        assert import_cmd.run(args) == 2
        assert "not yet implemented" in capsys.readouterr().err

    def test_subcommand_required(self):
        with pytest.raises(SystemExit):
            self._parse([])
