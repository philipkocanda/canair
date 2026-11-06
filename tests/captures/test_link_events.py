"""Tests for capture link events: when a recording could not see the car, and why.

Covers the pure leaf (``canair.captures.link_events``), the journal round trip into a
capture file, the monitor's recorder (``canair.live.monitor.link_events``) and its
hooks in the controller, and the readers (``--sessions``, ``validate captures``).
Plan: ``plans/2026-11-02-capture-link-events.md``.
"""

import json
from datetime import datetime, timedelta

from jsonschema import Draft202012Validator

from canair.captures import io as capture_io
from canair.captures.journal import CaptureJournal, build_session_from_records, recover
from canair.captures.link_events import (
    events_by_date,
    gap_intervals,
    in_closed_gap,
    session_link_events,
    summarize,
)
from canair.cli.profile_context import active
from canair.live.monitor.link_events import LinkEventTracker
from canair.validation._common import CAPTURES_SCHEMA_FILE
from tests.support.monitor_controller import make_controller

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
        assert set(summary) == {"disconnect", "resync"}
        assert summary["disconnect"].count == 2
        assert summary["disconnect"].seconds == 90.0
        assert summary["resync"].count == 2

    def test_session_link_events_tolerates_a_malformed_block(self):
        assert session_link_events({"link_events": "nope"}) == []
        assert session_link_events({"link_events": [1, {"kind": "x"}]}) == []


class TestJournalRoundTrip:
    def test_reconciled_session_carries_link_events_and_validates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="drive", transport="slcan-tcp", profile=active())
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
        j = CaptureJournal.open(tmp_path, label="drive", profile=active())
        j.append("0x7EC", "2101", "6101AA", "14:00:00.000", DAY)
        j.append_event("disconnect", _at(0), event_id=1, cause="error")
        j._close_fh()
        session = capture_io.load_capture_file(recover(j.path, profile=active()))["sessions"][0]
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
        sessions = build_session_from_records(recs, profile=active())
        assert "link_events" in sessions[0]
        assert "link_events" not in sessions[1]


class _ListJournal:
    """A journal stand-in that keeps what the tracker writes."""

    def __init__(self):
        self.rows: list[tuple] = []
        self.scopes: list[dict] = []

    def append_event(self, kind, at, *, event_id=None, **scope):
        self.rows.append(("event", kind, at, event_id, scope.get("cause"), scope.get("count")))
        self.scopes.append({"kind": kind, "id": event_id, **scope})

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

        steps = [{"type": "query", "ecu": "BMS", "pids": ["2101"]}]
        c = make_controller(object(), steps, {"ecus": {}}, verbose=False)
        c.last_queries = [{"ecu": "BMS"}]
        journal = _ListJournal()
        c.link_events = LinkEventTracker(lambda: journal)
        return c, journal

    def test_a_stale_trip_opens_a_gap_at_the_last_answer(self, monkeypatch):
        from canair.config import transport as config_transport

        monkeypatch.setattr(config_transport, "stale_cycles_before_reconnect", lambda: 2)
        c, journal = self._controller()
        c._displayify(("BMS", "2101"), {"raw_hex": "6101AA", "acquired_at": _at(1).timestamp()})
        for _ in range(2):
            c.health.cycle_answered = False
            c._displayify(("BMS", "2101"), {"error": "timeout"})
            c.health.check_liveness({"BMS"}, {"BMS"})
        assert c.disconnected
        assert journal.rows == [("event", "disconnect", _at(1), 1, "stale", None)]
        # The first coherent answer after the reconnect closes it; an NRC counts.
        c._displayify(
            ("BMS", "2101"), {"error": "NRC 0x31", "nrc": 0x31, "acquired_at": _at(9).timestamp()}
        )
        assert journal.rows[-1] == ("end", 1, _at(9))

    def test_mark_disconnected_records_the_cause(self):
        c, journal = self._controller()
        c.mark_disconnected("manual")
        assert c.disconnected
        assert journal.rows[0][4] == "manual"


