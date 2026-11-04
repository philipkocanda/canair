"""``canair read`` of named signals (:mod:`canair.live.param`)."""

from __future__ import annotations

import asyncio
import json

import pytest

from canair.live.param import mode_param
from tests.support.fakes import FakeTerminal, ok


def _pids_data() -> dict:
    def ecu(tx_id: int, pid: str, name: str) -> dict:
        return {
            "tx_id": tx_id,
            "pids": {pid: {"parameters": {name: {"expression": "B4", "unit": ""}}}},
        }

    return {
        "ecus": {
            "IGPM": ecu(0x770, "22BC03", "DOOR_STATE"),
            "BCM": ecu(0x7A0, "22B001", "LAMP_STATE"),
        }
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _SlowSessionTerminal(FakeTerminal):
    """Opening the second ECU's session takes long enough for the first's to go stale."""

    def __init__(self, clock: _Clock):
        super().__init__(
            {
                "1003": ok("5003"),
                "3E00": ok("7E00"),
                "22BC03": ok("62BC03AABB"),
                "22B001": ok("62B001CCDD"),
            }
        )
        self.clock = clock

    async def send_uds(self, req, *args, **kwargs):
        if req == "1003" and self.header == 0x7A0:
            self.clock.now += 2.0
        return await super().send_uds(req, *args, **kwargs)


def _keepalive_headers(terminal: FakeTerminal) -> list[int | None]:
    header = None
    sent_to = []
    for call, arg in terminal.calls:
        if call == "set_header":
            header = arg
        elif call == "send_uds" and arg == "3E00":
            sent_to.append(header)
    return sent_to


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr("canair.transport.session_manager.time.monotonic", clock)
    return clock


def test_every_ecus_session_is_kept_alive_on_its_own_header(clock, capsys):
    terminal = _SlowSessionTerminal(clock)
    names = ["DOOR_STATE", "LAMP_STATE"]
    asyncio.run(mode_param(terminal, _pids_data(), names, False, True, session=True))
    # IGPM's session went stale while BCM's was opened: its keepalive goes to IGPM.
    assert _keepalive_headers(terminal) == [0x770]
    assert [r["value"] for r in json.loads(capsys.readouterr().out)] == [0xAA, 0xCC]


def test_one_session_per_ecu_and_each_read_validated(clock, capsys):
    terminal = _SlowSessionTerminal(clock)
    asyncio.run(mode_param(terminal, _pids_data(), ["DOOR_STATE"], False, True, session=True))
    assert terminal.sent.count("1003") == 1
    read = terminal.uds_kwargs[terminal.sent.index("22BC03")]
    assert read["expected_sid"] == 0x22 and read["expected_echo"] == bytes.fromhex("BC03")


def test_without_session_nothing_is_opened(clock, capsys):
    terminal = _SlowSessionTerminal(clock)
    names = ["DOOR_STATE", "LAMP_STATE"]
    asyncio.run(mode_param(terminal, _pids_data(), names, False, True))
    assert terminal.sent == ["22BC03", "22B001"]
