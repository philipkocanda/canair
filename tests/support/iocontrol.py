"""A fake terminal and a real controller for the IOControl view tests."""

from __future__ import annotations

import contextlib

from canair.cli.profile_context import active
from canair.ecus.pids import build_iocontrol_index
from canair.live.actuate.iocontrol import IOControlController
from canair.safety.blocklist import SessionPolicy
from canair.transport.stats import TransportStats

IGPM_DATA = {
    "ecus": {
        "IGPM": {
            "tx_id": 0x770,
            "iocontrol": {
                "BC01": {
                    "label": "Low beam",
                    "on": "2FBC0103",
                    "off": "2FBC0100",
                    "verified": True,
                },
                "BC02": {"label": "High beam", "on": "2FBC0203", "off": "2FBC0200"},
                # No simple ON: toggling asks for a ShortTermAdjustment value.
                "BC03": {"label": "Dimmer", "on": "", "off": "2FBC0300"},
            },
            "iocontrol_discoveries": {"BC40": {"response": "6FBC4000"}},
        }
    }
}


class FakeIoTerminal:
    """Answers every request positively; a session request is kept apart from ``sent``."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.sessions: list[str] = []
        self.diag = TransportStats(transport="fake")
        self.session_policy = SessionPolicy()

    async def set_header(self, tx_id: int) -> None:
        pass

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    @contextlib.asynccontextmanager
    async def short_ecu_wait(self):
        yield

    async def send_uds(self, cmd: str, timeout: float = 3.0, **kwargs) -> dict:
        if len(cmd) == 4 and cmd.startswith("10"):
            self.sessions.append(cmd[2:])
            reply = "50" + cmd[2:]
        else:
            self.sent.append(cmd)
            reply = f"{int(cmd[:2], 16) + 0x40:02X}{cmd[2:]}"
        return {"ok": True, "hex": reply, "bytes": list(bytes.fromhex(reply))}


def io_controller(data: dict | None = None) -> IOControlController:
    data = data or IGPM_DATA
    info = build_iocontrol_index(data, include_discoveries=True)["IGPM"]
    return IOControlController(FakeIoTerminal(), "IGPM", info, data, profile=active())  # type: ignore[arg-type]
