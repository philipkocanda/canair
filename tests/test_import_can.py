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

    def test_gvret_deferred(self):
        with pytest.raises(can_logs.CanLogError, match="Stage 3"):
            can_logs.detect_format(Path("x.csv"), "gvret")

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

    def test_dbc_still_not_implemented(self, temp_profile, capsys):
        assert self._run(["dbc", "car.dbc"]) == 2
        assert "not yet implemented" in capsys.readouterr().err


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
