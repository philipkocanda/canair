"""Session-safety regressions: every path that sends 0x10 is guarded the same way.

Covers the raw monitor backend (which talks to :class:`RawUdsClient` directly,
bypassing the terminals), the monitor's session picker/switcher, the pipelined
client's own guard, and how a refusal is reported (``error: …``, exit 2) instead
of escaping as a traceback.
"""

from __future__ import annotations

import asyncio

import pytest

from canlib.addressing import EcuAddress
from canlib.modes.monitor import MonitorController
from canlib.safety import KWP, SafetyBlocked, SessionPolicy

PIDS = {
    "ecus": {
        "BMS": {"tx_id": 0x7E4, "identity": {"id_protocol": "KWP2000"}},
        "IGPM": {"tx_id": 0x770, "identity": {"id_protocol": "UDS"}},
        "BCM": {"tx_id": 0x745, "identity": {"id_protocol": "KWP2000"}},
    }
}


def _with_quirks(*quirks):
    return {**PIDS, "quirks": list(quirks)}


class RecordingRawClient:
    """The slice of RawUdsClient the monitor's raw branch uses; records reads."""

    def __init__(self):
        self.reads: list[tuple[str, bytes]] = []

    def read(self, ecu, req, timeout=None):
        self.reads.append((ecu, bytes(req)))
        return bytes([0x50, req[1]]) if req[0] == 0x10 else b""


def _raw_monitor(pids_data):
    client = RecordingRawClient()
    ctrl = MonitorController(None, [], pids_data, verbose=False, raw_client=client)
    return ctrl, client


class TestRawMonitorSetup:
    """Regression: setup()'s raw branch sent `10 {mode}` with no safety check."""

    @pytest.mark.parametrize("ecu,mode", [("IGPM", "02"), ("IGPM", "82"), ("BMS", "85")])
    def test_a_programming_session_is_refused_before_anything_is_sent(self, ecu, mode):
        ctrl, client = _raw_monitor(PIDS)
        steps = [{"type": "session", "target": ecu, "mode": mode}]
        with pytest.raises(SafetyBlocked, match=f"session {ecu} --mode {mode}"):
            asyncio.run(ctrl.setup(steps))
        assert client.reads == []

    def test_a_kwp_session_82_is_still_opened(self):
        ctrl, client = _raw_monitor(PIDS)
        asyncio.run(ctrl.setup([{"type": "session", "target": "BMS", "mode": "82"}]))
        assert client.reads == [("BMS", bytes.fromhex("1082"))]
        assert ctrl.active_session_modes() == {"BMS": "82"}

    def test_the_nissan_session_needs_the_quirk(self):
        steps = [{"type": "session", "target": "BCM", "mode": "C0"}]
        ctrl, client = _raw_monitor(PIDS)
        with pytest.raises(SafetyBlocked):
            asyncio.run(ctrl.setup(steps))
        ctrl, client = _raw_monitor(_with_quirks("nissan_c0_session"))
        asyncio.run(ctrl.setup(steps))
        assert client.reads == [("BCM", bytes.fromhex("10C0"))]


class TestSwitchSession:
    def _ready(self, pids_data):
        ctrl, client = _raw_monitor(pids_data)
        asyncio.run(ctrl.setup([]))
        return ctrl, client

    def test_c0_is_refused_without_the_quirk(self):
        ctrl, client = self._ready(PIDS)
        msg = asyncio.run(ctrl.switch_session("BCM", "C0"))
        assert msg.startswith("BLOCKED")
        assert client.reads == []

    def test_c0_switches_with_the_quirk(self):
        ctrl, client = self._ready(_with_quirks("nissan_c0_session"))
        msg = asyncio.run(ctrl.switch_session("BCM", "C0"))
        assert "switched to session 0x10C0" in msg
        assert client.reads == [("BCM", bytes.fromhex("10C0"))]

    def test_the_quirk_does_not_reach_a_uds_ecu(self):
        ctrl, client = self._ready(_with_quirks("nissan_c0_session"))
        assert asyncio.run(ctrl.switch_session("IGPM", "C0")).startswith("BLOCKED")
        assert client.reads == []

    def test_82_is_refused_on_uds_and_allowed_on_kwp(self):
        ctrl, client = self._ready(PIDS)
        assert "programmingSession" in asyncio.run(ctrl.switch_session("IGPM", "82"))
        assert "switched" in asyncio.run(ctrl.switch_session("BMS", "82"))
        assert client.reads == [("BMS", bytes.fromhex("1082"))]

    def test_a_non_hex_mode_is_rejected(self):
        ctrl, client = self._ready(PIDS)
        assert asyncio.run(ctrl.switch_session("IGPM", "zz")).startswith("Invalid")
        assert client.reads == []


class TestSessionPicker:
    """The fallback list never offers a mode the guard would refuse."""

    def _modes(self, pids_data, ecu):
        ctrl, _ = _raw_monitor(pids_data)
        asyncio.run(ctrl.setup([]))
        return [m for m, _label, _sup in ctrl.known_session_types(ecu)]

    def test_uds_ecu_is_never_offered_82(self):
        assert self._modes(PIDS, "IGPM") == ["01", "03", "81", "83"]

    def test_kwp_ecu_keeps_82(self):
        assert self._modes(PIDS, "BMS") == ["01", "03", "81", "82", "83"]

    def test_recorded_history_is_filtered_too(self):
        data = {
            "ecus": {
                "IGPM": {
                    "tx_id": 0x770,
                    "identity": {"id_protocol": "UDS"},
                    "sessions": {"03": {"supported": True}, "82": {"supported": True}},
                }
            }
        }
        assert self._modes(data, "IGPM") == ["03"]


