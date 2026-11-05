"""The `canair scan range` wizard: its yes/no questions default to no."""

from __future__ import annotations

import argparse
import io

import pytest

from canair.cli.commands import scan


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        tx="7E4", service=None, range=None, session=False, wake=False, append=None
    )


def _run(monkeypatch, typed: list[str], final_answer: str) -> tuple[bool, argparse.Namespace, list]:
    """Run the wizard with ``typed`` for its value prompts and ``final_answer`` for the confirm."""
    asked: list[str] = []
    answers = iter(typed)

    def fake_input(prompt=""):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("sys.stdin", _Tty(final_answer))
    monkeypatch.setattr(scan, "plan_scan", lambda *a, **k: None)
    args = _args()
    return scan._run_wizard(args), args, asked


def test_enter_declines_the_run_prompt(monkeypatch, capsys):
    # service, range, session, wake: all left at their defaults.
    ok, args, asked = _run(monkeypatch, ["", "", "", ""], "\n")
    assert ok is False
    assert args.session is False and args.wake is False
    assert "Aborted — nothing changed." in capsys.readouterr().err
    assert all("[y]" not in q and "(y/n)" not in q for q in asked)


def test_yes_runs(monkeypatch):
    ok, args, _ = _run(monkeypatch, ["", "", "", ""], "y\n")
    assert ok is True and args.tx == "7E4"


def test_session_and_wake_answers_are_kept(monkeypatch):
    ok, args, asked = _run(monkeypatch, ["", "", "y", "yes"], "y\n")
    assert ok is True and args.session is True and args.wake is True
    assert any("[y/N]" in q for q in asked)


def test_a_declined_wizard_exits_1(monkeypatch):
    monkeypatch.setattr(scan, "_run_wizard", lambda args: False)
    monkeypatch.setattr("sys.stdin", _Tty(""))
    args = argparse.Namespace(interactive=True, tx=None)
    assert scan.run_range(args) == 1


def test_without_a_terminal_and_without_an_ecu_it_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert scan.run_range(argparse.Namespace(interactive=False, tx=None)) == 2
    assert capsys.readouterr().err.startswith("error: no ECU given")


def test_slots_with_a_range_is_a_usage_error(capsys):
    args = argparse.Namespace(service=None, range="01-FF", slots=3, tx="BMS")
    with pytest.raises(SystemExit) as raised:
        scan._resolve_plan(args)
    assert raised.value.code == 2
    assert capsys.readouterr().err.startswith("error: --slots")
