"""The read pipeline's ``iocontrol`` step: what it switched on is released when it ends."""

from __future__ import annotations

import asyncio

from canair.cli.profile_context import active
from canair.live.read.multi import mode_multi
from tests.support.fakes import FakeTerminal, ok

ON, OFF = "2FBC0C03", "2FBC0C00"


def _pids_data() -> dict:
    return {
        "ecus": {
            "IGPM": {
                "tx_id": 0x770,
                "iocontrol": {"BC0C": {"label": "Rear defogger", "on": ON, "off": OFF}},
                "pids": {"22BC03": {"parameters": {"X": {"expression": "B4"}}}},
            }
        }
    }


def _run(steps: list[str]) -> FakeTerminal:
    terminal = FakeTerminal({ON: ok("6FBC0C03"), OFF: ok("6FBC0C00"), "22BC03": ok("62BC03AA")})
    asyncio.run(mode_multi(terminal, steps, _pids_data(), False, no_repl=True, profile=active()))
    return terminal


def test_an_actuator_left_on_is_released_when_the_pipeline_ends(capsys):
    terminal = _run(["iocontrol IGPM BC0C", "query IGPM:BC03"])
    assert terminal.requests == [ON, "22BC03", OFF]
    assert "releasing IGPM BC0C" in capsys.readouterr().err


def test_an_actuator_switched_off_is_not_released_again():
    terminal = _run(["iocontrol IGPM BC0C", "iocontrol IGPM BC0C --off"])
    assert terminal.requests == [ON, OFF]


def test_the_release_goes_out_before_the_sessions_close():
    terminal = _run(["iocontrol IGPM BC0C"])
    # The OFF comes before the 10 01 that ends the session it was switched on in.
    assert terminal.sent.index(OFF) < terminal.sent.index("1001")
