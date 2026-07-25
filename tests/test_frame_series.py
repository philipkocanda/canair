"""Stage-2 tests: raw broadcast-CAN frame time-series + `correlate --can-log`.

Frame byte/bit series (`canlib.frame_series`) feed the *same* correlate core as
diagnostic captures. Logs are generated in-test (candump text) with real Ioniq-28
arbitration IDs and a deliberately co-moving cross-ID pair + a noise byte.
"""

from __future__ import annotations

import argparse

import pytest

from canlib import frame_series
from canlib.commands import correlate


def _write_log(tmp_path, n=40):
    """A candump log: 0x220:r0 and 0x386:r0 ramp together; 0x2B0:r0 is noise."""
    lines = []
    for i in range(n):
        t = i * 0.1
        v = i % 256
        lines.append(f"({t:.6f}) can0 220#{v:02X}00000000000000")
        lines.append(f"({t:.6f}) can0 386#{v:02X}FF000000000000")
        lines.append(f"({t:.6f}) can0 2B0#{(i * 7) % 5:02X}00000000000000")
    p = tmp_path / "drive.log"
    p.write_text("\n".join(lines) + "\n")
    return p


class TestBuildFrameSeries:
    def test_labels_and_varying_only(self, tmp_path):
        series = frame_series.build_frame_series(_write_log(tmp_path), min_distinct=4)
        # Only the varying byte-0 of each ID; constant bytes dropped.
        assert set(series) == {"0x220:r0", "0x386:r0", "0x2B0:r0"}
        assert all(k.count(":") == 1 for k in series)  # arbitration-ID grouping intact

    def test_id_filter(self, tmp_path):
        series = frame_series.build_frame_series(
            _write_log(tmp_path), min_distinct=4, id_filter={0x220}
        )
        assert set(series) == {"0x220:r0"}

    def test_bit_series(self, tmp_path):
        bits = frame_series.build_frame_bit_series(_write_log(tmp_path), id_filter={0x386})
        # 0x386:r0 low bits toggle as the byte ramps; r1 is constant 0xFF (no toggle).
        assert any(k.startswith("0x386:r0.") for k in bits)
        assert not any(k.startswith("0x386:r1.") for k in bits)

    def test_parse_id_filter(self):
        assert frame_series.parse_id_filter("0x220,0x386") == {0x220, 0x386}
        assert frame_series.parse_id_filter("220,386") == {0x220, 0x386}
        assert frame_series.parse_id_filter(None) is None
        assert frame_series.parse_id_filter("") is None


class TestCorrelateCanLog:
    def _run(self, argv):
        p = correlate.add_parser(argparse.ArgumentParser().add_subparsers())
        return correlate.run(p.parse_args(argv))

    def test_ranked_finds_cross_id_pair(self, tmp_path, capsys):
        log = _write_log(tmp_path)
        assert self._run(["--can-log", str(log), "--min-r", "0.9", "--join-tol", "0.05"]) == 0
        out = capsys.readouterr().out
        assert "0x220:r0" in out and "0x386:r0" in out
        assert "0x2B0:r0" not in out  # noise byte excluded

    def test_against(self, tmp_path, capsys):
        log = _write_log(tmp_path)
        rc = self._run(
            ["--can-log", str(log), "--against", "0x386:r0", "--min-r", "0.5", "--join-tol", "0.05"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "0x220:r0" in out and "r=+1.000" in out

    def test_json(self, tmp_path, capsys):
        log = _write_log(tmp_path)
        assert (
            self._run(["--can-log", str(log), "--min-r", "0.9", "--join-tol", "0.05", "--json"])
            == 0
        )
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["can_log"] == "drive.log"
        assert {payload["hits"][0]["a"], payload["hits"][0]["b"]} == {"0x220:r0", "0x386:r0"}

    def test_missing_file(self, tmp_path, capsys):
        assert self._run(["--can-log", str(tmp_path / "nope.log")]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_bad_against_label(self, tmp_path, capsys):
        log = _write_log(tmp_path)
        assert self._run(["--can-log", str(log), "--against", "0xNOPE:r0"]) == 1
        assert "not a varying byte" in capsys.readouterr().err

    def test_id_filter_flag(self, tmp_path, capsys):
        log = _write_log(tmp_path)
        # Restrict to one ID -> no cross-ID pair -> no correlations.
        rc = self._run(["--can-log", str(log), "--id", "0x220", "--min-r", "0.9"])
        assert rc == 0
        assert "No cross-ID" in capsys.readouterr().out


def _write_hunt_log(tmp_path, n=40):
    """0x220 byte3 ramps; 0x386 byte0 ramps identically (the reference)."""
    lines = []
    for i in range(n):
        t = i * 0.1
        v = i % 256
        lines.append(f"({t:.6f}) can0 220#000000{v:02X}00000000")
        lines.append(f"({t:.6f}) can0 386#{v:02X}FF000000000000")
    p = tmp_path / "hunt.log"
    p.write_text("\n".join(lines) + "\n")
    return p


class TestHuntFrame:
    def test_finds_matching_byte(self, tmp_path):
        from datetime import datetime

        from canlib.align import TimePoint

        log = _write_hunt_log(tmp_path)
        ref = [TimePoint(datetime.fromtimestamp(i * 0.1), float(i % 256)) for i in range(40)]
        hits = frame_series.hunt_frame(log, None, 0x220, ref, tol_s=0.05, min_n=15, top=5)
        assert hits
        top = hits[0]
        assert top.r == pytest.approx(1.0, abs=1e-6)
        assert top.expr == "r3"  # narrowest exact match preferred
        assert top.width == 1

    def test_no_frames_for_id(self, tmp_path):
        log = _write_hunt_log(tmp_path)
        # No frames for 0x999 → empty result (returns before touching ref).
        assert frame_series.hunt_frame(log, None, 0x999, [], tol_s=1.0) == []


class TestHuntCanLogCommand:
    def _run(self, argv):
        from canlib.commands import hunt

        p = hunt.add_parser(argparse.ArgumentParser().add_subparsers())
        return hunt.run(p.parse_args(argv))

    def test_human_and_json(self, tmp_path, capsys):
        log = _write_hunt_log(tmp_path)
        base = [
            "--can-log",
            str(log),
            "--id",
            "0x220",
            "--against",
            "0x386:r0",
            "--min-n",
            "15",
            "--join-tol",
            "0.05",
        ]
        assert self._run(base) == 0
        assert "r3" in capsys.readouterr().out
        assert self._run([*base, "--json"]) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["target"] == "0x220"
        assert payload["hits"][0]["expr"] == "r3"

    def test_bad_against(self, tmp_path, capsys):
        log = _write_hunt_log(tmp_path)
        assert self._run(["--can-log", str(log), "--id", "0x220", "--against", "0x386:r7"]) == 1
        assert "not a varying byte" in capsys.readouterr().err

    def test_missing_id(self, tmp_path, capsys):
        log = _write_hunt_log(tmp_path)
        assert self._run(["--can-log", str(log), "--against", "0x386:r0"]) == 2
        assert "--id" in capsys.readouterr().err

    def test_promote_rejected(self, tmp_path, capsys):
        log = _write_hunt_log(tmp_path)
        rc = self._run(
            ["--can-log", str(log), "--id", "0x220", "--against", "0x386:r0", "--promote", "X"]
        )
        assert rc == 2
        assert "not supported for frames" in capsys.readouterr().err

    def test_diagnostic_path_requires_ecu_pid(self, capsys):
        assert self._run(["--against", "X:Y:Z"]) == 2
        assert "ECU and PID are required" in capsys.readouterr().err
