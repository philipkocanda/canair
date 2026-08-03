"""Tests for canlib.modes._iocontrol_actuate: the IOControl actuator backend.

This is the only code in canair that **energises physical vehicle outputs**, and
it was almost entirely untested — replacing `release_all` (the "switch every
actuator you turned on back off before exiting" safety net) with a no-op passed
the whole suite. These tests pin the behaviour that matters when a real relay,
lamp, or motor is on the other end:

- ON/OFF send the *requested* command and set the state the renderer reads;
- `release_all` actually releases everything it turned on, is resilient to a
  failing release, and doesn't touch actuators it never enabled;
- `cleanup` releases before cancelling the TesterPresent keepalive.

Device-free: a fake terminal stands in for the UDS surface.
"""

from __future__ import annotations

import asyncio

import pytest

from canlib.modes._iocontrol_actuate import IOControlActuator

ON_CMD = "2FBC0103"
OFF_CMD = "2FBC0100"


class FakeTerminal:
    """Minimal stand-in for the terminal surface the actuator uses."""

    def __init__(self, *, fail_on: set[str] | None = None):
        self.sent: list[str] = []
        self.headers: list[int] = []
        self.fail_on = fail_on or set()

    async def set_header(self, tx_id: int) -> None:
        self.headers.append(tx_id)

    async def send_uds(self, cmd: str, timeout: float = 3.0) -> dict:
        self.sent.append(cmd)
        if cmd in self.fail_on:
            raise ConnectionError("bus dropped")
        return {"ok": True, "hex": "6F" + cmd[2:]}

    async def enter_extended_session(self):
        return True, None


class FakeTui:
    """The display/session state the actuator reads and updates via `self.t`."""

    def __init__(self, dids: list[str], *, terminal: FakeTerminal | None = None):
        self.dids = dids
        self.tx_id = 0x770
        self.terminal = terminal or FakeTerminal()
        self.cmds = {
            d: {"on": ON_CMD, "off": OFF_CMD, "label": f"actuator {d}", "session": False}
            for d in dids
        }
        self.state = dict.fromkeys(dids, "off")
        self.last_response: dict[str, str] = {}
        self.status_bytes: dict[str, str] = {}
        self.last_value: dict[str, str] = {}
        self._session_active = True
        self._tester_task = None
        self._busy = False
        self._status = ""
        self._quit = False
        self._status_polling = False


def _actuator(dids=("BC01", "BC02", "BC03"), **kw):
    tui = FakeTui(list(dids), **kw)
    return IOControlActuator(tui), tui


class TestSendOnOff:
    def test_on_sends_the_on_command_and_marks_state_on(self):
        act, tui = _actuator()
        asyncio.run(act.send_on("BC01"))
        assert tui.terminal.sent == [ON_CMD]
        assert tui.state["BC01"] == "on"

    def test_off_sends_the_off_command_and_marks_state_off(self):
        act, tui = _actuator()
        asyncio.run(act.send_off("BC01"))
        assert tui.terminal.sent == [OFF_CMD]
        assert tui.state["BC01"] == "off"

    def test_missing_command_is_refused_without_touching_the_bus(self):
        act, tui = _actuator()
        tui.cmds["BC01"]["on"] = ""
        asyncio.run(act.send_on("BC01"))
        assert tui.terminal.sent == []
        assert tui.state["BC01"] == "error"

    def test_busy_flag_is_cleared_even_when_the_send_raises(self):
        # A stuck `_busy` would freeze the status-poll loop for the rest of the
        # session (it only polls when not busy).
        act, tui = _actuator(terminal=FakeTerminal(fail_on={ON_CMD}))
        asyncio.run(act.send_on("BC01"))
        assert tui._busy is False
        assert tui.state["BC01"] == "error"