class TestSegmentRotation:
    def test_an_open_gap_is_restated_in_the_next_segment(self, tmp_path):
        from canair.live.monitor.record import MonitorRecorder
        from tests.support.monitor_recorder import FakeController

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
        from canair.captures.store import flatten_session
        from canair.cli.commands.captures.sessions import cmd_sessions

        events = [
            {"at": "14:01:00.000", "until": "14:02:00.000", "kind": "disconnect"},
            {"at": "14:04:00.000", "kind": "disconnect"},
            {"at": "14:03:00.000", "kind": "resync", "count": 3},
        ]
        entries = flatten_session(
            tmp_path / f"{DAY}.json", 0, _session(events), rx_index={}, profile=active()
        )
        assert entries[0]["session_link_events"] == events
        cmd_sessions(entries)
        out = capsys.readouterr().out
        assert "gaps:" in out
        assert "2 disconnects (2m)" in out
        assert "3 resyncs" in out

    def test_json_view_carries_the_events(self, tmp_path, capsys):
        from canair.captures.store import flatten_session
        from canair.cli.commands.captures.sessions import cmd_sessions

        events = [{"at": "14:01:00.000", "kind": "resync"}]
        entries = flatten_session(
            tmp_path / f"{DAY}.json", 0, _session(events), rx_index={}, profile=active()
        )
        cmd_sessions(entries, as_json=True)
        assert json.loads(capsys.readouterr().out)[0]["link_events"] == events

    def test_a_session_without_events_prints_no_gaps_line(self, tmp_path, capsys):
        from canair.captures.store import flatten_session
        from canair.cli.commands.captures.sessions import cmd_sessions

        session = _session([])
        del session["link_events"]
        cmd_sessions(
            flatten_session(tmp_path / f"{DAY}.json", 0, session, rx_index={}, profile=active())
        )
        assert "gaps:" not in capsys.readouterr().out


class TestValidateWarning:
    def test_a_payload_inside_a_closed_disconnect_warns(self, tmp_path):
        from canair.validation.link_events import link_event_warnings

        events = [{"at": "14:01:00.000", "until": "14:10:00.000", "kind": "disconnect"}]
        path = tmp_path / f"{DAY}.json"
        capture_io.dump_capture_file(path, {"sessions": [_session(events)]})
        (warning,) = link_event_warnings(path)
        assert "1 payload capture(s) timed inside a recorded gap" in str(warning)

    def test_payloads_at_the_bounds_do_not_warn(self, tmp_path):
        from canair.validation.link_events import link_event_warnings

        events = [{"at": "14:00:00.000", "until": "14:05:00.000", "kind": "disconnect"}]
        path = tmp_path / f"{DAY}.json"
        capture_io.dump_capture_file(path, {"sessions": [_session(events)]})
        assert link_event_warnings(path) == []


# ---------------------------------------------------------------------------
# Scoped gaps: paused, ecu_silent, unpolled, backoff
# ---------------------------------------------------------------------------


