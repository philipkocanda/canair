"""Regression tests for `canair decode --param` accumulation.

`--param` must accept multiple names both space-separated after one flag and
across repeated flags — repeating the flag must NOT silently drop earlier
values (the pre-2026-07-27 nargs="+" behaviour kept only the last occurrence).
"""

import argparse
from typing import ClassVar

from canair.cli import main
from canair.cli.commands import decode as decode_script
from canair.cli.commands.decode.query import signal_suggestion
from tests.support.golden import FIXTURE_PROFILES_DIR

FIXTURE = str(FIXTURE_PROFILES_DIR / "single-frame")


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subs = root.add_subparsers()
    decode_script.add_parser(subs)
    return root


class TestParamAccumulation:
    def test_absent_is_none(self):
        args = _parser().parse_args(["decode", "BMS", "2101"])
        assert args.param is None

    def test_single_flag_multiple_names(self):
        args = _parser().parse_args(["decode", "BMS", "2101", "--param", "A", "B"])
        assert args.param == ["A", "B"]

    def test_repeated_flag_accumulates(self):
        # The reported bug: repeated --param used to keep only the last value.
        args = _parser().parse_args(
            ["decode", "BMS", "2101", "--param", "A", "--param", "B", "--param", "C"]
        )
        assert args.param == ["A", "B", "C"]

    def test_mixed_repeat_and_multivalue(self):
        args = _parser().parse_args(["decode", "BMS", "2101", "--param", "A", "B", "--param", "C"])
        assert args.param == ["A", "B", "C"]


class TestSignalSuggestion:
    DEFINED: ClassVar[list[str]] = ["MCU_MOTOR_RPM", "MCU_MOTOR_TORQUE", "MCU_COOLANT_TEMP"]

    def test_a_substring_is_suggested(self):
        hint = signal_suggestion({"MOTOR_RPM"}, self.DEFINED, "MCU", "2102")
        assert hint == "did you mean: MCU_MOTOR_RPM"

    def test_a_typo_is_suggested_closest_first(self):
        hint = signal_suggestion({"MCU_MOTOR_TORQE"}, self.DEFINED, "MCU", "2102")
        assert hint.startswith("did you mean: MCU_MOTOR_TORQUE")

    def test_no_close_name_lists_every_signal(self):
        hint = signal_suggestion({"FOO"}, self.DEFINED, "MCU", "2102")
        assert hint == "MCU 2102 defines: MCU_MOTOR_RPM, MCU_MOTOR_TORQUE, MCU_COOLANT_TEMP"


class TestUnknownTargets:
    """A miss is an error on stderr with exit 2, and an unknown --param names the real ones."""

    def test_unknown_pid(self, capsys):
        assert main(["--profile", FIXTURE, "decode", "ALPHA", "22F0FF"]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: PID '22F0FF' not found for ALPHA. Available: 22F001" in captured.err

    def test_unknown_ecu(self, capsys):
        assert main(["--profile", FIXTURE, "decode", "NOPE", "22F001"]) == 2
        assert "error: " in capsys.readouterr().err

    def test_unknown_param_suggests_a_close_name(self, capsys):
        argv = ["--profile", FIXTURE, "decode", "ALPHA", "22F001", "--param", "RAMP"]
        assert main(argv) == 1
        err = capsys.readouterr().err
        assert "warning: signals not found: RAMP" in err
        assert "hint: did you mean: ALPHA_RAMP" in err
        assert "error: no signals match the filters (2 defined for ALPHA 22F001)" in err
