"""Tests for ``canair import uds --from-log`` (frame-log extraction, end to end)."""

from __future__ import annotations

import argparse
import json

import pytest

from canlib import can_logs
from canlib.captures import io as capture_io
from canlib.commands.import_uds import run
from tests._isotp_frames import message_frames

BMS_TX, BMS_RX = 0x7E4, 0x7EC
BODY = bytes(range(1, 30))


def write_gvret(path, frames):
    """A SavvyCAN GVRET CSV (microsecond timestamps) holding ``frames``."""
    lines = ["Time Stamp,ID,Extended,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8"]
    for f in frames:
        data = ",".join(f"{b:02X}" for b in f.data)
        lines.append(
            f"{int(f.timestamp * 1_000_000)},{f.arbitration_id:08X},false,"
            f"{f.channel or 0},{len(f.data)},{data}"
        )
    path.write_text("\n".join(lines) + "\n")


def poll(pid: str, ts: float, channel: int = 1, reply: str | None = None):
    """A request for ``pid`` and the BMS reply (a multi-frame one by default)."""
    did = pid[2:]
    body = bytes.fromhex(reply) if reply else bytes.fromhex(f"62{did}") + BODY
    frames = [
        *message_frames(BMS_TX, bytes.fromhex(pid), ts, channel),
        *message_frames(BMS_RX, body, ts + 0.01, channel),
    ]
    for i, f in enumerate(frames):
        f.timestamp = ts + i * 0.001
    return frames


@pytest.fixture(autouse=True)
def _named_car(monkeypatch):
    """Imports must name the car on a multi-variant profile: give the suite's one."""
    import dataclasses

    from canlib import profile as profile_mod

    prof = dataclasses.replace(profile_mod.active(), variant="28kwh", variant_source="flag")
    monkeypatch.setattr(profile_mod, "_active", prof)


