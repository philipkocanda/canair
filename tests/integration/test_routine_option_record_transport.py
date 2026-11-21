"""A long routine option record crosses the real ISO-TP stack on an in-memory CAN bus."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid

import can
import isotp

from canair.cli import build_parser
from canair.cli.live.handlers import actuators
from canair.safety.blocklist import SessionPolicy
from canair.transport.link_latency import LinkLatency
from canair.transport.raw import terminal as raw_terminal

DATA = {
    "ecus": {
        "BCM": {
            "tx_id": 0x745,
            "identity": {"id_protocol": "UDS"},
            "routines": {"12A1": {"label": "Self test"}},
        }
    }
}


def test_long_option_record_is_segmented_and_reassembled_offline(monkeypatch, capsys):
    channel = f"routine-options-{uuid.uuid4()}"
    tester = can.Bus(interface="virtual", channel=channel)
    ecu_bus = can.Bus(interface="virtual", channel=channel)
    observer = can.Bus(interface="virtual", channel=channel)
    tester.link = LinkLatency()
    monkeypatch.setattr(raw_terminal, "SlcanTcpBus", lambda *a, **kw: tester)

    term = raw_terminal.RawTerminal("in-memory", 0)
    term.session_policy = SessionPolicy.from_profile(DATA)
    send = term.send_uds
    routine_timeouts = []

    async def record_send(request, *args, **kwargs):
        if request.startswith("31"):
            routine_timeouts.append(kwargs.get("timeout"))
        return await send(request, *args, **kwargs)

    monkeypatch.setattr(term, "send_uds", record_send)
    ecu = isotp.CanStack(
        bus=ecu_bus,
        address=isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x74D, rxid=0x745),
    )
    ecu.start()
    received: list[bytes] = []

    def answer() -> None:
        for reply in ("5003", "710112A1EE"):
            request = ecu.recv(block=True, timeout=3)
            if request is None:
                return
            received.append(bytes(request))
            ecu.send(bytes.fromhex(reply))

    responder = threading.Thread(target=answer, daemon=True)
    responder.start()
    args = build_parser().parse_args(
        [
            "routines",
            "BCM",
            "--rid",
            "12A1",
            "--sf",
            "start",
            "--option-record",
            "00112233445566778899",
            "--timeout",
            "8.5",
            "--yes",
            "--json",
        ]
    )
    try:
        asyncio.run(actuators.handle_routines(args, term, DATA, None))
        responder.join(timeout=3)
        assert not responder.is_alive()
        assert received == [bytes.fromhex("1003"), bytes.fromhex("310112A100112233445566778899")]
        assert routine_timeouts == [8.5]
        result = json.loads(capsys.readouterr().out)
        assert result["command"] == "310112A100112233445566778899"
        assert result["response"] == "710112A1EE"

        frames = []
        while (message := observer.recv(timeout=0.05)) is not None:
            if message.arbitration_id == 0x745:
                frames.append(bytes(message.data))
        assert any(frame[0] >> 4 == 1 for frame in frames)  # ISO-TP First Frame
        assert any(frame[0] >> 4 == 2 for frame in frames)  # Consecutive Frame
    finally:
        asyncio.run(term.close())
        responder.join(timeout=3)
        ecu.stop()
        ecu_bus.shutdown()
        observer.shutdown()
