"""Tests for capture provenance: transport + data-quality in session metadata."""

from __future__ import annotations

from canlib import capture_io
from canlib.capture_journal import CaptureJournal, build_session_from_records
from canlib.captures import build_manual_session, build_query_session, save_session


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


class TestVersionStamping:
    def test_save_session_stamps_current_version(self, tmp_path):
        import canlib

        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "lbl", [], "")
        assert "version" not in s  # builders don't stamp it; save_session does
        written = save_session(s, tmp_path)
        data = capture_io.load_capture_file(written)
        assert data["sessions"][0]["version"] == canlib.__version__

    def test_version_stamped_after_label(self, tmp_path):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "lbl", [], "")
        written = save_session(s, tmp_path)
        data = capture_io.load_capture_file(written)
        keys = list(data["sessions"][0].keys())
        assert keys.index("version") == keys.index("label") + 1

    def test_existing_version_preserved(self, tmp_path):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "lbl", [], "")
        s["version"] = "0.0.1-pinned"
        written = save_session(s, tmp_path)
        data = capture_io.load_capture_file(written)
        assert data["sessions"][0]["version"] == "0.0.1-pinned"

    def test_journaled_session_carries_version(self, tmp_path):
        import canlib

        j = CaptureJournal.open(tmp_path, label="run", transport="wican-ws")
        j.append("0x7EC", "2101", "6101AA")
        written = j.reconcile()
        assert written is not None
        data = capture_io.load_capture_file(written)
        assert data["sessions"][0]["version"] == canlib.__version__


class TestElapsedMs:
    """Per-capture wall-clock round-trip (single per-DID reads only)."""

    def test_query_session_stores_elapsed_ms(self):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00", 47)], "lbl", [], "")
        assert s["captures"][0]["elapsed_ms"] == 47

    def test_query_session_omits_elapsed_when_none(self):
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00", None)], "lbl", [], "")
        assert "elapsed_ms" not in s["captures"][0]

    def test_query_session_back_compat_four_tuple(self):
        # Monitor/direct callers still pass 4-tuples (no elapsed).
        s = build_query_session([("0x7EC", "2101", "6101AA", "12:00:00")], "lbl", [], "")
        assert "elapsed_ms" not in s["captures"][0]

    def test_journal_append_persists_elapsed_ms(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="run", transport="slcan-tcp")
        j.append("0x7EC", "2101", "6101AA", elapsed_ms=42)
        written = j.reconcile()
        assert written is not None
        data = capture_io.load_capture_file(written)
        assert data["sessions"][0]["captures"][0]["elapsed_ms"] == 42

    def test_journal_append_omits_elapsed_when_absent(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="run", transport="slcan-tcp")
        j.append("0x7EC", "2101", "6101AA")
        written = j.reconcile()
        assert written is not None
        data = capture_io.load_capture_file(written)
        assert "elapsed_ms" not in data["sessions"][0]["captures"][0]

    def test_build_session_from_records_threads_elapsed(self):
        records = [
            {"type": "meta", "label": "run"},
            {
                "type": "capture",
                "rx": "0x7EC",
                "pid": "2101",
                "payload": "6101",
                "date": "2026-07-28",
                "elapsed_ms": 33,
            },
        ]
        sessions = build_session_from_records(records)
        assert sessions[0]["captures"][0]["elapsed_ms"] == 33


class TestDecodePidResultElapsed:
    """_decode_pid_result carries elapsed only for single reads."""

    def test_single_read_carries_elapsed(self):
        from canlib.modes.multi_batch import _decode_pid_result

        entry = _decode_pid_result(
            "2101", None, True, "6101AA", bytes.fromhex("6101AA"), 1.0, elapsed_ms=55
        )
        assert entry["elapsed_ms"] == 55

    def test_batched_read_omits_elapsed(self):
        from canlib.modes.multi_batch import _decode_pid_result

        # Batched callers don't pass elapsed_ms (defaults to None → omitted).
        entry = _decode_pid_result("22B001", None, True, "62B00100", bytes.fromhex("62B00100"), 1.0)
        assert "elapsed_ms" not in entry
