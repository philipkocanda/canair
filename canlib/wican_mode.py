"""WiCAN protocol-mode switching (ELM327 ⇄ raw SLCAN, etc.).

The WiCAN runs one ``protocol`` at a time, chosen at boot from its config. To
use a raw-CAN backend we must rewrite the device config and reboot, then restore
the previous protocol (usually ``elm327``) when done. Switching costs a reboot
(~2-5 s) and, while in a raw mode, the device stops serving ELM327/AutoPID (so
Home Assistant goes quiet) — hence the consent prompt and the guaranteed
restore-on-exit guard.

Mode changes go through the HTTP config API:
    GET  /load_config   -> current config dict
    POST /store_config  -> write full config verbatim; device reboots ~2s later
"""

from __future__ import annotations

import socket
import time

import requests

from .wican_api import resolve_wican_url, store_config


class ModeError(RuntimeError):
    """Raised when a protocol switch cannot be completed."""


def load_config(base_url: str, timeout: float = 10.0) -> dict:
    """GET /load_config as a dict (raises on failure; does not sys.exit)."""
    resp = requests.get(f"{base_url}/load_config", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def current_protocol(base_url: str, timeout: float = 10.0) -> str:
    """Return the device's active ``protocol`` string."""
    return str(load_config(base_url, timeout=timeout).get("protocol", "")).lower()


def require_protocol(
    wican: str, expected: str, *, transport_name: str | None = None, timeout: float = 6.0
) -> None:
    """Raise :class:`ModeError` if the WiCAN is reachable but not in ``expected``.

    No-op when the HTTP config API isn't reachable (a non-WiCAN gateway, or a
    device that's offline) — in that case the transport connect itself will
    surface the problem. This never switches the mode (explicit-only policy).
    """
    base_url = resolve_wican_url(wican)
    try:
        proto = current_protocol(base_url, timeout=timeout)
    except Exception:
        return
    if proto and proto != expected:
        tname = transport_name or f"the '{expected}'"
        msg = (
            f"canair is configured for the {tname} transport, so the WiCAN also "
            f"needs to be in '{expected}' mode — but it's currently in '{proto}'.\n"
            f"  • put the device in '{expected}':  canair wican mode set {expected}\n"
            f"    (restore afterwards with:         canair wican mode set {proto})"
        )
        if expected == "slcan":
            msg += (
                "\n  • or keep the device as-is and use the ELM327 terminal, which works "
                "in any device mode:\n"
                "    pass --transport wican-ws, or set transport.type: wican-ws in your config"
            )
        raise ModeError(msg)


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def require_ws_reachable(host: str, *, port: int = 80, timeout: float = 4.0) -> None:
    """Raise :class:`ModeError` if the WiCAN's WebSocket/HTTP port doesn't respond.

    The ``wican-ws`` transport reaches the ELM327 terminal over ``ws://host/ws``,
    which lives on the device's HTTP port. When that port is closed or the host
    is silent (wrong IP, VPN down, device asleep, or a protocol that isn't
    serving the WebSocket), ``websockets.connect()`` either fails deep in the
    stack or hangs out its full open-timeout before raising a bare
    ``TimeoutError`` — neither tells the user what's actually wrong. This fast
    TCP pre-check fails up front with an actionable alert instead.

    A no-op when the port answers (the ``/ws`` terminal works in any device
    mode, so an open port is good enough — we don't gate on the protocol here).
    """
    if _tcp_open(host, port, timeout):
        return
    raise ModeError(
        f"can't reach the WiCAN's ELM327 WebSocket terminal at ws://{host}/ws — "
        f"TCP port {port} didn't respond within {timeout:.0f}s.\n"
        f"The 'wican-ws' transport needs the device's HTTP/WebSocket server. "
        f"Likely causes:\n"
        f"  • the device is powered off, asleep, or still booting\n"
        f"  • the host is wrong — trying '{host}' (check --wican / transport.host in config)\n"
        f"  • you're not on the same network as the device (VPN down? wrong Wi-Fi?)\n"
        f"  • the device is in a mode that isn't serving the WebSocket (e.g. raw "
        f"'slcan') — the ELM327 terminal needs 'elm327'/'auto_pid'\n"
        f"  • diagnose:      canair status --wican {host}\n"
        f"  • switch mode:   canair wican mode set elm327"
    )


def wait_until_ready(host: str, port: int = 80, timeout: float = 45.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection (device back up).

    Returns True once reachable, False on timeout. Used to wait out the reboot
    that follows /store_config.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _tcp_open(host, port, timeout=2.0):
            return True
        time.sleep(1.0)
    return False


def _host_of(base_url: str) -> str:
    """Extract the bare host from an http(s)://host[:port] base URL."""
    return base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def set_protocol(
    base_url: str,
    protocol: str,
    *,
    extra: dict | None = None,
    wait: bool = True,
    reboot_grace: float = 2.5,
) -> str:
    """Switch the device to ``protocol`` (+ optional extra config keys).

    Loads the full current config, mutates it, POSTs it back (device reboots),
    then optionally waits for the device to come back up. Returns the *previous*
    protocol so callers can restore it. No-op (returns current) if already set.
    """
    cfg = load_config(base_url)
    previous = str(cfg.get("protocol", "")).lower()
    if previous == protocol and not extra:
        return previous

    cfg["protocol"] = protocol
    if extra:
        cfg.update(extra)
    store_config(base_url, cfg)

    if wait:
        host = _host_of(base_url)
        time.sleep(reboot_grace)  # let it actually drop before we poll
        if not wait_until_ready(host):
            raise ModeError(f"WiCAN did not come back online after switching to '{protocol}'.")
    return previous
