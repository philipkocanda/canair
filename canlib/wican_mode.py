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

import contextlib
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


def wait_until_ready(host: str, port: int = 80, timeout: float = 45.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection (device back up).

    Returns True once reachable, False on timeout. Used to wait out the reboot
    that follows /store_config.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection((host, port), timeout=2.0):
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
