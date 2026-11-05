"""NRC names on the TUI/monitor surfaces follow the addressed ECU's protocol.

The same code can mean different things on UDS and KWP2000 (0x72 is
generalProgrammingFailure on UDS and transferAborted on KWP2000), so every place
that names one asks for the ECU's protocol rather than assuming UDS.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from canair.live.actuate.routines import _RoutinesTUI
from canair.live.monitor.raw_poller import MonitorRawPoller, _raw_pid_result
from canair.safety.blocklist import SessionPolicy
from canair.uds.protocol import KWP, UDS
from tests.support.fakes import FakeTerminal, nrc


class TestRawMonitor:
    def test_an_nrc_is_named_for_the_ecus_protocol(self):
        reply = bytes.fromhex("7F2172")
        kwp = _raw_pid_result("2101", None, False, reply, 1.0, "BMS", KWP)
        uds = _raw_pid_result("2101", None, False, reply, 1.0, "BMS", UDS)
        assert kwp["error"] == "NRC 0x72 (transferAborted)"
        assert uds["error"] == "NRC 0x72 (generalProgrammingFailure)"

    def test_the_poller_reads_the_protocol_from_the_definitions(self):
        pids_data = {
            "ecus": {
                "BMS": {"tx_id": 0x7E4, "identity": {"id_protocol": "KWP2000"}},
                "BCM": {"tx_id": 0x7A0, "identity": {"id_protocol": "UDS"}},
                "AMP": {"tx_id": 0x7B0},
            }
        }
        poller = MonitorRawPoller(SimpleNamespace(pids_data=pids_data))
        assert poller._protocol("BMS") == KWP
        assert poller._protocol("BCM") == UDS
        assert poller._protocol("AMP") == "unknown"
        assert poller._protocol("NOPE") == "unknown"

    def test_a_truncated_negative_response_is_not_an_nrc(self):
        r = _raw_pid_result("2101", None, False, bytes.fromhex("7F21"), 1.0, "BMS", KWP)
        assert not r["error"].startswith("NRC")


class TestRoutinesTui:
    def _tui(self, protocol: str) -> tuple[_RoutinesTUI, FakeTerminal]:
        term = FakeTerminal(responses={"31030201": nrc(0x72)})
        term.session_policy = SessionPolicy(protocols={0x7E2: protocol})
        info = {"tx_id": 0x7E2, "routines": {"0201": {"label": "x"}}}
        return _RoutinesTUI(term, "VCU", info, {}), term  # type: ignore[arg-type]

    def test_the_last_response_uses_the_ecus_protocol(self):
        for protocol, abbrev in ((KWP, "TA"), (UDS, "GPF")):
            tui, _term = self._tui(protocol)
            asyncio.run(tui._send_sf("0201", 0x03))
            assert tui.last_response["0201"] == f"NRC 0x72 {abbrev}"


class TestBix:
    def test_the_service_line_names_an_nrc_for_the_ecus_protocol(self, capsys):
        from canair.cli.commands.bix import _print_service_line
        from canair.uds.layout import layout_of

        payload = bytes.fromhex("7F2172")
        layout = layout_of(payload)
        assert layout is not None
        frame = [(b, i) for i, b in enumerate(payload)]
        _print_service_line(layout, frame, KWP)
        assert "transferAborted" in capsys.readouterr().out
        _print_service_line(layout, frame)
        assert "generalProgrammingFailure" in capsys.readouterr().out
