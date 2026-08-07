"""Tests for the corpus/ECU-wide `canair investigate` sweep (Part E).

A bare `investigate` (or an ECU/QUERY alone) sweeps every matching captured PID
and prints a ranked summary; `--counters` sweeps for monotonic counters across
the whole scope. The single-target `ECU PID` form is unchanged. Uses a real
tmp-dir profile via `profile.set_active` so discovery + capture loading resolve
naturally (no loader monkeypatching).
"""

from __future__ import annotations

import argparse
import json

from canlib import profile
from canlib.commands import investigate
from canlib.pids import clear_cache

_ODO_DAYS = [
    ("2026-04-16", 70047),
    ("2026-05-20", 71200),
    ("2026-06-14", 72010),
    ("2026-07-21", 72982),
    ("2026-08-05", 73048),
]


def _odometer_payload(km: int, noise: int) -> str:
    # 62 B0 02 E0 00 00 00 00 <noise> <km hi><mid><lo> 00 00 00 — 3-byte BE odometer at i9-i11
    return f"62B002E000000000{noise:02X}{km:06X}000000"


def _mk_profile(root) -> None:
    (root / "ecus").mkdir(parents=True)
    (root / "captures").mkdir()
    (root / "profile.yaml").write_text('car_model: "T"\ninit: "ATSP6;"\n')
    (root / "ecus" / "clu.yaml").write_text(
        "CLU:\n  tx_id: 0x7C6\n  identity:\n    id_protocol: UDS\n"
    )
    (root / "ecus" / "eng.yaml").write_text(
        "ENG:\n  tx_id: 0x7E4\n  identity:\n    id_protocol: UDS\n"
    )
    noise = [0xAA, 0xAD, 0xAB, 0xAD]
    for date, km in _ODO_DAYS:
        clu_caps = [
            {
                "ecu": "CLU",
                "pid": "22B002",
                "date": date,
                "time": f"09:0{i}:00",
                "payload": _odometer_payload(km, noise[i]),
            }
            for i in range(4)
        ]
        # A second PID with a plainly varying (non-counter) byte at i3.
        eng_caps = [
            {
                "ecu": "ENG",
                "pid": "2101",
                "date": date,
                "time": f"09:0{i}:30",
                "payload": f"6101{(0x10 + (i * 7) % 200):02X}0000",
            }
            for i in range(4)
        ]
        (root / "captures" / f"{date}.json").write_text(
            json.dumps(
                {
                    "sessions": [
                        {"date": date, "vehicle_states": ["READY"], "captures": clu_caps + eng_caps}
                    ]
                }
            )
        )


def _run(root, argv):
    profile.set_active(str(root))
    clear_cache()
    p = investigate.add_parser(argparse.ArgumentParser().add_subparsers())
    return investigate.run(p.parse_args(["uds", *argv]))


def _reset():
    profile._active = None
    clear_cache()


def test_bare_counters_sweep_finds_the_odometer(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["--counters", "--min-bits", "4"])
    finally:
        _reset()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Monotonic counters" in out
    assert "CLU" in out and "22B002" in out
    assert "[B12:B14]" in out  # the odometer window


def test_counters_sweep_json_lists_every_counter(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["--counters", "--min-bits", "4", "--json"])
    finally:
        _reset()
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["mode"] == "counters"
    assert any(c["ecu"] == "CLU" and c["pid"] == "22B002" for c in doc["counters"])


def test_bare_default_sweep_reports_both_pids(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", [])
    finally:
        _reset()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Investigate sweep" in out
    assert "CLU" in out and "22B002" in out
    assert "ENG" in out and "2101" in out


def test_ecu_only_sweep_restricts_to_that_ecu(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["ENG"])
    finally:
        _reset()
    assert rc == 0
    out = capsys.readouterr().out
    assert "ENG" in out
    assert "CLU" not in out


def test_single_target_form_unchanged(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["CLU", "22B002"])
    finally:
        _reset()
    assert rc == 0
    out = capsys.readouterr().out
    # The single-PID deep-dive header, not the sweep summary header.
    assert "Investigate CLU 22B002" in out
    assert "Investigate sweep" not in out


def test_events_sweep_is_refused(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["--events"])
    finally:
        _reset()
    assert rc == 2
    assert "single ECU PID" in capsys.readouterr().err


def test_top_caps_the_summary(tmp_path, capsys):
    _mk_profile(tmp_path / "prof")
    try:
        rc = _run(tmp_path / "prof", ["--top", "1"])
    finally:
        _reset()
    assert rc == 0
    out = capsys.readouterr().out
    assert "+1 more" in out  # two PIDs, capped to one
