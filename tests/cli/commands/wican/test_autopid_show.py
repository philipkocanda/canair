"""``canair wican autopid show``: the generated AutoPID JSON, narrowed to a QUERY, offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canair.cli import main
from canair.config import file as config_file

REPO = Path(__file__).parents[4]
OUT = REPO / "profiles" / "hyundai-ioniq-ae-ev" / "out" / "28kwh"
SHOW = ["--profile", "hyundai-ioniq-ae-ev", "--variant", "28kwh", "wican", "autopid", "show"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CANAIR_VARIANT", raising=False)
    config_file.load_config.cache_clear()
    yield
    config_file.load_config.cache_clear()


def _show(capsys, *args: str) -> tuple[int, dict | None, str]:
    code = main([*SHOW, *args])
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if captured.out else None, captured.err


def test_without_a_query_it_is_the_committed_build(capsys):
    code, doc, _ = _show(capsys)
    assert code == 0
    assert doc == {
        "v4": json.loads((OUT / "autopid-v4.json").read_text()),
        "v6": json.loads((OUT / "autopid-v6.json").read_text()),
    }


def test_a_query_keeps_only_its_pids_and_progress_goes_to_stderr(capsys):
    code, doc, err = _show(capsys, "BMS:2101", "--firmware", "4")
    assert code == 0
    assert list(doc) == ["v4"]
    assert [p["pid"][:4] for p in doc["v4"]["pids"]] == ["2101"]
    assert "Generating AutoPID profile" in err


def test_an_unmatched_selector_warns_beside_a_match(capsys):
    code, doc, err = _show(capsys, "BMS:2101 BMS:99", "--firmware", "6")
    assert code == 0
    assert [p["name"] for p in doc["v6"]["pids"]] == ["BMS_2101"]
    assert "warning: no shipped PID matches 'BMS:99'" in err


def test_nothing_matched_is_a_failure_with_no_document(capsys):
    code, doc, err = _show(capsys, "BMS:99")
    assert code == 1 and doc is None
    assert "error: no shipped PID matches" in err


def test_it_writes_nothing(capsys):
    before = {p: p.stat().st_mtime_ns for p in OUT.iterdir()}
    assert main([*SHOW, "VCU"]) == 0
    assert {p: p.stat().st_mtime_ns for p in OUT.iterdir()} == before
