"""Tests for canlib.capture_migrate — YAML→JSON capture-store migration."""

from __future__ import annotations

import json

import pytest
import yaml

from canlib import capture_io
from canlib.capture_migrate import MigrationError, migrate_dir, migrate_file

_DOC = {
    "sessions": [
        {
            "date": "2026-01-01",
            "label": "test drive",
            "vehicle_states": ["ready", "driving"],
            "notes": "café ☕",  # non-ASCII survives
            "captures": [
                {"ecu": "0x7EC", "pid": "2101", "payload": "6101AABB", "time": "09:00:01"},
                {"ecu": "0x7EC", "pid": "2102", "payload": "6102CCDD", "time": "09:00:02"},
            ],
        }
    ]
}


def _write_yaml(dirp, name, doc):
    p = dirp / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


class TestMigrateFile:
    def test_converts_and_removes_yaml(self, tmp_path):
        yp = _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        res = migrate_file(yp)
        assert res.written is True
        assert res.sessions == 1 and res.captures == 2
        assert not yp.exists()  # original removed
        jp = tmp_path / "2026-01-01.json"
        assert jp.exists()
        assert capture_io.load_capture_file(jp) == _DOC  # data preserved exactly

    def test_dry_run_writes_nothing(self, tmp_path):
        yp = _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        res = migrate_file(yp, dry_run=True)
        assert res.written is False
        assert yp.exists()  # untouched
        assert not (tmp_path / "2026-01-01.json").exists()

    def test_aborts_if_json_already_exists(self, tmp_path):
        yp = _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        (tmp_path / "2026-01-01.json").write_text("{}")
        with pytest.raises(MigrationError, match="already exists"):
            migrate_file(yp)
        assert yp.exists()  # nothing destroyed

    def test_type_drift_aborts_without_writing(self, tmp_path):
        # An unquoted date scalar parses as a datetime.date — not JSON-native.
        yp = tmp_path / "2026-01-01.yaml"
        yp.write_text("sessions:\n  - date: 2026-01-01\n    captures: []\n")
        # Sanity: YAML really parsed it as a date, not a string.
        assert not isinstance(yaml.safe_load(yp.read_text())["sessions"][0]["date"], str)
        with pytest.raises(MigrationError):
            migrate_file(yp)
        assert yp.exists()
        assert not (tmp_path / "2026-01-01.json").exists()


class TestMigrateDir:
    def test_migrates_all_yaml(self, tmp_path):
        _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        _write_yaml(tmp_path, "2026-01-02.yaml", _DOC)
        (tmp_path / "SCHEMA.yaml").write_text("# doc\n")  # skipped
        results = migrate_dir(tmp_path)
        assert {r.json_path.name for r in results} == {"2026-01-01.json", "2026-01-02.json"}
        assert (tmp_path / "SCHEMA.yaml").exists()  # doc left alone
        assert capture_io.find_legacy_yaml(tmp_path) == []  # all day files converted

    def test_dry_run_touches_nothing(self, tmp_path):
        _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        results = migrate_dir(tmp_path, dry_run=True)
        assert results and all(not r.written for r in results)
        assert len(capture_io.find_legacy_yaml(tmp_path)) == 1

    def test_empty_dir(self, tmp_path):
        assert migrate_dir(tmp_path) == []

    def test_migrated_json_loads_back(self, tmp_path):
        # After migration the JSON is readable and preserves the captures.
        # (Stage 2 wires JSON into load_all_captures; here we read it directly.)
        _write_yaml(tmp_path, "2026-01-01.yaml", _DOC)
        migrate_dir(tmp_path)
        entries = _load_json_dir(tmp_path)
        assert len(entries) == 2
        assert {e["pid"] for e in entries} == {"2101", "2102"}


def _load_json_dir(dirp):
    """Minimal JSON capture reader for the end-to-end assertion (Stage 1 has no
    JSON reader wired into load_all_captures yet — that's Stage 2)."""
    out = []
    for p in capture_io.iter_capture_files(dirp):
        data = json.loads(p.read_text())
        for s in data.get("sessions", []):
            for cap in s.get("captures", []):
                out.append(cap)
    return out
