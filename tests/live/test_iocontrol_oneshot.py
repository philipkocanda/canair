"""``canair io ECU --did DID``: lookups that fail are errors, and ``--json`` keeps stdout clean."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.live.iocontrol_execute import mode_iocontrol_execute

DATA = {
    "ecus": {
        "IGPM": {
            "tx_id": 0x770,
            "iocontrol": {
                "BC01": {"label": "Low beam", "on": "2FBC0103", "off": "2FBC0100"},
                "BC02": {"label": "No ON", "on": "", "off": "2FBC0200"},
            },
        }
    }
}


class FakeTerminal:
    def __init__(self):
        self.sent: list[str] = []

    async def set_header(self, tx_id: int) -> None:
        pass

    async def send_uds(self, cmd: str, timeout: float = 3.0, **kwargs) -> dict:
        self.sent.append(cmd)
        reply = f"{int(cmd[:2], 16) + 0x40:02X}{cmd[2:]}"
        return {"ok": True, "hex": reply, "bytes": list(bytes.fromhex(reply))}

    async def enter_extended_session(self, mode: str = "03"):
        return True, None


def _run(ecu: str, did: str, **kw):
    term = FakeTerminal()
    asyncio.run(mode_iocontrol_execute(term, DATA, ecu, did, **kw))  # type: ignore[arg-type]
    return term


class TestLookupFailures:
    def test_unknown_ecu_is_an_error_with_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _run("NOPE", "BC01", yes=True)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: no IOControl DIDs for ECU: NOPE" in captured.err
        assert "hint: ECUs with IOControl: IGPM" in captured.err

    def test_unknown_did_is_an_error_with_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _run("IGPM", "FFFF", yes=True)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: unknown DID FFFF for IGPM" in captured.err
        assert "hint: available DIDs: BC01, BC02" in captured.err

    def test_a_missing_command_is_an_error_with_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _run("IGPM", "BC02", yes=True)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: no ON command defined for IGPM BC02 (No ON)" in captured.err


class TestJson:
    def test_stdout_is_only_the_json_document(self, capsys):
        term = _run("IGPM", "BC01", yes=True, as_json=True)
        assert term.sent[0] == "2FBC0103"
        captured = capsys.readouterr()
        doc = json.loads(captured.out)
        assert doc["ok"] is True and doc["command"] == "2FBC0103"
        assert "Command: 2FBC0103" in captured.err
        assert "Positive response" in captured.err

    def test_without_json_the_report_is_on_stdout(self, capsys):
        _run("IGPM", "BC01", yes=True)
        captured = capsys.readouterr()
        assert "Command: 2FBC0103" in captured.out
        assert "Positive response" in captured.out
        assert captured.err == ""

    def test_no_escape_reaches_a_pipe(self, capsys, monkeypatch):
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        _run("IGPM", "BC01", yes=True, as_json=True)
        captured = capsys.readouterr()
        assert "\x1b" not in captured.out + captured.err
