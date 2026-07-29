"""Tests for canlib.capture_field_migrate — the capture `ecu` → `rx` field rename."""

from __future__ import annotations

import json

from canlib import capture_io
from canlib.capture_field_migrate import migrate_dir, migrate_file


def _write(dirp, name, doc):
    p = dirp / name
    p.write_text(json.dumps(doc, indent=2))
    return p


class TestMigrateFile:
    def test_renames_capture_level_ecu(self, tmp_path):
        doc = {
            "sessions": [
                {
                    "date": "2026-01-01",
                    "label": "x",
                    "captures": [
                        {"ecu": "0x7EC", "pid": "2101", "payload": "6101AA"},
                        {"ecu": "0x7EA", "pid": "2102", "payload": "6102BB"},
                    ],
                }
            ]
        }
        p = _write(tmp_path, "2026-01-01.json", doc)
        res = migrate_file(p)
        assert res.written is True
        assert res.renamed == 2
        out = capture_io.load_capture_file(p)
        caps = out["sessions"][0]["captures"]
        assert [c["rx"] for c in caps] == ["0x7EC", "0x7EA"]
        assert all("ecu" not in c for c in caps)

    def test_renames_responding_entry_ecu(self, tmp_path):
        doc = {
            "sessions": [
                {
                    "date": "2026-01-01",
                    "label": "discover",
                    "captures": [
                        {
                            "ecu": "broadcast",
                            "pid": "discover 700-7FF",
                            "scan_results": {
                                "responding": [
                                    {"ecu": "0x7EC", "response": "BMS"},
                                    {"ecu": "0x7EA", "response": "VCU"},
                                ]
                            },
                        }
                    ],
                }
            ]
        }
        p = _write(tmp_path, "2026-01-01.json", doc)
        res = migrate_file(p)
        # 1 capture-level + 2 responding entries.
        assert res.renamed == 3
        out = capture_io.load_capture_file(p)
        cap = out["sessions"][0]["captures"][0]
        assert cap["rx"] == "broadcast"
        responders = cap["scan_results"]["responding"]
        assert [r["rx"] for r in responders] == ["0x7EC", "0x7EA"]
        assert all("ecu" not in r for r in responders)

    def test_preserves_field_order(self, tmp_path):
        # The key is renamed in place, not appended at the end.
        doc = {
            "sessions": [
                {
                    "date": "2026-01-01",
                    "label": "x",
                    "captures": [{"ecu": "0x7EC", "pid": "2101", "payload": "6101AA"}],
                }
            ]
        }
        p = _write(tmp_path, "2026-01-01.json", doc)
        migrate_file(p)
        cap = capture_io.load_capture_file(p)["sessions"][0]["captures"][0]
        assert list(cap.keys()) == ["rx", "pid", "payload"]

    def test_idempotent_when_already_rx(self, tmp_path):
        doc = {
            "sessions": [
                {
                    "date": "2026-01-01",
                    "label": "x",
                    "captures": [{"rx": "0x7EC", "pid": "2101", "payload": "6101AA"}],
                }
            ]
        }
        p = _write(tmp_path, "2026-01-01.json", doc)
        res = migrate_file(p)
        assert res.renamed == 0
        assert res.written is False

    def test_dry_run_writes_nothing(self, tmp_path):
        doc = {
            "sessions": [
                {
                    "date": "2026-01-01",
                    "label": "x",
                    "captures": [{"ecu": "0x7EC", "pid": "2101", "payload": "6101AA"}],
                }
            ]
        }
        p = _write(tmp_path, "2026-01-01.json", doc)
        res = migrate_file(p, dry_run=True)
        assert res.renamed == 1 and res.written is False
        # File untouched.
        assert "ecu" in capture_io.load_capture_file(p)["sessions"][0]["captures"][0]


class TestMigrateDir:
    def test_migrates_all_files(self, tmp_path):
        for name in ("2026-01-01.json", "2026-01-02.json"):
            _write(
                tmp_path,
                name,
                {
                    "sessions": [
                        {
                            "date": name[:10],
                            "label": "x",
                            "captures": [{"ecu": "0x7EC", "pid": "2101", "payload": "AA"}],
                        }
                    ]
                },
            )
        results = migrate_dir(tmp_path)
        assert sum(r.renamed for r in results) == 2
        for r in results:
            out = capture_io.load_capture_file(r.path)
            assert "rx" in out["sessions"][0]["captures"][0]
