"""``canair read STEP --json``: stdout is one JSON document, everything human is on stderr."""

from __future__ import annotations

import asyncio
import json

from canair.live.read.multi import mode_multi
from canair.profile import active
from tests.support.fakes import FakeTerminal, nrc, ok


def _pids_data() -> dict:
    return {
        "ecus": {
            "IGPM": {
                "tx_id": 0x770,
                "pids": {
                    "22BC03": {
                        "parameters": {
                            "DOOR_STATE": {"expression": "B4", "unit": "", "verified": True},
                            "LAMP_STATE": {"expression": "B5", "unit": "x"},
                        }
                    },
                    "22BC04": {"parameters": {"UNUSED": {"expression": "B4"}}},
                },
            }
        }
    }


def _run(steps, replies, **kw) -> None:
    terminal = FakeTerminal(replies)
    asyncio.run(
        mode_multi(terminal, steps, _pids_data(), False, no_repl=True, profile=active(), **kw)
    )


def test_stdout_is_one_json_document(capsys):
    _run(["query IGPM:BC03"], {"22BC03": ok("62BC03AABB")}, as_json=True)
    captured = capsys.readouterr()
    doc = json.loads(captured.out)
    assert list(doc) == ["results"]
    (entry,) = doc["results"]
    assert entry["ecu"] == "IGPM"
    assert entry["pid"] == "22BC03"
    assert entry["raw"] == "62BC03AABB"
    assert entry["params"] == [
        {"name": "DOOR_STATE", "value": 170.0, "unit": "", "verified": True},
        {"name": "LAMP_STATE", "value": 187.0, "unit": "x", "verified": False},
    ]
    assert "error" not in entry


def test_the_step_banners_go_to_stderr(capsys):
    _run(["query IGPM:BC03"], {"22BC03": ok("62BC03AABB")}, as_json=True)
    captured = capsys.readouterr()
    assert "Query IGPM" in captured.err
    assert "Closing all sessions" in captured.err
    assert "Query IGPM" not in captured.out
    assert "DOOR_STATE" not in captured.err  # the results are not rendered twice


def test_a_pid_that_failed_carries_its_error(capsys):
    _run(["query IGPM:BC03,BC04"], {"22BC03": nrc(0x31, "requestOutOfRange")}, as_json=True)
    doc = json.loads(capsys.readouterr().out)
    bad = next(e for e in doc["results"] if e["pid"] == "22BC03")
    assert bad["raw"] is None and bad["params"] == []
    assert "NRC" in bad["error"] or "0x31" in bad["error"]


def test_a_raw_step_is_an_entry_too(capsys):
    _run(["raw 770:22BC04"], {"22BC04": ok("62BC04CC")}, as_json=True)
    doc = json.loads(capsys.readouterr().out)
    assert doc["results"] == [
        {"ecu": "0x770", "pid": "22BC04", "raw": "62BC04CC", "params": []},
    ]


def test_a_failed_raw_step_says_why(capsys):
    _run(["raw 770:22BC04"], {}, as_json=True)
    (entry,) = json.loads(capsys.readouterr().out)["results"]
    assert entry["raw"] is None and entry["error"]


def test_without_json_the_results_still_print_to_stdout(capsys):
    _run(["query IGPM:BC03"], {"22BC03": ok("62BC03AABB")})
    captured = capsys.readouterr()
    assert "DOOR_STATE" in captured.out
    assert "Query IGPM" in captured.out
