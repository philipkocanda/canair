"""Tests for `canair signal` — one signal's values over time, found by name."""

import json
import shutil

import pytest

from canair.cli import main
from tests.support.golden import FIXTURE_PROFILES_DIR

FIXTURE = str(FIXTURE_PROFILES_DIR / "single-frame")


@pytest.fixture
def repeated(tmp_path):
    """The single-frame fixture, with BETA also defining ALPHA_RAMP (a repeated name)."""
    root = tmp_path / "repeated"
    shutil.copytree(FIXTURE_PROFILES_DIR / "single-frame", root)
    beta = root / "ecus" / "beta.yaml"
    beta.write_text(
        beta.read_text().replace(
            "        BETA_RAMP:",
            "        ALPHA_RAMP:\n          expression: B5\n          unit: ''\n        BETA_RAMP:",
        )
    )
    return str(root)


class TestOneMatch:
    def test_a_bare_name_prints_the_table_for_its_pid(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "alpha_ramp"]) == 0
        out = capsys.readouterr().out
        assert "ALPHA PID 22F001" in out
        assert "ALPHA_RAMP" in out
        assert "ALPHA_FLAGS_B6_0" not in out

    def test_json_is_per_capture_values_of_that_signal_only(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "ALPHA_RAMP", "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows
        assert all(list(r["parameters"]) == ["ALPHA_RAMP"] for r in rows)

    def test_last_limits_the_captures(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "ALPHA_RAMP", "--json", "--last", "2"]) == 0
        assert len(json.loads(capsys.readouterr().out)) == 2


class TestNoMatch:
    def test_an_unknown_name_is_an_error_with_a_suggestion(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "ALPHA_RAMPS"]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: no signal named 'ALPHA_RAMPS'" in captured.err
        assert "hint: did you mean: ALPHA_RAMP" in captured.err

    def test_a_malformed_name_is_a_usage_error(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "A:B:C:D"]) == 2
        assert "error: invalid signal 'A:B:C:D'" in capsys.readouterr().err

    def test_changes_only_needs_the_table_view(self, capsys):
        assert main(["--profile", FIXTURE, "signal", "ALPHA_RAMP", "--stats", "-c"]) == 2
        assert "--changes-only applies to the table view" in capsys.readouterr().err


class TestRepeatedName:
    def test_the_candidates_are_listed_and_nothing_is_decoded(self, repeated, capsys):
        assert main(["--profile", repeated, "signal", "ALPHA_RAMP"]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: ALPHA_RAMP is defined 2 times. Name one, or pass --all:" in captured.err
        assert "ALPHA:22F001:ALPHA_RAMP" in captured.err
        assert "BETA:22F002:ALPHA_RAMP" in captured.err

    def test_the_ecu_picks_one(self, repeated, capsys):
        assert main(["--profile", repeated, "signal", "BETA:ALPHA_RAMP"]) == 0
        assert "BETA PID 22F002" in capsys.readouterr().out

    def test_the_pid_picks_one(self, repeated, capsys):
        assert main(["--profile", repeated, "signal", "ALPHA:22F001:ALPHA_RAMP"]) == 0
        assert "ALPHA PID 22F001" in capsys.readouterr().out

    def test_all_shows_every_definition(self, repeated, capsys):
        assert main(["--profile", repeated, "signal", "ALPHA_RAMP", "--all", "--json"]) == 0
        blocks = json.loads(capsys.readouterr().out)
        assert sorted((b["ecu"], b["pid"]) for b in blocks) == [
            ("ALPHA", "22F001"),
            ("BETA", "22F002"),
        ]

    def test_all_with_a_single_pid_mode_is_refused_by_decode(self, repeated, capsys):
        assert main(["--profile", repeated, "signal", "ALPHA_RAMP", "--all", "--stats"]) == 2
        assert "requires the query to resolve to a single PID" in capsys.readouterr().err
