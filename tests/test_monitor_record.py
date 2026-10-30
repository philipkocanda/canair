"""Unit tests for the monitor recorder's span-aware state back-fill.

The recorder (``canlib.modes._monitor_record.MonitorRecorder``) accumulates the
states auto-suggested across a segment's whole lifetime, so a segment that
charged then went idle still reconciles as ``charging`` — a point-in-time
snapshot at reconcile time alone would miss it (the 2026-07-27 bug).

Drives the recorder with a lightweight fake controller (no CAN, no TTY) and a
real :class:`canlib.captures.journal.CaptureJournal` so reconciliation is
exercised end-to-end into an on-disk capture file.
"""

import json

from canlib.modes._monitor_record import MonitorRecorder


class FakeController:
    """Minimal stand-in exposing only the surface the recorder reads.

    ``suggested_states`` returns whatever ``next_state`` is set to, so a test can
    script the state changing cycle-to-cycle (charging → idle) the way the live
    states.yaml auto-suggest would from decoded values. ``next_state`` accepts a
    single token (composed into a one-element list) or a list of tokens.
    """

    def __init__(self, captures_dir, *, keep_mode=None, save=True):
        self.captures_dir = captures_dir
        self.keep_mode = keep_mode
        self.save = save
        self.prev_hex: dict = {}
        self.next_state: str | list[str] | None = None

    def query_label(self) -> str:
        return "BMS:2101"

    def suggested_states(self) -> list[str]:
        if not self.next_state:
            return []
        if isinstance(self.next_state, str):
            return [self.next_state]
        return list(self.next_state)

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


class TestSaveDialogOffer:
    """The `s` dialog pre-fills the span's union, not the keypress-instant state.

    Regression: a parked 12 V top-up that ended before `s` was pressed saved as
    plain PARKED, because the dialog offered only what matched at that moment.
    """

    def _topup_then_parked(self, rec, c):
        c.next_state = ["PARKED", "TOPUP"]
        rec.observe([_frame("2101", "6101AA")])
        c.next_state = ["PARKED"]
        rec.observe([_frame("2101", "6101BB")])

    def test_snapshot_save_offers_and_writes_the_union(self, tmp_path):
        c = FakeController(tmp_path, save=False)
        rec = MonitorRecorder(c)
        self._topup_then_parked(rec, c)

        offered = rec.offer_states()
        assert offered == ["PARKED", "TOPUP"]
        rec.save_now("topup", ",".join(offered), None)
        assert _read_session(tmp_path)["vehicle_states"] == ["PARKED", "TOPUP"]

    def test_snapshot_save_offer_resets_after_each_write(self, tmp_path):
        c = FakeController(tmp_path, save=False)
        rec = MonitorRecorder(c)
        self._topup_then_parked(rec, c)
        rec.save_now("first", ",".join(rec.offer_states()), None)

        c.next_state = ["READY"]
        rec.observe([_frame("2101", "6101CC")])
        assert rec.offer_states() == ["READY"]  # the next write's span only

    def test_journal_offer_is_segment_union(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)
        self._topup_then_parked(rec, c)
        assert rec.offer_states() == ["PARKED", "TOPUP"]

    def test_accepting_the_offer_keeps_the_segment_auto(self, tmp_path):
        # Accepting the pre-fill unchanged must not pin the state: a state seen
        # after the keypress still reaches the reconciled session.
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)
        c.next_state = ["PARKED"]
        rec.observe([_frame("2101", "6101AA")])

        rec.save_now("seg", ",".join(rec.offer_states()), None)
        assert rec.state_explicit is False

        c.next_state = ["PARKED", "TOPUP"]
        rec.observe([_frame("2101", "6101BB")])
        rec.new_segment("seg2", None, None)
        assert _read_session(tmp_path)["vehicle_states"] == ["PARKED", "TOPUP"]

    def test_edited_offer_pins_the_state(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg", None, None)
        self._topup_then_parked(rec, c)

        rec.offer_states()
        rec.save_now("seg", "TOPUP", None)
        assert rec.state_explicit is True
        assert rec.offer_states() == ["TOPUP"]  # the dialog re-shows the user's choice

    def test_new_segment_offer_is_current_state_and_stays_auto(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg1", None, None)
        self._topup_then_parked(rec, c)

        offered = rec.offer_states(next_segment=True)
        assert offered == ["PARKED"]
        rec.new_segment("seg2", ",".join(offered), None)
        assert rec.state_explicit is False


class TestSegmentHistory:
    def test_new_segment_records_closed_segment_summary(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.session_label = "seg1"
        rec.open_journal("seg1", None, None)

        c.next_state = "charging"
        rec.observe([_frame("2101", "6101AA")])
        rec.observe([_frame("2101", "6101BB")])
        assert rec.segments == []  # nothing closed yet

        rec.new_segment("seg2", None, None)
        assert len(rec.segments) == 1
        seg = rec.segments[0]
        assert seg["label"] == "seg1"
        assert seg["states"] == ["charging"]
        assert seg["frames"] == 2  # both observed payloads
        assert seg["written"] is not None
        assert seg["started_at"] is not None and seg["ended_at"] is not None

    def test_segment_frame_baseline_resets_per_segment(self, tmp_path):
        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        rec.open_journal("seg1", None, None)
        rec.observe([_frame("2101", "6101AA")])  # 1 frame in seg1
        rec.new_segment("seg2", None, None)
        assert rec.segment_frames_base == rec.total_frames == 1
        rec.observe([_frame("2101", "6101CC")])  # 1 frame in seg2
        # Per-segment count is total minus the baseline at segment start.
        assert rec.total_frames - rec.segment_frames_base == 1
