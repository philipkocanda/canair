"""Tests for the pure text-scanning primitives (:mod:`canlib.textscan`)."""

from __future__ import annotations

import pytest

from canlib import textscan as ts


class TestFold:
    def test_keeps_printable_and_masks_the_rest(self):
        data = b"\x00AB\x07 c~\x7f\xff"
        folded = ts.ascii_fold(data)
        assert folded == "\x00AB\x00 c~\x00\x00"
        assert len(folded) == len(data)  # offsets stay 1:1 with the bytes

    def test_printable_runs_report_byte_offsets(self):
        data = b"\x01\x02HELLO\x00\x00WORLD!\x03ab"
        assert ts.printable_runs(data, 5) == [(2, "HELLO"), (9, "WORLD!")]
        assert ts.printable_runs(data, 2)[-1] == (16, "ab")

    def test_enclosing_run_widens_to_the_whole_run(self):
        folded = ts.ascii_fold(b"\x00\x00ABCDEF\x00GH")
        assert ts.enclosing_run(folded, 4, 6) == (2, 8)
        assert ts.enclosing_run(folded, 9, 10) == (9, 11)


class TestPlausibility:
    # Numeric telemetry bytes that happen to be printable ASCII.
    NOISE = ("&H&H", "hOy*iJy(n", "]??H", "qz{|}~ab", ")(*+,-.", "\\^_`abc")
    REAL = (
        "95400G7470",
        "46700-G7100",
        "AEEV__ BMS ",
        "EHEL-MS2",
        "0x0620",
        "AEE MFC  AT EUR LHD 1.00 1.00 95740-G2200 161014",
        "M3062000307 EAEAAFP201",
    )

    @pytest.mark.parametrize("text", REAL)
    def test_keeps_real_identifiers(self, text):
        assert ts.is_plausible_token(text)

    @pytest.mark.parametrize("text", NOISE)
    def test_drops_noise(self, text):
        assert not ts.is_plausible_token(text)

    def test_filter_is_the_feature(self):
        """A bare ``strings(1)`` sweep drowns in noise; the filter separates it."""
        blob = b"\x00".join(t.encode() for t in (*self.NOISE, *self.REAL))
        runs = [t for _, t in ts.printable_runs(blob, 4)]
        kept = [t for t in runs if ts.is_plausible_token(t)]
        assert len(runs) == len(self.NOISE) + len(self.REAL)  # naive: every run is a hit
        assert sorted(kept) == sorted(self.REAL)

    def test_empty_is_not_plausible(self):
        assert not ts.is_plausible_token("")


class TestVin:
    def test_vin_shaped_token(self):
        assert ts.has_vin_token("KMHC851HFHU012435")
        assert ts.has_vin_token("1HGCM82633A004352")

    def test_all_digit_serial_is_not_a_vin(self):
        assert not ts.has_vin_token("31114851712211429")

    def test_excluded_letters_and_length(self):
        assert not ts.has_vin_token("KMHC851HFHU01243I")  # I is not a VIN character
        assert not ts.has_vin_token("KMHC851HFHU01243")  # 16 chars

    def test_spans_locate_the_vin_inside_text(self):
        text = "vin=KMHC851HFHU012435;"
        ((s, e),) = ts.vin_spans(text)
        assert text[s:e] == "KMHC851HFHU012435"

    def test_a_vin_in_a_folded_payload_is_found_by_byte_offset(self):
        data = b"\x62\xf1\x90" + b"KMHC851HFHU012435" + b"\xaa\xaa"
        ((s, e),) = ts.vin_spans(ts.ascii_fold(data))
        assert data[s:e] == b"KMHC851HFHU012435"

    def test_redaction_mask(self):
        assert ts.looks_redacted("KMHCXXXXXXXXXXXXX")
        assert ts.looks_redacted("WBA****1234")
        assert not ts.looks_redacted("KMHC851HFHU012435")
        assert not ts.looks_redacted("ABCXXX123")  # three is not a mask


class TestMatchers:
    def test_literal_is_case_insensitive_by_default(self):
        assert ts.find_literal("AbcABC", "abc") == [(0, 3), (3, 6)]

    def test_literal_case_sensitive(self):
        assert ts.find_literal("AbcABC", "ABC", case_sensitive=True) == [(3, 6)]

    def test_literal_matches_do_not_overlap(self):
        assert ts.find_literal("aaaa", "aa") == [(0, 2), (2, 4)]

    def test_empty_needle_matches_nothing(self):
        assert ts.find_literal("abc", "") == []
        assert ts.find_hex(b"abc", b"") == []

    def test_literal_span_indexes_the_original_text_when_lowercasing_changes_length(self):
        text = "İAB"  # str.lower() turns İ into two characters
        ((s, e),) = ts.find_literal(text, "ab")
        assert text[s:e] == "AB"

    def test_regex_skips_empty_matches(self):
        pattern = ts.compile_regex("x*")
        assert ts.find_regex("abc", pattern) == []
        assert ts.find_regex("axxb", pattern) == [(1, 3)]

    def test_regex_case_flag(self):
        assert ts.find_regex("ABC", ts.compile_regex("abc")) == [(0, 3)]
        assert ts.find_regex("ABC", ts.compile_regex("abc", case_sensitive=True)) == []

    def test_bad_regex_raises(self):
        import re

        with pytest.raises(re.error):
            ts.compile_regex("(")

    @pytest.mark.parametrize("text", ["DE AD be ef", "0xDEADBEEF", "de:ad:be:ef", "DEAD_BEEF"])
    def test_hex_needle_spellings(self, text):
        assert ts.parse_hex_needle(text) == b"\xde\xad\xbe\xef"

    @pytest.mark.parametrize("text", ["", "  ", "abc", "zz"])
    def test_bad_hex_needle(self, text):
        with pytest.raises(ValueError):
            ts.parse_hex_needle(text)

    def test_find_hex(self):
        assert ts.find_hex(b"\x01\xde\xad\x02\xde\xad", b"\xde\xad") == [(1, 3), (4, 6)]
