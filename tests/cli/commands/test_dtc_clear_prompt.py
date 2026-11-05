"""`canair dtc --clear` asks first: a decline exits 1, no terminal and no --yes exits 2."""

from __future__ import annotations

import argparse
import asyncio
import io

import pytest

from canair.cli.live.handlers.diagnostics import handle_dtc
from canair.core.prompt import PromptUnavailable


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _args(**kw) -> argparse.Namespace:
    base = {
        "dtc": "7E4",
        "dtc_all": False,
        "clear": True,
        "group": "FFFFFF",
        "protocol": "auto",
        "session": False,
        "wake": False,
        "json": False,
        "verbose": False,
        "label": None,
        "yes": False,
    }
    return argparse.Namespace(**(base | kw))


@pytest.fixture
def cleared(monkeypatch):
    calls: list[dict] = []

    async def fake_clear(terminal, tx_id, **kw):
        calls.append({"tx_id": tx_id, **kw})

    monkeypatch.setattr("canair.live.dtc.mode_dtc_clear", fake_clear)
    return calls


def _run(args):
    asyncio.run(handle_dtc(args, None, {}, "h"))  # type: ignore[arg-type]


def test_a_decline_exits_1_and_clears_nothing(cleared, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    with pytest.raises(SystemExit) as raised:
        _run(_args())
    assert raised.value.code == 1
    assert cleared == []
    err = capsys.readouterr().err
    assert "warning: clearing DTCs erases the stored fault memory" in err
    assert "Aborted — nothing changed." in err


def test_without_a_terminal_the_prompt_is_unavailable_not_skipped(cleared, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(PromptUnavailable, match="--yes"):
        _run(_args())
    assert cleared == []


def test_yes_clears_without_asking(cleared, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    _run(_args(yes=True))
    assert [c["tx_id"] for c in cleared] == [0x7E4]


def test_json_does_not_mean_yes(cleared, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(PromptUnavailable):
        _run(_args(json=True))
    assert cleared == []


def test_an_answer_of_yes_clears(cleared, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    _run(_args())
    assert [c["tx_id"] for c in cleared] == [0x7E4]


def test_a_bad_group_is_a_usage_error(cleared, capsys):
    with pytest.raises(SystemExit) as raised:
        _run(_args(group="ZZ", yes=True))
    assert raised.value.code == 2
    assert capsys.readouterr().err.startswith("error: --group must be hex")
