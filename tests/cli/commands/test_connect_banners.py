"""The connect banners are status lines: stderr, so `--json` stdout stays one document."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from canair.cli.live import connect
from canair.transport.config import ConnectionOptions


class _FakeElm:
    adapter_timeout_cmd = None

    def __init__(self, *a, **k):
        pass

    async def connect(self):
        pass

    async def init_elm(self, init):
        pass

    async def send_command(self, cmd):
        pass

    async def close(self):
        pass


def test_connecting_lines_go_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(connect, "WiCANTerminal", _FakeElm)
    transport = SimpleNamespace(host="192.0.2.9", type="wican-ws", port=None)
    pids = {"init": "ATSP6;", "response_timeout_ms": 400}
    asyncio.run(connect.connect_elm_terminal(transport, pids, ConnectionOptions()))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Connecting to WiCAN at 192.0.2.9…" in captured.err
    assert "Initializing ELM327…" in captured.err
    assert "ELM327 timeout: ATST" in captured.err
    assert captured.err.rstrip().endswith("Ready.")