class TestScopedEvents:
    def test_scoped_kinds_keep_their_scope_and_need_an_ecu(self):
        recs = [
            {"type": "event", "id": 1, "kind": "ecu_silent", "rx": "0x7EC", "at": "14:00:00"},
            {
                "type": "event",
                "id": 2,
                "kind": "unpolled",
                "rx": "0x7EC",
                "pid": "2105",
                "at": "14:00:01",
            },
            {
                "type": "event",
                "id": 3,
                "kind": "backoff",
                "rx": "0x7EA",
                "factor": 4,
                "at": "14:00:02",
            },
            {"type": "event", "id": 4, "kind": "ecu_silent", "at": "14:00:03"},  # no ECU
            {
                "type": "event",
                "id": 5,
                "kind": "backoff",
                "rx": "0x7EA",
                "at": "14:00:04",
            },  # no factor
            {"type": "event", "id": 6, "kind": "unpolled", "rx": "BMS", "at": "14:00:05"},  # a name
            {
                "type": "event",
                "id": 7,
                "kind": "backoff",
                "rx": "0x7EA",
                "pid": "22b002",
                "factor": 2,
                "at": "14:00:06",
            },
        ]
        out = events_by_date(recs, DAY)[DAY]
        assert out == [
            {"at": "14:00:00", "kind": "ecu_silent", "rx": "0x7EC"},
            {"at": "14:00:01", "kind": "unpolled", "rx": "0x7EC", "pid": "2105"},
            {"at": "14:00:02", "kind": "backoff", "rx": "0x7EA", "factor": 4},
            {"at": "14:00:06", "kind": "backoff", "rx": "0x7EA", "pid": "22B002", "factor": 2},
        ]

    def test_applies_to(self):
        from canair.captures.link_events import applies_to

        whole = {"kind": "disconnect"}
        ecu = {"kind": "ecu_silent", "rx": "0x7EC"}
        pid = {"kind": "unpolled", "rx": "0x7EC", "pid": "2105"}
        assert applies_to(whole) and applies_to(whole, "0x7EA", "2101")
        assert applies_to(ecu, "0x7ec", "2101") and not applies_to(ecu, "0x7EA", "2101")
        assert not applies_to(ecu)  # a session-wide question
        assert applies_to(pid, "0x7EC", "2105") and not applies_to(pid, "0x7EC", "2101")

    def test_gap_intervals_by_scope_and_kind(self):
        from canair.captures.link_events import SESSION_GAP_KINDS

        events = [
            {"at": "14:00:00", "until": "14:00:10", "kind": "paused"},
            {"at": "14:01:00", "until": "14:01:10", "kind": "ecu_silent", "rx": "0x7EC"},
            {"at": "14:02:00", "kind": "backoff", "rx": "0x7EC", "factor": 2},  # not a gap
        ]
        assert len(gap_intervals(events, "0x7EC", "2101")) == 2
        assert len(gap_intervals(events, "0x7EA", "2101")) == 1
        assert len(gap_intervals(events, "0x7EC", "2101", kinds=SESSION_GAP_KINDS)) == 1

    def test_summarize_per_kind(self):
        events = [
            {"at": "14:00:00", "until": "14:00:30", "kind": "ecu_silent", "rx": "0x7EC"},
            {"at": "14:01:00", "until": "14:01:10", "kind": "ecu_silent", "rx": "0x7EA"},
            {"at": "14:02:00", "until": "14:03:00", "kind": "backoff", "rx": "0x7EC", "factor": 2},
            {"at": "14:03:00", "kind": "backoff", "rx": "0x7EC", "factor": 8},
        ]
        summary = summarize(events, session_end=parse_key("14:05:00"))
        assert summary["ecu_silent"].count == 2
        assert summary["ecu_silent"].seconds == 40.0
        assert summary["ecu_silent"].rxs == ["0x7EC", "0x7EA"]
        assert summary["backoff"].factor == 8
        assert summary["backoff"].seconds == 180.0

    def test_a_scoped_journal_round_trip_validates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="drive", profile=active())
        j.append("0x7EC", "2101", "6101AA", "14:00:00.000", DAY)
        j.append_event("unpolled", _at(1), event_id=1, rx="0x7EC", pid="2105")
        j.append_event("backoff", _at(2), event_id=2, rx="0x7EA", factor=4)
        j.append_event_end(2, _at(9))
        data = capture_io.load_capture_file(j.reconcile())
        assert list(_schema_validator().iter_errors(data)) == []
        assert data["sessions"][0]["link_events"][1] == {
            "at": "14:00:02.000",
            "kind": "backoff",
            "rx": "0x7EA",
            "factor": 4,
            "until": "14:00:09.000",
        }


def parse_key(raw: str) -> float:
    from canair.states.spans import parse_time_key

    key = parse_time_key(raw)
    assert key is not None
    return key


RX = {"BMS": "0x7EC", "VCU": "0x7EA"}


