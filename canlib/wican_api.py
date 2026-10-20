"""Lightweight HTTP helpers for the WiCAN 4.x device API.

These functions provide basic GET access to the WiCAN REST endpoints
without pulling in the full CLI logic. They raise on failure rather than
exiting, so a caller deep inside a live session (the sleep banner) can treat a
missing or unreachable API as "no data" instead of having the session killed.
Firmware v6 serves none of these routes (see :mod:`canlib.wican_firmware`).
"""

import requests


def resolve_wican_url(wican: str) -> str:
    """Resolve a named address or raw host to a base HTTP URL."""
    from canlib.config import wican_addresses

    addresses = wican_addresses()
    if wican in addresses:
        addr = addresses[wican]
    else:
        addr = wican
    if not addr.startswith("http"):
        return f"http://{addr}"
    return addr


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
