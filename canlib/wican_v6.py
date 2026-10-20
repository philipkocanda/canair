"""Live reads of a WiCAN firmware v6 device: its status and its data paths.

v6 replaced 4.x's single protocol mode with servers, WebSocket channels and
bridges, all configured in the device's settings. A port or channel can
therefore be open and still lead nowhere: a server with no bridge accepts a
client and silently discards everything it sends. So before a transport relies
on one, this module reads the device's own description of the path
(``/api/ws``, ``/api/sockets``, ``/api/bridges``, ``/api/can``) and refines the
static per-surface verdict from :mod:`canlib.wican_firmware` with what it found.

Every read is a best-effort ``GET``: a route that doesn't answer means
"unknown", never an error, and the static verdict then stands. Shares the
firmware probe's HTTP seam, so the test suite's network stub covers both.
Audit and route sources: ``plans/2026-10-20-wican-fw-v6-compatibility.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .wican_firmware import (
    OBD_WS_PATH,
    FirmwareInfo,
    Surface,
    Verdict,
    compatibility,
    get_object,
)


def get_json(host: str, path: str, *, timeout: float | None = None) -> dict | None:
    """``GET`` a v6 API route as a JSON object, or None when it doesn't answer one."""
    if timeout is None:
        from .config import fallback_settings

        _, timeout, _ = fallback_settings()
    return get_object(host, path, timeout=timeout)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _flag(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _rows(body: dict | None, key: str) -> list[dict]:
    rows = (body or {}).get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


# --- device status --------------------------------------------------------------


@dataclass(frozen=True)
class DeviceStatus:
    """The power and OBD-chip state ``canair status`` and the sleep banner show."""

    battery_v: float | None = None
    sleep_enabled: bool | None = None
    sleep_state: str | None = None  # normal / low_voltage / sleeping / wake_pending
    sleep_v: float | None = None
    obd_chip_ready: bool | None = None
    obd_chip_claim: str | None = None  # the chip's current claim: none/command/monitor/exclusive

    @property
    def reachable(self) -> bool:
        return any(v is not None for v in self.as_dict().values())

    def as_dict(self) -> dict:
        return {
            "battery_v": self.battery_v,
            "sleep_enabled": self.sleep_enabled,
            "sleep_state": self.sleep_state,
            "sleep_v": self.sleep_v,
            "obd_chip_ready": self.obd_chip_ready,
            "obd_chip_claim": self.obd_chip_claim,
        }


def sleep_status(host: str, *, timeout: float | None = None) -> DeviceStatus:
    """Battery voltage and sleep state from ``/api/sleep`` alone (one request)."""
    body = get_json(host, "/api/sleep", timeout=timeout) or {}
    state = body.get("state")
    return DeviceStatus(
        battery_v=_number(body.get("voltage")),
        sleep_enabled=_flag(body.get("enabled")),
        sleep_state=state if isinstance(state, str) else None,
        sleep_v=_number(body.get("sleep_v")),
    )


def device_status(host: str, *, timeout: float | None = None) -> DeviceStatus:
    """Battery, sleep and OBD-chip state; each field None when its route is silent."""
    sleep = sleep_status(host, timeout=timeout)
    battery = _number((get_json(host, "/api/battery", timeout=timeout) or {}).get("voltage"))
    chip = get_json(host, "/api/obd_chip", timeout=timeout) or {}
    claim = chip.get("claim")
    return DeviceStatus(
        # /api/battery is the dedicated reading; /api/sleep's copy is the fallback.
        battery_v=battery if battery is not None else sleep.battery_v,
        sleep_enabled=sleep.sleep_enabled,
        sleep_state=sleep.sleep_state,
        sleep_v=sleep.sleep_v,
        obd_chip_ready=_flag(chip.get("ready")),
        obd_chip_claim=claim if isinstance(claim, str) and claim else None,
    )


# --- data paths -----------------------------------------------------------------


def _bridge_row(host: str, name: str, timeout: float | None) -> dict | None:
    for row in _rows(get_json(host, "/api/bridges", timeout=timeout), "bridges"):
        if row.get("name") == name:
            return row
    return None


def _peer(row: dict, jack: str) -> str | None:
    """The endpoint a bridge row connects ``jack`` to."""
    if row.get("a") == jack:
        return row.get("b")
    if row.get("b") == jack:
        return row.get("a")
    return None


def ws_obd_verdict(host: str, *, timeout: float | None = None) -> Verdict | None:
    """Is ``/ws/obd`` enabled, running and bridged raw to the OBD chip?

    None when the device doesn't describe its channels (the connect then simply
    tries). A refusal names the setting to change.
    """
    body = get_json(host, "/api/ws", timeout=timeout)
    if body is None:
        return None
    channel = next((c for c in _rows(body, "channels") if c.get("path") == OBD_WS_PATH), None)
    where = f"WiCAN firmware v6 at {host}"
    if channel is None:
        return Verdict(
            "refuse",
            f"{where} has no {OBD_WS_PATH} WebSocket channel, which the 'wican-ws' transport "
            "uses.\n"
            "  • add it back in websocket_manager: "
            '{"name":"ws_obd","path":"/ws/obd","mode":"binary","enabled":true}, with a '
            "bridge_manager row bridging 'obd' to 'ws_obd' (translator 'raw')\n"
            "  • or use the ELM327 socket: --transport elm327-tcp (port 35000)",
        )
    name = channel.get("name") or "ws_obd"
    if channel.get("enabled") is not True or channel.get("up") is False:
        state = "disabled" if channel.get("enabled") is not True else "configured but not running"
        return Verdict(
            "refuse",
            f"{where}: its {OBD_WS_PATH} channel ({name}) is {state}.\n"
            "  • enable it in the device's websocket_manager settings, then submit\n"
            "  • or use the ELM327 socket: --transport elm327-tcp (port 35000)",
        )
    bridge = channel.get("bridge")
    if not isinstance(bridge, str):
        return Verdict(
            "refuse",
            f"{where}: nothing is bridged to its {OBD_WS_PATH} channel, so it would accept the "
            "connection and discard every command.\n"
            f"  • add a bridge_manager row bridging 'obd' to '{name}' with translator 'raw', "
            "then submit",
        )
    row = _bridge_row(host, bridge, timeout)
    if row is not None and (_peer(row, name) != "obd" or row.get("translator") != "raw"):
        return Verdict(
            "refuse",
            f"{where}: {OBD_WS_PATH} is bridged ({bridge}) to '{_peer(row, name)}' with "
            f"translator '{row.get('translator')}', not raw to the OBD chip ('obd'), so it "
            "doesn't speak ELM327.",
        )
    stats = channel.get("stats")
    clients = stats.get("clients") if isinstance(stats, dict) else None
    limit = channel.get("max_clients")
    if isinstance(clients, int) and isinstance(limit, int) and clients >= limit:
        return Verdict(
            "warn",
            f"{where}: its {OBD_WS_PATH} channel already has {clients} of {limit} clients "
            "(the web UI's terminal counts as one), so the device may refuse this connection.",
        )
    return None


_SLCAN_SETUP = (
    "  • in the device's settings: enable the slcan0 server (socket_manager), bridge 'can' to "
    "it with translator 'slcan' (bridge_manager) and enable can_manager, then submit"
)


def slcan_verdict(
    host: str, port: int, bitrate: int | None = None, *, timeout: float | None = None
) -> Verdict | None:
    """Does TCP ``port`` actually carry SLCAN to a running CAN bus?

    Catches v6's silent-discard trap (a server with no bridge accepts the client
    and drops everything) before every ECU looks dead, and warns when the
    device's fixed bitrate differs from ``bitrate`` (v6 ignores SLCAN's ``S``
    command unless ``can_manager.baud`` is ``auto``). None when the device
    doesn't describe its sockets.
    """
    body = get_json(host, "/api/sockets", timeout=timeout)
    if body is None:
        return None
    where = f"WiCAN firmware v6 at {host}"
    server = next(
        (s for s in _rows(body, "servers") if s.get("port") == port and s.get("proto") == "tcp"),
        None,
    )
    if server is None:
        return Verdict(
            "refuse", f"{where} serves nothing on TCP {port}, so there is no SLCAN.\n{_SLCAN_SETUP}"
        )
    name = server.get("name") or "?"
    if server.get("enabled") is not True or server.get("up") is False:
        state = "disabled" if server.get("enabled") is not True else "configured but not running"
        return Verdict(
            "refuse", f"{where}: the TCP {port} server ({name}) is {state}.\n{_SLCAN_SETUP}"
        )
    bridge = server.get("bridge")
    if not isinstance(bridge, str):
        return Verdict(
            "refuse",
            f"{where}: nothing is bridged to the TCP {port} server ({name}), so it accepts the "
            f"connection and silently discards everything — every ECU would look dead.\n"
            f"{_SLCAN_SETUP}",
        )
    row = _bridge_row(host, bridge, timeout)
    if row is not None and row.get("translator") != "slcan":
        peer = _peer(row, name)
        hint = (
            "\n  • that is the ELM327 server: use --transport elm327-tcp for it"
            if peer == "obd"
            else ""
        )
        return Verdict(
            "refuse",
            f"{where}: TCP {port} ({name}) is bridged to '{peer}' with translator "
            f"'{row.get('translator')}', not 'slcan'.{hint}\n{_SLCAN_SETUP}",
        )
    can = get_json(host, "/api/can", timeout=timeout)
    if can is not None and can.get("enabled") is False:
        return Verdict(
            "refuse",
            f"{where}: can_manager is disabled, so the CAN transceiver stays in standby.\n"
            f"{_SLCAN_SETUP}",
        )
    if can is not None and can.get("baud_auto") is False and bitrate:
        kbps = can.get("baud_kbps")
        if isinstance(kbps, int) and kbps * 1000 != bitrate:
            return Verdict(
                "warn",
                f"{where}: its CAN bus is fixed at {kbps} kbit/s but canair wants "
                f"{bitrate // 1000} kbit/s, and v6 ignores SLCAN's bitrate command.\n"
                "  • set can_manager.baud on the device to the car's bitrate (or 'auto')",
            )
    return Verdict(
        "note",
        f"{where}: TCP {port} ({name}) is bridged to the CAN bus. On v6 the OBD chip shares "
        "that bus, and SLCAN's bitrate and listen-only commands are ignored.",
    )


def listen_only_verdict(host: str, *, timeout: float | None = None) -> Verdict | None:
    """A passive sniff is possible on v6 only when ``can_manager.silent`` is on."""
    can = get_json(host, "/api/can", timeout=timeout)
    if can is None or can.get("silent") is not True:
        return None
    return Verdict(
        "note",
        "WiCAN firmware v6: can_manager.silent is on, so the device is passive on the bus "
        "(v6 ignores the SLCAN listen-only command itself).",
    )


def device_defaults(host: str, *, timeout: float | None = None) -> dict:
    """The SLCAN port and bus bitrate a v6 device is set up for, in 4.x config keys.

    Shaped like 4.x's ``/load_config`` (``port``, ``can_datarate``) so
    :meth:`TransportConfig.resolve_device_defaults` reads either generation the
    same way. Empty when nothing is bridged through the ``slcan`` translator.
    """
    out: dict = {}
    bridges = _rows(get_json(host, "/api/bridges", timeout=timeout), "bridges")
    slcan = {
        _peer(r, "can") for r in bridges if r.get("translator") == "slcan" and r.get("enabled")
    }
    for server in _rows(get_json(host, "/api/sockets", timeout=timeout), "servers"):
        if server.get("name") in slcan and server.get("enabled") and server.get("proto") == "tcp":
            out["port"] = server.get("port")
            break
    can = get_json(host, "/api/can", timeout=timeout) or {}
    if can.get("baud_auto") is False and isinstance(can.get("baud_kbps"), int):
        out["can_datarate"] = f"{can['baud_kbps']}K"
    return out


# --- the one call surfaces make -------------------------------------------------

_LIVE_SURFACES: frozenset[Surface] = frozenset({"wican-ws", "slcan-tcp", "sniff-listen-only"})


def assess(
    info: FirmwareInfo,
    surface: Surface,
    host: str,
    *,
    port: int | None = None,
    bitrate: int | None = None,
    timeout: float | None = None,
) -> Verdict:
    """The static verdict for ``surface``, refined by the device's live settings on v6.

    Only an ungated v6 device is asked anything: a 4.x or unknown device, or a
    v6 behind its password / untrusted-network gate, gets the static verdict.
    """
    static = compatibility(info, surface)
    if info.generation != "v6" or info.gated is not None or surface not in _LIVE_SURFACES:
        return static
    if surface == "wican-ws":
        return ws_obd_verdict(host, timeout=timeout) or static
    if surface == "slcan-tcp":
        if port is None:
            return static
        return slcan_verdict(host, port, bitrate, timeout=timeout) or static
    return listen_only_verdict(host, timeout=timeout) or static
