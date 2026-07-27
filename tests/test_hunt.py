"""Tests for `canair hunt` external-reference (--against-file) support."""

import argparse
import json

import yaml

from canlib.commands import hunt


def _write_ramp(tmp_path):
    """AAF 2181 where the first data byte (B3) ramps over five timed captures."""
    caps = []
    times = ["09:00:00", "09:00:01", "09:00:02", "09:00:03", "09:00:04"]
    for i, t in enumerate(times):
        v = 10 + i * 10  # 10,20,30,40,50 — ≥3 distinct
        caps.append({"ecu": "AAF", "pid": "2181", "payload": f"6181{v:02X}0000", "time": t})
    doc = {"sessions": [{"date": "2026-07-24", "vehicle_states": ["driving"], "captures": caps}]}
    (tmp_path / "2026-07-24.yaml").write_text(yaml.safe_dump(doc))


def _run(tmp_path, monkeypatch, argv):
    import canlib.align as align

    orig = align.load_signal_captures
    monkeypatch.setattr(
        "canlib.commands.hunt.load_signal_captures",
        lambda s, **kw: orig(
            s, captures_dir=tmp_path, **{k: v for k, v in kw.items() if k != "captures_dir"}
        ),
    )
    monkeypatch.setattr("canlib.ecus.canonical_ecu_name_safe", lambda e: e.upper())
    p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
    args = p.parse_args(["uds", *argv])
    return args.func(args)


class TestHuntAgainstFile:
    def test_external_reference_finds_byte(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        csv = tmp_path / "gps.csv"
        csv.write_text(
            "timestamp,value\n"
            "2026-07-24 09:00:00,1.0\n"
            "2026-07-24 09:00:01,2.0\n"
            "2026-07-24 09:00:02,3.0\n"
            "2026-07-24 09:00:03,4.0\n"
            "2026-07-24 09:00:04,5.0\n"
        )
        rc = _run(
            tmp_path,
            monkeypatch,
            ["AAF", "2181", "--against-file", str(csv), "--min-n", "4", "--json"],
        )
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["reference"] == "gps.csv"
        # The ramping byte B3 should be the top (perfectly-correlated) hit.
        assert data["hits"], "expected at least one hit"
        assert data["hits"][0]["expr"] == "B3"
        assert abs(data["hits"][0]["r"]) > 0.99

    def test_bad_file_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        rc = _run(
            tmp_path, monkeypatch, ["AAF", "2181", "--against-file", str(tmp_path / "nope.csv")]
        )
        assert rc == 1
        assert "--against-file error" in capsys.readouterr().err

    def test_against_and_against_file_mutually_exclusive(self, tmp_path):
        # argparse enforces the mutually-exclusive required group at parse time.
        p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
        try:
            p.parse_args(
                ["uds", "AAF", "2181", "--against", "ESC:22C101:X", "--against-file", "f.csv"]
            )
            raise AssertionError("expected SystemExit from mutually-exclusive group")
        except SystemExit:
            pass


class TestHuntPhysical:
    def _write_voltage(self, tmp_path):
        # OBC 2101 multi-frame: a centivolt AC-voltage word at [B4:B5].
        caps = []
        for i, cv in enumerate((21850, 22100, 22400, 22750, 22200)):
            caps.append(
                {
                    "ecu": "OBC",
                    "pid": "2101",
                    "payload": f"6101{cv:04X}00000000",
                    "time": f"09:00:0{i}",
                }
            )
        doc = {
            "sessions": [{"date": "2026-07-24", "vehicle_states": ["charging"], "captures": caps}]
        }
        (tmp_path / "2026-07-24.yaml").write_text(yaml.safe_dump(doc))

    def test_physical_flags_mains_band(self, tmp_path, monkeypatch, capsys):
        self._write_voltage(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["OBC", "2101", "--physical", "--min-n", "3", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "physical"
        assert any(h["band"] == "mains RMS V" and h["scaling"] == "/100" for h in data["hits"])

    def test_physical_needs_no_reference(self, tmp_path, monkeypatch):
        # --physical is a valid member of the required reference group.
        self._write_voltage(tmp_path)
        rc = _run(tmp_path, monkeypatch, ["OBC", "2101", "--physical", "--min-n", "3"])
        assert rc == 0


class TestHuntControl:
    def test_control_and_control_file_mutually_exclusive(self, tmp_path, monkeypatch, capsys):
        _write_ramp(tmp_path)
        rc = _run(
            tmp_path,
            monkeypatch,
            [
                "AAF",
                "2181",
                "--against",
                "ESC:22C101:X",
                "--control",
                "A:B:C",
                "--control-file",
                "f",
            ],
        )
        assert rc == 2
        assert "mutually exclusive" in capsys.readouterr().err
