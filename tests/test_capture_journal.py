"""Tests for the capture journal (write-ahead log) + reconciliation/recovery."""

import json

import yaml

from canlib.capture_journal import (
    CaptureJournal,
    build_session_from_records,
    list_orphans,
    reconcile_file,
    recover,
)


def _read_capture_file(captures_dir):
    files = list(captures_dir.glob("*.yaml"))
    assert len(files) == 1
    return yaml.safe_load(files[0].read_text())


class TestJournalBasics:
    def test_open_writes_meta_and_creates_dir(self, tmp_path):
        j = CaptureJournal.open(
            tmp_path, label="L", vehicle_states=["ready"], notes="n", source="monitor"
        )
        assert j.path.exists()
        assert j.path.parent == tmp_path / ".journal"
        lines = j.path.read_text().splitlines()
        meta = json.loads(lines[0])
        assert meta["type"] == "meta"
        assert meta["label"] == "L"
        assert meta["vehicle_states"] == ["ready"]
        assert meta["source"] == "monitor"

    def test_append_is_buffered_and_flush_syncs_once(self, tmp_path, monkeypatch):
        # append() is buffered (no per-row fsync); flush() syncs the whole batch
        # once — the per-cycle durability model for the monitor.
        import canlib.capture_journal as cj

        calls = {"n": 0}
        monkeypatch.setattr(cj.os, "fsync", lambda _fd: calls.__setitem__("n", calls["n"] + 1))

        j = CaptureJournal.open(tmp_path, label="L")  # meta write is durable -> 1 fsync
        base = calls["n"]
        j.append("0x7EC", "2101", "6101aabb", "12:00:01")
        j.append("0x7EC", "2102", "6102ccdd", "12:00:02")
        assert calls["n"] == base  # no per-append fsync
        j.flush()
        assert calls["n"] == base + 1  # a single fsync for the batch

        recs = [json.loads(x) for x in j.path.read_text().splitlines()]
        # append() stamps a per-record date (for midnight-correct day-splitting)
        # alongside the time.
        assert recs[-1]["type"] == "capture"
        assert recs[-1]["ecu"] == "0x7EC"
        assert recs[-1]["pid"] == "2102"
        assert recs[-1]["payload"] == "6102CCDD"
        assert recs[-1]["time"] == "12:00:02"
        assert "date" in recs[-1]


