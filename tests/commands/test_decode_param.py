"""Regression tests for `canair decode --param` accumulation.

`--param` must accept multiple names both space-separated after one flag and
across repeated flags — repeating the flag must NOT silently drop earlier
values (the pre-2026-07-27 nargs="+" behaviour kept only the last occurrence).
"""

import argparse

from canlib.commands import decode as decode_script


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
