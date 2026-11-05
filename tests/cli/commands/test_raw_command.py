"""Tests for the ``canair raw`` command's argument parsing."""

from __future__ import annotations

import pytest

from canair.cli import build_parser


def test_odd_length_request_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["raw", "7B3:2313000000040"])
    assert exc.value.code == 2
    assert "odd number of hex digits" in capsys.readouterr().err


def test_valid_request_parses():
    args = build_parser().parse_args(["raw", "7E4:2101"])
    assert args.raw == "7E4:2101"