class TestReconcile:
    def test_reconcile_builds_session_and_deletes_journal(self, tmp_path):
        j = CaptureJournal.open(
            tmp_path, label="Live ref", vehicle_states=["ready", "parked"], notes="18C"
        )
        j.append("0x7EB", "2102", "6102AABB")
        j.append("0x7EA", "2101", "6101CCDD", "12:00:01")
        written = j.reconcile()
        assert written is not None
        assert not j.path.exists()  # journal removed after reconcile
        data = yaml.safe_load(written.read_text())
        sess = data["sessions"][0]
        assert sess["label"] == "Live ref"
        assert sess["vehicle_states"] == ["ready", "parked"]
        assert sess["captures"][0]["payload"] == "6102AABB"
        assert sess["captures"][1]["time"] == "12:00:01"

    def test_meta_last_wins(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="orig", vehicle_states=["acc"])
        j.append("0x7EC", "2101", "6101")
        j.update_meta(label="edited", vehicle_states=["ready"], notes="final")
        written = j.reconcile()
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert sess["label"] == "edited"
        assert sess["vehicle_states"] == ["ready"]
        assert sess["notes"] == "final"

    def test_keep_mode_unique_dedups(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L", keep_mode="unique")
        j.append("0x7EC", "2101", "6101AA")
        j.append("0x7EC", "2101", "6101AA")  # dup
        j.append("0x7EC", "2101", "6101BB")
        written = j.reconcile()
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert [c["payload"] for c in sess["captures"]] == ["6101AA", "6101BB"]
        # keep_mode must be persisted so later analysis knows return-to-previous
        # states may be absent (only rising-edge transitions were stored).
        assert sess["keep_mode"] == "unique"

    def test_keep_all_keeps_duplicates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L", keep_mode="all")
        j.append("0x7EC", "2101", "6101AA")
        j.append("0x7EC", "2101", "6101AA")
        written = j.reconcile()
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert len(sess["captures"]) == 2
        # "all" is not a dedup mode — don't clutter the session with it.
        assert "keep_mode" not in sess

    def test_empty_journal_reconcile_returns_none_and_removes(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L")
        assert j.reconcile() is None
        assert not j.path.exists()

    def test_append_session_roundtrips(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="Scan", vehicle_states=["ready"])
        session = {
            "date": "2026-07-22",
            "label": "placeholder",
            "captures": [
                {"ecu": "0x7EC", "pid": "scan 21 01-FF", "scan_results": {"rejected": "x"}}
            ],
        }
        j.append_session(session)
        written = j.reconcile()
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert sess["label"] == "Scan"
        assert sess["vehicle_states"] == ["ready"]
        assert sess["captures"][0]["pid"] == "scan 21 01-FF"


class TestMidnightSplit:
    def test_reconcile_splits_across_midnight_into_per_day_files(self, tmp_path):
        # A monitor/query session spanning midnight must land in the correct
        # per-day capture files (date derived from each payload, not from
        # reconcile time) so time-aligned analysis keeps the right calendar day.
        j = CaptureJournal.open(tmp_path, label="overnight drive")
        j.append("0x7EC", "2101", "6101AA", "23:59:58.100", "2026-07-22")
        j.append("0x7EC", "2101", "6101BB", "00:00:03.200", "2026-07-23")
        written = j.reconcile()
        assert written is not None
        assert not j.path.exists()

        f22 = tmp_path / "2026-07-22.yaml"
        f23 = tmp_path / "2026-07-23.yaml"
        assert f22.exists() and f23.exists()

        s22 = yaml.safe_load(f22.read_text())["sessions"][0]
        s23 = yaml.safe_load(f23.read_text())["sessions"][0]
        assert s22["date"] == "2026-07-22"
        assert s23["date"] == "2026-07-23"
        assert s22["captures"][0]["payload"] == "6101AA"
        assert s22["captures"][0]["time"] == "23:59:58.100"
        assert s23["captures"][0]["payload"] == "6101BB"

    def test_recovery_uses_capture_date_not_today(self, tmp_path):
        # A journal recovered days later must be dated to when the payloads were
        # captured, not the recovery day.
        j = CaptureJournal.open(tmp_path, label="L", notes="orig")
        j.append("0x7EC", "2101", "6101", "12:00:00", "2020-01-01")
        j._close_fh()
        written = recover(j.path)
        assert written is not None
        assert written.name == "2020-01-01.yaml"
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert sess["date"] == "2020-01-01"
        assert "[recovered]" in sess["notes"]


class TestContextManager:
    def test_clean_exit_reconciles(self, tmp_path):
        with CaptureJournal.open(tmp_path, label="ctx") as j:
            j.append("0x7EC", "2101", "6101")
            path = j.path
        assert not path.exists()
        assert _read_capture_file(tmp_path)["sessions"][0]["label"] == "ctx"

    def test_exception_leaves_journal(self, tmp_path):
        try:
            with CaptureJournal.open(tmp_path, label="ctx") as j:
                j.append("0x7EC", "2101", "6101")
                path = j.path
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert path.exists()  # preserved for recovery
        assert not list(tmp_path.glob("*.yaml"))  # not saved yet


class TestTruncatedLine:
    def test_truncated_final_line_tolerated(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101AA")
        j._close_fh()
        # Simulate a kill mid-write: append a partial JSON line.
        with open(j.path, "a") as f:
            f.write('{"type": "capture", "ecu": "0x7E')
        written = reconcile_file(j.path)
        caps = yaml.safe_load(written.read_text())["sessions"][0]["captures"]
        assert [c["payload"] for c in caps] == ["6101AA"]


class TestOrphanRecovery:
    def test_list_orphans(self, tmp_path):
        assert list_orphans(tmp_path) == []
        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        orphans = list_orphans(tmp_path)
        assert orphans == [j.path]

    def test_recover_tags_notes_and_saves(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L", notes="orig")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        written = recover(j.path)
        assert written is not None
        assert not j.path.exists()
        sess = yaml.safe_load(written.read_text())["sessions"][0]
        assert "[recovered]" in sess["notes"]
        assert "orig" in sess["notes"]

    def test_discard_deletes_without_saving(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        assert recover(j.path, discard=True) is None
        assert not j.path.exists()
        assert not list(tmp_path.glob("*.yaml"))


class TestBuildSessionFromRecords:
    def test_default_label_when_missing(self):
        recs = [{"type": "capture", "ecu": "0x7EC", "pid": "2101", "payload": "6101"}]
        sessions = build_session_from_records(recs)
        assert len(sessions) == 1
        assert sessions[0]["label"] == "Recovered session"

    def test_none_when_no_payloads(self):
        recs = [{"type": "meta", "label": "L"}]
        assert build_session_from_records(recs) == []

    def test_splits_sessions_by_capture_date(self):
        # A journal spanning midnight must reconcile into one session per
        # calendar day, each carrying its own date + the shared metadata.
        recs = [
            {"type": "meta", "label": "overnight", "vehicle_states": ["driving"]},
            {
                "type": "capture",
                "ecu": "0x7EC",
                "pid": "2101",
                "payload": "6101AA",
                "date": "2026-07-22",
                "time": "23:59:58.100",
            },
            {
                "type": "capture",
                "ecu": "0x7EC",
                "pid": "2101",
                "payload": "6101BB",
                "date": "2026-07-23",
                "time": "00:00:03.200",
            },
        ]
        sessions = build_session_from_records(recs)
        assert len(sessions) == 2
        by_date = {s["date"]: s for s in sessions}
        assert set(by_date) == {"2026-07-22", "2026-07-23"}
        for s in sessions:
            assert s["label"] == "overnight"
            assert s["vehicle_states"] == ["driving"]
        assert by_date["2026-07-22"]["captures"][0]["payload"] == "6101AA"
        assert by_date["2026-07-22"]["captures"][0]["time"] == "23:59:58.100"
        assert by_date["2026-07-23"]["captures"][0]["payload"] == "6101BB"

    def test_record_date_falls_back_to_meta_date(self):
        # Older/partial journals without per-record dates use the meta date.
        recs = [
            {"type": "meta", "label": "L", "date": "2026-01-05"},
            {
                "type": "capture",
                "ecu": "0x7EC",
                "pid": "2101",
                "payload": "6101",
                "time": "08:00:00",
            },
        ]
        sessions = build_session_from_records(recs)
        assert len(sessions) == 1
        assert sessions[0]["date"] == "2026-01-05"


class TestRecoverCommand:
    def test_cmd_recover_no_orphans(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_recover

        rc = cmd_recover(tmp_path)
        assert rc == 0
        assert "No orphaned" in capsys.readouterr().out

    def test_cmd_recover_reconciles(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_recover

        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        rc = cmd_recover(tmp_path)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Recovered 1 session" in out
        assert list(tmp_path.glob("*.yaml"))
        assert list_orphans(tmp_path) == []

    def test_cmd_recover_discard(self, tmp_path, capsys):
        from canlib.commands.captures import cmd_recover

        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        rc = cmd_recover(tmp_path, discard=True)
        assert rc == 0
        assert "discarded" in capsys.readouterr().out
        assert list_orphans(tmp_path) == []
        assert not list(tmp_path.glob("*.yaml"))

    def test_orphan_notice(self, tmp_path, capsys):
        from canlib.commands.captures import orphan_notice

        orphan_notice(tmp_path)
        assert capsys.readouterr().out == ""
        j = CaptureJournal.open(tmp_path, label="L")
        j.append("0x7EC", "2101", "6101")
        j._close_fh()
        orphan_notice(tmp_path)
        assert "orphaned capture journal" in capsys.readouterr().out
