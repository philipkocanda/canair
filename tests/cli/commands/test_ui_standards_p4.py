"""The CLI output standards for the captures, analysis, status, update and contribute commands.

Colour follows ``NO_COLOR``/``FORCE_COLOR``; a declined prompt exits 1 and a prompt with no
terminal raises; a note never shares stdout with a ``--json`` result; data put into Rich markup
is escaped. See the ``cli-tui-standards`` skill.
"""

from __future__ import annotations

import io
import json
import re
import sys

import pytest

from canair.captures import build_query_session, save_session
from canair.captures.store import load_all_captures
from canair.cli.commands import _contribute_report as report
from canair.cli.commands import status as status_cmd
from canair.cli.commands import update as update_cmd
from canair.cli.commands.captures.backfill import cmd_backfill_states
from canair.cli.commands.captures.backfill_render import print_report
from canair.cli.commands.captures.backfill_spans_render import print_span_report
from canair.cli.commands.captures.delete import cmd_delete
from canair.cli.commands.captures.set_state import cmd_set_state
from canair.core.prompt import PromptUnavailable

ESC = re.compile(r"\x1b\[")

# VCU:2101 with EV_READY=1 and GEAR_PARK=1: READY, PARKED and IG3 can be inferred from it.
_VCU_READY_PARK = "6101ffe0000009215a5e064803000000009478340420"


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool = True):
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _Console:
    def print(self, *args, **kwargs) -> None:
        pass


def _rows() -> list[dict]:
    return [
        {
            "verdict": "fill",
            "will_write": True,
            "times": ["10:00:00"],
            "date": "2026-11-05",
            "label": "drive",
            "recorded": [],
            "inferred": ["READY"],
            "definitely_false": [],
            "new_states": ["READY"],
        },
        {
            "verdict": "conflict",
            "will_write": False,
            "times": ["11:00:00"],
            "date": "2026-11-05",
            "label": "park",
            "recorded": ["READY"],
            "inferred": ["PARKED"],
            "definitely_false": ["READY"],
            "new_states": ["PARKED"],
        },
    ]


def _span_rows() -> list[dict]:
    return [
        {
            "verdict": "spans",
            "will_write": True,
            "times": ["10:00:00"],
            "date": "2026-11-05",
            "label": "drive",
            "recorded": ["READY", "PARKED"],
            "n_spans": 2,
            "n_cycles": 10,
            "carried": ["PLUGGED"],
            "n_untimed": 0,
        },
        {
            "verdict": "no-evidence",
            "will_write": False,
            "times": [],
            "date": "2026-11-05",
            "label": "x",
            "recorded": ["ACC", "RUN"],
            "n_spans": 0,
            "n_cycles": 3,
            "carried": [],
            "n_untimed": 3,
        },
    ]


def _seed(cdir, payload="6101EEFF", n=2, rx="0x7EB", pid="2101"):
    for i in range(n):
        s = build_query_session([(rx, pid, payload, "")], f"s{i}", [], "", transport="slcan-tcp")
        save_session(s, cdir)


class TestBackfillRenderersFollowTheGate:
    @pytest.mark.parametrize(
        "render",
        [
            lambda: print_report(_rows(), overwrite=False, dry_run=True),
            lambda: print_span_report(_span_rows(), dry_run=True),
        ],
        ids=["states", "spans"],
    )
    def test_force_color_colours_a_pipe_and_no_color_does_not(self, monkeypatch, capsys, render):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("FORCE_COLOR", "1")
        render()
        assert ESC.search(capsys.readouterr().out)

        # A terminal that asked for no colour gets none (the old private palette ignored this).
        monkeypatch.delenv("FORCE_COLOR")
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        render()
        assert not ESC.search(capsys.readouterr().out)

    def test_the_report_goes_where_it_is_told(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        print_report(_rows(), overwrite=False, dry_run=True, out=sys.stderr)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Analyzed 2 session(s)" in captured.err

    def test_a_conflict_is_a_warning_not_an_error(self, monkeypatch, capsys):
        monkeypatch.setenv("FORCE_COLOR", "1")
        print_report(_rows(), overwrite=False, dry_run=True)
        out = capsys.readouterr().out
        assert "\x1b[91m" not in out  # red is for errors
        assert "\x1b[93m" in out


class TestPromptsDeclineWithExit1:
    def test_delete_declined(self, tmp_path, monkeypatch, capsys):
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("n\n"))
        capsys.readouterr()
        assert cmd_delete(entries, "MCU", captures_dir=tmp_path) == 1
        assert "Aborted — nothing changed." in capsys.readouterr().err
        assert len(load_all_captures(tmp_path)) == 2

    def test_delete_with_no_terminal_raises(self, tmp_path, monkeypatch):
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("y\n", tty=False))
        with pytest.raises(PromptUnavailable, match="--yes"):
            cmd_delete(entries, "MCU", captures_dir=tmp_path)
        assert len(load_all_captures(tmp_path)) == 2

    def test_set_state_declined(self, tmp_path, monkeypatch, capsys):
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("\n"))
        capsys.readouterr()
        assert cmd_set_state(entries, "ACC", captures_dir=tmp_path) == 1
        assert "Aborted — nothing changed." in capsys.readouterr().err

    def test_backfill_declined(self, tmp_path, monkeypatch, capsys):
        _seed(tmp_path, _VCU_READY_PARK, n=1, rx="0x7EA")
        entries = load_all_captures(tmp_path)
        monkeypatch.setattr("sys.stdin", _Stdin("n\n"))
        capsys.readouterr()
        assert cmd_backfill_states(entries, captures_dir=tmp_path) == 1
        captured = capsys.readouterr()
        assert "Aborted — nothing changed." in captured.err
        assert "Write states to these 1 session(s)? [y/N]" in captured.err
        assert "[y/N]" not in captured.out

    def test_write_confirmation_is_a_tick_with_the_full_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        capsys.readouterr()
        assert cmd_set_state(entries, "ACC", captures_dir=tmp_path, assume_yes=True) == 0
        out = capsys.readouterr().out
        assert re.search(r"✓ Set state on 2 session\(s\)\s+\(/.*\.json\)", out)


