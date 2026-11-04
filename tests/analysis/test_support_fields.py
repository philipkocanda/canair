"""Tests for the field-map solver: one bitmap bit per parameter of a record, any width.

Rows are synthetic, shaped like the captures the design was checked against (Ioniq VCU 2101,
Kona BMS 220101, Ioniq BMS 21F2), so the expected split is known by construction.
"""

import random

from canair.analysis.support_fields import Field, known_fields, mask_bit, solve_fields


def _rows(mask: bytes, layout, n=60, seed=1):
    """``n`` rows of ``mask`` + data: ``layout`` is ``(width, live)`` per parameter."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        data = bytearray(mask)
        for width, live in layout:
            data += bytes(rng.getrandbits(8) if live else 0 for _ in range(width))
        rows.append(bytes(data))
    return rows


def _spans(layout, start=4):
    out, at = [], start
    for width, _live in layout:
        out.append((at, at + width))
        at += width
    return out


class TestKnownFields:
    def test_a_combination_of_two_parameters_is_dropped(self):
        # BATTERY_POWER reads current (2 bytes) and voltage (2 bytes): one signal, two fields.
        assert known_fields([{10, 11}, {12, 13}, {10, 11, 12, 13}]) == [(10, 12), (12, 14)]

    def test_non_contiguous_reads_are_dropped(self):
        assert known_fields([{5, 6}, {5, 6, 11, 12}]) == [(5, 7)]

    def test_overlapping_reads_are_one_parameter(self):
        # A coarse and a fine reading of one value; several flags of one byte.
        assert known_fields([{11, 12}, {12}, {5}, {5}]) == [(5, 6), (11, 13)]

    def test_empty(self):
        assert known_fields([]) == []


class TestMaskBit:
    def test_msb_first_is_the_spec_order(self):
        assert mask_bit(b"\x80\x00", 0) and not mask_bit(b"\x80\x00", 1)

    def test_lsb_first_counts_from_the_last_byte(self):
        assert mask_bit(b"\x00\x01", 0, "lsb") and not mask_bit(b"\x00\x01", 15, "lsb")


# VCU 2101: FF E0 marks 11 parameters; five of them are 2 bytes wide.
VCU = [(1, False), (1, True), (1, True), (2, True), (2, True), (2, True), (2, True), (2, True),
       (1, True), (1, True), (1, True)]  # fmt: skip
VCU_MASK = bytes.fromhex("FFE00000")


class TestExact:
    def test_a_fully_defined_record_splits_one_way(self):
        rows = _rows(VCU_MASK, VCU)
        fit = solve_fields(rows, _spans(VCU)[1:])  # the first byte is undefined
        assert fit.verdict == "exact"
        assert fit.field_counts == (11, 11)
        assert [f.end - f.start for f in fit.fields] == [w for w, _ in VCU]

    def test_the_undefined_parameter_is_open(self):
        fit = solve_fields(_rows(VCU_MASK, VCU), _spans(VCU)[1:])
        first = fit.fields[0]
        assert (first.start, first.end, first.number, first.marked, first.defined) == (
            4, 5, 1, True, False,
        )  # fmt: skip

    def test_one_parameter_too_many_is_broken(self):
        fit = solve_fields(_rows(bytes.fromhex("FFF00000"), VCU), _spans(VCU)[1:])
        assert fit.verdict == "broken"
        assert "at most 11" in fit.reason


# Kona BMS 220101: parameters 13, 20 and 21 unsupported, their bytes constant 00.
KONA = [(1, True)] * 12 + [(1, False)] + [(1, True)] * 6 + [(1, False), (1, False)]
KONA += [(1, True)] * 11
KONA_MASK = bytes.fromhex("FFF7E7FF")


class TestFill:
    def test_unmarked_parameters_are_fill(self):
        known = [s for s, (_w, live) in zip(_spans(KONA), KONA, strict=True) if live]
        fit = solve_fields(_rows(KONA_MASK, KONA), known)
        assert fit.verdict == "exact"
        assert sorted(fit.fill) == [16, 23, 24]
        assert [f.number for f in fit.fields if not f.marked] == [13, 20, 21]

    def test_fill_is_found_even_when_the_split_is_not_unique(self):
        # Leave two live neighbours undefined: they may be one 2-byte or two 1-byte
        # parameters, but the constant bytes the map leaves unmarked are fill either way.
        known = [s for s, (_w, live) in zip(_spans(KONA), KONA, strict=True) if live][:-2]
        fit = solve_fields(_rows(KONA_MASK, KONA), known)
        assert fit.verdict in ("exact", "consistent")
        assert {16, 23, 24} <= fit.fill

    def test_an_unmarked_parameter_that_moves_is_broken(self):
        layout = list(KONA)
        layout[12] = (1, True)  # the unsupported module temperature moves after all
        fit = solve_fields(_rows(KONA_MASK, layout), _spans(layout))
        assert fit.verdict == "broken"
        assert "unmarked parameter constant" in fit.reason


class TestSplits:
    def test_undefined_bytes_that_group_two_ways_are_consistent(self):
        layout = [(1, True), (2, True)]
        rows = _rows(bytes.fromhex("C0000000"), [(1, True), (1, True), (1, True)])
        fit = solve_fields(rows, _spans(layout)[:1])
        assert fit.verdict == "consistent"
        assert fit.splits == 2 and not fit.fields

    def test_a_defined_field_is_never_cut(self):
        # A 2-byte signal at the front: three marks cannot be three 1-byte parameters there.
        rows = _rows(bytes.fromhex("E0000000"), [(2, True), (1, True)])
        assert solve_fields(rows, [(4, 6)]).verdict == "broken"

    def test_lsb_first(self):
        mask = bytes.fromhex("00000007")
        fit = solve_fields(_rows(mask, [(1, True)] * 3), _spans([(1, True)] * 3), order="lsb")
        assert fit.verdict == "exact" and fit.order == "lsb"
        assert solve_fields(_rows(mask, [(1, True)] * 3), _spans([(1, True)] * 3)).verdict == (
            "broken"
        )


class TestFurtherBitmaps:
    def test_parameter_32_is_the_next_bitmap(self):
        # 31 parameters, a second FF FF FF FF bitmap, then 32 more (BMS 21F2's shape).
        layout = [(1, True)] * 31
        mask = bytes.fromhex("FFFFFFFF")
        rows = [r + mask + r[4:] + b"\x01" for r in _rows(mask, layout)]
        known = _spans(layout) + [(o + 35, o + 36) for o in range(4, 36)]
        fit = solve_fields(rows, known)
        assert fit.verdict == "exact"
        assert [o for o, _m in fit.blocks] == [0, 35]
        assert fit.field_counts == (63, 63)
        maps = [f for f in fit.fields if f.is_map]
        assert maps == [Field(35, 39, 32, 0, True, False, True)]

    def test_parameter_32_may_end_the_record(self):
        layout = [(1, True)] * 32
        fit = solve_fields(_rows(bytes.fromhex("FFFFFFFF"), layout), _spans(layout))
        assert fit.verdict == "exact" and len(fit.blocks) == 1


class TestGuards:
    def test_too_short_for_a_bitmap(self):
        assert solve_fields([b"\xff\xff"] * 30, []) is None

    def test_a_bitmap_that_changes_is_not_one(self):
        rows = [bytes([0xF0 | (i % 2), 0, 0, 0, 1, 2]) for i in range(40)]
        assert solve_fields(rows, []) is None

    def test_no_rows(self):
        assert solve_fields([], []) is None
