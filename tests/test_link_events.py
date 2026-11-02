"""Tests for capture link events: when a recording could not see the car, and why.

Covers the pure leaf (``canlib.captures.link_events``), the journal round trip into a
capture file, the monitor's recorder (``canlib.modes._monitor_link_events``) and its
hooks in the controller, and the readers (``--sessions``, ``validate captures``).
Plan: ``plans/2026-11-02-capture-link-events.md``.
"""

import json
from datetime import datetime, timedelta

from jsonschema import Draft202012Validator

from canlib.captures import io as capture_io
from canlib.captures.journal import CaptureJournal, build_session_from_records, recover
from canlib.captures.link_events import (
    events_by_date,
    gap_intervals,
    in_closed_gap,
    session_link_events,
    summarize,
)
from canlib.commands.validate._common import CAPTURES_SCHEMA_FILE
from canlib.modes._monitor_link_events import LinkEventTracker

DAY = "2026-11-02"
T0 = datetime(2026, 11, 2, 14, 0, 0)


def _at(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


def _start(eid, at, *, date=DAY, cause="error"):
    return {
        "type": "event",
        "id": eid,
        "kind": "disconnect",
        "cause": cause,
        "date": date,
        "at": at,
    }


def _end(eid, at, *, date=DAY):
    return {"type": "event_end", "id": eid, "date": date, "at": at}


def _schema_validator():
    return Draft202012Validator(json.loads(CAPTURES_SCHEMA_FILE.read_text()))


class TestEventsByDate:
    def test_a_start_and_its_end_pair_into_one_interval(self):
        out = events_by_date([_start(1, "14:00:05.000"), _end(1, "14:01:00.500")], DAY)
        assert out == {
            DAY: [
                {
                    "at": "14:00:05.000",
                    "kind": "disconnect",
                    "cause": "error",
                    "until": "14:01:00.500",
                }
            ]
        }

    def test_an_unmatched_start_stays_open(self):
        out = events_by_date([_start(1, "14:00:05.000")], DAY)
        assert "until" not in out[DAY][0]

    def test_an_unmatched_end_is_ignored(self):
        assert events_by_date([_end(7, "14:01:00.000")], DAY) == {}

    def test_an_interval_across_midnight_is_split_per_day(self):
        recs = [
            _start(1, "23:59:50.000", date="2026-11-02"),
            _end(1, "00:00:20.000", date="2026-11-03"),
        ]
        out = events_by_date(recs, DAY)
        assert out["2026-11-02"] == [{"at": "23:59:50.000", "kind": "disconnect", "cause": "error"}]
        assert out["2026-11-03"] == [
            {"at": "00:00:00.000", "kind": "disconnect", "cause": "error", "until": "00:00:20.000"}
        ]

    def test_instants_and_sorting(self):
        recs = [
            {"type": "event", "kind": "resync", "count": 3, "date": DAY, "at": "14:05:00.000"},
            {"type": "event", "kind": "resync", "count": 1, "date": DAY, "at": "14:01:00.000"},
        ]
        out = events_by_date(recs, DAY)[DAY]
        assert [e["at"] for e in out] == ["14:01:00.000", "14:05:00.000"]
        assert "count" not in out[0]  # a single resync carries no count
        assert out[1]["count"] == 3

    def test_unknown_kinds_causes_and_times_are_dropped(self):
        recs = [
            {"type": "event", "kind": "teleport", "date": DAY, "at": "14:00:00"},
            {"type": "event", "kind": "resync", "date": DAY, "at": "soon"},
            {"type": "event", "id": 1, "kind": "disconnect", "cause": "gremlins", "at": "14:00:01"},
        ]
        out = events_by_date(recs, DAY)
        assert out == {DAY: [{"at": "14:00:01", "kind": "disconnect"}]}

    def test_missing_dates_fall_back_to_the_session_date(self):
        out = events_by_date([{"type": "event", "kind": "resync", "at": "14:00:00"}], DAY)
        assert list(out) == [DAY]


EVENTS = (
    {"at": "14:00:10", "until": "14:01:10", "kind": "disconnect"},
    {"at": "14:02:00", "kind": "resync", "count": 2},
    {"at": "14:10:00", "kind": "disconnect"},
)


class TestReaders:
    def test_gap_intervals_skips_instants(self):
        assert gap_intervals(EVENTS) == [(50410.0, 50470.0), (51000.0, None)]

    def test_in_closed_gap_is_strict_and_ignores_open_gaps(self):
        gaps = gap_intervals(EVENTS)
        assert in_closed_gap(gaps, 50420.0)
        assert not in_closed_gap(gaps, 50410.0)  # the last answer itself
        assert not in_closed_gap(gaps, 50470.0)  # the first answer after
        assert not in_closed_gap(gaps, 52000.0)  # inside an open gap: undecidable

    def test_summarize_measures_an_open_gap_to_the_session_end(self):
        summary = summarize(EVENTS, session_end=51030.0)
        assert summary == {"disconnects": 2, "gap_seconds": 90.0, "resyncs": 2}

    def test_session_link_events_tolerates_a_malformed_block(self):
        assert session_link_events({"link_events": "nope"}) == []
        assert session_link_events({"link_events": [1, {"kind": "x"}]}) == []


class TestJournalRoundTrip:
    def test_reconciled_session_carries_link_events_and_validates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="drive", transport="slcan-tcp")
        j.append("0x7EC", "2101", "6101AA", "14:00:00.000", DAY)
        j.append_event("disconnect", _at(0), event_id=1, cause="stale")
        j.append_event_end(1, _at(42.5))
        j.append_event("resync", _at(60), count=2)
        j.append("0x7EC", "2101", "6101BB", "14:00:42.500", DAY)
        j.update_meta(quality={"exchanges": 5})
        written = j.reconcile()

        data = capture_io.load_capture_file(written)
        session = data["sessions"][0]
        assert session["link_events"] == [
            {"at": "14:00:00.000", "kind": "disconnect", "cause": "stale", "until": "14:00:42.500"},
            {"at": "14:01:00.000", "kind": "resync", "count": 2},
        ]
        assert list(session)[-1] == "captures"
        assert list(_schema_validator().iter_errors(data)) == []
        # One event per line, like a capture.
        text = written.read_text()
        assert '{"at": "14:01:00.000", "kind": "resync", "count": 2}' in text

    def test_a_crash_mid_disconnect_recovers_open_ended(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="drive")
        j.append("0x7EC", "2101", "6101AA", "14:00:00.000", DAY)
        j.append_event("disconnect", _at(0), event_id=1, cause="error")
        j._close_fh()
        session = capture_io.load_capture_file(recover(j.path))["sessions"][0]
        assert session["link_events"] == [
            {"at": "14:00:00.000", "kind": "disconnect", "cause": "error"}
        ]

    def test_a_day_without_events_gets_none(self, tmp_path):
        recs = [
            {"type": "meta", "label": "L", "date": DAY},
            {
                "type": "capture",
                "rx": "0x7EC",
                "pid": "2101",
                "payload": "61",
                "date": DAY,
                "time": "14:00:00",
            },
            {
                "type": "capture",
                "rx": "0x7EC",
                "pid": "2101",
                "payload": "62",
                "date": "2026-11-03",
                "time": "00:01:00",
            },
            _start(1, "23:59:00", date=DAY),
            _end(1, "23:59:30", date=DAY),
        ]
        sessions = build_session_from_records(recs)
        assert "link_events" in sessions[0]
        assert "link_events" not in sessions[1]


