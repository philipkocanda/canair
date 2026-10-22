"""Tests for ``canair search``'s location grouping (:mod:`canlib.commands.search.findings`).

Pure functions over hand-built payload rows: no captures, no parser. The command-level
behaviour is in ``tests/test_search.py``.
"""

from __future__ import annotations

import pytest

from canlib import ansi
from canlib.byteindex import payload_to_wican_bytes
from canlib.commands.search import render
from canlib.commands.search.findings import (
    PayloadRow,
    data_start,
    find_in_payloads,
    inventory_payloads,
    snippet,
)
from canlib.decode_value import decode_typed
from canlib.notation import ByteNotation
from canlib.textscan import compile_regex, find_hex, find_literal, find_regex

# 62 F1 00: a ReadDataByIdentifier response for a DID whose bytes are not printable,
# so only the body under test can form a printable run.
HEADER = bytes.fromhex("62F100")


def _row(body: bytes, weight: int = 1, *, header: bytes = HEADER, **kw) -> PayloadRow:
    return PayloadRow("ECU", "PID", header + body, weight, "", "", **kw)


def _inventory(*rows: PayloadRow, min_len: int = 6):
    return inventory_payloads(rows, min_len=min_len, plausible_only=True)


def _literal(needle: str, *, case_sensitive: bool = False):
    return lambda t: find_literal(t, needle, case_sensitive=case_sensitive)


def _decodes_to(row: PayloadRow, expression: str) -> str:
    wican = payload_to_wican_bytes(row.data.hex().upper())
    return decode_typed({"type": "ascii", "expression": expression}, wican).text


class TestDisjointLocations:
    """A printable byte in one capture must not merge two strings found apart elsewhere."""

    def test_a_bridging_capture_keeps_both_strings(self):
        apart = _row(b"ABCDEFGH\x00IJKLMNOP", weight=100)
        bridged = _row(b"ABCDEFGH#IJKLMNOP", weight=1)
        findings = _inventory(apart, bridged)
        assert [(f.text, f.offset, f.hits, f.variants) for f in findings] == [
            ("ABCDEFGH", 3, 101, 1),
            ("IJKLMNOP", 12, 101, 1),
        ]

    def test_a_chain_of_overlaps_keeps_its_last_link(self):
        # A[0,8) / B[4,18) / C[12,20) of the body: A and C never overlap. B joins A's
        # location (their shared core is [4,8)); C starts past that core, so it is a
        # location of its own instead of vanishing into a merged, empty region.
        a = _row(b"AAAAAAAA" + b"\x00" * 12)
        b = _row(b"\x00" * 4 + b"BBBBBBBBBBBBBB" + b"\x00\x00")
        c = _row(b"\x00" * 12 + b"CCCCCCCC")
        findings = _inventory(a, b, c, min_len=4)
        assert [(f.offset, f.length, f.hits, f.variants) for f in findings] == [
            (3 + 4, 4, 2, 2),
            (3 + 12, 8, 1, 1),
        ]
        assert findings[1].text == "CCCCCCCC"

    def test_every_reading_is_sliced_inside_its_own_run(self):
        # The old fallback sliced runs that did not cover the reported region with
        # negative indices, so ``variants`` counted junk readings.
        findings = _inventory(_row(b"ABCDEFGH\x00IJKLMNOP"), _row(b"ABCDEFGH#IJKLMNOP"))
        assert all(f.variants == 1 for f in findings)

    def test_moving_boundaries_still_collapse_to_one_core(self):
        findings = _inventory(
            _row(b"\x01 #EHEL-MS2\x00", weight=2), _row(b"\x01\x02EHEL-MS2\x07", weight=3)
        )
        assert [(f.text, f.hits) for f in findings] == [("EHEL-MS2", 5)]

    def test_hits_count_captures_not_runs(self):
        # One capture holding two strings counts once at each location.
        findings = _inventory(_row(b"ABCDEFGH\x00IJKLMNOP", weight=7))
        assert [f.hits for f in findings] == [7, 7]


class TestRegexPerRun:
    def test_a_greedy_regex_stops_at_a_non_printable_byte(self):
        row = _row(b"EHEL-MS2\x00\x01AB")
        pattern = compile_regex("EHEL.*")
        (finding,) = find_in_payloads([row], text_spans=lambda t: find_regex(t, pattern))
        assert (finding.text, finding.match, finding.offset) == ("EHEL-MS2", "EHEL-MS2", 3)

    @pytest.mark.parametrize("pattern", [r"MS2\W*", r"[^Z]+2", r"\d.*"])
    def test_negated_classes_cannot_cross_runs(self, pattern):
        row = _row(b"EHEL-MS2\x00\x00X")
        compiled = compile_regex(pattern)
        findings = find_in_payloads([row], text_spans=lambda t: find_regex(t, compiled))
        assert findings and all("\x00" not in (f.match or "") for f in findings)


