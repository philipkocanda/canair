"""Tests for the pure text-scanning primitives (:mod:`canlib.textscan`)."""

from __future__ import annotations

import pytest

from canlib import textscan as ts

# Synthetic: VIN-shaped (17 characters, no I/O/Q, has letters) but no real vehicle.
SYNTH_VIN = "SYNTHVXN012345678"


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

    def test_a_header_mask_keeps_offsets_and_hides_the_header(self):
        data = b"bZ1SW123"
        assert ts.ascii_fold(data, 3) == "\x00\x00\x00SW123"
        assert ts.printable_runs(data, 2, start=3) == [(3, "SW123")]
        assert ts.ascii_fold(b"ab", 5) == "\x00\x00"  # a header longer than the data

    def test_folded_runs_split_at_placeholders(self):
        assert ts.folded_runs("\x00AB\x00\x00CDE\x00") == [(1, "AB"), (5, "CDE")]
        assert ts.folded_runs("\x00AB\x00CDE", 3) == [(4, "CDE")]


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

    def test_a_vin_glued_to_a_non_vin_word_character_is_found(self):
        # ``\b`` treated these neighbours as part of the word and missed the VIN.
        for text in (f"x{SYNTH_VIN}", f"{SYNTH_VIN}_2", f"vin:{SYNTH_VIN}i"):
            ((s, e),) = ts.vin_spans(text)
            assert text[s:e] == SYNTH_VIN

    def test_a_longer_vin_character_run_is_not_a_text_vin(self):
        # In free text and hex strings an 18+ character run is not a VIN token: a
        # payload's hex is all VIN characters and would otherwise always match.
        assert ts.vin_spans(SYNTH_VIN + "A") == []
        assert not ts.has_vin_token("62F190" + "0123456789ABCDEF" * 2)

    @pytest.mark.parametrize(
        "data,expected",
        [
            (SYNTH_VIN, SYNTH_VIN),  # the plain case: the same as vin_spans
            (SYNTH_VIN + "UUUU", SYNTH_VIN),  # 0x55 fill bytes after it
            ("000" + SYNTH_VIN, SYNTH_VIN),  # ASCII-zero fill before it
            ("Z1" + SYNTH_VIN, "Z1" + SYNTH_VIN),  # unknown neighbours: the whole run
            ("1" + SYNTH_VIN, "1" + SYNTH_VIN),  # one byte is ambiguous: pad or header
        ],
    )
    def test_data_vin_spans_finds_a_vin_beside_vin_character_bytes(self, data, expected):
        ((s, e),) = ts.data_vin_spans(data)
        assert data[s:e] == expected
        assert ts.has_data_vin(data)

    def test_data_vin_spans_is_a_superset_of_vin_spans(self):
        texts = (SYNTH_VIN, f" {SYNTH_VIN} ", f"x{SYNTH_VIN}", "31114851712211429", "short")
        for text in texts:
            assert set(ts.vin_spans(text)) <= set(ts.data_vin_spans(text))

    def test_data_vin_spans_still_needs_a_letter(self):
        assert ts.data_vin_spans("1234567890123456789") == []

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

    @pytest.mark.parametrize(
        "text",
        [
            "DE AD be ef",
            "0xDEADBEEF",
            "de:ad:be:ef",
            "DEAD_BEEF",
            "0xDE 0xAD 0xBE 0xEF",
            "0xDE0xAD0xBE0xEF",  # 0x starts a token even with no space before it
            "0XDE,0XAD, BE-EF",
        ],
    )
    def test_hex_needle_spellings(self, text):
        assert ts.parse_hex_needle(text) == b"\xde\xad\xbe\xef"

    @pytest.mark.parametrize("text", ["", "  ", "abc", "zz", "0x", "0xZZ"])
    def test_bad_hex_needle(self, text):
        with pytest.raises(ValueError):
            ts.parse_hex_needle(text)

    @pytest.mark.parametrize("text", ["0xA 0xB", "1 2 3 4", "DE A", "0xDE0xA"])
    def test_a_one_digit_token_is_refused_not_glued_to_its_neighbour(self, text):
        # Stripping separators first used to read '0xA 0xB' as the one byte AB.
        with pytest.raises(ValueError, match="odd number of digits"):
            ts.parse_hex_needle(text)

    def test_find_hex(self):
        assert ts.find_hex(b"\x01\xde\xad\x02\xde\xad", b"\xde\xad") == [(1, 3), (4, 6)]