class TestReleaseAll:
    """The exit-time safety net: nothing may be left energised.

    Regression: `release_all` selects actuators via `t.state[d] == "on"`. Replacing
    its whole body with `return None` — i.e. leaving every actuator energised on
    exit — passed the entire test suite before these tests existed.
    """

    def test_releases_every_actuator_that_is_on(self):
        act, tui = _actuator()
        tui.state["BC01"] = "on"
        tui.state["BC03"] = "on"
        asyncio.run(act.release_all())
        # One OFF per energised actuator, and both are now off.
        assert tui.terminal.sent == [OFF_CMD, OFF_CMD]
        assert tui.state["BC01"] == "off"
        assert tui.state["BC03"] == "off"

    def test_does_not_touch_actuators_that_were_never_enabled(self):
        act, tui = _actuator()
        asyncio.run(act.release_all())
        assert tui.terminal.sent == []

    def test_ignores_actuators_left_in_an_error_state(self):
        # `error` is not `on` — we don't know it's energised, and blindly sending
        # OFF to a DID whose ON was refused isn't the safety net's job.
        act, tui = _actuator()
        tui.state["BC01"] = "error"
        asyncio.run(act.release_all())
        assert tui.terminal.sent == []

    def test_a_failing_release_does_not_abandon_the_remaining_actuators(self):
        """One dead actuator must not leave the others energised."""
        act, tui = _actuator(terminal=FakeTerminal())
        tui.state["BC01"] = "on"
        tui.state["BC02"] = "on"
        tui.state["BC03"] = "on"

        calls: list[str] = []
        original = act.send_off

        async def flaky(did: str) -> None:
            calls.append(did)
            if did == "BC01":
                raise ConnectionError("bus dropped mid-release")
            await original(did)

        act.send_off = flaky  # type: ignore[method-assign]
        asyncio.run(act.release_all())
        assert calls == ["BC01", "BC02", "BC03"], "release must continue past a failure"
        assert tui.state["BC02"] == "off"
        assert tui.state["BC03"] == "off"


class TestCleanup:
    def test_releases_actuators_before_cancelling_the_keepalive(self):
        act, tui = _actuator()
        tui.state["BC01"] = "on"

        order: list[str] = []
        original = act.release_all

        async def traced() -> None:
            order.append("release")
            await original()

        act.release_all = traced  # type: ignore[method-assign]

        async def run():
            async def keepalive():
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    order.append("keepalive-cancelled")
                    raise

            tui._tester_task = asyncio.create_task(keepalive())
            await asyncio.sleep(0)  # let it start
            await act.cleanup()

        asyncio.run(run())
        assert order == ["release", "keepalive-cancelled"]
        assert tui.state["BC01"] == "off"

    def test_cleanup_without_a_keepalive_task_is_safe(self):
        act, tui = _actuator()
        tui.state["BC01"] = "on"
        tui._tester_task = None
        asyncio.run(act.cleanup())  # must not raise
        assert tui.state["BC01"] == "off"


class TestEnsureSession:
    def test_opens_a_session_on_the_target_ecu(self):
        act, tui = _actuator()
        tui._session_active = False
        asyncio.run(act.ensure_session())
        assert tui.terminal.headers == [0x770]
        assert tui._session_active is True

    def test_is_a_noop_when_a_session_is_already_active(self):
        act, tui = _actuator()
        tui._session_active = True
        asyncio.run(act.ensure_session())
        assert tui.terminal.headers == []


class TestExtractStatusBytes:
    @pytest.mark.parametrize(
        "resp,expected",
        [
            # A positive 0x2F response is `6F {DID_HI} {DID_LO} [tail...]`; the
            # controlStatusRecord is everything after that 3-byte echo.
            ({"ok": True, "bytes": [0x6F, 0xBC, 0x01, 0xAA, 0xBB]}, "AA BB"),
            ({"ok": True, "bytes": [0x6F, 0xBC, 0x01]}, ""),  # positive, no tail
            ({"ok": False, "nrc": 0x31}, "NRC 31 ROOR"),  # negative response
            ({"ok": False}, "ERR"),  # transport error
        ],
    )
    def test_status_record_rendering(self, resp, expected):
        act, tui = _actuator()
        act.extract_status_bytes("BC01", resp)
        assert tui.status_bytes["BC01"] == expected
