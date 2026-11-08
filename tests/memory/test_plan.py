"""Tests for canair.memory.plan and canair.memory.job — the image planner (pure)."""

from __future__ import annotations

from canair.memory.job import (
    DATA,
    ERASED,
    FULL,
    SAMPLE,
    SPARSE,
    THOROUGH,
    Area,
    JobPlan,
    continuity_extents,
    missing_extents,
)
from canair.memory.plan import (
    alias_candidates,
    alias_pairs,
    aliased,
    areas,
    bisect_mid,
    changes,
    estimate,
    flash_size,
    is_erased,
)
from canair.memory.store import Dump, Extent

MIB = 1024 * 1024


def test_erased_is_all_ff_or_all_00():
    assert is_erased(b"\xff" * 8) and is_erased(b"\x00" * 8)
    assert not is_erased(b"\xff\x00") and not is_erased(b"")


def test_flash_size_is_the_smallest_common_size_that_holds_the_span():
    assert flash_size(4 * MIB) == 4 * MIB
    assert flash_size(4 * MIB - 1) == 4 * MIB
    assert flash_size(1 * MIB + 1) == 3 * MIB // 2


class TestAliasing:
    def test_a_candidate_needs_room_for_both_samples(self):
        assert max(alias_candidates(0, 0xFFFF, 0x40)) == 0x8000

    def test_pairs_compare_across_the_period_and_to_the_far_end(self):
        pairs = alias_pairs(0, 0xFFFF, 0x4000, 0x40)
        assert (0, 0x4000) in pairs and (0, 0xC000) in pairs
        assert all(b - a in (0x4000, 0x8000, 0xC000) for a, b in pairs)

    def test_erased_samples_are_no_evidence(self):
        assert not aliased([(b"\xff" * 4, b"\xff" * 4)] * 8)
        assert aliased([(b"ab", b"ab"), (b"cd", b"cd"), (b"\xff", b"\xff")])

    def test_one_mismatch_refutes(self):
        assert not aliased([(b"ab", b"ab"), (b"cd", b"cd"), (b"ef", b"eg")])


class TestSurvey:
    def test_changes_are_the_neighbours_that_differ(self):
        assert changes({0: True, 0x1000: True, 0x2000: False, 0x3000: True}) == [
            (0x1000, 0x2000),
            (0x2000, 0x3000),
        ]

    def test_bisection_stops_at_the_granule(self):
        assert bisect_mid(0, 0x1000, 0x40) == 0x800
        assert bisect_mid(0, 0x40, 0x40) is None

    def test_areas_join_samples_of_one_kind(self):
        points = {0: False, 0x1000: False, 0x1F80: False, 0x1FC0: True, 0x3000: True}
        assert areas(points, 0, 0x3FFF) == [Area(0, 0x1FBF, DATA), Area(0x1FC0, 0x3FFF, ERASED)]


AREAS = (Area(0, 0x17FF, DATA), Area(0x1800, 0x3FFF, ERASED))


def _plan(mode=SPARSE, areas=AREAS):
    return JobPlan(mode, 0, 0x3FFF, tuple(areas), ceiling=61, sample_size=61)


class TestPlan:
    def test_an_extent_with_any_data_is_read_in_full(self):
        assert [(e.start, e.kind) for e in _plan().extents()] == [
            (0, FULL),
            (0x1000, FULL),
            (0x2000, SAMPLE),
            (0x3000, SAMPLE),
        ]

    def test_thorough_reads_every_extent_in_full(self):
        assert {e.kind for e in _plan(THOROUGH).extents()} == {FULL}

    def test_extents_cover_only_the_areas(self):
        plan = _plan(areas=(Area(0, 0xFFF, DATA), Area(0x3000, 0x3FFF, ERASED)))
        assert [e.start for e in plan.extents()] == [0, 0x3000]

    def test_the_plan_round_trips_through_the_manifest(self):
        plan = JobPlan(
            SPARSE,
            0,
            0x3FFF,
            (Area(0, 0x3FFF, DATA),),
            ceiling=61,
            software="8D:X",
            state="CHARGING",
            alias=0x4000,
            baseline={"VCU": ["U1112"], "BMS": None},
            seed=7,
        )
        assert JobPlan.from_doc(plan.to_doc()) == plan

    def test_estimate_counts_reads_at_the_block(self):
        est = estimate(_plan(THOROUGH), block=61, seconds_per_read=0.05)
        assert est.full_bytes == 0x4000 and est.sampled_bytes == 0
        assert est.reads == 2 * 4 * -(-0x1000 // 60.5)
        assert round(est.seconds, 3) == round(est.reads * 0.05, 3)


def test_missing_and_continuity_extents():
    plan = _plan()
    dump = Dump("BCM", 0x7A0, "24", "t", [], extents=[Extent(0, 0xFFF, "a")])
    assert [e.start for e in missing_extents(plan, dump)] == [0x1000, 0x2000, 0x3000]
    dump.extents += [Extent(0x1000, 0x1FFF, "b"), Extent(0x2000, 0x2FFF, "c")]
    assert [e.start for e in continuity_extents(dump)] == [0, 0x1000, 0x2000]
