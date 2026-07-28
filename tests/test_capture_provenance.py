"""Tests for capture provenance: transport + data-quality in session metadata."""

from __future__ import annotations

from canlib import capture_io
from canlib.capture_journal import CaptureJournal, build_session_from_records
from canlib.captures import build_manual_session, build_query_session


class TestSessionBuilders:
    def test_query_session_records_transport_and_quality(self):
        s = build_query_session(
            [("0x7EC", "2101", "6101AA", "12:00:00")],
            "lbl",
            [],
            "",
            transport="slcan-tcp",
            quality={"exchanges": 10, "drop": 1},
        )
        assert s["transport"] == "slcan-tcp"
        assert s["quality"] == {"exchanges": 10, "drop": 1}

    def test_query_session_omits_absent_provenance(self):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "lbl", [], "")
        assert "transport" not in s
        assert "quality" not in s

    def test_manual_session_defaults_transport_to_import(self):
        s = build_manual_session([{"ecu": "0x7EC", "pid": "2101", "payload": "6101"}], label="x")
        assert s["transport"] == "import"

    def test_manual_session_transport_overridable(self):
        s = build_manual_session([], label="x", transport=None)
        assert "transport" not in s


class TestJournalProvenance:
    def test_open_records_transport_in_meta(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="run", transport="wican-ws")
        j.append("0x7EC", "2101", "6101AA")
        j.update_meta(quality={"exchanges": 5, "drop": 1})
        written = j.reconcile()
        assert written is not None
        data = capture_io.load_capture_file(written)
        session = data["sessions"][0]
        assert session["transport"] == "wican-ws"
        assert session["quality"] == {"exchanges": 5, "drop": 1}

    def test_build_session_from_records_threads_provenance(self):
        records = [
            {"type": "meta", "label": "run", "transport": "slcan-tcp", "quality": {"exchanges": 3}},
            {
                "type": "capture",
                "ecu": "0x7EC",
                "pid": "2101",
                "payload": "6101",
                "date": "2026-07-28",
            },
        ]
        sessions = build_session_from_records(records)
        assert len(sessions) == 1
        assert sessions[0]["transport"] == "slcan-tcp"
        assert sessions[0]["quality"] == {"exchanges": 3}

    def test_oneshot_session_keeps_its_transport(self):
        records = [
            {"type": "meta", "label": "scan", "transport": "slcan-tcp"},
            {
                "type": "session",
                "session": {
                    "date": "2026-07-28",
                    "label": "scan",
                    "captures": [{"ecu": "0x7EC", "pid": "scan 22 0000-00FF", "scan_results": {}}],
                },
            },
        ]
        sessions = build_session_from_records(records)
        assert sessions[0]["transport"] == "slcan-tcp"
