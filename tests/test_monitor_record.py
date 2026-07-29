"""Unit tests for the monitor recorder's span-aware state back-fill.

The recorder (``canlib.modes._monitor_record.MonitorRecorder``) accumulates the
states auto-suggested across a segment's whole lifetime, so a segment that
charged then went idle still reconciles as ``charging`` — a point-in-time
snapshot at reconcile time alone would miss it (the 2026-07-27 bug).

Drives the recorder with a lightweight fake controller (no CAN, no TTY) and a
real :class:`canlib.capture_journal.CaptureJournal` so reconciliation is
exercised end-to-end into an on-disk capture file.
"""

import json

from canlib.modes._monitor_record import MonitorRecorder


class FakeController:
    """Minimal stand-in exposing only the surface the recorder reads.

    ``suggested_state`` returns whatever ``next_state`` is set to, so a test can
    script the state changing cycle-to-cycle (charging → idle) the way the live
    states.yaml auto-suggest would from decoded values.
    """

    def __init__(self, captures_dir, *, keep_mode=None, save=True):
        self.captures_dir = captures_dir
        self.keep_mode = keep_mode
        self.save = save
        self.prev_hex: dict = {}
        self.next_state: str | None = None

    def query_label(self) -> str:
        return "BMS:2101"

    def suggested_state(self) -> str | None:
        return self.next_state

    def _ecu_ref(self, ecu_label: str) -> str:
        return "0x7EC"


def _frame(pid: str, raw: str):
    """One EcuFrame-shaped ``(ecu_label, [entry, ...])`` for observe()."""
    return ("BMS", [{"pid": pid, "raw_hex": raw}])


def _read_session(captures_dir):
    files = list(captures_dir.glob("*.json"))
    assert len(files) == 1, files
    data = json.loads(files[0].read_text())
    sessions = data["sessions"] if isinstance(data, dict) else data
    assert len(sessions) == 1, sessions
    return sessions[0]


class TestSpanAwareBackfill:
    def test_charging_then_idle_backfills_charging(self, tmp_path):
        # A segment that observed `charging` early then went idle (no state) must
        # still reconcile as charging — the exit snapshot alone would be empty.
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)

        c.next_state = "charging"
        rec.observe([_frame("2101", "6101AA")])  # cycle 1: charging
        c.next_state = None
        rec.observe([_frame("2101", "6101BB")])  # cycle 2: idle, no state

        assert rec.observed_states == {"charging": None}
        assert rec._backfill_states() == ["charging"]

    def test_backfill_is_union_of_all_observed_states(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)

        c.next_state = "charging"
        rec.observe([_frame("2101", "6101AA")])
        c.next_state = "ready"
        rec.observe([_frame("2101", "6101BB")])

        # Insertion order preserved; union of everything seen.
        assert rec._backfill_states() == ["charging", "ready"]

    def test_backfill_none_when_nothing_observed(self, tmp_path):
        c = FakeController(tmp_path)  # suggested_state stays None
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)

        rec.observe([_frame("2101", "6101AA")])

        assert rec.observed_states == {}
        assert rec._backfill_states() is None

    def test_new_segment_backfills_span_and_resets_accumulator(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg1", None, None)

        c.next_state = "charging"
        rec.observe([_frame("2101", "6101AA")])
        c.next_state = None  # idle at rotate time
        rec.observe([_frame("2101", "6101BB")])

        # Rotate to a fresh, un-labelled segment: the closing segment reconciles
        # with the charging it saw, and the accumulator resets for the new one.
        rec.new_segment("seg2", None, None)
        assert rec.observed_states == {}

        session = _read_session(tmp_path)
        assert session["vehicle_states"] == ["charging"]

    def test_explicit_state_not_clobbered_by_backfill(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg1", None, None)

        c.next_state = "charging"
        rec.observe([_frame("2101", "6101AA")])
        # User explicitly labels the segment `ready` via the save dialog.
        rec.save_now("seg1", "ready", None)
        assert rec.state_explicit is True

        rec.new_segment("seg2", None, None)
        session = _read_session(tmp_path)
        assert session["vehicle_states"] == ["READY"]
