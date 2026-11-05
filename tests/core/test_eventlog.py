"""Tests for the central rotating diagnostics event log."""

from __future__ import annotations

import logging

import pytest

import canair.core.eventlog as clog


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Point the central event log at a temp file (never the real user log)."""
    path = tmp_path / "logs" / "canair.log"
    monkeypatch.setattr(clog, "event_log_path", lambda: path)
    monkeypatch.setattr(clog, "_event_logger", None)
    # A prior test may have attached a handler pointing at the real log; drop it
    # so _get_event_logger re-opens against the isolated path.
    logging.getLogger("canair.events").handlers.clear()
    yield path
    # Drop any handlers this test opened so pytest doesn't leak file handles.
    logging.getLogger("canair.events").handlers.clear()
    monkeypatch.setattr(clog, "_event_logger", None)


class TestEventLog:
    def test_log_event_writes_line_with_fields(self, isolated_log):
        clog.log_event("drop", "truncated ISO-TP", transport="slcan-tcp", ecu="0x7E4", pid="2102")
        lines = clog.read_event_log()
        assert len(lines) == 1
        parsed = clog.parse_event_line(lines[0])
        assert parsed["category"] == "drop"
        assert parsed["transport"] == "slcan-tcp"
        assert parsed["ecu"] == "0x7E4"
        assert parsed["pid"] == "2102"
        assert parsed["detail"] == "truncated ISO-TP"

    def test_read_event_log_tail(self, isolated_log):
        for i in range(10):
            clog.log_event("no_data", f"event {i}")
        assert len(clog.read_event_log(lines=3)) == 3
        assert len(clog.read_event_log()) == 10

    def test_read_empty_log(self, isolated_log):
        assert clog.read_event_log() == []

    def test_clear_event_log(self, isolated_log):
        clog.log_event("bus", "boom")
        assert clog.read_event_log()
        removed = clog.clear_event_log()
        assert removed >= 1
        assert clog.read_event_log() == []

    def test_log_exception_records_traceback(self, isolated_log):
        try:
            raise ValueError("kaboom")
        except ValueError as e:
            clog.log_exception("while polling", e)
        lines = clog.read_event_log()
        assert len(lines) == 1
        assert "kaboom" in lines[0]
        assert "while polling" in lines[0]

    def test_parse_unmatched_line_is_raw(self):
        assert clog.parse_event_line("garbage") == {"raw": "garbage"}

    def test_events_carry_this_process_run_id(self, isolated_log):
        clog.log_event("drop", "x", ecu="BMS")
        parsed = clog.parse_event_line(clog.read_event_log()[0])
        assert parsed["run"] == clog.run_id()
        assert parsed["ecu"] == "BMS" and parsed["detail"] == "x"

    def test_read_filters_by_run_before_the_tail(self, isolated_log, monkeypatch):
        monkeypatch.setattr(clog, "_RUN_ID", "other")
        for i in range(5):
            clog.log_event("no_data", f"other {i}")
        monkeypatch.setattr(clog, "_RUN_ID", "mine")
        clog.log_event("no_data", "mine 0")
        clog.log_event("no_data", "mine 1")
        monkeypatch.setattr(clog, "_RUN_ID", "other")
        clog.log_event("no_data", "other 5")

        mine = clog.read_event_log(lines=3, run="mine")
        assert [clog.parse_event_line(ln)["detail"] for ln in mine] == ["mine 0", "mine 1"]
        assert len(clog.read_event_log()) == 8

    def test_monitor_modal_shows_only_this_session(self, isolated_log, monkeypatch):
        from canair.tui.apps.monitor import EventLogModal

        monkeypatch.setattr(clog, "_RUN_ID", "earlier")
        clog.log_event("drop", "from an earlier session")
        monkeypatch.setattr(clog, "_RUN_ID", "now")
        assert "no events logged this session" in EventLogModal()._render_events().plain
        clog.log_event("stale", "from this session")

        shown = EventLogModal()._render_events().plain
        assert "from this session" in shown
        assert "earlier session" not in shown