class _ListJournal:
    """A journal stand-in that keeps what the tracker writes."""

    def __init__(self):
        self.rows: list[tuple] = []

    def append_event(self, kind, at, *, event_id=None, cause=None, count=None):
        self.rows.append(("event", kind, at, event_id, cause, count))

    def append_event_end(self, event_id, at):
        self.rows.append(("end", event_id, at))


class _Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class TestTracker:
    def _tracker(self, journal=None, now=None):
        journal = journal if journal is not None else _ListJournal()
        clock = _Clock(now or _at(100))
        return LinkEventTracker(lambda: journal, clock=clock), journal, clock

    def test_a_gap_runs_from_the_last_answer_to_the_next(self):
        tracker, journal, _clock = self._tracker()
        tracker.answered(_at(5).timestamp())
        tracker.disconnected("stale")
        assert tracker.gap_open
        tracker.answered(_at(50).timestamp())
        assert not tracker.gap_open
        assert journal.rows == [
            ("event", "disconnect", _at(5), 1, "stale", None),
            ("end", 1, _at(50)),
        ]

    def test_without_any_answer_the_gap_starts_now(self):
        tracker, journal, _clock = self._tracker(now=_at(7))
        tracker.disconnected("error")
        assert journal.rows[0][2] == _at(7)

    def test_a_second_failure_does_not_reopen(self):
        tracker, journal, _clock = self._tracker()
        tracker.disconnected("error")
        tracker.disconnected("stale")
        assert len(journal.rows) == 1

    def test_a_reply_already_in_flight_does_not_close_the_gap(self):
        tracker, journal, _clock = self._tracker()
        tracker.answered(_at(10).timestamp())
        tracker.disconnected("error")
        tracker.answered(_at(9).timestamp())
        tracker.answered(_at(10).timestamp())
        assert tracker.gap_open
        assert len(journal.rows) == 1

    def test_rotation_restates_an_open_gap_in_the_new_journal(self):
        first, second = _ListJournal(), _ListJournal()
        current = {"j": first}
        clock = _Clock(_at(30))
        tracker = LinkEventTracker(lambda: current["j"], clock=clock)
        tracker.disconnected("manual")
        current["j"] = second
        tracker.segment_rotated()
        assert first.rows == [("event", "disconnect", _at(30), 1, "manual", None)]
        assert second.rows == [("event", "disconnect", _at(30), 2, "manual", None)]
        tracker.answered(_at(40).timestamp())
        assert second.rows[-1] == ("end", 2, _at(40))

    def test_resyncs_are_instants_with_a_count_only_when_several(self):
        tracker, journal, _clock = self._tracker(now=_at(3))
        tracker.resynced(0)
        tracker.resynced(1)
        tracker.resynced(4)
        assert journal.rows == [
            ("event", "resync", _at(3), None, None, None),
            ("event", "resync", _at(3), None, None, 4),
        ]

    def test_not_recording_is_a_no_op(self):
        tracker = LinkEventTracker(lambda: None)
        tracker.answered()
        tracker.disconnected("error")
        tracker.resynced(2)
        assert tracker.gap_open  # still tracked, just not written