class TestScopedTracker:
    def _tracker(self):
        journal, clock = _ListJournal(), _Clock(_at(100))
        tracker = LinkEventTracker(lambda: journal, rx_of=RX.get, clock=clock)
        return tracker, journal, clock

    def test_an_ecu_goes_silent_after_three_misses_while_others_answer(self):
        tracker, journal, _clock = self._tracker()
        tracker.answered(_at(5).timestamp(), ecu="BMS")
        for _ in range(2):
            tracker.ecu_polled("BMS", answered=False, link_ok=True)
        assert journal.scopes == []
        tracker.ecu_polled("BMS", answered=False, link_ok=True)
        assert journal.scopes == [{"kind": "ecu_silent", "id": 1, "rx": "0x7EC"}]
        assert journal.rows[0][2] == _at(5)  # from its last answer
        tracker.answered(_at(120).timestamp(), ecu="BMS")
        assert journal.rows[-1] == ("end", 1, _at(120))

    def test_misses_during_a_disconnect_do_not_count(self):
        tracker, journal, _clock = self._tracker()
        for _ in range(5):
            tracker.ecu_polled("BMS", answered=False, link_ok=False)
        assert journal.scopes == []

    def test_an_answer_resets_the_count(self):
        tracker, journal, _clock = self._tracker()
        for answered in (False, False, True, False, False):
            tracker.ecu_polled("BMS", answered=answered, link_ok=True)
        assert journal.scopes == []

    def test_an_ecu_without_an_address_records_nothing(self):
        tracker, journal, _clock = self._tracker()
        for _ in range(3):
            tracker.ecu_polled("XYZ", answered=False, link_ok=True)
        tracker.backoff("XYZ", 4)
        assert journal.scopes == []

    def test_backoff_is_one_interval_per_factor(self):
        tracker, journal, clock = self._tracker()
        tracker.backoff("BMS", 2)
        clock.at = _at(110)
        tracker.backoff("BMS", 4)
        clock.at = _at(120)
        tracker.backoff("BMS", 1)
        assert [r[0] for r in journal.rows] == ["event", "end", "event", "end"]
        assert [s.get("factor") for s in journal.scopes] == [2, 4]
        assert not tracker.is_open("backoff", "BMS")

    def test_each_pid_backs_off_on_its_own(self):
        tracker, journal, _clock = self._tracker()
        tracker.backoff("BMS", 2, pid="2101")
        tracker.backoff("BMS", 4, pid="2102")
        tracker.backoff("BMS", 1, pid="2101")
        assert [(s.get("pid"), s.get("factor")) for s in journal.scopes] == [
            ("2101", 2),
            ("2102", 4),
        ]
        assert not tracker.is_open("backoff", "BMS", "2101")
        assert tracker.is_open("backoff", "BMS", "2102")

    def test_pause_and_resume(self):
        tracker, journal, clock = self._tracker()
        tracker.paused(True)
        tracker.paused(True)
        clock.at = _at(140)
        tracker.paused(False)
        assert journal.rows == [
            ("event", "paused", _at(100), 1, None, None),
            ("end", 1, _at(140)),
        ]

    def test_a_dropped_pid_is_unpolled_until_readded(self):
        tracker, journal, clock = self._tracker()
        both = {("BMS", "2101"), ("BMS", "2105")}
        tracker.poll_set_changed(both, {("BMS", "2101")})
        assert journal.scopes == [{"kind": "unpolled", "id": 1, "rx": "0x7EC", "pid": "2105"}]
        clock.at = _at(130)
        tracker.poll_set_changed({("BMS", "2101")}, both)
        assert journal.rows[-1] == ("end", 1, _at(130))

    def test_a_dropped_ecu_is_one_event_split_when_partly_readded(self):
        tracker, journal, clock = self._tracker()
        old = {("BMS", "2101"), ("BMS", "2105"), ("VCU", "2101")}
        tracker.poll_set_changed(old, {("VCU", "2101")})
        assert journal.scopes == [{"kind": "unpolled", "id": 1, "rx": "0x7EC"}]
        clock.at = _at(130)
        tracker.poll_set_changed({("VCU", "2101")}, {("VCU", "2101"), ("BMS", "2101")})
        assert journal.rows[-2] == ("end", 1, _at(130))
        assert journal.scopes[-1] == {"kind": "unpolled", "id": 2, "rx": "0x7EC", "pid": "2105"}

    def test_rotation_restates_every_open_event(self):
        first, second = _ListJournal(), _ListJournal()
        current = {"j": first}
        tracker = LinkEventTracker(lambda: current["j"], rx_of=RX.get, clock=_Clock(_at(1)))
        tracker.paused(True)
        tracker.backoff("VCU", 8)
        current["j"] = second
        tracker.segment_rotated()
        assert [s["kind"] for s in second.scopes] == ["paused", "backoff"]
        assert second.scopes[1]["factor"] == 8


