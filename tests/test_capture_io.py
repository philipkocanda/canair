"""Tests for canlib.capture_io — the JSON capture-file format seam."""

from __future__ import annotations

import json

import pytest

from canlib import capture_io


class TestCaptureRx:
    def test_reads_rx_key(self):
        assert capture_io.capture_rx({"rx": "0x7EC", "pid": "2101"}) == "0x7EC"

    def test_falls_back_to_legacy_ecu_key(self):
        # Un-migrated file / stale journal still resolves.
        assert capture_io.capture_rx({"ecu": "0x7EC", "pid": "2101"}) == "0x7EC"

    def test_rx_wins_over_ecu(self):
        assert capture_io.capture_rx({"rx": "0x7EC", "ecu": "0x000"}) == "0x7EC"

    def test_empty_when_neither_present(self):
        assert capture_io.capture_rx({"pid": "2101"}) == ""


class TestGlobbing:
    def test_iter_capture_files_only_json_sorted(self, tmp_path):
        (tmp_path / "2026-01-02.json").write_text("{}")
        (tmp_path / "2026-01-01.json").write_text("{}")
        (tmp_path / "2026-01-01.yaml").write_text("{}")  # legacy, ignored by iter
        got = [p.name for p in capture_io.iter_capture_files(tmp_path)]
        assert got == ["2026-01-01.json", "2026-01-02.json"]

    def test_iter_skips_schema_and_underscore(self, tmp_path):
        (tmp_path / "2026-01-01.json").write_text("{}")
        (tmp_path / "SCHEMA.json").write_text("{}")
        (tmp_path / "_scratch.json").write_text("{}")
        got = [p.name for p in capture_io.iter_capture_files(tmp_path)]
        assert got == ["2026-01-01.json"]

    def test_find_legacy_yaml(self, tmp_path):
        (tmp_path / "2026-01-01.yaml").write_text("{}")
        (tmp_path / "SCHEMA.yaml").write_text("{}")  # doc, skipped
        (tmp_path / "2026-01-02.json").write_text("{}")
        got = [p.name for p in capture_io.find_legacy_yaml(tmp_path)]
        assert got == ["2026-01-01.yaml"]


class TestRoundTrip:
    def test_dump_then_load(self, tmp_path):
        data = {"sessions": [{"date": "2026-01-01", "label": "x", "captures": [{"pid": "2101"}]}]}
        p = tmp_path / "2026-01-01.json"
        capture_io.dump_capture_file(p, data)
        assert capture_io.load_capture_file(p) == data

    def test_dump_is_pretty_and_utf8(self, tmp_path):
        p = tmp_path / "d.json"
        capture_io.dump_capture_file(p, {"notes": "café", "a": 1})
        text = p.read_text(encoding="utf-8")
        assert "café" in text  # ensure_ascii=False
        assert text.endswith("\n")
        assert "\n  " in text  # indent=2

    def test_dump_preserves_key_order(self, tmp_path):
        # Insertion order, not sorted — stable diffs / builder grouping.
        p = tmp_path / "d.json"
        capture_io.dump_capture_file(p, {"date": "d", "label": "l", "captures": []})
        keys = list(json.loads(p.read_text()).keys())
        assert keys == ["date", "label", "captures"]

    def test_dump_is_atomic_no_tmp_left(self, tmp_path):
        p = tmp_path / "d.json"
        capture_io.dump_capture_file(p, {"a": 1})
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "d.json"]
        assert leftovers == []

    def test_dump_creates_parent(self, tmp_path):
        p = tmp_path / "nested" / "d.json"
        capture_io.dump_capture_file(p, {"a": 1})
        assert p.exists()


def test_load_capture_file_rejects_nonjson(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not: json: at all")
    with pytest.raises(json.JSONDecodeError):
        capture_io.load_capture_file(p)


class TestEnsureMigrated:
    def test_ok_when_only_json(self, tmp_path):
        (tmp_path / "2026-01-01.json").write_text("{}")
        capture_io.ensure_migrated(tmp_path)  # no raise

    def test_ok_when_empty(self, tmp_path):
        capture_io.ensure_migrated(tmp_path)  # no raise

    def test_raises_on_legacy_yaml(self, tmp_path):
        (tmp_path / "2026-01-01.yaml").write_text("sessions: []\n")
        with pytest.raises(capture_io.LegacyCaptureError, match="captures migrate"):
            capture_io.ensure_migrated(tmp_path)

    def test_ignores_schema_yaml_doc(self, tmp_path):
        (tmp_path / "SCHEMA.yaml").write_text("# doc\n")
        capture_io.ensure_migrated(tmp_path)  # SCHEMA is a doc, not a capture file


def test_load_all_captures_fails_fast_on_legacy_yaml(tmp_path):
    # The bulk reader raises the actionable error rather than silently skipping.
    from canlib.commands._captures_query import load_all_captures

    (tmp_path / "2026-01-01.yaml").write_text("sessions: []\n")
    with pytest.raises(capture_io.LegacyCaptureError):
        load_all_captures(tmp_path)


def test_migrate_then_load_all_captures_roundtrip(tmp_path):
    # End-to-end: a YAML fixture migrated to JSON is read identically by the
    # production bulk loader (the Stage-2 reader path).
    import yaml

    from canlib.capture_migrate import migrate_dir
    from canlib.commands._captures_query import load_all_captures

    doc = {
        "sessions": [
            {
                "date": "2026-01-01",
                "label": "x",
                "captures": [
                    {"rx": "0x7EC", "pid": "2101", "payload": "6101AA", "time": "09:00:00"},
                    {"rx": "0x7EC", "pid": "2102", "payload": "6102BB", "time": "09:00:01"},
                ],
            }
        ]
    }
    (tmp_path / "2026-01-01.yaml").write_text(yaml.safe_dump(doc))
    migrate_dir(tmp_path)
    entries = load_all_captures(tmp_path)
    assert [e["pid"] for e in entries] == ["2101", "2102"]
    assert [e["payload"] for e in entries] == ["6101AA", "6102BB"]
