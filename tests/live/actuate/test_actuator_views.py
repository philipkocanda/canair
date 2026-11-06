"""The rows of the IOControl and routines views, their closing table, and the terminal check."""

from __future__ import annotations

import asyncio
import io

import pytest

from canair.cli.profile_context import active
from canair.ecus.pids import build_iocontrol_index, build_routines_index
from canair.live.actuate.iocontrol import IOControlController, _log_file, run_iocontrol_view
from canair.live.actuate.iocontrol_render import iocontrol_table, row_cells, session_text
from canair.live.actuate.routines import (
    RoutinesController,
    routine_cells,
    routines_header,
    routines_table,
    run_routines_view,
)
from canair.live.actuate.routines import _log_file as _routines_log_file
from canair.render.console import console

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


def _io() -> IOControlController:
    info = build_iocontrol_index(DATA, include_discoveries=True)["IGPM"]
    return IOControlController(object(), "IGPM", info, pids_data=DATA, profile=active())  # type: ignore[arg-type]


def _routines() -> RoutinesController:
    info = build_routines_index(DATA)["IGPM"]
    return RoutinesController(object(), "IGPM", info, pids_data=DATA, profile=active())  # type: ignore[arg-type]


async def _never(_controller) -> None:
    raise AssertionError("the view must not open")


class TestIoRows:
    def test_a_verified_entry_is_a_green_check_and_its_did_is_cyan(self):
        mark, did, label, cmd, status, response = row_cells(_io(), "BC01")
        assert (mark.plain, str(mark.style)) == ("✓", "green")
        assert (did.plain, str(did.style)) == ("BC01", "cyan")
        assert label.plain == "Low beam"
        assert (cmd.plain, status.plain, response.plain) == ("·", "—", "")

    def test_an_unverified_entry_is_a_yellow_question_mark(self):
        mark = row_cells(_io(), "BC02")[0]
        assert (mark.plain, str(mark.style)) == ("?", "yellow")

    def test_on_is_green_and_an_nrc_is_yellow(self):
        c = _io()
        c.state["BC01"], c.status_bytes["BC01"] = "on", "00"
        c.state["BC02"], c.status_bytes["BC02"] = "error", "NRC 31 ROOR"
        assert str(row_cells(c, "BC01")[3].style) == "bold green"
        cmd, status = row_cells(c, "BC02")[3:5]
        assert (cmd.plain, str(cmd.style)) == ("ERR", "bold red")
        assert str(status.style) == "yellow"

    def test_the_header_names_the_ecu_session_and_view(self):
        text = session_text(_io()).plain
        assert "IGPM (0x770)" in text and "not started" in text and "curated (2)" in text

    def test_the_closing_table_has_a_row_per_did(self):
        assert iocontrol_table(_io()).row_count == 2


class TestRoutineRows:
    def test_a_verified_rid_is_a_green_check_and_its_rid_is_cyan(self):
        mark, rid, label, result, subfn, response = routine_cells(_routines(), "12A1")
        assert (mark.plain, str(mark.style)) == ("✓", "green")
        assert (rid.plain, str(rid.style)) == ("12A1", "cyan")
        assert (label.plain, result.plain, subfn.plain, response.plain) == (
            "Self test",
            "·",
            "",
            "",
        )

    def test_a_start_is_bold_yellow_and_an_nrc_is_yellow(self):
        c = _routines()
        c.state["12A1"], c.last_sf["12A1"] = "ok", 0x01
        c.state["12A2"], c.last_sf["12A2"] = "nrc", 0x03
        result, subfn = routine_cells(c, "12A1")[3:5]
        assert (result.plain, str(result.style)) == ("OK", "bold green")
        assert str(subfn.style) == "bold yellow"
        assert str(routine_cells(c, "12A2")[3].style) == "yellow"

    def test_the_header_names_the_ecu_and_counts_the_rids(self):
        text = routines_header(_routines()).plain
        assert "IGPM (0x770)" in text and "not started" in text and "2 RIDs" in text


class TestClosingTable:
    @pytest.mark.parametrize(
        "table", [lambda: iocontrol_table(_io()), lambda: routines_table(_routines())]
    )
    def test_a_pipe_gets_no_escape(self, table, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        out = io.StringIO()
        console(file=out).print(table())
        assert out.getvalue().strip()
        assert "\x1b" not in out.getvalue()


class TestTerminalCheck:
    def test_the_io_view_refuses_without_a_terminal(self, capsys):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(run_iocontrol_view(_io(), _never))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert err.startswith("error: the IOControl view needs a terminal")
        assert "--json" in err and "--did" in err

    def test_the_routines_view_refuses_without_a_terminal(self, capsys):
        with pytest.raises(SystemExit) as exc:
            asyncio.run(run_routines_view(_routines(), _never))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert err.startswith("error: the routines view needs a terminal")
        assert "--json" in err and "--rid" in err


def test_the_debug_logs_live_in_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _log_file() == tmp_path / "canair" / "logs" / "iocontrol-tui.log"
    assert _routines_log_file() == tmp_path / "canair" / "logs" / "routines-tui.log"