class TestRawUdsClientGuard:
    """The pipelined client checks every request itself, so no caller can bypass it."""

    def _client(self, monkeypatch, policy=None):
        from canlib.transport import uds_raw
        from tests.test_uds_raw import FakeNotifier, FakeStack

        table = {(0x7E4, bytes.fromhex("1082")): bytes.fromhex("5082")}
        monkeypatch.setattr(uds_raw.can, "Notifier", FakeNotifier)
        monkeypatch.setattr(uds_raw, "STACK_WARMUP_S", 0)
        monkeypatch.setattr(
            uds_raw.isotp,
            "NotifierBasedCanStack",
            lambda bus, notifier, address=None, params=None: FakeStack(address._txid, table),
        )
        addresses = {"BMS": EcuAddress(0x7E4, 0x7EC), "IGPM": EcuAddress(0x770, 0x778)}
        return uds_raw.RawUdsClient(
            bus=object(), addresses=addresses, timeout=0.3, session_policy=policy
        )

    def test_read_refuses_a_programming_session(self, monkeypatch):
        c = self._client(monkeypatch, SessionPolicy.from_profile(PIDS))
        with pytest.raises(SafetyBlocked):
            c.read("IGPM", bytes.fromhex("1002"))
        with pytest.raises(SafetyBlocked):
            c.read("IGPM", bytes.fromhex("1082"))
        assert c._stacks["IGPM"].sent == []

    def test_read_allows_the_kwp_session_on_a_kwp_ecu(self, monkeypatch):
        c = self._client(monkeypatch, SessionPolicy.from_profile(PIDS))
        assert c.read("BMS", bytes.fromhex("1082")) == bytes.fromhex("5082")

    def test_without_a_policy_the_stricter_reading_applies(self, monkeypatch):
        c = self._client(monkeypatch)
        with pytest.raises(SafetyBlocked):
            c.read("BMS", bytes.fromhex("1082"))

    def test_poll_refuses_before_sending_anything(self, monkeypatch):
        c = self._client(monkeypatch, SessionPolicy.from_profile(PIDS))
        with pytest.raises(SafetyBlocked):
            c.poll([("BMS", bytes.fromhex("2101")), ("IGPM", bytes.fromhex("2E0101AA"))])
        assert c._stacks["BMS"].sent == []

    def test_build_raw_client_wires_the_profile_policy(self, monkeypatch):
        from canlib.modes import raw_monitor
        from canlib.transport import slcan_tcp

        seen = {}

        class _Client:
            def __init__(self, *a, session_policy=None, **k):
                seen["policy"] = session_policy

        monkeypatch.setattr("canlib.transport.RawUdsClient", _Client)
        monkeypatch.setattr(slcan_tcp, "SlcanTcpBus", lambda *a, **k: object())
        monkeypatch.setattr("canlib.transport.SlcanTcpBus", lambda *a, **k: object())

        class _Args:
            timeout = None

        raw_monitor.build_raw_client("h", 1, 500_000, {}, _Args(), PIDS)
        assert seen["policy"].protocol(0x7E4) == KWP


class TestRefusalIsReportedNotRaised:
    def test_run_session_guarded_reports_and_exits_2(self, monkeypatch, capsys):
        from canlib.modes import dispatch

        async def _blocked(*a, **k):
            raise SafetyBlocked("BLOCKED: programmingSession")

        monkeypatch.setattr(dispatch, "dispatch_mode", _blocked)
        monkeypatch.setattr(dispatch, "_persist_frame_counts", lambda *a: None)

        class _Args:
            pass

        rc = asyncio.run(
            dispatch.run_session_guarded(_Args(), object(), {}, "h", transport_label="x")
        )
        assert rc == 2
        assert "error: BLOCKED: programmingSession" in capsys.readouterr().err

    def test_cli_main_reports_and_exits_2(self, monkeypatch, capsys):
        from canlib import cli
        from canlib.commands import io

        def _blocked(args):
            raise SafetyBlocked("BLOCKED: nope")

        monkeypatch.setattr(io, "run", _blocked)
        assert cli.main(["io", "IGPM"]) == 2
        assert "error: BLOCKED: nope" in capsys.readouterr().err

    def test_a_blocked_iocontrol_session_mode_surfaces_as_safety_blocked(self):
        """The ValueError the terminal raises is the dedicated, catchable type."""
        from canlib.modes.iocontrol import mode_iocontrol_execute
        from canlib.transport.raw_terminal import RawTerminal

        term = RawTerminal.__new__(RawTerminal)
        term.unsafe = False
        term.session_policy = SessionPolicy.from_profile(PIDS)
        term._cur = None

        async def set_header(tx_id):
            term._cur = tx_id

        term.set_header = set_header  # type: ignore[method-assign]
        data = {
            "ecus": {
                "IGPM": {
                    "tx_id": 0x770,
                    "identity": {"id_protocol": "UDS"},
                    "iocontrol": {"BC01": {"label": "x", "on": "2FBC0103", "session_mode": "82"}},
                }
            }
        }
        with pytest.raises(SafetyBlocked):
            asyncio.run(mode_iocontrol_execute(term, data, "IGPM", "BC01"))