class TestControllerHooks:
    def _controller(self):
        from canlib.modes.monitor import MonitorController

        steps = [{"type": "query", "ecu": "BMS", "pids": ["2101"]}]
        c = MonitorController(object(), steps, {"ecus": {}}, verbose=False)
        c.last_queries = [{"ecu": "BMS"}]
        journal = _ListJournal()
        c.link_events = LinkEventTracker(lambda: journal)
        return c, journal

    def test_a_stale_trip_opens_a_gap_at_the_last_answer(self, monkeypatch):
        from canlib import config

        monkeypatch.setattr(config, "stale_cycles_before_reconnect", lambda: 2)
        c, journal = self._controller()
        c._displayify(("BMS", "2101"), {"raw_hex": "6101AA", "acquired_at": _at(1).timestamp()})
        for _ in range(2):
            c._cycle_answered = False
            c._displayify(("BMS", "2101"), {"error": "timeout"})
            c._check_liveness({"BMS"}, {"BMS"})
        assert c.disconnected
        assert journal.rows == [("event", "disconnect", _at(1), 1, "stale", None)]
        # The first coherent answer after the reconnect closes it; an NRC counts.
        c._displayify(("BMS", "2101"), {"error": "NRC 0x31", "acquired_at": _at(9).timestamp()})
        assert journal.rows[-1] == ("end", 1, _at(9))

    def test_mark_disconnected_records_the_cause(self):
        c, journal = self._controller()
        c.mark_disconnected("manual")
        assert c.disconnected
        assert journal.rows[0][4] == "manual"


