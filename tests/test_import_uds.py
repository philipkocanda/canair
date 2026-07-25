"""Tests for canair import uds (device-free capture import)."""

from __future__ import annotations

import argparse

import pytest
import yaml

from canlib.captures import build_manual_session
from canlib.commands.import_uds import _build_capture, _parse_spec, run

_NAME_INDEX = {"CLU": 0x7C6}


class TestParseSpec:
    def test_valid(self):
        assert _parse_spec("CLU:22B002=62B002E0") == ("CLU", "22B002", "62B002E0")

    def test_lowercase_and_spaces_normalized(self):
        assert _parse_spec("clu:22b002=62 b0 02") == ("clu", "22B002", "62B002")

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="missing '='"):
            _parse_spec("CLU:22B002")

    def test_missing_colon(self):
        with pytest.raises(ValueError, match="missing ':'"):
            _parse_spec("CLU22B002=62B0")

    def test_empty_part(self):
        with pytest.raises(ValueError, match="non-empty"):
            _parse_spec("CLU:=62B0")


class TestBuildCapture:
    def test_resolves_ecu_to_rx(self):
        cap, warns = _build_capture("CLU:22B002=62B002E0", _NAME_INDEX)
        assert cap == {"ecu": "0x7CE", "pid": "22B002", "payload": "62B002E0"}
        assert warns == []

    def test_hex_tx_id_accepted(self):
        cap, _ = _build_capture("0x7C6:22B002=62B002E0", _NAME_INDEX)
        assert cap["ecu"] == "0x7CE"

    def test_unknown_ecu_raises(self):
        with pytest.raises(ValueError, match="unknown ECU"):
            _build_capture("NOPE:2101=6101", _NAME_INDEX)

    def test_non_hex_payload_raises(self):
        with pytest.raises(ValueError, match="not hex"):
            _build_capture("CLU:22B002=NODATA", _NAME_INDEX)

    def test_echo_mismatch_warns_not_raises(self):
        # 62B002... filed under 2102 echoes 62 (not the expected 61) -> warn.
        cap, warns = _build_capture("CLU:2102=62B002E0", _NAME_INDEX)
        assert cap["pid"] == "2102"
        assert warns and "SID" in warns[0]


class TestBuildManualSession:
    def test_minimal(self):
        s = build_manual_session([{"ecu": "0x7CE", "pid": "22B002", "payload": "62"}], label="Odo")
        assert s["label"] == "Odo"
        assert "date" in s and s["captures"][0]["pid"] == "22B002"
        assert "vehicle_states" not in s and "notes" not in s

    def test_full(self):
        s = build_manual_session(
            [{"ecu": "0x7CE", "pid": "22B002", "payload": "62"}],
            label="Odo",
            date="2026-07-25",
            vehicle_states=["acc2"],
            notes="ctx",
        )
        assert s["date"] == "2026-07-25"
        assert s["vehicle_states"] == ["acc2"]
        assert s["notes"] == "ctx"


def _args(**kw) -> argparse.Namespace:
    base = {
        "spec": [],
        "label": "L",
        "state": None,
        "notes": None,
        "capture_note": None,
        "time": None,
        "date": None,
        "json": False,
        "dir": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


class TestRun:
    def test_writes_capture_file(self, tmp_path):
        # Uses the suite-pinned ioniq-2017 profile for ECU name resolution;
        # writes into an isolated tmp captures dir.
        rc = run(
            _args(
                spec=["CLU:22B002=62B002E0000000FFB7008D08000000"],
                label="Odometer",
                state=["acc2"],
                time="09:38:15",
                date="2026-07-25",
                notes="verified on dash",
                dir=tmp_path,
            )
        )
        assert rc == 0
        data = yaml.safe_load((tmp_path / "2026-07-25.yaml").read_text())
        sess = data["sessions"][0]
        assert sess["label"] == "Odometer"
        assert sess["vehicle_states"] == ["acc2"]
        cap = sess["captures"][0]
        assert cap == {
            "ecu": "0x7CE",
            "pid": "22B002",
            "payload": "62B002E0000000FFB7008D08000000",
            "time": "09:38:15",
        }

    def test_multi_capture_one_session(self, tmp_path):
        rc = run(
            _args(
                spec=["CLU:22B002=62B00201", "CLU:22B003=62B00302"],
                label="two",
                date="2026-01-01",
                dir=tmp_path,
            )
        )
        assert rc == 0
        data = yaml.safe_load((tmp_path / "2026-01-01.yaml").read_text())
        assert len(data["sessions"]) == 1
        assert len(data["sessions"][0]["captures"]) == 2

    def test_unknown_ecu_returns_error_code(self, tmp_path):
        rc = run(_args(spec=["NOPE:2101=6101"], label="x", dir=tmp_path))
        assert rc == 2
        assert not list(tmp_path.glob("*.yaml"))  # nothing written
