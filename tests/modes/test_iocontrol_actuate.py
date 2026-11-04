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
        self.sessions: list[str] = []
        self.kwargs: list[dict] = []
        self.fail_on = fail_on or set()

    async def set_header(self, tx_id: int) -> None:
        self.headers.append(tx_id)

    async def send_uds(self, cmd: str, timeout: float = 3.0, **kwargs) -> dict:
        self.sent.append(cmd)
        self.kwargs.append(kwargs)
        if cmd in self.fail_on:
            raise ConnectionError("bus dropped")
        reply = f"{int(cmd[:2], 16) + 0x40:02X}{cmd[2:]}"
        return {"ok": True, "hex": reply, "bytes": list(bytes.fromhex(reply))}

    async def enter_extended_session(self, mode: str = "03"):
        self.sessions.append(mode)
        return True, None


class FakeTui:
    """The display/session state the actuator reads and updates via `self.t`."""

    def __init__(self, dids: list[str], *, terminal: FakeTerminal | None = None):
        self.dids = dids
        self.tx_id = 0x770
        self.terminal = terminal or FakeTerminal()
        self.cmds = {
            d: {
                "on": ON_CMD,
                "off": OFF_CMD,
                "release": OFF_CMD,
                "label": f"actuator {d}",
                "session": False,
                "session_mode": "03",
            }
            for d in dids
        }
        self.state = dict.fromkeys(dids, "off")
        self.last_response: dict[str, str] = {}
        self.status_bytes: dict[str, str] = {}
        self.last_value: dict[str, str] = {}
        self._session_active = True
        self._session_mode: str | None = "03"
        self._tester_task = None
        self._busy = False
        self._status = ""
        self._quit = False
        self._status_polling = False
        self.protocol = "uds"


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
        original = act.send_release

        async def flaky(did: str) -> None:
            calls.append(did)
            if did == "BC01":
                raise ConnectionError("bus dropped mid-release")
            await original(did)

        act.send_release = flaky  # type: ignore[method-assign]
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

    def test_opens_the_session_mode_the_actuator_declares(self):
        act, tui = _actuator()
        tui._session_active, tui._session_mode = False, None
        tui.cmds["BC01"].update(session=True, session_mode="C0")
        asyncio.run(act.send_on("BC01"))
        assert tui.terminal.sessions == ["C0"]
        assert tui._session_mode == "C0"
        assert tui.terminal.sent == [ON_CMD]

    def test_reuses_an_open_session_of_the_same_mode(self):
        act, tui = _actuator()
        asyncio.run(act.ensure_session("03"))
        assert tui.terminal.sessions == []

    def test_switches_session_and_stops_the_old_keepalive(self):
        act, tui = _actuator()

        async def run():
            old = asyncio.create_task(asyncio.sleep(3600))
            tui._tester_task = old
            await act.ensure_session("C0")
            return old

        old = asyncio.run(run())
        assert old.cancelled()
        assert tui.terminal.sessions == ["C0"]
        assert tui._session_mode == "C0"


class TestExtractStatusBytes:
    @pytest.mark.parametrize(
        "resp,expected",
        [
            # A positive 0x2F response is `6F {DID_HI} {DID_LO} [tail...]`; the
            # controlStatusRecord is everything after that 3-byte echo.
            ({"ok": True, "bytes": [0x6F, 0xBC, 0x01, 0xAA, 0xBB]}, "AA BB"),
            ({"ok": True, "bytes": [0x6F, 0xBC, 0x01]}, ""),  # positive, no tail
            # KWP2000 0x30 echoes a 1-byte local identifier: `70 {LID} [tail...]`.
            ({"ok": True, "bytes": [0x70, 0x01, 0x11, 0x22]}, "11 22"),
            ({"ok": False, "nrc": 0x31}, "NRC 31 ROOR"),  # negative response
            ({"ok": False}, "ERR"),  # transport error
        ],
    )
    def test_status_record_rendering(self, resp, expected):
        act, tui = _actuator()
        act.extract_status_bytes("BC01", resp)
        assert tui.status_bytes["BC01"] == expected


