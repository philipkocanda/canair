"""Tests for canair.live.actuate.iocontrol_actuate: the IOControl actuator bank.

This is the only code in canair that **energises physical vehicle outputs**, and
it was almost entirely untested — replacing `release_all` (the "switch every
actuator you turned on back off before exiting" safety net) with a no-op passed
the whole suite. These tests pin the behaviour that matters when a real relay,
lamp, or motor is on the other end:

- ON/OFF send the *requested* command and set the state the renderer reads;
- `release_all` actually releases everything it turned on, is resilient to a
  failing release, and doesn't touch actuators it never enabled;
- `canair io` releases before it stops the TesterPresent keepalive.

Device-free: a fake terminal stands in for the UDS surface.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from canair.cli.profile_context import active
from canair.live.actuate.iocontrol_actuate import IoBank
from canair.live.actuate.ports import OwnSessions
from canair.safety.blocklist import SessionPolicy
from canair.transport.session_manager import SessionManager
from canair.transport.stats import TransportStats

ON_CMD = "2FBC0103"
OFF_CMD = "2FBC0100"


class FakeTerminal:
    """Minimal stand-in for the terminal surface the actuator uses.

    A session request (``10 xx``) is answered positively and recorded in ``sessions``, not
    in ``sent``, so ``sent`` is what the actuator itself put on the bus.
    """

    def __init__(self, *, fail_on: set[str] | None = None):
        self.sent: list[str] = []
        self.headers: list[int] = []
        self.sessions: list[str] = []
        self.kwargs: list[dict] = []
        self.fail_on = fail_on or set()
        self.diag = TransportStats(transport="fake")
        self.session_policy = SessionPolicy()

    async def set_header(self, tx_id: int) -> None:
        self.headers.append(tx_id)

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self):
        yield

    async def session_reply(self, mode: str) -> dict:
        self.sessions.append(mode)
        return {"ok": True, "hex": "50" + mode, "bytes": list(bytes.fromhex("50" + mode))}

    async def send_uds(self, cmd: str, timeout: float = 3.0, **kwargs) -> dict:
        if len(cmd) == 4 and cmd.startswith("10"):
            return await self.session_reply(cmd[2:])
        self.sent.append(cmd)
        self.kwargs.append(kwargs)
        if cmd in self.fail_on:
            raise ConnectionError("bus dropped")
        reply = f"{int(cmd[:2], 16) + 0x40:02X}{cmd[2:]}"
        return {"ok": True, "hex": reply, "bytes": list(bytes.fromhex(reply))}


class Rig:
    """An IoBank on a fake terminal, with its own session and the view state tests read."""

    def __init__(self, dids: list[str], *, terminal: FakeTerminal | None = None):
        self.dids = dids
        self.tx_id = 0x770
        self.terminal = terminal or FakeTerminal()
        cmds = {
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
        self.sm = SessionManager(self.terminal)
        self.sessions = OwnSessions(self.sm, active=True, mode="03")
        self.status = ""
        self.status_level = "info"
        self.polling = False
        self.bank = IoBank(
            lambda: self.terminal,
            0x770,
            "uds",
            cmds,
            self.sessions,
            self.report,  # type: ignore[arg-type]
        )
        for d in dids:
            self.bank.state[d] = "off"

    def report(self, text: str, level: str = "info") -> None:
        self.status, self.status_level = text, level

    @property
    def cmds(self):
        return self.bank.all_cmds

    @property
    def state(self):
        return self.bank.state

    @property
    def status_bytes(self):
        return self.bank.status_bytes

    @property
    def last_value(self):
        return self.bank.last_value

    @property
    def busy(self):
        return self.bank.busy

    @property
    def protocol(self):
        return self.bank.protocol

    @protocol.setter
    def protocol(self, value):
        self.bank.protocol = value

    @property
    def session_active(self):
        return self.sessions.active

    @session_active.setter
    def session_active(self, value):
        self.sessions.active = value

    @property
    def session_mode(self):
        return self.sessions.mode

    @session_mode.setter
    def session_mode(self, value):
        self.sessions.mode = value


def _actuator(dids=("BC01", "BC02", "BC03"), **kw):
    rig = Rig(list(dids), **kw)
    return rig.bank, rig


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
        # A stuck `busy` would freeze the status-poll loop for the rest of the
        # session (it only polls when not busy).
        act, tui = _actuator(terminal=FakeTerminal(fail_on={ON_CMD}))
        asyncio.run(act.send_on("BC01"))
        assert tui.busy is False
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


IGPM_DATA = {
    "ecus": {
        "IGPM": {
            "tx_id": 0x770,
            "iocontrol": {"BC01": {"label": "Low beam", "on": ON_CMD, "off": OFF_CMD}},
        }
    }
}


def _controller(terminal: FakeTerminal):
    from canair.ecus.pids import build_iocontrol_index
    from canair.live.actuate.iocontrol import IOControlController

    entry = build_iocontrol_index(IGPM_DATA, include_discoveries=True)["IGPM"]
    return IOControlController(terminal, "IGPM", entry, IGPM_DATA, profile=active())  # type: ignore[arg-type]


class TestClose:
    def test_releases_actuators_before_stopping_the_keepalive(self):
        terminal = FakeTerminal()
        c = _controller(terminal)
        order: list[str] = []

        async def run() -> None:
            await c.actuator.send_on("BC01")
            c.sm.start_background_keepalive()
            original = c.sm.stop_background_keepalive

            async def stop() -> None:
                order.append(f"keepalive stopped after {terminal.sent[-1]}")
                await original()

            c.sm.stop_background_keepalive = stop  # type: ignore[method-assign]
            await c.close()

        asyncio.run(run())
        assert order == [f"keepalive stopped after {OFF_CMD}"]
        assert c.state["BC01"] == "off"

    def test_close_without_a_keepalive_task_is_safe(self):
        c = _controller(FakeTerminal())
        c.actuator.all_cmds["BC01"]["session"] = False  # no session, so no keepalive
        asyncio.run(c.actuator.send_on("BC01"))
        asyncio.run(c.close())  # must not raise
        assert c.state["BC01"] == "off"


class TestEnsureSession:
    def test_opens_a_session_on_the_target_ecu(self):
        _act, tui = _actuator()
        tui.session_active = False
        asyncio.run(tui.sessions.ensure(0x770, None))
        assert tui.terminal.headers == [0x770]
        assert tui.session_active is True

    def test_is_a_noop_when_a_session_is_already_active(self):
        _act, tui = _actuator()
        tui.session_active = True
        asyncio.run(tui.sessions.ensure(0x770, None))
        assert tui.terminal.headers == []

    def test_opens_the_session_mode_the_actuator_declares(self):
        act, tui = _actuator()
        tui.session_active, tui.session_mode = False, None
        tui.cmds["BC01"].update(session=True, session_mode="C0")
        asyncio.run(act.send_on("BC01"))
        assert tui.terminal.sessions == ["C0"]
        assert tui.session_mode == "C0"
        assert tui.terminal.sent == [ON_CMD]

    def test_reuses_an_open_session_of_the_same_mode(self):
        _act, tui = _actuator()
        asyncio.run(tui.sessions.ensure(0x770, "03"))
        assert tui.terminal.sessions == []

    def test_switches_session_and_stops_the_old_keepalive(self):
        _act, tui = _actuator()

        async def run():
            old = tui.sm.start_background_keepalive()
            await tui.sessions.ensure(0x770, "C0")
            assert tui.sm.keepalive_running
            await tui.sm.stop_background_keepalive()
            return old

        old = asyncio.run(run())
        assert old.done()
        assert tui.terminal.sessions == ["C0"]
        assert tui.session_mode == "C0"


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
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

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
        asyncio.run(mode_iocontrol_execute(term, pids_data, "BCM", "6900", yes=True))  # type: ignore[arg-type]
        assert term.sessions == ["C0"]
        assert term.sent == ["30690001"]

    @staticmethod
    def _stdin(monkeypatch, text: str, *, tty: bool = True) -> None:
        import io

        class _Stdin(io.StringIO):
            def isatty(self) -> bool:
                return tty

        monkeypatch.setattr("sys.stdin", _Stdin(text))

    def test_nothing_is_sent_when_the_user_declines(self, monkeypatch, capsys):
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        self._stdin(monkeypatch, "n\n")
        term = FakeTerminal()
        with pytest.raises(SystemExit) as exc:
            asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01", off=True))  # type: ignore[arg-type]
        assert exc.value.code == 1
        assert term.sent == [] and term.sessions == [] and term.headers == []
        assert "Aborted" in capsys.readouterr().err

    def test_a_yes_answer_sends(self, monkeypatch):
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        self._stdin(monkeypatch, "y\n")
        term = FakeTerminal()
        asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01", off=True))  # type: ignore[arg-type]
        assert term.sent == ["300111"]

    def test_no_terminal_and_no_yes_refuses_before_anything_is_sent(self, monkeypatch):
        from canair.core.prompt import PromptUnavailable
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        self._stdin(monkeypatch, "y\n", tty=False)
        term = FakeTerminal()
        with pytest.raises(PromptUnavailable, match="--yes"):
            asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01", off=True))  # type: ignore[arg-type]
        assert term.sent == [] and term.headers == []

    def test_the_command_has_a_yes_flag(self):
        from canair.cli import build_parser

        args = build_parser().parse_args(["io", "IGPM", "--did", "BC01", "-y"])
        assert args.yes is True

    def test_a_kwp_on_without_a_release_is_refused_before_anything_is_sent(self, capsys):
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        term = FakeTerminal()
        with pytest.raises(SystemExit) as exc:
            asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01"))  # type: ignore[arg-type]
        assert exc.value.code == 2
        assert term.sent == [] and term.sessions == []
        assert "no release declared" in capsys.readouterr().err

    def test_off_is_still_sent_without_a_release(self):
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        term = FakeTerminal()
        asyncio.run(mode_iocontrol_execute(term, OBC_DATA, "OBC", "01", off=True, yes=True))  # type: ignore[arg-type]
        assert term.sent == ["300111"]

    def test_a_held_kwp_on_is_released_with_its_declared_release(self):
        from canair.live.actuate.iocontrol_execute import mode_iocontrol_execute

        data = _with_release(OBC_DATA, "3001AA")

        async def run():
            task = asyncio.ensure_future(
                mode_iocontrol_execute(term, data, "OBC", "01", yes=True)  # type: ignore[arg-type]
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
    async def session_reply(self, mode: str) -> dict:
        self.sessions.append(mode)
        raise ValueError("BLOCKED: refused")


class TestEnsureSessionFailure:
    """Regression: a failed re-entry left the header claiming the old session."""

    def test_a_raising_session_open_forgets_the_old_session(self):
        _act, tui = _actuator(terminal=FailingSessionTerminal())

        async def run():
            tui.sm.start_background_keepalive()
            with pytest.raises(ValueError):
                await tui.sessions.ensure(0x770, "C0")

        asyncio.run(run())
        assert tui.session_active is False
        assert tui.session_mode is None
        assert not tui.sm.keepalive_running

    def test_a_refused_session_is_unconfirmed_not_active(self):
        _act, tui = _actuator(terminal=FakeTerminal())
        tui.terminal.session_reply = _refusing  # type: ignore[method-assign]
        tui.session_active = False
        asyncio.run(tui.sessions.ensure(0x770, "C0"))
        assert tui.session_active is False
        assert tui.session_mode == "C0"


async def _refusing(mode: str = "03") -> dict:
    return {"ok": False, "nrc": 0x12, "nrc_desc": "subFunctionNotSupported"}


class TestSessionHeader:
    def _header(self, active, mode):
        from canair.live.actuate.iocontrol_render import session_text

        _, tui = _actuator()
        tui.session_active, tui.session_mode = active, mode
        tui.ecu_key, tui.view_mode = "IGPM", "curated"
        tui.counts = lambda: (len(tui.dids), 0)  # type: ignore[attr-defined]
        return session_text(tui).plain  # type: ignore[arg-type]

    def test_active(self):
        assert "active (10 C0)" in self._header(True, "C0")

    def test_unconfirmed(self):
        assert "unconfirmed (10 03)" in self._header(False, "03")

    def test_not_started(self):
        assert "not started" in self._header(False, None)


class TestEnableMask:
    """A packeted DID's controlEnableMaskRecord rides on every request (ISO 14229-1 12.2.2)."""

    def test_adjust_appends_the_entrys_mask(self):
        act, tui = _actuator()
        tui.cmds["BC01"]["enable_mask"] = "80"
        asyncio.run(act.send_adjust("BC01", b"\x07\x00"))
        assert tui.terminal.sent == ["2FBC0103070080"]

    def test_adjust_without_a_mask_sends_the_state_only(self):
        act, tui = _actuator()
        asyncio.run(act.send_adjust("BC01", b"\x07"))
        assert tui.terminal.sent == ["2FBC010307"]


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
        assert "UDS-only" in tui.status

    def test_adjust_is_refused_for_a_0x30_entry(self):
        act, tui = _actuator()
        tui.cmds["BC01"].update(on="30070001", off="30070000")
        asyncio.run(act.send_adjust("BC01", b"\x01"))
        assert tui.terminal.sent == []

    def test_toggle_without_on_asks_for_a_value(self):
        act, tui = _actuator()
        tui.cmds["BC01"]["on"] = ""
        tui.state["BC01"] = None
        assert asyncio.run(act.toggle("BC01")) == "00"
        tui.last_value["BC01"] = b"\x0a"
        assert asyncio.run(act.toggle("BC01")) == "0A"
        assert tui.terminal.sent == []

    def test_toggle_without_on_does_not_ask_for_a_value_on_kwp(self):
        act, tui = _actuator()
        tui.protocol = "kwp"
        tui.cmds["BC01"]["on"] = ""
        tui.state["BC01"] = None
        assert asyncio.run(act.toggle("BC01")) is None
        assert "UDS-only" in tui.status
        assert tui.status_level == "error"

    def test_on_without_a_release_is_refused_and_sends_nothing(self):
        # 30 {LID} 00 turns a Kia Soul EV charger LED ON: with no known release,
        # nothing could switch it back off on exit.
        act, tui = self._kwp()
        tui.session_active = False
        asyncio.run(act.send_on("01"))
        assert tui.terminal.sent == [] and tui.terminal.sessions == []
        assert tui.state["01"] != "on"
        assert "no release declared" in tui.status

    def test_on_with_a_declared_release_is_sent_and_released_on_exit(self):
        act, tui = self._kwp(release="3001AA")
        asyncio.run(act.send_on("01"))
        assert tui.state["01"] == "on"
        asyncio.run(act.release_all())
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
        assert "addresses 30 02" in tui.status

    def test_a_release_equal_to_on_is_refused(self):
        act, tui = self._kwp(release="300100")
        asyncio.run(act.send_on("01"))
        assert tui.terminal.sent == []

    def test_poll_skips_kwp_entries_without_a_release(self):
        act, tui = self._kwp()
        tui.session_active = False
        asyncio.run(act.poll_status_once(tui.dids))
        assert tui.terminal.sent == []
        assert tui.terminal.sessions == []

    def test_poll_sends_a_declared_kwp_release(self):
        act, tui = self._kwp(release="3001AA")
        asyncio.run(act.poll_status_once(tui.dids))
        assert tui.terminal.sent == ["3001AA"]
        # 70 {LID} echo, then the status record.
        assert tui.status_bytes["01"] == "AA"

    def test_poll_uses_each_actuators_session_mode(self):
        act, tui = _actuator(dids=("BC01",))
        tui.session_active, tui.session_mode = False, None
        tui.cmds["BC01"].update(session=True, session_mode="83")
        asyncio.run(act.poll_status_once(tui.dids))
        assert tui.terminal.sessions == ["83"]
        assert tui.terminal.sent == ["2FBC0100"]

    def test_poll_stops_when_no_session_can_be_opened(self):
        act, tui = _actuator(dids=("BC01", "BC02"))
        tui.terminal.session_reply = _refusing  # type: ignore[method-assign]
        tui.session_active, tui.session_mode = False, None
        for c in tui.cmds.values():
            c["session"] = True
        asyncio.run(act.poll_status_once(tui.dids))
        assert tui.terminal.sent == []

    def test_release_reenters_the_actuators_own_session(self):
        # After another actuator moved the ECU to 10 03, the C0 actuator's OFF
        # must go out in C0 again.
        act, tui = _actuator(dids=("BC01",))
        tui.cmds["BC01"].update(session=True, session_mode="C0")
        tui.state["BC01"] = "on"
        tui.session_active, tui.session_mode = True, "03"
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
    """A terminal whose ECU accepts the session, so --did holds."""


