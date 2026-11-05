"""The warning any other command prints while a session holds the device connection."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from canair.cli import build_parser, main
from canair.cli import lock_notice as notice
from canair.device.lock import HolderInfo, LockState

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _held(*, ours: bool = False) -> LockState:
    holder = HolderInfo(pid=4242, alive=True, cmdline="canair monitor BMS", age_s=720.0)
    return LockState(path=Path("/tmp/x"), held=True, pid=4242, ours=ours, holder=holder)


def _free() -> LockState:
    return LockState(path=Path("/tmp/x"), held=False)


@pytest.fixture
def probe(monkeypatch):
    def _set(state: LockState) -> None:
        monkeypatch.setattr(notice, "lock_state", lambda *a, **k: state)

    return _set


class TestWarning:
    def test_names_the_holder_and_the_way_out(self, probe, capsys, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        probe(_held())
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("warning: another canair session holds the device")
        assert "PID 4242" in captured.err
        assert "canair lock steal" in captured.err and "canair lock kill" in captured.err

    def test_is_yellow_with_cyan_commands_when_colour_is_on(self, probe, capsys, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.delenv("NO_COLOR", raising=False)
        probe(_held())
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        err = capsys.readouterr().err
        first, second = err.splitlines()
        assert first.startswith("\x1b[93mwarning:")
        assert "\x1b[96mcanair lock steal\x1b[0m" in second
        assert _ANSI.sub("", err).startswith("warning: another canair session")

    def test_is_plain_without_colour(self, probe, capsys, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        probe(_held())
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        assert "\x1b[" not in capsys.readouterr().err

    def test_silent_when_the_lock_is_free(self, probe, capsys):
        probe(_free())
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        assert capsys.readouterr().err == ""

    def test_silent_when_this_process_holds_it(self, probe, capsys):
        probe(_held(ours=True))
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        assert capsys.readouterr().err == ""

    def test_a_failing_probe_never_breaks_the_command(self, monkeypatch, capsys):
        def _boom(*a, **k):
            raise OSError("no /tmp")

        monkeypatch.setattr(notice, "lock_state", _boom)
        notice.warn_if_connection_held(argparse.Namespace(command="decode"))
        assert capsys.readouterr().err == ""


class TestWhichCommands:
    @pytest.mark.parametrize("command", ["lock", "status", "st", "completion"])
    def test_commands_that_report_the_lock_or_print_a_script_stay_quiet(
        self, probe, capsys, command
    ):
        probe(_held())
        notice.warn_if_connection_held(argparse.Namespace(command=command))
        assert capsys.readouterr().err == ""

    def test_a_command_that_takes_the_lock_is_left_to_its_own_refusal(self, probe, capsys):
        probe(_held())
        notice.warn_if_connection_held(argparse.Namespace(command="read", takes_device_lock=True))
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("argv", [["read", "BMS:2101"], ["monitor"], ["sniff"], ["io", "IGPM"]])
    def test_every_command_that_takes_the_lock_is_marked(self, argv):
        assert build_parser().parse_args(argv).takes_device_lock is True

    @pytest.mark.parametrize("argv", [["decode"], ["captures"], ["bus"], ["lock"]])
    def test_offline_commands_are_not_marked(self, argv):
        assert not getattr(build_parser().parse_args(argv), "takes_device_lock", False)


class TestEndToEnd:
    def test_an_offline_command_warns_before_it_runs(self, probe, capsys, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        probe(_held())
        main(["--profile", "hyundai-ioniq-ae-ev", "--variant", "28kwh", "bus", "--json"])
        captured = capsys.readouterr()
        assert "warning: another canair session holds the device connection" in captured.err
        assert "warning" not in captured.out  # stdout stays the result

    def test_the_lock_command_itself_does_not_repeat_it(self, probe, capsys, monkeypatch):
        probe(_held())
        monkeypatch.setattr("canair.device.lock.lock_state", lambda *a, **k: _held())
        main(["lock", "--json"])
        assert "warning:" not in capsys.readouterr().err