class TestSingleCommand:
    """`canair io ECU --did X` opens the session mode the entry declares."""

    def test_execute_opens_the_declared_session_mode(self, capsys):
        from canlib.modes.iocontrol import mode_iocontrol_execute

        pids_data = {
            "ecus": {
                "BCM": {
                    "tx_id": 0x745,
                    "iocontrol": {
                        "6900": {
                            "label": "Mirrors",
                            "on": "30690001",
                            "off": "30690000",
                            "release": "30690000",
                            "session_mode": "C0",
                            "hold": False,
                        }
                    },
                }
            }
        }
        term = FakeTerminal()
        asyncio.run(mode_iocontrol_execute(term, pids_data, "BCM", "6900"))  # type: ignore[arg-type]
        assert term.sessions == ["C0"]
        assert term.sent == ["30690001"]

    def test_a_kwp_on_without_a_release_is_refused_before_anything_is_sent(self, capsys):
        from canlib.modes.iocontrol import mode_iocontrol_execute

        term = FakeTerminal()
        with pytest.raises(SystemExit) as exc:
            asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01"))  # type: ignore[arg-type]
        assert exc.value.code == 2
        assert term.sent == [] and term.sessions == []
        assert "no release declared" in capsys.readouterr().err

    def test_off_is_still_sent_without_a_release(self):
        from canlib.modes.iocontrol import mode_iocontrol_execute

        term = FakeTerminal()
        asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01", off=True))  # type: ignore[arg-type]
        assert term.sent == ["300111"]

    def test_a_held_kwp_on_is_released_with_its_declared_release(self):
        from canlib.modes.iocontrol import mode_iocontrol_execute

        data = _with_release(OBC_DATA, "3001AA")

        async def run():
            task = asyncio.ensure_future(
                mode_iocontrol_execute(term, data, "OBC", "01")  # type: ignore[arg-type]
            )
            while "300100" not in term.sent:
                await asyncio.sleep(0)
            task.cancel()  # Ctrl+C while holding
            await asyncio.gather(task, return_exceptions=True)

        term = HoldingTerminal()
        asyncio.run(run())
        assert term.sent == ["300100", "3001AA"]
        assert term.kwargs[-1] == {"expected_sid": 0x30, "expected_echo": b"\x01"}


class FailingSessionTerminal(FakeTerminal):
    async def enter_extended_session(self, mode: str = "03"):
        self.sessions.append(mode)
        raise ValueError("BLOCKED: refused")


class TestEnsureSessionFailure:
    """Regression: a failed re-entry left the header claiming the old session."""

    def test_a_raising_session_open_forgets_the_old_session(self):
        act, tui = _actuator(terminal=FailingSessionTerminal())

        async def run():
            tui._tester_task = asyncio.create_task(asyncio.sleep(3600))
            with pytest.raises(ValueError):
                await act.ensure_session("C0")

        asyncio.run(run())
        assert tui._session_active is False
        assert tui._session_mode is None
        assert tui._tester_task is None

    def test_a_refused_session_is_unconfirmed_not_active(self):
        act, tui = _actuator(terminal=FakeTerminal())
        tui.terminal.enter_extended_session = _refusing  # type: ignore[method-assign]
        tui._session_active = False
        asyncio.run(act.ensure_session("C0"))
        assert tui._session_active is False
        assert tui._session_mode == "C0"


async def _refusing(mode: str = "03"):
    return False, None


class TestSessionHeader:
    def _header(self, active, mode):
        from canlib.modes._iocontrol_render import render_iocontrol

        _, tui = _actuator()
        tui._session_active, tui._session_mode = active, mode
        tui.ecu_key, tui.cursor, tui._scroll_top = "IGPM", 0, 0
        tui.all_cmds, tui.view_mode = tui.cmds, "curated"
        tui._hex_input = tui._edit_input = None
        tui.status_bytes = dict.fromkeys(tui.dids)
        for c in tui.cmds.values():
            c["verified"] = False
        return render_iocontrol(tui).splitlines()[1]  # type: ignore[arg-type]

    def test_active(self):
        assert "active (10 C0)" in self._header(True, "C0")

    def test_unconfirmed(self):
        assert "unconfirmed (10 03)" in self._header(False, "03")

    def test_not_started(self):
        assert "not started" in self._header(False, None)


