"""Tests for canair.query.steps — the raw ``TX:REQUEST`` spec."""

import pytest

from canair.query.steps import parse_raw_spec


class TestParseRawSpec:
    def test_colon_form(self):
        assert parse_raw_spec("7e4:2101") == (0x7E4, "2101")

    def test_space_separator(self):
        assert parse_raw_spec("770 22BC03") == (0x770, "22BC03")

    @pytest.mark.parametrize("spec", ["7B3:2313000000040", "770:221", "7E4:2"])
    def test_odd_length_request_refused(self, spec):
        # An ELM327 reads a trailing nibble as a response-frame count, so an odd
        # request would go out as a different request rather than fail.
        with pytest.raises(ValueError, match="odd number of hex digits"):
            parse_raw_spec(spec)

    @pytest.mark.parametrize("spec", ["7B3", "7E4:", "7E4:21G1", "7E40:2101", "BMS:2101"])
    def test_malformed_spec_refused(self, spec):
        with pytest.raises(ValueError, match="expected TX:REQUEST"):
            parse_raw_spec(spec)
