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


class TestHuntControlBehaviour:
    def _write_confounded(self, tmp_path):
        # B4 == the confounder Z; B9 == an independent component W (placed a frame
        # apart so no multi-byte read merges them). Reference X = Z + W, so both
        # bytes correlate with X. Controlling for Z should collapse everything at
        # offset 4 (it is Z) and keep B9 (the part of X not explained by Z).
        z = [0, 1, 2, 3, 4, 5, 6, 7]
        w = [0, 3, 1, 4, 2, 5, 3, 6]
        caps = []
        for i in range(8):
            # raw payload: 61 01 Z 00 00 00 W 00 -> WiCAN B4=Z, B9=W
            payload = f"6101{z[i]:02X}000000{w[i]:02X}00"
            caps.append({"ecu": "OBC", "pid": "2101", "payload": payload, "time": f"09:00:0{i}"})
        doc = {"sessions": [{"date": "2026-07-24", "captures": caps}]}
        (tmp_path / "2026-07-24.yaml").write_text(yaml.safe_dump(doc))
        x_csv = tmp_path / "ref.csv"
        x_csv.write_text("".join(f"2026-07-24 09:00:0{i},{z[i] + w[i]}\n" for i in range(8)))
        z_csv = tmp_path / "ctrl.csv"
        z_csv.write_text("".join(f"2026-07-24 09:00:0{i},{z[i]}\n" for i in range(8)))
        return x_csv, z_csv

    def test_control_demotes_the_confounder_byte(self, tmp_path, monkeypatch, capsys):
        x_csv, z_csv = self._write_confounded(tmp_path)

        def max_abs_r_by_offset(out):
            by_off: dict[int, float] = {}
            for h in json.loads(out)["hits"]:
                by_off[h["offset"]] = max(by_off.get(h["offset"], 0.0), abs(h["r"]))
            return by_off

        # Without control: the confounder byte at offset 4 correlates strongly.
        rc = _run(
            tmp_path,
            monkeypatch,
            ["OBC", "2101", "--against-file", str(x_csv), "--min-n", "4", "--json"],
        )
        assert rc == 0
        plain = max_abs_r_by_offset(capsys.readouterr().out)
        assert plain.get(4, 0.0) > 0.6  # strong apparent link via the confounder

        # With control for Z: the strong Z-driven read at offset 4 has an
        # undefined/collapsed partial correlation, so offset 4's strength drops
        # sharply (only weak float-sweep noise remains); the independent byte at
        # offset 9 stays strong.
        rc = _run(
            tmp_path,
            monkeypatch,
            [
                "OBC",
                "2101",
                "--against-file",
                str(x_csv),
                "--control-file",
                str(z_csv),
                "--min-n",
                "4",
                "--json",
            ],
        )
        assert rc == 0
        controlled = max_abs_r_by_offset(capsys.readouterr().out)
        assert controlled.get(4, 0.0) < 0.6  # confounder-driven correlation removed
        assert controlled.get(4, 0.0) < plain[4] - 0.3  # markedly weaker than uncontrolled
        assert controlled.get(9, 0.0) > 0.4  # the genuinely-independent byte remains