class TestKwpActuators:
    """KWP2000 0x30 has no standard release: ON needs a declared one, value keys never."""

    def _kwp(self, release=""):
        act, tui = _actuator(dids=("01",))
        tui.protocol = "kwp"
        tui.cmds["01"].update(on="300100", off="300111", release=release)
        return act, tui

    def test_adjust_is_refused_for_a_kwp_ecu(self):
        act, tui = _actuator()
        tui.protocol = "kwp"
        asyncio.run(act.send_adjust("BC01", b"\x01"))
        assert tui.terminal.sent == []
        assert "UDS-only" in tui._status

    def test_adjust_is_refused_for_a_0x30_entry(self):
        act, tui = _actuator()
        tui.cmds["BC01"].update(on="30070001", off="30070000")
        asyncio.run(act.send_adjust("BC01", b"\x01"))
        assert tui.terminal.sent == []

    def test_toggle_without_on_does_not_open_the_value_prompt_on_kwp(self):
        act, tui = _actuator()
        tui.protocol = "kwp"
        tui._hex_input = None
        tui.cmds["BC01"]["on"] = ""
        tui.state["BC01"] = None
        asyncio.run(act.toggle("BC01"))
        assert tui._hex_input is None
        assert "UDS-only" in tui._status

    def test_on_without_a_release_is_refused_and_sends_nothing(self):
        # 30 {LID} 00 turns a Kia Soul EV charger LED ON: with no known release,
        # nothing could switch it back off on exit.
        act, tui = self._kwp()
        tui._session_active = False
        asyncio.run(act.send_on("01"))
        assert tui.terminal.sent == [] and tui.terminal.sessions == []
        assert tui.state["01"] != "on"
        assert "no release declared" in tui._status

    def test_on_with_a_declared_release_is_sent_and_released_on_exit(self):
        act, tui = self._kwp(release="3001AA")
        asyncio.run(act.send_on("01"))
        assert tui.state["01"] == "on"
        asyncio.run(act.cleanup())
        assert tui.terminal.sent == ["300100", "3001AA"]
        assert tui.state["01"] == "off"

    def test_the_kwp_reply_echo_is_validated(self):
        act, tui = self._kwp(release="3001AA")
        asyncio.run(act.send_on("01"))
        assert tui.terminal.kwargs == [{"expected_sid": 0x30, "expected_echo": b"\x01"}]

    def test_a_release_for_another_lid_is_refused(self):
        act, tui = self._kwp(release="300211")
        asyncio.run(act.send_on("01"))
        assert tui.terminal.sent == []
        assert "addresses 30 02" in tui._status

    def test_a_release_equal_to_on_is_refused(self):
        act, tui = self._kwp(release="300100")
        asyncio.run(act.send_on("01"))
        assert tui.terminal.sent == []

    def test_poll_skips_kwp_entries_without_a_release(self):
        act, tui = self._kwp()
        tui._session_active = False
        asyncio.run(act.poll_status_once())
        assert tui.terminal.sent == []
        assert tui.terminal.sessions == []

    def test_poll_sends_a_declared_kwp_release(self):
        act, tui = self._kwp(release="3001AA")
        asyncio.run(act.poll_status_once())
        assert tui.terminal.sent == ["3001AA"]
        # 70 {LID} echo, then the status record.
        assert tui.status_bytes["01"] == "AA"

    def test_poll_uses_each_actuators_session_mode(self):
        act, tui = _actuator(dids=("BC01",))
        tui._session_active, tui._session_mode = False, None
        tui.cmds["BC01"].update(session=True, session_mode="83")
        asyncio.run(act.poll_status_once())
        assert tui.terminal.sessions == ["83"]
        assert tui.terminal.sent == ["2FBC0100"]

    def test_poll_stops_when_no_session_can_be_opened(self):
        act, tui = _actuator(dids=("BC01", "BC02"))
        tui.terminal.enter_extended_session = _refusing  # type: ignore[method-assign]
        tui._session_active, tui._session_mode = False, None
        for c in tui.cmds.values():
            c["session"] = True
        asyncio.run(act.poll_status_once())
        assert tui.terminal.sent == []

    def test_release_reenters_the_actuators_own_session(self):
        # After another actuator moved the ECU to 10 03, the C0 actuator's OFF
        # must go out in C0 again.
        act, tui = _actuator(dids=("BC01",))
        tui.cmds["BC01"].update(session=True, session_mode="C0")
        tui.state["BC01"] = "on"
        tui._session_active, tui._session_mode = True, "03"
        asyncio.run(act.release_all())
        assert tui.terminal.sessions == ["C0"]
        assert tui.terminal.sent == [OFF_CMD]
        assert tui.state["BC01"] == "off"

    def test_release_falls_back_to_off_without_a_release(self):
        act, tui = _actuator(dids=("BC01",))
        tui.cmds["BC01"]["release"] = ""
        tui.state["BC01"] = "on"
        asyncio.run(act.release_all())
        assert tui.terminal.sent == [OFF_CMD]


