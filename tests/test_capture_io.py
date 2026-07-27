"""Tests for canlib.capture_io — the JSON capture-file format seam."""

from __future__ import annotations

import json

import pytest

from canlib import capture_io


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
