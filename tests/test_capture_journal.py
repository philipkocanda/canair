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
        assert recs[-1] == {
            "type": "capture",
            "ecu": "0x7EC",
            "pid": "2102",
            "payload": "6102CCDD",
            "time": "12:00:02",
        }


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
        caps = yaml.safe_load(written.read_text())["sessions"][0]["captures"]
        assert [c["payload"] for c in caps] == ["6101AA", "6101BB"]

    def test_keep_all_keeps_duplicates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="L", keep_mode="all")
        j.append("0x7EC", "2101", "6101AA")
        j.append("0x7EC", "2101", "6101AA")
        written = j.reconcile()
        caps = yaml.safe_load(written.read_text())["sessions"][0]["captures"]
        assert len(caps) == 2

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
        sess = build_session_from_records(recs)
        assert sess["label"] == "Recovered session"

    def test_none_when_no_payloads(self):
        recs = [{"type": "meta", "label": "L"}]
        assert build_session_from_records(recs) is None


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