class TestJsonStdoutIsOneDocument:
    def test_delete_json_with_yes(self, tmp_path, capsys):
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        capsys.readouterr()
        rc = cmd_delete(entries, "MCU", captures_dir=tmp_path, assume_yes=True, as_json=True)
        captured = capsys.readouterr()
        assert rc == 0
        assert len(json.loads(captured.out)) == 2  # nothing but the JSON
        assert "✓ Deleted 2 capture(s)" in captured.err

    def test_set_state_json_with_yes(self, tmp_path, capsys):
        _seed(tmp_path)
        entries = load_all_captures(tmp_path)
        capsys.readouterr()
        assert cmd_set_state(entries, "ACC", assume_yes=True, as_json=True) == 0
        captured = capsys.readouterr()
        assert len(json.loads(captured.out)) == 2
        assert "✓ Set state on 2 session(s)" in captured.err

    def test_backfill_json_with_yes(self, tmp_path, capsys):
        _seed(tmp_path, _VCU_READY_PARK, n=1, rx="0x7EA")
        entries = load_all_captures(tmp_path)
        capsys.readouterr()
        rc = cmd_backfill_states(entries, captures_dir=tmp_path, assume_yes=True, as_json=True)
        captured = capsys.readouterr()
        assert rc == 0
        assert json.loads(captured.out)[0]["verdict"] == "fill"
        assert "Analyzed 1 session(s)" in captured.err


class TestReporterConfirm:
    def test_decline_exits_1_and_says_what_was_kept(self, monkeypatch):
        said: list[str] = []

        class Console:
            def print(self, text, *args, **kwargs) -> None:
                said.append(text)

        rep = report.Reporter(console=Console(), json_mode=False, yes=False)
        monkeypatch.setattr(report, "confirm", lambda *a, **k: False)
        with pytest.raises(report.Stop) as stop:
            rep.confirm("Push?", error="not confirmed", after_decline="Nothing was pushed.")
        assert stop.value.code == 1
        assert said == ["  Nothing was pushed."]

    def test_yes_does_not_ask(self, monkeypatch):
        rep = report.Reporter(console=_Console(), json_mode=False, yes=True)
        monkeypatch.setattr(report, "confirm", lambda *a, **k: pytest.fail("asked"))
        rep.confirm("Push?", error="not confirmed")


class TestRichMarkupIsEscaped:
    def test_status_prints_names_and_paths_with_brackets_literally(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        status_cmd._render(
            {
                "profile": {"name": "car[bold]x[/bold]", "root": "/tmp/[red]p"},
                "warnings": ["see [link=x]this[/link]"],
                "errors": ["bad [/nope]"],
            }
        )
        out = capsys.readouterr().out
        assert "car[bold]x[/bold]" in out
        assert "/tmp/[red]p" in out
        assert "see [link=x]this[/link]" in out
        assert "bad [/nope]" in out

    def test_status_uses_the_plain_cross_for_a_failure(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        status_cmd._render({"errors": ["boom"]})
        out = capsys.readouterr().out
        assert "✗ boom" in out and "✖" not in out

    def test_status_follows_force_color_and_no_color(self, monkeypatch, capsys):
        monkeypatch.setenv("FORCE_COLOR", "1")
        status_cmd._render({"warnings": ["careful"]})
        assert ESC.search(capsys.readouterr().out)
        monkeypatch.delenv("FORCE_COLOR")
        monkeypatch.setenv("NO_COLOR", "1")
        status_cmd._render({"warnings": ["careful"]})
        assert not ESC.search(capsys.readouterr().out)

    def test_update_prints_install_paths_with_brackets_literally(self, monkeypatch, capsys):
        monkeypatch.setenv("NO_COLOR", "1")
        install = {
            "running_origin": "repo",
            "running_package_dir": "/tmp/[bold]pkg",
            "tool_version": "1.0[x]",
            "clone_version": "1.0[x]",
            "out_of_sync": False,
        }
        update_cmd._print_install_context(update_cmd.make_console(), install)
        out = capsys.readouterr().out
        assert "/tmp/[bold]pkg" in out and "1.0[x]" in out

    def test_update_declines_with_the_shared_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", _Stdin("n\n"))
        assert update_cmd.confirm("Proceed?") is False
        assert "Aborted — nothing changed." in capsys.readouterr().err
        monkeypatch.setattr("sys.stdin", _Stdin("", tty=False))
        with pytest.raises(PromptUnavailable):
            update_cmd.confirm("Proceed?")
