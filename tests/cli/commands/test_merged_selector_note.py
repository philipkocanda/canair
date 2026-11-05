"""The merged-selector note is a `note:` on stderr, so `read --json` stdout stays clean."""

from __future__ import annotations

from canair.cli.live.steps import report_merged_selectors
from canair.query.steps import normalize_query_steps


def test_the_note_goes_to_stderr(capsys):
    steps = normalize_query_steps(
        [
            {"type": "query", "ecu": "BMS", "pids": []},
            {"type": "query", "ecu": "BMS", "pids": ["2101"]},
        ]
    )
    report_merged_selectors(steps)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("note: merged overlapping selectors")
    assert "BMS" in captured.err


def test_no_overlap_prints_nothing(capsys):
    report_merged_selectors([{"type": "query", "ecu": "BMS", "pids": ["2101"]}])
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