class TestControllerScopedHooks:
    def _controller(self):

        c = make_controller(object(), [], {"ecus": {}}, verbose=False)
        journal = _ListJournal()
        c.link_events = LinkEventTracker(lambda: journal, rx_of={"BMS": "0x7EC"}.get)
        c.health.link_events = c.link_events
        return c, journal

    def test_a_silent_ecu_among_answering_ones(self):
        from canair.live.monitor.backoff import PollCost

        c, journal = self._controller()
        for _ in range(3):
            c.health.cycle_answered = True  # VCU answered this tick
            c.health.poll_costs = {
                ("BMS", "2101"): PollCost(reads=1, answered=0),
                ("BMS", "2102"): PollCost(reads=1, answered=0),
                ("VCU", "2101"): PollCost(reads=1, answered=1),
            }
            c.health.observe_ecu_answers()
        assert journal.scopes == [{"kind": "ecu_silent", "id": 1, "rx": "0x7EC"}]

    def test_backoff_changes_are_recorded(self, monkeypatch):
        from canair.live.monitor.backoff import PollCost

        c, journal = self._controller()
        monkeypatch.setattr(c.backoff, "observe", lambda _key, _cost: 4)
        c.health.poll_costs = {("BMS", "2101"): PollCost(reads=1, answered=0)}
        c.health.apply_backoff()
        assert journal.scopes == [
            {"kind": "backoff", "id": 1, "rx": "0x7EC", "pid": "2101", "factor": 4}
        ]

    def test_an_ecu_with_one_answering_pid_is_not_silent(self):
        from canair.live.monitor.backoff import PollCost

        c, journal = self._controller()
        for _ in range(3):
            c.health.cycle_answered = True
            c.health.poll_costs = {
                ("BMS", "2101"): PollCost(reads=1, answered=0),
                ("BMS", "2102"): PollCost(reads=1, answered=1),
            }
            c.health.observe_ecu_answers()
        assert journal.scopes == []


class TestScopedFill:
    def _rows(self, events, rx="0x7EC", pid="2101"):
        from canair.analysis.fill import FillPolicy, hold_until_vector, session_end_times

        rows = []
        for sec in (0, 600):
            rows.append(
                {
                    "file": f"{DAY}.json",
                    "_session_idx": 0,
                    "keep_mode": "changes",
                    "date": DAY,
                    "time": _at(sec).strftime("%H:%M:%S.%f"),
                    "ecu_addr": rx,
                    "pid": pid,
                    "session_link_events": events,
                }
            )
        dts = [_at(0), _at(600)]
        return hold_until_vector(
            rows, dts, session_ends=session_end_times(rows), policy=FillPolicy()
        )

    def test_a_silent_ecu_cuts_only_its_own_rows(self):
        events = [{"at": "14:00:30.000", "kind": "ecu_silent", "rx": "0x7EC"}]
        assert self._rows(events)[0] == _at(30)
        assert self._rows(events, rx="0x7EA")[0] == _at(600)

    def test_an_unpolled_pid_cuts_only_that_pid(self):
        events = [{"at": "14:00:30.000", "kind": "unpolled", "rx": "0x7EC", "pid": "2105"}]
        assert self._rows(events, pid="2105")[0] == _at(30)
        assert self._rows(events, pid="2101")[0] == _at(600)

    def test_a_backoff_does_not_cut(self):
        events = [{"at": "14:00:30.000", "kind": "backoff", "rx": "0x7EC", "factor": 4}]
        assert self._rows(events)[0] == _at(600)


class TestFillReport:
    def test_cut_carries_are_counted_and_reported(self):
        from canair.analysis.align import LoadedPid
        from canair.analysis.fill import FillPolicy, session_end_times
        from canair.cli.args.join import fill_summaries, fill_summary_line

        events = [{"at": "14:01:00.000", "until": "14:02:00.000", "kind": "disconnect"}]
        caps = [
            {
                "file": f"{DAY}.json",
                "_session_idx": 0,
                "keep_mode": "changes",
                "date": DAY,
                "time": _at(sec).strftime("%H:%M:%S.%f"),
                "ecu_addr": "0x7EC",
                "pid": "2101",
                "payload": "6101AA",
                "session_link_events": events,
            }
            for sec in (0, 600)
        ]
        lp = LoadedPid("BMS", "2101", captures=caps, session_ends=session_end_times(caps))
        (summary,) = fill_summaries([lp], FillPolicy(), tol_s=5.0)
        assert summary.n_cut == 1
        assert summary.as_json()["gap_cut_rows"] == 1
        line = fill_summary_line([summary], FillPolicy())
        assert line is not None and "1 value not carried into a recorded gap" in line