class TestHeaderClamp:
    """A printable SID/identifier echo must not join the string that follows it."""

    def test_printable_did_echo_is_not_part_of_the_run(self):
        row = _row(b"SW123456", header=bytes.fromhex("625A31"))  # 'b', 'Z', '1'
        (finding,) = _inventory(row)
        assert (finding.text, finding.offset) == ("SW123456", 3)
        assert finding.expression is not None
        assert _decodes_to(row, finding.expression) == "SW123456"

    def test_needle_mode_expression_round_trips_past_a_printable_header(self):
        row = _row(b"SW123456" + b"\x00" * 4, header=bytes.fromhex("625A31"))
        (finding,) = find_in_payloads([row], text_spans=_literal("123"))
        assert finding.text == "SW123456" and finding.expression is not None
        assert _decodes_to(row, finding.expression) == "SW123456"

    def test_a_search_for_the_header_finds_nothing(self):
        row = _row(b"\x00\x00", header=bytes.fromhex("625A31"))
        assert find_in_payloads([row], text_spans=_literal("bZ1")) == []

    @pytest.mark.parametrize(
        "data,start",
        [
            (bytes.fromhex("62F190AA"), 3),  # UDS 0x22: SID + 2-byte DID
            (bytes.fromhex("6101AA"), 2),  # KWP 0x21: SID + 1-byte LID
            (bytes.fromhex("5A90AA"), 2),  # KWP 0x1A: SID + record
            (bytes.fromhex("7F2231"), 3),  # a negative response is all header
            (bytes.fromhex("62F1"), 2),  # a short payload never over-reaches
            (bytes.fromhex("0042"), 0),  # an unknown service keeps every byte
            (b"", 0),
        ],
    )
    def test_data_start(self, data, start):
        assert data_start(data) == start

    def test_hex_search_still_sees_the_header(self):
        # --hex is an exact byte match; it reports what was asked for, header or not.
        row = _row(b"\x01", header=bytes.fromhex("62F190"))
        (finding,) = find_in_payloads([row], byte_spans=lambda b: find_hex(b, b"\xf1\x90"))
        assert finding.offset == 1


class TestCaseInsensitiveGrouping:
    def test_one_location_is_one_finding_whatever_the_case(self):
        rows = [_row(b"EHEL-MS2", weight=3), _row(b"ehel-ms2", weight=1)]
        (finding,) = find_in_payloads(rows, text_spans=_literal("ehel"), match_key=str.lower)
        assert (finding.hits, finding.match, finding.text) == (4, "EHEL", "EHEL-MS2")
        assert finding.variants == 2

    def test_without_a_match_key_each_reading_is_its_own_finding(self):
        rows = [_row(b"EHEL-MS2"), _row(b"ehel-ms2")]
        findings = find_in_payloads(rows, text_spans=_literal("ehel"))
        assert sorted(f.match for f in findings) == ["EHEL", "ehel"]


class TestMixedFrameLayouts:
    @staticmethod
    def _mixed() -> list[PayloadRow]:
        return [_row(b"ABCD"), _row(b"ABCD" + b"\x00" * 8)]  # 7 bytes (SF) and 15 (MF)

    def test_a_text_group_gets_no_expression_and_an_iso_tp_label(self):
        (finding,) = find_in_payloads(self._mixed(), text_spans=_literal("ABCD"))
        assert finding.mixed_frames and finding.expression is None
        assert render._at(finding, ByteNotation.WICAN) == "i3-6 (mixed SF/MF)"
        assert render._at(finding, ByteNotation.ISOTP) == "i3"

    def test_a_hex_group_gets_the_same_label(self):
        (finding,) = find_in_payloads(self._mixed(), byte_spans=lambda b: find_hex(b, b"AB"))
        assert finding.mixed_frames and finding.expression is None
        assert render._at(finding, ByteNotation.WICAN) == "i3-4 (mixed SF/MF)"

    def test_a_uniform_group_keeps_its_wican_label(self):
        (finding,) = find_in_payloads([_row(b"ABCD")], text_spans=_literal("ABCD"))
        assert not finding.mixed_frames
        assert render._at(finding, ByteNotation.WICAN) == "B4 (i3-6)"  # SF: one PCI byte


class TestScanNoteRows:
    def test_scan_note_bytes_are_never_definable(self):
        row = _row(b"95400G7470", field="scan notes")
        (finding,) = _inventory(row)
        assert finding.field == "scan notes" and finding.expression is None

    def test_a_truncated_row_marks_its_finding_lossy(self):
        row = _row(b"95400G7470", field="scan notes", lossy=True)
        (finding,) = find_in_payloads([row], text_spans=_literal("954"))
        assert finding.lossy and finding.expression is None


class TestSnippetAndHighlight:
    def test_snippet_cuts_long_text_with_ellipses(self):
        text = "x" * 40 + "NEEDLE" + "y" * 40
        cut = snippet(text, (40, 46))
        assert cut.startswith("…") and cut.endswith("…") and "NEEDLE" in cut
        assert snippet("short NEEDLE", (6, 12)) == "short NEEDLE"

    def test_snippet_collapses_whitespace(self):
        assert snippet("a \n\t b  NEEDLE", (8, 14)) == "a b NEEDLE"

    def test_highlight_finds_a_match_whose_whitespace_was_collapsed(self, monkeypatch):
        monkeypatch.setattr(ansi, "BOLD", "<b>")
        monkeypatch.setattr(ansi, "RESET", "</b>")
        text = snippet("before two   spaces after", (7, 19))
        assert render._highlight(text, "two   spaces") == "'before <b>two spaces</b> after'"

    def test_highlight_degrades_to_plain_quotes_when_the_match_is_absent(self):
        assert render._highlight("abc", "zzz") == "'abc'"
        assert render._highlight("abc", None) == "'abc'"


def test_string_locations_never_produce_an_empty_core():
    from canlib.commands.search.findings import _Run, string_locations

    row = _row(b"")
    runs = [_Run(0, 8, "A" * 8, row), _Run(4, 18, "B" * 14, row), _Run(12, 20, "C" * 8, row)]
    assert string_locations(runs) == [(4, 8), (12, 20)]
