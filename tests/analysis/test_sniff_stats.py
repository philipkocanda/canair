"""Tests for the per-ID aggregation of sniffed CAN frames."""

from canair.analysis.sniff_stats import SniffStats


class TestSniffStats:
    def test_first_frame(self):
        s = SniffStats()
        s.record(0x7E4, bytes.fromhex("0201FF"), ts=100.0)
        (row,) = s.snapshot()
        assert row["id"] == 0x7E4
        assert row["count"] == 1
        assert row["hz"] == 0.0
        assert row["data"] == bytes.fromhex("0201FF")
        assert row["changed"] == b"\x00\x00\x00"  # nothing changed yet

    def test_changed_byte_mask(self):
        s = SniffStats()
        s.record(0x100, bytes.fromhex("AA00CC"), ts=0.0)
        s.record(0x100, bytes.fromhex("AA01CC"), ts=0.1)  # byte 1 changed
        s.record(0x100, bytes.fromhex("AA02CC"), ts=0.2)  # byte 1 again
        (row,) = s.snapshot()
        assert row["count"] == 3
        assert row["changed"] == b"\x00\x01\x00"  # only index 1 ever varied
        assert row["data"] == bytes.fromhex("AA02CC")

    def test_rate_hz(self):
        s = SniffStats()
        for i in range(11):  # 11 frames over 1.0s -> 10 intervals -> 10 Hz
            s.record(0x200, b"\x00", ts=float(i) * 0.1)
        (row,) = s.snapshot()
        assert round(row["hz"]) == 10

    def test_dlc_change_marks_all_volatile(self):
        s = SniffStats()
        s.record(0x300, b"\x11\x22", ts=0.0)
        s.record(0x300, b"\x11\x22\x33", ts=0.1)  # length changed
        (row,) = s.snapshot()
        assert row["changed"] == b"\x01\x01\x01"

    def test_multiple_ids_sorted(self):
        s = SniffStats()
        s.record(0x7E8, b"\x01", ts=0.0)
        s.record(0x100, b"\x02", ts=0.0)
        s.record(0x455, b"\x03", ts=0.0)
        ids = [r["id"] for r in s.snapshot()]
        assert ids == [0x100, 0x455, 0x7E8]

    def test_total_frames(self):
        s = SniffStats()
        s.record(0x1, b"\x00", ts=0.0)
        s.record(0x1, b"\x00", ts=0.1)
        s.record(0x2, b"\x00", ts=0.1)
        assert s.total_frames == 3

    def test_clear(self):
        s = SniffStats()
        s.record(0x1, b"\x00", ts=0.0)
        s.clear()
        assert s.snapshot() == []
        assert s.total_frames == 0
