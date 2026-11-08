"""Tests for canair.memory.verify — the image's stability and end checks (pure)."""

from __future__ import annotations

import pytest

from canair.memory.job import DATA, ERASED, SPARSE, THOROUGH, Area, JobPlan
from canair.memory.store import Dump, Extent, Stretch
from canair.memory.verify import (
    FIRST_READ_ERROR,
    REREAD_ERROR,
    SAME,
    VOLATILE,
    dtc_changes,
    find_strings,
    identity_strings,
    recheck_starts,
    settle,
)


class TestSettle:
    def test_two_equal_reads_are_the_same(self):
        assert settle("a", "a") == SAME

    def test_a_third_read_decides_two_of_three(self):
        assert settle("a", "b", "a") == REREAD_ERROR
        assert settle("a", "b", "b") == FIRST_READ_ERROR
        assert settle("a", "b", "c") == VOLATILE

    def test_two_different_reads_need_a_third(self):
        with pytest.raises(ValueError):
            settle("a", "b")


def _extents(n, events=()):
    return [
        Extent(i * 0x1000, i * 0x1000 + 0xFFF, "h", events=events if i == 5 else ())
        for i in range(n)
    ]


class TestRecheck:
    AREAS = (Area(0, 0x9FFF, DATA), Area(0xA000, 0x63FFF, ERASED))

    def test_thorough_rechecks_everything(self):
        plan = JobPlan(THOROUGH, 0, 0x63FFF, self.AREAS, ceiling=61)
        assert len(recheck_starts(plan, _extents(100))) == 100

    def test_sparse_rechecks_events_area_edges_and_a_seeded_share(self):
        plan = JobPlan(SPARSE, 0, 0x63FFF, self.AREAS, ceiling=61, seed=3)
        picked = recheck_starts(plan, _extents(100, events=("reread",)))
        assert {0, 0x9000, 0xA000, 0x63000, 0x5000} <= set(picked)
        assert len(picked) == 5 + round(95 * 0.05)
        assert picked == recheck_starts(plan, _extents(100, events=("reread",)))


def test_identity_strings_never_include_the_vin():
    identity = {"part_number": "36601-0E250", "sw_id": "EAE", "vin": "KMHXXXX", "ecu_id": ""}
    assert identity_strings(identity) == {"part_number": "36601-0E250", "sw_id": "EAE"}


def test_find_strings_reports_every_address():
    dump = Dump(
        "VCU",
        0x7E2,
        "kwp",
        "t",
        [],
        stretches=[Stretch(0x100, b"xxPN1xxPN1"), Stretch(0x900, b"PN1")],
    )
    assert find_strings(dump, ["PN1", "nope"]) == {"PN1": [0x102, 0x107, 0x900], "nope": []}


def test_dtc_changes():
    changes = dtc_changes(
        {"VCU": ["U1112"], "BMS": [], "MCU": None},
        {"VCU": ["U1112", "P0A0D"], "BMS": [], "MCU": []},
    )
    by = {c.ecu: c for c in changes}
    assert by["VCU"].new == ("P0A0D",) and not by["BMS"].new and by["MCU"].unknown
