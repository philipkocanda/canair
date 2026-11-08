"""RoutineBank: one ECU's routine requests, sent in a session its owner opens."""

from __future__ import annotations

import asyncio
import contextlib

from canair.live.actuate.routine_bank import RoutineBank
from canair.safety.blocklist import SessionPolicy
from canair.uds.subfunctions import RoutineControlType


class Terminal:
    def __init__(self, reply: dict | None = None):
        self.calls: list[tuple[str, object]] = []
        self.in_transaction = False
        self.session_policy = SessionPolicy()
        self.reply = reply

    @contextlib.asynccontextmanager
    async def transaction(self):
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    async def set_header(self, tx_id: int) -> None:
        self.calls.append(("header", (tx_id, self.in_transaction)))

    async def send_uds(self, cmd: str, **kwargs) -> dict:
        self.calls.append(("send", (cmd, self.in_transaction)))
        if self.reply is not None:
            return self.reply
        hexs = f"71{cmd[2:]}"
        return {"ok": True, "hex": hexs, "bytes": list(bytes.fromhex(hexs))}


class Sessions:
    def __init__(self, ok: bool = True):
        self.asked: list[tuple[int, str | None]] = []
        self.ok = ok

    async def ensure(self, tx_id: int, mode: str | None) -> bool:
        self.asked.append((tx_id, mode))
        return self.ok


def _bank(terminal: Terminal, sessions: Sessions | None = None):
    reports: list[tuple[str, str]] = []
    bank = RoutineBank(
        lambda: terminal,  # type: ignore[arg-type, return-value]
        0x7A0,
        {"12A1": {"label": "Test"}},
        sessions or Sessions(),
        lambda text, level: reports.append((text, level)),
    )
    return bank, reports


def test_a_request_opens_the_session_then_sends_in_one_transaction():
    terminal, sessions = Terminal(), Sessions()
    bank, _ = _bank(terminal, sessions)
    asyncio.run(bank.send("12A1", RoutineControlType.REQUEST_ROUTINE_RESULTS))
    assert sessions.asked == [(0x7A0, None)]
    assert terminal.calls == [("header", (0x7A0, True)), ("send", ("310312A1", True))]
    assert bank.state["12A1"] == "ok"
    assert bank.last_sf["12A1"] == RoutineControlType.REQUEST_ROUTINE_RESULTS
    assert bank.busy is False


def test_an_nrc_is_named():
    reply = {"ok": False, "nrc": 0x22, "nrc_desc": "conditionsNotCorrect", "hex": "7F3122"}
    bank, _ = _bank(Terminal(reply))
    asyncio.run(bank.send("12A1", RoutineControlType.START_ROUTINE))
    assert bank.state["12A1"] == "nrc"
    assert bank.last_response["12A1"].startswith("NRC 0x22 ")


def test_a_reload_keeps_known_state():
    bank, _ = _bank(Terminal())
    asyncio.run(bank.send("12A1", RoutineControlType.REQUEST_ROUTINE_RESULTS))
    bank.set_routines({"12A1": {"label": "Renamed"}, "12A2": {}})
    assert bank.state == {"12A1": "ok", "12A2": None}
