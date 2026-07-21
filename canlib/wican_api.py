"""Lightweight HTTP helpers for WiCAN device API.

These functions provide basic GET access to the WiCAN REST endpoints
without pulling in the full CLI logic.
"""

import sys

import requests

from canlib.constants import WICAN_ADDRESSES


def resolve_wican_url(wican: str) -> str:
    """Resolve a named address or raw host to a base HTTP URL."""
    if wican in WICAN_ADDRESSES:
        addr = WICAN_ADDRESSES[wican]
    else:
        addr = wican
    if not addr.startswith("http"):
        return f"http://{addr}"
    return addr


def get_config(base_url: str, timeout: int = 10) -> dict:
    """GET /load_config and return parsed JSON."""
    url = f"{base_url}/load_config"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        print("  Is the device reachable? Try: --wican vpn", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"ERROR: Timeout connecting to {base_url}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)


def get_status(base_url: str, timeout: int = 10) -> dict:
    """GET /check_status and return parsed JSON."""
    url = f"{base_url}/check_status"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to WiCAN at {base_url}", file=sys.stderr)
        print("  Is the device reachable? Try: --wican vpn", file=sys.stderr)
        sys.exit(1)
    except requests.Timeout:
        print(f"ERROR: Timeout connecting to {base_url}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to get status: {e}", file=sys.stderr)
        sys.exit(1)


def store_config(base_url: str, config: dict, timeout: int = 10) -> None:
    """POST a full config JSON to /store_config.

    The device writes the body verbatim to ``config.json`` and reboots ~2s
    later. Always send a *complete* config (load → mutate → store); a partial
    body is rejected at next boot and reset to defaults. Raises on HTTP failure
    (callers decide how to react — unlike get_config, this does not sys.exit,
    so restore-on-exit paths can warn and continue).
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
