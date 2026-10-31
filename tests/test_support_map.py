"""Tests for support-map detection (the leaf) and declaration handling.

Rows are synthetic: a constant map followed by data whose moving bits are drawn only from the
positions the map marks (MSB first), so the expected fit is known by construction.
"""

import math
import random

import pytest

from canlib.analysis.support_map import (
    MIN_EXPLAINED,
    MapFit,
    Slot,
    _chance_log10,
    best_fit,
    continuation_bits,
    corroborated,
    detect_support_maps,
    family_chance_log10,
    marked_bits,
    marked_byte_indices,
    reverse_bits,
)
from canlib.uds.support_map import MapDecl, SupportMapError, declaration, resolve

BIT_MAP = bytes([0xB5, 0x3F, 0xF4, 0xEA])  # marks: B 0,2,3,5,7 | 2-7 | 0,1,2,3,5 | 0,1,2,4,6


def _bit_rows(mask: bytes = BIT_MAP, n: int = 80, seed: int = 1, extra: dict | None = None):
    """``n`` rows of ``mask`` + one data byte per map byte, bits moving only where marked."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        data = bytearray()
        for j, m in enumerate(mask):
            allowed = reverse_bits(m)
            value = rng.getrandbits(8) & allowed
            data.append(value | (extra or {}).get(j, 0) * rng.getrandbits(1))
        rows.append(bytes(mask) + bytes(data))
    return rows


class TestPrimitives:
    def test_reverse_bits(self):
        assert reverse_bits(0x01) == 0x80
        assert reverse_bits(0xB5) == 0xAD

    def test_map_bit_7_marks_data_bit_0(self):
        assert marked_bits(0x80) == {0}
        assert marked_bits(0x01) == {7}
        assert marked_bits(0xB5) == {0, 2, 3, 5, 7}

    def test_byte_indices_run_msb_first_across_the_map(self):
        assert marked_byte_indices(b"\xa0\x01", 16) == {0, 2, 15}

    def test_byte_indices_stop_at_the_covered_length(self):
        assert marked_byte_indices(b"\xff\xff", 5) == {0, 1, 2, 3, 4}

    def test_a_continuation_extends_a_mark_upward(self):
        assert continuation_bits({2}, {3}) == {3}

    def test_a_field_is_at_most_three_bits_wide(self):
        assert continuation_bits({2}, {3, 4, 5}) == {3, 4}

    def test_a_gap_breaks_the_chain(self):
        # Bit 4 never set: bit 5 is not the rest of the field that starts at 2.
        assert continuation_bits({2}, {3, 5, 6, 7}) == {3}

    def test_nothing_continues_without_a_mark_below(self):
        assert continuation_bits({4}, {1, 2}) == frozenset()

    def test_a_marked_bit_is_never_its_own_continuation(self):
        assert continuation_bits({2, 3}, {3}) == frozenset()


class TestDetection:
    def test_finds_a_per_bit_map(self):
        fit = best_fit(detect_support_maps(_bit_rows()))
        assert fit is not None
        assert (fit.unit, fit.at, fit.length) == ("bit", 0, 4)
        assert fit.mask == BIT_MAP
        assert not fit.violations
        assert fit.verdict() == "strong"

    def test_marked_slots_carry_their_behaviour(self):
        fit = best_fit(detect_support_maps(_bit_rows()))
        by_pos = {(s.offset, s.bit): s for s in fit.marked}
        assert by_pos[(4, 0)].state == "moving"
        assert len(fit.marked) == sum(len(marked_bits(m)) for m in BIT_MAP)

    def test_a_set_bit_outside_the_map_is_a_violation(self):
        mask = bytes([0x80, 0x80, 0x80, 0x80])  # only bit 0 of each byte
        rows = []
        rng = random.Random(3)
        for _ in range(80):
            rows.append(mask + bytes([rng.getrandbits(1) | (rng.getrandbits(1) << 6)] * 4))
        fit = best_fit(detect_support_maps(rows))
        assert {s.bit for s in fit.violations} == {6}
        assert fit.verdict() == "rejected"
        assert fit.verdict(max_violations=4) != "rejected"

    def test_the_bit_directly_above_a_mark_is_a_continuation_not_a_violation(self):
        mask = bytes([0x80, 0x00, 0x00, 0x00])  # bit 0 of the first byte is the field start
        rng = random.Random(5)
        rows = [mask + bytes([rng.getrandbits(2), 0, 0, 0]) for _ in range(80)]
        fit = best_fit(detect_support_maps(rows, max_len=4))
        assert [s.bit for s in fit.continuations] == [1]
        assert not fit.violations

    def test_a_constant_data_byte_is_not_evidence(self):
        # A byte that never changes proves nothing about the map that covers it.
        mask = bytes([0xFF, 0x00, 0x00, 0x00])
        rows = [mask + bytes([0b1010_0000, 0, 0, 0]) for _ in range(40)]
        assert best_fit(detect_support_maps(rows)) is None

    @pytest.mark.parametrize("mask", [b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"])
    def test_trivial_maps_are_rejected(self, mask):
        assert detect_support_maps([mask + bytes([i % 7, 3, 4, 5]) for i in range(60)]) == []

    def test_a_map_that_changes_between_rows_is_not_a_map(self):
        rng = random.Random(9)
        rows = [bytes(rng.getrandbits(8) for _ in range(8)) for _ in range(80)]
        assert not any(f.length == 4 and f.verdict() == "strong" for f in detect_support_maps(rows))

    def test_a_few_stale_rows_are_dropped_not_fatal(self):
        rows = _bit_rows(n=100)
        rows[3] = bytes(8)  # a different map once in 100 rows
        fit = best_fit(detect_support_maps(rows))
        assert fit.mask == BIT_MAP
        assert fit.n_rows == 99

    def test_too_few_rows_is_no_evidence(self):
        assert detect_support_maps(_bit_rows(n=5)) == []

    def test_finds_a_per_byte_map(self):
        # 32 covered bytes; map AF E0 00 00 marks bytes 0,2,4-7,8,9,10.
        mask = bytes([0xAF, 0xE0, 0x00, 0x00])
        marked = marked_byte_indices(mask, 32)
        rng = random.Random(7)
        rows = []
        for _ in range(80):
            data = bytearray(32)
            for i in marked:
                data[i] = rng.getrandbits(8)
            rows.append(mask + bytes(data))
        fit = best_fit(detect_support_maps(rows))
        assert (fit.unit, fit.length) == ("byte", 4)
        assert not fit.violations
        assert fit.verdict() == "strong"

    def test_a_live_byte_outside_a_per_byte_map_is_a_violation(self):
        mask = bytes([0xC0, 0x00, 0x00, 0x00])
        rng = random.Random(11)
        rows = []
        for _ in range(80):
            data = bytearray(16)
            data[0], data[1], data[5] = (rng.getrandbits(8) for _ in range(3))
            rows.append(mask + bytes(data))
        fit = best_fit(detect_support_maps(rows))
        assert fit.unit == "byte"
        assert [s.offset for s in fit.violations] == [4 + 5]

    def test_the_map_must_start_where_asked(self):
        # A counter byte in front: nothing constant starts at offset 0, so the map is at 1.
        rows = [bytes([i % 251]) + r for i, r in enumerate(_bit_rows())]
        assert best_fit(detect_support_maps(rows, max_at=0)) is None
        assert best_fit(detect_support_maps(rows, max_at=1)).at == 1


class TestEvidence:
    def test_chance_is_the_hypergeometric_tail(self):
        # 1 map byte marking bits 0-3; 4 of them move: C(4,4)/C(8,4) = 1/70.
        rng = random.Random(2)
        rows = [bytes([0xF0, rng.getrandbits(4)]) for _ in range(60)]
        fit = next(f for f in detect_support_maps(rows, max_len=1) if f.unit == "bit")
        assert fit.inside == 4
        assert fit.chance_log10 == pytest.approx(-math.log10(70), abs=1e-6)

    def test_more_explained_bits_means_less_chance(self):
        few = best_fit(detect_support_maps(_bit_rows(mask=bytes([0xF0, 0, 0, 0]))))
        many = best_fit(detect_support_maps(_bit_rows()))
        assert many.chance_log10 < few.chance_log10

    def test_the_placement_penalty_is_paid(self):
        fit = best_fit(detect_support_maps(_bit_rows()))
        assert fit.penalised_log10 == pytest.approx(fit.chance_log10 + math.log10(fit.placements))

    def test_an_even_chance_is_exactly_zero(self):
        # C(1,1)/C(8,1) = 1/8 against 8 placements: exactly 1, on every platform's libm (lgamma
        # left an ulp that printed as 10^-0.0 on macOS and 10^0.0 on Linux).
        fit = _fit(_chance_log10([(1, 1, 8)]))
        assert fit.penalised_log10 == 0.0
        assert math.copysign(1.0, fit.penalised_log10) == 1.0


def _fit(chance: float, inside: int = 6, violations: int = 0, key=("bit", 0, 4)) -> MapFit:
    unit, at, length = key
    slots = tuple(Slot(4, b, 5, 2, 10) for b in range(violations))
    return MapFit(
        unit, at, length, b"\x01", 80, 4, 4, (), (), slots, inside, chance, placements=8
    )  # fmt: skip


class TestSiblings:
    def test_weak_siblings_are_strong_together(self):
        a, b = _fit(-1.6), _fit(-1.6)
        assert a.verdict() == "weak"
        assert family_chance_log10([a, b]) == pytest.approx(-3.2 + math.log10(8))
        assert corroborated({"X": a, "Y": b}) == {"X", "Y"}

    def test_a_lone_fit_has_no_family(self):
        assert family_chance_log10([_fit(-1.6)]) == 0.0
        assert corroborated({"X": _fit(-1.6)}) == set()

    def test_a_violating_sibling_does_not_testify(self):
        good, bad = _fit(-1.6), _fit(-1.6, violations=1)
        assert corroborated({"X": good, "Y": bad}) == set()

    def test_a_near_total_map_does_not_testify(self):
        # Chance -0.4: a map that marks almost everything is satisfied by anything.
        weak, vacuous = _fit(-1.6), _fit(-0.4)
        assert corroborated({"X": weak, "Y": vacuous}) == set()

    def test_only_the_same_placement_counts(self):
        a, b = _fit(-1.6), _fit(-1.6, key=("byte", 0, 4))
        assert corroborated({"X": a, "Y": b}) == set()

    def test_a_fit_with_too_little_set_is_never_promoted(self):
        thin = _fit(-1.6, inside=MIN_EXPLAINED - 1)
        assert corroborated({"X": _fit(-1.6), "Y": _fit(-1.6), "Z": thin}) == {"X", "Y"}


class TestDeclaration:
    def test_a_valid_mapping(self):
        d = MapDecl.from_mapping({"at": 0, "length": 4, "unit": "bit", "order": "msb"})
        assert d == MapDecl(0, 4, "bit")
        assert d.flow() == "{at: 0, length: 4, unit: bit, order: msb}"

    def test_order_defaults_to_msb(self):
        assert MapDecl.from_mapping({"at": 1, "length": 2, "unit": "byte"}).order == "msb"

    @pytest.mark.parametrize(
        "raw, match",
        [
            ({"length": 4, "unit": "bit"}, "needs 'at'"),
            ({"at": 0, "unit": "bit"}, "needs 'length'"),
            ({"at": 0, "length": 4}, "needs 'unit'"),
            ({"at": 0, "length": 4, "unit": "nibble"}, "unit"),
            ({"at": 0, "length": 0, "unit": "bit"}, "length"),
            ({"at": 0, "length": 5, "unit": "bit"}, "length"),
            ({"at": -1, "length": 4, "unit": "bit"}, "at"),
            ({"at": True, "length": 4, "unit": "bit"}, "integer"),
            ({"at": 0, "length": "4", "unit": "bit"}, "integer"),
            ({"at": 0, "length": 4, "unit": "bit", "order": "lsb"}, "order"),
            ({"at": 0, "length": 4, "unit": "bit", "width": 2}, "unknown"),
        ],
    )
    def test_malformed_declarations_are_refused(self, raw, match):
        with pytest.raises(SupportMapError, match=match):
            MapDecl.from_mapping(raw)

    def test_a_pid_without_one(self):
        assert declaration({"status": "active"}) is None
        assert declaration(None) is None

    def test_a_non_mapping_is_refused(self):
        with pytest.raises(SupportMapError, match="mapping"):
            declaration({"support_map": "yes"})


# 62 BC03 | map FD EE 3C 73 | data 0A 00 00 00
PAYLOAD = bytes.fromhex("62BC03FDEE3C730A000000")


class TestResolve:
    def test_per_bit_map_read_off_a_payload(self):
        rm = resolve(MapDecl(0, 4, "bit"), PAYLOAD, header=3)
        assert rm.mask == bytes.fromhex("FDEE3C73")
        assert rm.map_indices == (3, 4, 5, 6)
        assert rm.covered == range(7, 11)
        assert rm.signal_bits(7) == marked_bits(0xFD)
        assert rm.is_signal_byte(9)

    def test_a_byte_with_no_marks_is_not_a_signal(self):
        payload = bytes.fromhex("62BC03FD003C730A000000")
        rm = resolve(MapDecl(0, 4, "bit"), payload, header=3)
        assert not rm.is_signal_byte(8)
        assert rm.signal_bits(8) == frozenset()

    def test_per_byte_map(self):
        payload = bytes.fromhex("22" + "BC03" + "A0000000" + "11223344")
        rm = resolve(MapDecl(0, 4, "byte"), bytes.fromhex("62BC03A0000000112233"), header=3)
        assert rm.is_signal_byte(7) and not rm.is_signal_byte(8) and rm.is_signal_byte(9)
        assert rm.signal_bits(7) == frozenset(range(8))
        assert payload  # the unused literal documents the DID layout above

    def test_offset_shifts_the_map(self):
        payload = bytes.fromhex("62BC03FFFD01020304")
        rm = resolve(MapDecl(1, 2, "bit"), payload, header=3)
        assert rm.map_indices == (4, 5)

    def test_a_payload_too_short_to_hold_the_map(self):
        assert resolve(MapDecl(0, 4, "bit"), bytes.fromhex("62BC03FDEE"), header=3) is None
        assert resolve(MapDecl(0, 4, "bit"), bytes.fromhex("62BC03FDEE3C73"), header=3) is None

    def test_outside_splits_bits_into_outside_and_continuation(self):
        rm = resolve(MapDecl(0, 4, "bit"), bytes.fromhex("62BC03800000000000"), header=3)
        # Map marks only bit 0 of the first covered byte.
        assert rm.outside(7, {0, 1, 6}) == ({6}, {1})
        assert rm.outside(99, {3}) == (set(), set())  # not covered: not judged

    def test_outside_in_a_per_byte_map_is_the_whole_unmarked_byte(self):
        rm = resolve(MapDecl(0, 1, "byte"), bytes.fromhex("62BC0380AABBCC"), header=3)
        assert rm.outside(4, {3}) == (set(), set())
        assert rm.outside(5, {3}) == ({3}, set())
