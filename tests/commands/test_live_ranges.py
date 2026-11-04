"""The hex-range flags of the sweeping commands (``--rid-range``, ``--did-range``, ``--range``).

One parser, :func:`canlib.core.id_ranges.parse_id_range`, reads every range: the scan flags, the
``iocontrol_scan_ranges:`` profile field and its validator. A backwards range used to be accepted
by the flag (and swept nothing) while the profile field rejected it.
"""

import argparse

import pytest

from canlib.commands._live.handlers.ranges import parse_range, parse_ranges
from canlib.live.iocontrol_scan import resolve_iocontrol_ranges
from canlib.validation.pids import _validate_iocontrol_scan_ranges


@pytest.mark.parametrize(
    ("text", "expected"),
    [("01-FF", (0x01, 0xFF)), ("E000-E0FF", (0xE000, 0xE0FF)), ("bc01-bc0b", (0xBC01, 0xBC0B))],
)
def test_a_range_flag_parses(text, expected):
    assert parse_range(text) == expected


@pytest.mark.parametrize("text", ["", "01", "01-", "zz-FF", "01-FF,02-03", "FF-01", "0x01-0xFF"])
def test_a_malformed_or_backwards_range_flag_is_an_argparse_error(text):
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid range"):
        parse_range(text)


def test_a_list_flag_keeps_the_given_order():
    assert parse_ranges("B400-B41F,B100-B11F") == ((0xB400, 0xB41F), (0xB100, 0xB11F))


def test_the_profile_field_and_its_validator_agree():
    entries = ["B000-BFFF", "0xB000-0xBFFF", "FFFF-0000", "B000", "G000-G0FF", "B000-1FFFF"]
    errors: list[str] = []
    for entry in entries:
        _validate_iocontrol_scan_ranges([entry], "BCM", errors)
    flagged = {e.split("'")[1] for e in errors}
    resolved = resolve_iocontrol_ranges({"iocontrol_scan_ranges": entries})
    # The resolver drops what is not a range; the validator also holds bounds to 16 bits.
    assert flagged == set(entries) - {"B000-BFFF"}
    assert resolved == [(0xB000, 0xBFFF), (0xB000, 0x1FFFF)]
