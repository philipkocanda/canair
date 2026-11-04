"""Tests for ``canair captures compact`` (rewrite capture files in the folded form)."""

from __future__ import annotations

import json

from canlib.captures import io as capture_io
from canlib.commands.captures.compact import cmd_compact

DOC = {
    "sessions": [
        {
            "date": "2026-10-25",
            "label": "keep-all",
            "captures": [
                {"rx": "0x7EC", "pid": "2101", "payload": "AA", "time": f"10:00:{s:02d}.000"}
                for s in range(0, 50, 5)
            ],
        }
    ]
}


def _legacy(tmp_path):
    """A capture file as written before folding: plain ``json.dumps(indent=2)``."""
    path = tmp_path / "2026-10-25.json"
    path.write_text(json.dumps(DOC, indent=2) + "\n")
    return path


def test_compacts_losslessly_and_is_idempotent(tmp_path, capsys):
    path = _legacy(tmp_path)
    before = path.stat().st_size
    assert cmd_compact(tmp_path) == 0
    assert path.stat().st_size < before
    assert capture_io.load_capture_file(path) == DOC
    assert "Compacted 1 of 1" in capsys.readouterr().out
    assert cmd_compact(tmp_path) == 0
    assert "already compact" in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, capsys):
    path = _legacy(tmp_path)
    text = path.read_text()
    assert cmd_compact(tmp_path, dry_run=True) == 0
    assert path.read_text() == text
    assert "Would compact" in capsys.readouterr().out


def test_json_reports_sizes(tmp_path, capsys):
    path = _legacy(tmp_path)
    before = path.stat().st_size
    assert cmd_compact(tmp_path, as_json=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bytes_before"] == before
    assert out["bytes_after"] == path.stat().st_size
    assert [f["file"] for f in out["files"]] == [path.name]


def test_missing_dir_is_an_error(tmp_path, capsys):
    assert cmd_compact(tmp_path / "nope") == 2
    assert "no such captures directory" in capsys.readouterr().err
