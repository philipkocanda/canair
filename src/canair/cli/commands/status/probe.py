"""The read-only probes of ``canair status``: the device's own status and config."""

from __future__ import annotations


def load_device_config(host: str, timeout: float) -> dict | None:
    """Best-effort GET /load_config; None if the WiCAN HTTP API isn't reachable."""
    try:
        from canair.device.wican.mode import load_config

        return load_config(f"http://{host}", timeout=timeout)
    except Exception:
        return None


def device_status(host: str, timeout: float) -> dict | None:
    try:
        import requests

        r = requests.get(f"http://{host}/check_status", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None