KWP_DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "KWP2000"},
            "iocontrol": {"6900": {"label": "Mirrors", "on": "30690001", "off": "30690000"}},
        }
    }
}

# The OVMS Kia Soul EV charge-port LED: 30 01 00 switches it ON.
OBC_DATA = {
    "ecus": {
        "OBC": {
            "tx_id": 0x7E5,
            "identity": {"id_protocol": "KWP2000"},
            "iocontrol": {"01": {"label": "Centre LED", "on": "300100", "off": "300111"}},
        }
    }
}


def _with_release(data: dict, release: str) -> dict:
    import copy

    out = copy.deepcopy(data)
    for ecu in out["ecus"].values():
        for entry in ecu["iocontrol"].values():
            entry["release"] = release
    return out


class HoldingTerminal(FakeTerminal):
    """A terminal whose session hands back a keepalive task, so --did holds."""

    async def enter_extended_session(self, mode: str = "03"):
        self.sessions.append(mode)
        return True, asyncio.ensure_future(asyncio.sleep(3600))


class TestTuiProtocol:
    KWP_DATA = KWP_DATA

    def test_poll_is_refused_when_no_entry_has_a_release(self, capsys):
        from canlib.modes.iocontrol import mode_iocontrol_tui

        with pytest.raises(SystemExit) as exc:
            asyncio.run(mode_iocontrol_tui(FakeTerminal(), self.KWP_DATA, "BCM", poll=True))  # type: ignore[arg-type]
        assert exc.value.code == 2
        assert "no BCM entry has one" in capsys.readouterr().err

    def test_the_tui_knows_its_ecu_is_kwp(self):
        from canlib.modes.iocontrol import _IOControlTUI
        from canlib.pids import build_iocontrol_index

        entry = build_iocontrol_index(self.KWP_DATA, include_discoveries=True)["BCM"]
        tui = _IOControlTUI(FakeTerminal(), "BCM", entry, self.KWP_DATA)  # type: ignore[arg-type]
        assert tui.protocol == "kwp"
        assert tui.actuator.is_kwp()


class TestListJson:
    def test_unknown_ecu_is_a_json_error(self, capsys):
        import json

        from canlib.modes.iocontrol import mode_iocontrol_list

        assert mode_iocontrol_list(TestTuiProtocol.KWP_DATA, "NOPE", as_json=True) is False
        out = json.loads(capsys.readouterr().out)
        assert out == {"error": "no IOControl DIDs for ECU: NOPE", "available": ["BCM"]}
