"""Stage-1 tests: `canair import can` (raw broadcast-CAN frame-log import).

Covers the log-store layer (`canlib.can_logs`), the `import can` command, and the
`canair captures --can` listing. Fixtures use a tiny candump slice with real
Ioniq-28 arbitration IDs (tests/fixtures/can/) plus formats generated in-test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import canlib.profile as profile
from canlib import can_logs
from canlib.commands import validate
from canlib.profile import Profile

FIXTURE = Path(__file__).parent / "fixtures" / "can" / "ioniq28_drive_slice.log"
EXPECT_IDS = ["0x200", "0x220", "0x2B0", "0x386"]

# A verbatim slice of the real uhi22 GVRET drive log (attribution in the fixture
# dir README): 0x354 gear lever + 0x386 wheel speeds, all four gear codes.
GVRET_FIXTURE = Path(__file__).parent / "fixtures" / "can" / "ioniq28_gear_drive_slice.csv"


@pytest.fixture
def temp_profile(tmp_path):
    profile._active = Profile(name="test", root=tmp_path)
    return Profile(name="test", root=tmp_path)


# ── detect_format ─────────────────────────────────────────────────────────


class TestDetectFormat:
    def test_by_extension(self):
        assert can_logs.detect_format(Path("x.asc")) == "asc"
        assert can_logs.detect_format(Path("x.blf")) == "blf"
        assert can_logs.detect_format(Path("x.log")) == "log"
        assert can_logs.detect_format(Path("x.csv")) == "csv"

    def test_explicit_overrides(self):
        assert can_logs.detect_format(Path("x.dat"), "asc") == "asc"

    def test_gvret_explicit(self):
        # GVRET is now a supported format (Stage 3), forced by --format even on .csv.
        assert can_logs.detect_format(Path("x.csv"), "gvret") == "gvret"

    def test_real_gvret_fixture_autodetects(self):
        # The committed real-log slice sniffs as gvret from its header on auto.
        assert can_logs.detect_format(GVRET_FIXTURE) == "gvret"

    def test_unknown_format_rejected(self):
        with pytest.raises(can_logs.CanLogError, match="unknown format"):
            can_logs.detect_format(Path("x.csv"), "bogus")

    def test_unknown_extension(self):
        with pytest.raises(can_logs.CanLogError, match="format"):
            can_logs.detect_format(Path("x.weird"))


# ── scan_log / import_log ─────────────────────────────────────────────────


class TestImportLog:
    def test_candump_fixture_summary(self, temp_profile):
        res = can_logs.import_log(FIXTURE, temp_profile, label="drive slice", bitrate=500000)
        assert res.summary.frame_count == 8
        assert res.entry["id_set"] == EXPECT_IDS
        assert res.entry["format"] == "log"
        assert res.entry["frame_count"] == 8
        assert res.entry["label"] == "drive slice"
        assert res.entry["bitrate"] == 500000
        # relative timestamps → no derived date (deterministic)
        assert "date" not in res.entry
        assert res.stored_path.exists()
        assert res.stored_path.parent == temp_profile.can_dir

    def test_index_written_and_valid(self, temp_profile):
        can_logs.import_log(FIXTURE, temp_profile, label="drive slice")
        assert validate._run_can() == 0  # written index passes the schema
        logs = can_logs.list_logs(temp_profile)
        assert len(logs) == 1 and logs[0]["file"] == FIXTURE.name

    def test_real_gvret_fixture_import(self, temp_profile):
        # Guards the RE'd powertrain signals: the real-log slice imports as gvret
        # and carries the gear-lever (0x354) and wheel-speed (0x386) frames.
        res = can_logs.import_log(
            GVRET_FIXTURE, temp_profile, fmt="gvret", label="uhi22 gear+speed slice"
        )
        assert res.entry["format"] == "gvret"
        assert "0x354" in res.entry["id_set"] and "0x386" in res.entry["id_set"]
        # all four gear codes (P/R/N/D) survive the trim
        gears = {
            m.data[5] for m in can_logs.iter_frames(GVRET_FIXTURE, "gvret")
            if m.arbitration_id == 0x354
        }
        assert gears == {1, 2, 3, 4}
        assert validate._run_can() == 0

    def test_states_split_and_source(self, temp_profile):
        res = can_logs.import_log(
            FIXTURE,
            temp_profile,
            vehicle_states=["driving", "ready"],
            source="https://example/uhi22",
        )
        assert res.entry["vehicle_states"] == ["driving", "ready"]
        assert res.entry["source"] == "https://example/uhi22"

    def test_missing_file(self, temp_profile):
        with pytest.raises(can_logs.CanLogError, match="no such file"):
            can_logs.import_log(Path("/nonexistent/x.log"), temp_profile)

    def test_empty_log_rejected(self, temp_profile, tmp_path):
        empty = tmp_path / "empty.log"
        empty.write_text("")
        with pytest.raises(can_logs.CanLogError, match="no CAN frames"):
            can_logs.import_log(empty, temp_profile)

    def test_collision_without_force(self, temp_profile):
        can_logs.import_log(FIXTURE, temp_profile)
        with pytest.raises(can_logs.CanLogError, match="already exists"):
            can_logs.import_log(FIXTURE, temp_profile)

    def test_force_replaces_entry_not_duplicates(self, temp_profile):
        can_logs.import_log(FIXTURE, temp_profile, label="first")
        can_logs.import_log(FIXTURE, temp_profile, label="second", force=True)
        logs = can_logs.list_logs(temp_profile)
        assert len(logs) == 1  # replaced, not appended
        assert logs[0]["label"] == "second"

    def test_asc_format_generated_in_test(self, temp_profile, tmp_path):
        import can

        asc = tmp_path / "gen.asc"
        with can.ASCWriter(str(asc)) as w:
            for i in range(5):
                w.on_message_received(
                    can.Message(
                        arbitration_id=0x220,
                        data=bytes.fromhex("0011223344556677"),
                        timestamp=float(i) / 100,
                        is_extended_id=False,
                    )
                )
        res = can_logs.import_log(asc, temp_profile)
        assert res.entry["format"] == "asc"
        assert res.entry["frame_count"] == 5
        assert res.entry["id_set"] == ["0x220"]


def _write_gvret(tmp_path, n=20):
    """A SavvyCAN GVRET CSV slice matching the uhi22 layout (µs timestamps)."""
    header = "Time Stamp,ID,Extended,Dir,Bus,LEN,D1,D2,D3,D4,D5,D6,D7,D8"
    rows = [header]
    for i in range(n):
        ts = 97637502 + i * 10000
        rows.append(f"{ts},0000010C,false,Rx,0,8,04,00,55,54,15,80,01,{i % 256:02X},")
        rows.append(f"{ts},0000057F,false,Rx,0,8,80,0A,66,00,00,00,00,00,")
    p = tmp_path / "IONIQ_PCAN_drive.csv"
    p.write_text("\n".join(rows) + "\n")
    return p


class TestGvret:
    def test_auto_detect_by_header(self, tmp_path):
        # A .csv with a GVRET header auto-resolves to gvret, not python-can csv.
        assert can_logs.detect_format(_write_gvret(tmp_path)) == "gvret"

    def test_parse_fields(self, tmp_path):
        msgs = list(can_logs.iter_frames(_write_gvret(tmp_path, n=3), "gvret"))
        assert len(msgs) == 6
        m0 = msgs[0]
        assert m0.arbitration_id == 0x10C
        assert m0.data.hex() == "0400555415800100"
        assert m0.dlc == 8
        assert m0.timestamp == pytest.approx(97.637502)  # microseconds -> seconds

    def test_import(self, temp_profile, tmp_path):
        res = can_logs.import_log(_write_gvret(tmp_path), temp_profile, label="gvret drive")
        assert res.entry["format"] == "gvret"
        assert res.entry["frame_count"] == 40
        assert res.entry["id_set"] == ["0x10C", "0x57F"]

    def test_non_gvret_csv_not_misdetected(self, tmp_path):
        # A python-can CSV header must NOT sniff as gvret.
        p = tmp_path / "pycan.csv"
        p.write_text("timestamp,arbitration_id,extended,remote,error,dlc,data\n")
        assert can_logs.detect_format(p) == "csv"


# ── import command ────────────────────────────────────────────────────────


class TestImportCanCommand:
    def _run(self, argv):
        import argparse

        from canlib.commands import import_ as import_cmd

        p = import_cmd.add_parser(argparse.ArgumentParser().add_subparsers())
        args = p.parse_args(argv)
        return import_cmd.run(args)

    def test_import_and_json(self, temp_profile, capsys):
        assert self._run(["can", str(FIXTURE), "--label", "drive", "--json"]) == 0
        out = capsys.readouterr().out
        import json

        payload = json.loads(out)
        assert payload["index_entry"]["frame_count"] == 8
        assert payload["index_entry"]["id_set"] == EXPECT_IDS

    def test_import_human_output(self, temp_profile, capsys):
        assert self._run(["can", str(FIXTURE)]) == 0
        out = capsys.readouterr().out
        assert "8 frames" in out and "0x220" in out

    def test_missing_file_returns_1(self, temp_profile, capsys):
        assert self._run(["can", "/nonexistent/x.log"]) == 1
        assert "no such file" in capsys.readouterr().err

    def test_dbc_missing_file(self, temp_profile, capsys):
        assert self._run(["dbc", "/nonexistent/car.dbc"]) == 1
        assert "no such file" in capsys.readouterr().err


# ── captures --can listing ────────────────────────────────────────────────


class TestCapturesCanListing:
    def _run_captures(self, argv):
        import argparse

        from canlib.commands import captures

        p = captures.add_parser(argparse.ArgumentParser().add_subparsers())
        return captures.run(p.parse_args(argv))

    def test_empty(self, temp_profile, capsys):
        assert self._run_captures(["--can"]) == 0
        assert "No imported CAN frame logs" in capsys.readouterr().out

    def test_lists_after_import(self, temp_profile, capsys):
        can_logs.import_log(FIXTURE, temp_profile, label="drive slice", bitrate=500000)
        assert self._run_captures(["--can"]) == 0
        out = capsys.readouterr().out
        assert FIXTURE.name in out and "8 frames" in out and "drive slice" in out

    def test_json(self, temp_profile, capsys):
        can_logs.import_log(FIXTURE, temp_profile)
        assert self._run_captures(["--can", "--json"]) == 0
        import json

        logs = json.loads(capsys.readouterr().out)
        assert len(logs) == 1 and logs[0]["id_set"] == EXPECT_IDS