class TestSegmentRotation:
    def test_an_open_gap_is_restated_in_the_next_segment(self, tmp_path):
        from canlib.modes._monitor_record import MonitorRecorder

        from .test_monitor_record import FakeController

        c = FakeController(tmp_path)
        rec = MonitorRecorder(c)
        c.link_events = LinkEventTracker(lambda: rec.journal)
        rec.open_journal("seg1", None, None)
        rec.observe([("BMS", [{"pid": "2101", "raw_hex": "6101AA"}])])
        c.link_events.disconnected("error")
        rec.new_segment("seg2", None, None)
        first = capture_io.load_capture_file(next(tmp_path.glob("*.json")))["sessions"][0]
        assert first["link_events"][0]["kind"] == "disconnect"
        assert "until" not in first["link_events"][0]
        rows = [json.loads(x) for x in rec.journal.path.read_text().splitlines()]
        assert [r["type"] for r in rows] == ["meta", "event"]


def _session(events, times=("14:00:00.000", "14:05:00.000")):
    return {
        "date": DAY,
        "label": "drive",
        "link_events": events,
        "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101", "time": t} for t in times],
    }


class TestSessionsView:
    def test_text_view_reports_gaps(self, tmp_path, capsys):
        from canlib.captures.store import flatten_session
        from canlib.commands.captures.sessions import cmd_sessions

        events = [
            {"at": "14:01:00.000", "until": "14:02:00.000", "kind": "disconnect"},
            {"at": "14:04:00.000", "kind": "disconnect"},
            {"at": "14:03:00.000", "kind": "resync", "count": 3},
        ]
        entries = flatten_session(tmp_path / f"{DAY}.json", 0, _session(events), rx_index={})
        assert entries[0]["session_link_events"] == events
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "gaps:" in out
        assert "2 disconnects (2m)" in out
        assert "3 resyncs" in out

    def test_json_view_carries_the_events(self, tmp_path, capsys):
        from canlib.captures.store import flatten_session
        from canlib.commands.captures.sessions import cmd_sessions

        events = [{"at": "14:01:00.000", "kind": "resync"}]
        entries = flatten_session(tmp_path / f"{DAY}.json", 0, _session(events), rx_index={})
        cmd_sessions(entries, as_json=True)
        assert json.loads(capsys.readouterr().out)[0]["link_events"] == events

    def test_a_session_without_events_prints_no_gaps_line(self, tmp_path, capsys):
        from canlib.captures.store import flatten_session
        from canlib.commands.captures.sessions import cmd_sessions

        session = _session([])
        del session["link_events"]
        cmd_sessions(flatten_session(tmp_path / f"{DAY}.json", 0, session, rx_index={}))
        assert "gaps:" not in capsys.readouterr().out


class TestValidateWarning:
    def test_a_payload_inside_a_closed_disconnect_warns(self, tmp_path):
        from canlib.commands.validate.link_events import link_event_warnings

        events = [{"at": "14:01:00.000", "until": "14:10:00.000", "kind": "disconnect"}]
        path = tmp_path / f"{DAY}.json"
        capture_io.dump_capture_file(path, {"sessions": [_session(events)]})
        (warning,) = link_event_warnings(path)
        assert "1 payload capture(s) timed inside a recorded disconnect" in str(warning)

    def test_payloads_at_the_bounds_do_not_warn(self, tmp_path):
        from canlib.commands.validate.link_events import link_event_warnings

        events = [{"at": "14:00:00.000", "until": "14:05:00.000", "kind": "disconnect"}]
        path = tmp_path / f"{DAY}.json"
        capture_io.dump_capture_file(path, {"sessions": [_session(events)]})
        assert link_event_warnings(path) == []
