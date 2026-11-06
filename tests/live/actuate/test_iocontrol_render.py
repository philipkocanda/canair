"""The IOControl rows and the routines full-screen view: colours, the cursor, the terminal check."""

from __future__ import annotations

import asyncio

import pytest

from canair.cli.profile_context import active
from canair.ecus.pids import build_iocontrol_index, build_routines_index
from canair.live.actuate.iocontrol import IOControlController, _log_file, run_iocontrol_view
from canair.live.actuate.iocontrol_render import iocontrol_table, row_cells, session_text
from canair.live.actuate.routines import _log_file as _routines_log_file
from canair.live.actuate.routines import _RoutinesTUI

DATA = {
    "ecus": {
        "IGPM": {
            "tx_id": 0x770,
            "iocontrol": {
                "BC01": {
                    "label": "Low beam",
                    "on": "2FBC0103",
                    "off": "2FBC0100",
                    "verified": True,
                },
                "BC02": {"label": "High beam", "on": "2FBC0203", "off": "2FBC0200"},
            },
            "routines": {
                "12A1": {"label": "Self test", "verified": True},
                "12A2": {"label": "Calibrate"},
            },
        }
    }
}


def _io_tui() -> IOControlController:
    info = build_iocontrol_index(DATA, include_discoveries=True)["IGPM"]
    return IOControlController(object(), "IGPM", info, pids_data=DATA, profile=active())  # type: ignore[arg-type]


def _routines_tui() -> _RoutinesTUI:
    info = build_routines_index(DATA)["IGPM"]
    return _RoutinesTUI(object(), "IGPM", info, pids_data=DATA, profile=active())  # type: ignore[arg-type]


@pytest.fixture
def tui():
    return _routines_tui()


class TestIoRows:
    def test_a_verified_entry_is_a_green_check_and_its_did_is_cyan(self):
        mark, did, label, cmd, status, response = row_cells(_io_tui(), "BC01")
        assert (mark.plain, str(mark.style)) == ("✓", "green")
        assert (did.plain, str(did.style)) == ("BC01", "cyan")
        assert label.plain == "Low beam"
        assert (cmd.plain, status.plain, response.plain) == ("·", "—", "")

    def test_an_unverified_entry_is_a_yellow_question_mark(self):
        mark = row_cells(_io_tui(), "BC02")[0]
        assert (mark.plain, str(mark.style)) == ("?", "yellow")

    def test_on_is_green_and_an_nrc_is_yellow(self):
        c = _io_tui()
        c.state["BC01"], c.status_bytes["BC01"] = "on", "00"
        c.state["BC02"], c.status_bytes["BC02"] = "error", "NRC 31 ROOR"
        assert str(row_cells(c, "BC01")[3].style) == "bold green"
        cmd, status = row_cells(c, "BC02")[3:5]
        assert (cmd.plain, str(cmd.style)) == ("ERR", "bold red")
        assert str(status.style) == "yellow"

    def test_the_header_names_the_ecu_session_and_view(self):
        text = session_text(_io_tui()).plain
        assert "IGPM (0x770)" in text and "not started" in text and "curated (2)" in text

    def test_the_closing_table_has_a_row_per_did(self):
        assert iocontrol_table(_io_tui()).row_count == 2


class TestColourGating:
    def test_no_color_emits_no_escape(self, tui, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        assert "\x1b" not in tui._render()

    def test_a_pipe_emits_no_escape(self, tui, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert "\x1b" not in tui._render()

    def test_forced_colour_uses_the_bright_palette_and_no_blink(self, tui, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        frame = tui._render()
        assert "\x1b[96m" in frame  # cyan names
        assert "\x1b[5m" not in frame  # no blink
        assert not any(f"\x1b[{n}m" in frame for n in (31, 32, 33, 35, 36))

    def test_the_cursor_is_a_filled_triangle(self, tui, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        frame = tui._render()
        assert "▶" in frame and "▸" not in frame

    def test_the_text_input_has_no_blink_in_colour(self, tui, monkeypatch):
        monkeypatch.setenv("FORCE_COLOR", "1")
        tui._edit_input = ("label", "x")
        frame = tui._render()
        assert "▏" in frame and "\x1b[5m" not in frame


class TestTerminalCheck:
    def test_the_io_view_refuses_without_a_terminal(self, capsys):
        async def never(_controller) -> None:
            raise AssertionError("the view must not open")

        with pytest.raises(SystemExit) as exc:
            asyncio.run(run_iocontrol_view(_io_tui(), never))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert err.startswith("error: the IOControl view needs a terminal")
        assert "--json" in err and "--did" in err

    def test_the_routines_view_refuses_without_a_terminal(self, capsys):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(_routines_tui().run())
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert err.startswith("error: the routines view needs a terminal")
        assert "--json" in err and "--rid" in err


def test_the_debug_logs_live_in_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _log_file() == tmp_path / "canair" / "logs" / "iocontrol-tui.log"
    assert _routines_log_file() == tmp_path / "canair" / "logs" / "routines-tui.log"
