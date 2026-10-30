"""Tests for canlib.id_ranges — the shared hex range-list primitives."""

import pytest

from canlib.id_ranges import as_id_ranges, count_ids, format_id_ranges, iter_ids, parse_id_ranges


def test_parse_a_list_in_the_order_given():
    assert parse_id_ranges("B400-B41F, B100-B11F") == ((0xB400, 0xB41F), (0xB100, 0xB11F))
    assert parse_id_ranges("01-FF") == ((0x01, 0xFF),)


@pytest.mark.parametrize("text", ["", ",", "B400", "B4-B3", "XX-YY", "B400-B41F,"])
def test_parse_refuses_malformed_lists(text):
    with pytest.raises(ValueError):
        parse_id_ranges(text)


def test_overlapping_ids_are_visited_once():
    ranges = ((0x10, 0x13), (0x12, 0x14))
    assert list(iter_ids(ranges)) == [0x10, 0x11, 0x12, 0x13, 0x14]
    assert count_ids(ranges) == 5


def test_a_single_pair_or_a_list_normalize_alike():
    assert as_id_ranges((0x01, 0x02)) == ((0x01, 0x02),)
    assert as_id_ranges([(0x01, 0x02), (0x05, 0x06)]) == ((0x01, 0x02), (0x05, 0x06))


def test_format_round_trips():
    text = "B000-B0FF,C000-C0FF"
    assert format_id_ranges(parse_id_ranges(text), 4) == text
    assert format_id_ranges(((0x01, 0xFF),), 2) == "01-FF"
