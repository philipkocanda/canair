"""Tests for the canair sniff flags (pure, no device)."""

from canair.cli.commands.sniff import _parse_filters
from canair.transport.config import _parse_datarate


class TestParseFilters:
    def test_none(self):
        assert _parse_filters(None) is None
        assert _parse_filters("") is None

    def test_standard_ids(self):
        f = _parse_filters("770,7E4")
        assert f == [
            {"can_id": 0x770, "can_mask": 0x7FF},
            {"can_id": 0x7E4, "can_mask": 0x7FF},
        ]

    def test_extended_id_gets_wide_mask(self):
        f = _parse_filters("18DAF110")
        assert f == [{"can_id": 0x18DAF110, "can_mask": 0x1FFFFFFF}]

    def test_whitespace_tolerated(self):
        assert _parse_filters(" 770 , 7e4 ") == [
            {"can_id": 0x770, "can_mask": 0x7FF},
            {"can_id": 0x7E4, "can_mask": 0x7FF},
        ]


class TestParseDatarate:
    def test_k_suffix(self):
        assert _parse_datarate("500K") == 500_000
        assert _parse_datarate("250k") == 250_000

    def test_m_suffix(self):
        assert _parse_datarate("1M") == 1_000_000

    def test_plain_int(self):
        assert _parse_datarate("500000") == 500_000

    def test_none_and_garbage(self):
        assert _parse_datarate(None) is None
        assert _parse_datarate("fast") is None