class TestTuiProtocol:
    KWP_DATA = KWP_DATA

    def test_poll_is_refused_when_no_entry_has_a_release(self, capsys):
        from canair.live.actuate.iocontrol import mode_iocontrol_tui

        with pytest.raises(SystemExit) as exc:
            asyncio.run(
                mode_iocontrol_tui(
                    FakeTerminal(),
                    self.KWP_DATA,
                    "BCM",
                    poll=True,
                    profile=active(),
                    interactive=_never,
                )
            )  # type: ignore[arg-type]
        assert exc.value.code == 2
        assert "no BCM entry has one" in capsys.readouterr().err

    def test_the_tui_knows_its_ecu_is_kwp(self):
        from canair.ecus.pids import build_iocontrol_index
        from canair.live.actuate.iocontrol import IOControlController

        entry = build_iocontrol_index(self.KWP_DATA, include_discoveries=True)["BCM"]
        tui = IOControlController(FakeTerminal(), "BCM", entry, self.KWP_DATA, profile=active())  # type: ignore[arg-type]
        assert tui.protocol == "kwp"
        assert tui.actuator.is_kwp()


async def _never(_controller) -> None:
    raise AssertionError("the view must not open")


class TestListJson:
    def test_unknown_ecu_is_an_error_on_stderr_and_nothing_on_stdout(self, capsys):
        from canair.live.actuate.iocontrol_list import mode_iocontrol_list

        assert mode_iocontrol_list(TestTuiProtocol.KWP_DATA, "NOPE", as_json=True) is False
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error: no IOControl DIDs for ECU: NOPE" in captured.err
        assert "hint: ECUs with IOControl: BCM" in captured.err


class TestOneTransaction:
    """Every request sets its header inside the transaction it is sent in."""

    def test_the_header_is_set_before_every_request(self):
        act, tui = _actuator(dids=("BC01",))
        tui.terminal.headers.clear()
        asyncio.run(act.send_on("BC01"))
        asyncio.run(act.send_off("BC01"))
        assert tui.terminal.headers == [0x770, 0x770]
        assert tui.terminal.sent == [ON_CMD, OFF_CMD]
