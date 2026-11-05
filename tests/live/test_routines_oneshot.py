"""``canair routines ECU --rid RID``: a failed lookup is an error; ``--json`` keeps stdout clean."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.live.routines_execute import mode_routines_execute
from canair.live.routines_list import mode_routines_list

DATA = {"ecus": {"BCM": {"tx_id": 0x745, "routines": {"12A1": {"label": "Self test"}}}}}


class FakeTerminal:
    def __init__(self):
        self.sent: list[str] = []

    async def set_header(self, tx_id: int) -> None:
        pass

    async def send_uds(self, cmd: str, timeout: float = 3.0, **kwargs) -> dict:
        self.sent.append(cmd)
        return {"ok": True, "hex": "7101" + cmd[4:]}

    async def enter_extended_session(self, mode: str = "03"):
        return True, None


def _run(ecu: str, rid: str, **kw):
    term = FakeTerminal()
    asyncio.run(mode_routines_execute(term, DATA, ecu, rid, **kw))  # type: ignore[arg-type]
    return term


def test_unknown_ecu_is_an_error_with_exit_1(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("NOPE", "12A1")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: no routines for ECU: NOPE" in captured.err
    assert "hint: ECUs with routines: BCM" in captured.err


def test_unknown_rid_is_an_error_with_exit_1(capsys):
    with pytest.raises(SystemExit) as exc:
        _run("BCM", "FFFF")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: unknown RID FFFF for BCM" in captured.err
    assert "hint: available RIDs: 12A1" in captured.err


def test_json_stdout_is_only_the_document(capsys):
    term = _run("BCM", "12A1", as_json=True)
    assert len(term.sent) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["rid"] == "12A1"
    assert "Command:" in captured.err and "Positive response" in captured.err


def test_without_json_the_report_is_on_stdout(capsys):
    _run("BCM", "12A1")
    captured = capsys.readouterr()
    assert "Positive response" in captured.out
    assert captured.err == ""


def test_the_list_of_an_unknown_ecu_is_an_error_on_stderr(capsys):
    assert mode_routines_list(DATA, "NOPE", as_json=True) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: no routines defined for ECU: NOPE" in captured.err
