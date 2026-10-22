"""Lightweight HTTP helpers for the WiCAN 4.x device API.

These functions provide basic access to the WiCAN REST endpoints
without pulling in the full CLI logic. The fetches raise on failure rather than
exiting, so a caller deep inside a live session (the sleep banner) can treat a
missing or unreachable API as "no data" instead of having the session killed;
:func:`reboot_device` returns its outcome instead. Firmware v6 serves none of
these routes (see :mod:`canlib.wican_firmware`).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Literal

import requests

if TYPE_CHECKING:
    from canlib.transport.config import TransportConfig


def resolve_wican_url(wican: str | None = None) -> str:
    """Base HTTP URL for a ``--wican`` value, resolved the way live commands do.

    A configured device alias maps to its host; anything else (an IP, a
    hostname, ``host:port``) is taken as the host itself, never swapped for
    another device; an ``http(s)://`` URL passes through. Only when no value is
    given does the selection fall back to ``transport.host``, then the
    ``default_wican`` device.
    """
    from canlib.config import load_config, wican_devices

    if wican and wican.startswith(("http://", "https://")):
        return wican.rstrip("/")
    devices, default_alias = wican_devices()
    if wican:
        host = devices[wican].host if wican in devices else wican
    else:
        raw_block = load_config().get("transport")
        block_host = raw_block.get("host") if isinstance(raw_block, dict) else None
        if block_host:
            host = str(block_host)
        else:
            host = devices[default_alias].host if default_alias in devices else default_alias
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    return f"http://{host}"


def get_config(base_url: str, timeout: float = 10) -> dict:
    """GET /load_config and return parsed JSON (raises on any failure)."""
    resp = requests.get(f"{base_url}/load_config", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_status(base_url: str, timeout: float = 10) -> dict:
    """GET /check_status and return parsed JSON (raises on any failure).

    The reply carries plaintext Wi-Fi and MQTT passwords: read the fields you
    need and never log, print or persist the rest.
    """
    resp = requests.get(f"{base_url}/check_status", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def store_config(base_url: str, config: dict, timeout: int = 10) -> None:
    """POST a full config JSON to /store_config.

    The device writes the body verbatim to ``config.json`` and reboots ~2s
    later. Always send a *complete* config (load → mutate → store); a partial
    body is rejected at next boot and reset to defaults. Raises on HTTP failure
    (callers decide how to react, so restore-on-exit paths can warn and
    continue).
    """
    import json

    url = f"{base_url}/store_config"
    resp = requests.post(
        url,
        data=json.dumps(config),
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()


RebootOutcome = Literal["rebooted", "skipped", "failed"]


def reboot_device(host: str, *, timeout: float = 5) -> RebootOutcome:
    """Reboot a WiCAN 4.x through ``POST /system_reboot`` (``host`` or base URL).

    Reports what happened, since callers word it differently: ``skipped`` on
    firmware v6, which has no such route and resumes AutoPID by itself (the
    policy note is printed), ``failed`` when the request errored or the device
    refused it, ``rebooted`` only when the device accepted it.
    """
    from .wican_firmware import check, print_verdict

    base = host.rstrip("/") if host.startswith(("http://", "https://")) else f"http://{host}"
    verdict = check(base, "reboot")
    if verdict.level == "skip":
        print_verdict(verdict)
        return "skipped"
    url = f"{base}/system_reboot"
    try:
        resp = requests.post(url, data="reboot", timeout=timeout)
    except requests.RequestException as e:
        print(f"  FAILED to reboot: {e}", file=sys.stderr)
        return "failed"
    if not resp.ok:
        print(f"  FAILED to reboot: HTTP {resp.status_code} from {url}", file=sys.stderr)
        return "failed"
    print(f"Rebooting WiCAN... ({resp.status_code})")
    return "rebooted"


def reboot_after_session(transport: TransportConfig) -> RebootOutcome:
    """``--reboot`` once a live session ends, on whichever transport it ran.

    The reboot is a plain HTTP request, so it works the same after a raw
    ``slcan-tcp`` session as after a ``wican-ws`` one. ``elm327-tcp`` reaches no
    WiCAN HTTP API to reboot through, so the flag is reported as skipped rather
    than silently dropped.
    """
    if not transport.host or not transport.is_wican_http:
        print(
            f"note: skipping --reboot: the {transport.type} transport has no WiCAN HTTP API "
            "to reboot the device through",
            file=sys.stderr,
        )
        return "skipped"
    return reboot_device(transport.host)
