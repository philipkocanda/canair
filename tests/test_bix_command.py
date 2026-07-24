"""Tests for the ``canair bix`` command layer (arg parsing + payload annotation)."""

import argparse

import pytest

from canlib.commands import bix


def _parse(argv):
    """Build a parser with just the bix subcommand and parse argv."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    bix.add_parser(subparsers)
    return parser.parse_args(["bix", *argv])


# ── --annotate accepts quoted, unquoted, and no-space hex identically ──


@pytest.mark.parametrize(
    "argv",
    [
        # unquoted space-separated (each byte its own shell token)
        ["-2", "-a", "62", "01", "A0", "55"],
        # quoted space-separated (one token)
        ["-2", "-a", "62 01 A0 55"],
        # no-space blob (one token)
        ["-2", "-a", "6201A055"],
    ],
)
def test_annotate_forms_parse_to_same_payload(argv, capsys):
    args = _parse(argv)
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    # SID + the two DID bytes + first data byte all present in the table
    assert "0x62" in out
    assert "0x01" in out
    assert "0xA0" in out
    assert "0x55" in out


def test_annotate_joins_tokens_before_parsing():
    args = _parse(["-a", "62", "01", "A0", "55"])
    # nargs="+" yields a list; run() joins with spaces
    assert args.annotate == ["62", "01", "A0", "55"]


def test_parse_hex_payload_spacing_equivalence():
    assert (
        bix._parse_hex_payload("62 01 A0 55")
        == bix._parse_hex_payload("6201A055")
        == [0x62, 0x01, 0xA0, 0x55]
    )


# ── error paths name the offending input ──


def test_parse_hex_payload_odd_length_exits(capsys):
    with pytest.raises(SystemExit):
        bix._parse_hex_payload("62 0")
    assert "odd number of hex characters" in capsys.readouterr().err


def test_parse_hex_payload_invalid_byte_names_token(capsys):
    with pytest.raises(SystemExit):
        bix._parse_hex_payload("62 ZZ A0")
    err = capsys.readouterr().err
    assert "invalid hex byte 'ZZ'" in err


# ── --annotate --ecu/--pid parameter overlay (T3.1) ──


def test_annotate_ecu_pid_overlay_shows_params(capsys):
    # IGPM 22BC03 B10 holds the door bits; the overlay must name them and flag
    # unmapped bytes — resolved against the active profile.
    args = _parse(["-2", "-a", "62BC03FDEE3C7320010000", "--ecu", "IGPM", "--pid", "22BC03"])
    assert bix.run(args) == 0
    out = capsys.readouterr().out
    assert "Param" in out  # overlay column present
    assert "DOOR_DRV_OPEN" in out  # a mapped bit is named
    assert "unmapped" in out  # a data byte with no param is flagged


def test_annotate_ecu_requires_pid(capsys):
    args = _parse(["-2", "-a", "62BC03", "--ecu", "IGPM"])
    assert bix.run(args) == 1
    assert "--ecu requires --pid" in capsys.readouterr().err


def test_annotate_pid_requires_ecu(capsys):
    args = _parse(["-2", "-a", "62BC03", "--pid", "22BC03"])
    assert bix.run(args) == 1
    assert "--pid requires --ecu" in capsys.readouterr().err


def test_annotate_no_overlay_has_no_param_column(capsys):
    args = _parse(["-2", "-a", "6201A055"])
    assert bix.run(args) == 0
    assert "Param" not in capsys.readouterr().out