class TestInferredGaps:
    def _times(self, *secs):
        return [_at(s).strftime("%H:%M:%S.%f")[:-3] for s in secs]

    def test_a_long_hole_in_a_keep_all_session_is_inferred(self):
        from canair.captures.inferred_gaps import infer_gaps

        times = self._times(*range(0, 20, 2), 200, 202)
        assert infer_gaps(times) == [(parse_key("14:00:18"), parse_key("14:03:20"))]

    def test_run_length_sessions_and_short_sessions_infer_nothing(self):
        from canair.captures.inferred_gaps import infer_gaps

        times = self._times(*range(0, 20, 2), 200)
        assert infer_gaps(times, keep_mode="changes") == []
        assert infer_gaps(self._times(0, 200)) == []

    def test_a_recorded_gap_explains_the_hole(self):
        from canair.captures.inferred_gaps import infer_gaps

        times = self._times(*range(0, 20, 2), 200, 202)
        events = [{"at": "14:00:18.000", "until": "14:03:20.000", "kind": "disconnect"}]
        assert infer_gaps(times, events) == []

    def test_the_spacing_scales_the_threshold(self):
        from canair.captures.inferred_gaps import infer_gaps

        # One PID polled once a minute: a two-minute hole is not ten times its spacing.
        assert infer_gaps(self._times(*range(0, 600, 60), 720)) == []

    def test_sessions_view_prints_inferred_gaps(self, tmp_path, capsys):
        from canair.captures.store import flatten_session
        from canair.cli.commands.captures.sessions import cmd_sessions

        session = _session([], times=self._times(*range(0, 20, 2), 200, 202))
        del session["link_events"]
        entries = flatten_session(
            tmp_path / f"{DAY}.json", 0, session, rx_index={}, profile=active()
        )
        cmd_sessions(entries)
        assert "inferred gaps: 1" in capsys.readouterr().out
        cmd_sessions(entries, as_json=True)
        out = json.loads(capsys.readouterr().out)
        assert out[0]["inferred_gaps"] == [{"at": "14:00:18", "until": "14:03:20"}]


class TestStepGaps:
    def _entry(self, sec, events, rx="0x7EC", pid="2101"):
        return {
            "_path": "x.json",
            "_session_idx": 0,
            "ecu": "BMS",
            "ecu_addr": rx,
            "pid": pid,
            "time": _at(sec).strftime("%H:%M:%S.%f"),
            "session_link_events": events,
        }

    def test_a_gap_between_two_frames_is_reported_once(self):
        from canair.tui.apps.captures_step.gaps import describe_gap, gaps_before

        events = [{"at": "14:00:10.000", "until": "14:01:10.000", "kind": "disconnect"}]
        a, b, c = (self._entry(s, events) for s in (0, 80, 90))
        (gap,) = gaps_before([b], _at(80), [a], _at(0))
        assert describe_gap(gap, {}) == "disconnect 14:00:10-14:01:10 (1m)"
        assert gaps_before([c], _at(90), [b], _at(80)) == []

    def test_scoped_gaps_show_only_for_the_ecu_in_view(self):
        from canair.tui.apps.captures_step.gaps import describe_gap, gaps_before

        events = [{"at": "14:00:10.000", "kind": "ecu_silent", "rx": "0x7EC"}]
        mine = self._entry(80, events)
        other = self._entry(80, events, rx="0x7EA")
        (gap,) = gaps_before([mine], _at(80), [self._entry(0, events)], _at(0))
        assert describe_gap(gap, {"0X7EC": "BMS"}) == "BMS silent from 14:00:10"
        assert gaps_before([other], _at(80), [self._entry(0, events, rx="0x7EA")], _at(0)) == []


class TestExplorerGaps:
    def test_session_wide_gaps_are_exported_as_offsets(self):
        from canair.interop.explorer.series import _Clock, _gaps

        session = {
            "link_events": [
                {"at": "14:00:10.000", "until": "14:00:20.000", "kind": "disconnect"},
                {"at": "14:00:30.000", "kind": "paused"},
                {"at": "14:00:40.000", "kind": "ecu_silent", "rx": "0x7EC"},
                {"at": "14:00:45.000", "kind": "resync"},
            ]
        }
        clock = _Clock()
        origin = clock("14:00:00.000")
        assert origin is not None
        assert _gaps(session, clock, origin, 60_000) == [
            {"t": 10_000, "end": 20_000, "kind": "disconnect"},
            {"t": 30_000, "end": 60_000, "kind": "paused"},
        ]
