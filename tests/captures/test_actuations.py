"""Capture actuations: what a monitor recording switched on the car, and when.

Covers the pure leaf (``canair.captures.actuations``), the journal round trip into a capture
file, and the monitor's recording of its actuator pane's requests.
Plan: ``plans/2026-11-07-monitor-actuator-pane.md``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from jsonschema import Draft202012Validator

from canair.captures import io as capture_io
from canair.captures.actuations import (
    actuation_record,
    actuations_by_date,
    describe,
    session_actuations,
)
from canair.captures.journal import CaptureJournal
from canair.cli.profile_context import active
from canair.ecus.pids import build_ecu_index
from canair.validation._common import CAPTURES_SCHEMA_FILE
from tests.support.fakes import FakeTerminal, ok
from tests.support.monitor_controller import make_controller

DAY = "2026-11-07"
T0 = datetime(2026, 11, 7, 14, 0, 0, 120000)


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(CAPTURES_SCHEMA_FILE.read_text()))


class TestLeaf:
    def test_a_record_keeps_only_what_its_outcome_explains(self):
        rec = actuation_record(
            T0,
            "0x778",
            "iocontrol",
            "bc0c",
            "on",
            request="2fbc0c03",
            outcome="nrc",
            nrc=0x22,
            response="7F2F22",
        )
        assert rec == {
            "type": "actuation",
            "date": DAY,
            "at": "14:00:00.120",
            "rx": "0x778",
            "kind": "iocontrol",
            "id": "BC0C",
            "action": "on",
            "request": "2FBC0C03",
            "outcome": "nrc",
            "nrc": "0x22",
        }

    def test_records_become_per_day_lists_and_bad_ones_are_dropped(self):
        good = actuation_record(T0, "0x778", "iocontrol", "BC0C", "on", outcome="ok")
        later = actuation_record(
            datetime(2026, 11, 8, 0, 0, 1), "0x7A8", "routine", "12A1", "start", outcome="ok"
        )
        bad = {**good, "rx": "IGPM"}
        by_day = actuations_by_date([later, bad, good], DAY)
        assert [e["id"] for e in by_day[DAY]] == ["BC0C"]
        assert [e["id"] for e in by_day["2026-11-08"]] == ["12A1"]

    def test_a_held_entry_carries_no_request(self):
        rec = actuation_record(T0, "0x778", "iocontrol", "BC0C", "held", request="2FBC0C03")
        (entry,) = actuations_by_date([rec], DAY)[DAY]
        assert "request" in rec and "request" not in entry

    def test_the_reader_is_tolerant(self):
        assert session_actuations({"actuations": "x"}) == []
        assert session_actuations({"actuations": [1, {"kind": "x"}]}) == []

    def test_describe(self):
        assert describe({"kind": "iocontrol", "id": "BC0C", "action": "on", "outcome": "ok"}) == (
            "iocontrol BC0C on (ok)"
        )
        assert (
            describe(
                {
                    "kind": "routine",
                    "id": "12A1",
                    "action": "start",
                    "outcome": "nrc",
                    "nrc": "0x22",
                }
            )
            == "routine 12A1 start (NRC 0x22)"
        )
        assert describe({"kind": "iocontrol", "id": "BC0C", "action": "held"}) == (
            "iocontrol BC0C held"
        )


class TestJournalRoundTrip:
    def test_a_reconciled_session_carries_its_actuations_and_validates(self, tmp_path):
        j = CaptureJournal.open(tmp_path, label="defogger", transport="slcan-tcp", profile=active())
        j.append("0x778", "22BC03", "62BC0300", "14:00:00.000", DAY)
        j.append_actuation(
            actuation_record(
                T0,
                "0x778",
                "iocontrol",
                "BC0C",
                "on",
                request="2FBC0C03",
                outcome="ok",
                response="6FBC0C03",
            )
        )
        j.append("0x778", "22BC03", "62BC0301", "14:00:01.000", DAY)
        written = j.reconcile()
        assert written is not None
        data = capture_io.load_capture_file(written)
        session = data["sessions"][0]
        assert session["actuations"] == [
            {
                "at": "14:00:00.120",
                "rx": "0x778",
                "kind": "iocontrol",
                "id": "BC0C",
                "action": "on",
                "request": "2FBC0C03",
                "outcome": "ok",
                "response": "6FBC0C03",
            }
        ]
        assert list(session)[-1] == "captures"
        assert list(_validator().iter_errors(data)) == []
        assert '"action": "on"' in written.read_text()  # one entry per line


PIDS = {
    "ecus": {
        "IGPM": {
            "tx_id": 0x770,
            "identity": {"id_protocol": "UDS"},
            "iocontrol": {
                "BC0C": {
                    "label": "Rear defogger",
                    "on": "2FBC0C03",
                    "off": "2FBC0C00",
                    "session": False,
                }
            },
            "pids": {"22BC03": {"parameters": {}}},
        }
    }
}


class TestMonitorRecording:
    def _controller(self, tmp_path):
        terminal = FakeTerminal({"2FBC0C03": ok("6FBC0C03"), "2FBC0C00": ok("6FBC0C00")})
        steps = [{"type": "query", "ecu": "IGPM", "pids": ["BC03"]}]
        c = make_controller(terminal, steps, PIDS, actuate=("IGPM",))
        c._ecu_index = build_ecu_index(PIDS)
        c.captures_dir = tmp_path
        c.recorder.journal = CaptureJournal.open(tmp_path, label="toggle", profile=active())
        return c

    def _records(self, c) -> list[dict]:
        lines = c.recorder.journal.path.read_text().splitlines()
        return [r for r in map(json.loads, lines) if r.get("type") == "actuation"]

    def test_each_request_is_journaled(self, tmp_path):
        c = self._controller(tmp_path)
        act = c.actuation.actuator("IGPM/iocontrol/BC0C")

        async def run() -> None:
            await c.actuation.submit_toggle(act)
            await c.actuation.submit_off(act)
            await c.foreground.stop()

        asyncio.run(run())
        records = self._records(c)
        assert [(r["action"], r["request"], r["outcome"]) for r in records] == [
            ("on", "2FBC0C03", "ok"),
            ("off", "2FBC0C00", "ok"),
        ]
        assert {r["rx"] for r in records} == {"0x778"}

    def test_a_new_segment_starts_with_what_is_still_on(self, tmp_path):
        c = self._controller(tmp_path)
        act = c.actuation.actuator("IGPM/iocontrol/BC0C")

        async def run() -> None:
            await c.actuation.submit_toggle(act)
            await c.foreground.stop()

        asyncio.run(run())
        c.recorder.new_segment("second")
        records = self._records(c)
        assert [(r["action"], r["id"]) for r in records] == [("held", "BC0C")]


def _stored_session() -> dict:
    return {
        "date": DAY,
        "label": "defogger",
        "actuations": [
            {
                "at": "14:00:05.000",
                "rx": "0x778",
                "kind": "iocontrol",
                "id": "BC0C",
                "action": "on",
                "request": "2FBC0C03",
                "outcome": "ok",
                "response": "6FBC0C03",
            },
            {
                "at": "14:00:20.000",
                "rx": "0x778",
                "kind": "iocontrol",
                "id": "BC0C",
                "action": "off",
                "request": "2FBC0C00",
                "outcome": "ok",
                "response": "6FBC0C00",
            },
        ],
        "captures": [
            {"rx": "0x778", "pid": "22BC03", "payload": "62BC0300", "time": "14:00:00.000"},
            {"rx": "0x778", "pid": "22BC03", "payload": "62BC0301", "time": "14:00:10.000"},
            {"rx": "0x778", "pid": "22BC03", "payload": "62BC0300", "time": "14:00:30.000"},
        ],
    }


def _entries(tmp_path):
    from canair.captures.store import flatten_session

    return flatten_session(
        tmp_path / f"{DAY}.json", 0, _stored_session(), rx_index={0x778: "IGPM"}, profile=active()
    )


class TestReaders:
    def test_the_sessions_view_says_what_was_switched(self, tmp_path, capsys):
        from canair.cli.commands.captures.sessions import cmd_sessions

        cmd_sessions(_entries(tmp_path))
        assert "actuated: 2 requests · IGPM BC0C" in capsys.readouterr().out
        cmd_sessions(_entries(tmp_path), as_json=True)
        doc = json.loads(capsys.readouterr().out)
        assert [a["action"] for a in doc[0]["actuations"]] == ["on", "off"]

    def test_captures_step_names_what_was_switched_between_frames(self, tmp_path):
        from canair.tui.apps.captures_step.gaps import actuations_before, between_lines

        entries = _entries(tmp_path)
        at = [datetime(2026, 11, 7, 14, 0, s) for s in (0, 10, 30)]
        assert actuations_before([entries[0]], at[0], [], None) == []
        (on,) = actuations_before([entries[1]], at[1], [entries[0]], at[0])
        assert on["action"] == "on"
        lines = between_lines([], [on], {"0X778": "IGPM"})
        assert lines == {"gaps": "", "actuated": "IGPM iocontrol BC0C on (ok) at 14:00:05"}

    def test_investigate_events_puts_them_among_the_edges(self, tmp_path, capsys):
        from canair.cli.commands.investigate import render

        class LP:
            captures = _entries(tmp_path)
            n_no_time = 0

        args = type("A", (), {"field": None, "json": False, "bits": False, "notation": None})()
        render.print_events("IGPM", "22BC03", LP(), {}, {}, args, {})
        lines = [line.strip() for line in capsys.readouterr().out.splitlines() if line.strip()]
        timeline = [line for line in lines if line[:2] == "14"]
        assert [line.split()[1] for line in timeline] == ["switched", "B4", "switched", "B4"]
        assert "IGPM iocontrol BC0C on (ok)" in timeline[0]