def _args(log, **kw) -> argparse.Namespace:
    base = {
        "spec": [],
        "from_log": str(log),
        "log_format": "auto",
        "bus": None,
        "from_ts": None,
        "until_ts": None,
        "keep_nrc": False,
        "dry_run": False,
        "label": "Dongle polling",
        "state": None,
        "notes": None,
        "capture_note": None,
        "time": None,
        "date": "2023-01-12",
        "json": False,
        "dir": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def log(tmp_path):
    frames = [
        *poll("220101", 100.0),
        *poll("220102", 100.5),
        *poll("220101", 101.0),
        *poll("220101", 400.0, channel=0),  # another bus
    ]
    path = tmp_path / "tap.csv"
    write_gvret(path, frames)
    return path


def session_of(tmp_path):
    data = capture_io.load_capture_file(tmp_path / "out" / "2023-01-12.json")
    (session,) = data["sessions"]
    return session


class TestRun:
    def test_records_each_reply_with_its_own_time(self, log, tmp_path):
        assert run(_args(log, dir=tmp_path / "out", time="09:30:00", bus=1)) == 0
        session = session_of(tmp_path)
        assert [(c["pid"], c["time"]) for c in session["captures"]] == [
            ("220101", "09:30:00.000"),
            ("220102", "09:30:00.500"),
            ("220101", "09:30:01.000"),
        ]
        assert {c["rx"] for c in session["captures"]} == {"0x7EC"}
        assert session["transport"] == "import"
        assert "tap.csv" in session["notes"]
        assert "quality" in session and "drop" not in session["quality"]

    def test_a_lost_frame_is_recorded_in_the_session_quality(self, tmp_path):
        frames = poll("220101", 1.0) + poll("220102", 2.0)
        del frames[-2]  # a consecutive frame of the second reply
        path = tmp_path / "gap.csv"
        write_gvret(path, frames)
        assert run(_args(path, dir=tmp_path / "out", bus=1)) == 0
        session = session_of(tmp_path)
        assert [c["pid"] for c in session["captures"]] == ["220101"]
        assert session["quality"] == {"exchanges": 2, "drop": 1}

    def test_bus_and_window_select_frames(self, log, tmp_path):
        assert run(_args(log, dir=tmp_path / "out", bus=1, from_ts=100.4, until_ts=100.9)) == 0
        assert [c["pid"] for c in session_of(tmp_path)["captures"]] == ["220102"]

    def test_without_a_bus_every_bus_is_read(self, log, tmp_path):
        assert run(_args(log, dir=tmp_path / "out")) == 0
        assert len(session_of(tmp_path)["captures"]) == 4

    def test_dry_run_writes_nothing(self, log, tmp_path):
        assert run(_args(log, dir=tmp_path / "out", dry_run=True)) == 0
        assert not (tmp_path / "out").exists()

    def test_the_same_log_is_not_imported_twice(self, log, tmp_path, capsys):
        assert run(_args(log, dir=tmp_path / "out")) == 0
        assert run(_args(log, dir=tmp_path / "out")) == 2
        assert "identical session" in capsys.readouterr().err
        assert len(json.loads((tmp_path / "out" / "2023-01-12.json").read_text())["sessions"]) == 1

    def test_json_summary(self, log, tmp_path, capsys):
        assert run(_args(log, dir=tmp_path / "out", json=True, bus=1)) == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["captures"] == 3
        assert summary["by_pid"] == {"0x7EC:220101": 2, "0x7EC:220102": 1}

    def test_wall_clock_log_supplies_its_own_date_and_time(self, tmp_path):
        epoch = 1_673_516_400.0  # a wall-clock (epoch) log; the date is read in local time
        path = tmp_path / "abs.csv"
        write_gvret(path, poll("220101", epoch))
        assert run(_args(path, dir=tmp_path / "out", date=None)) == 0
        (file,) = (tmp_path / "out").glob("*.json")
        session = json.loads(file.read_text())["sessions"][0]
        assert session["date"] == file.stem
        assert session["captures"][0]["time"].count(":") == 2


class TestRefusals:
    @pytest.mark.parametrize(
        ("kw", "message"),
        [
            ({"spec": ["BMS:220101=6201"]}, "not both"),
            ({"capture_note": "x"}, "--capture-note"),
            ({"from_ts": 5.0, "until_ts": 5.0}, "earlier"),
            ({"time": "25:00"}, "HH:MM:SS"),
            ({"date": "12/01/2023"}, "YYYY-MM-DD"),
        ],
    )
    def test_bad_options(self, log, tmp_path, capsys, kw, message):
        assert run(_args(log, dir=tmp_path / "out", **kw)) == 2
        assert message in capsys.readouterr().err
        assert not (tmp_path / "out").exists()

    def test_missing_file(self, tmp_path, capsys):
        assert run(_args(tmp_path / "nope.csv", dir=tmp_path / "out")) == 2
        assert "no such file" in capsys.readouterr().err

    def test_log_with_no_replies_from_a_known_ecu(self, tmp_path, capsys):
        path = tmp_path / "quiet.csv"
        write_gvret(path, message_frames(0x6AA, bytes.fromhex("620101AA"), 1.0, 1))
        assert run(_args(path, dir=tmp_path / "out")) == 2
        err = capsys.readouterr().err
        assert "0x6AA" in err and "no data-read replies" in err

    def test_running_past_midnight(self, log, tmp_path, capsys):
        assert run(_args(log, dir=tmp_path / "out", time="23:59:59")) == 2
        assert "past midnight" in capsys.readouterr().err

    def test_without_specs_or_a_log(self, tmp_path, capsys):
        args = _args(tmp_path / "x.csv", from_log=None, dir=tmp_path)
        assert run(args) == 2
        assert "--from-log" in capsys.readouterr().err

    def test_dry_run_alone_is_not_a_preview_of_pasted_captures(self, tmp_path, capsys):
        args = _args(tmp_path / "x.csv", from_log=None, spec=["BMS:220101=6201"], dry_run=True)
        assert run(args) == 2
        assert not list(tmp_path.glob("*.json"))


class TestGvretChannel:
    def test_bus_column_becomes_the_message_channel(self, log):
        channels = {m.channel for m in can_logs.iter_frames(log, "gvret")}
        assert channels == {0, 1}
