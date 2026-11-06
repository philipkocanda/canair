"""Talking to the device's AutoPID store: download and upload."""

from __future__ import annotations

import sys
import time

import requests

from canair.device.wican.api import reboot_device
from canair.render import messages

from .common import WICAN_TIMEOUT


def download_profile(base_url: str) -> dict | None:
    """Download current vehicle profile from WiCAN device (raw device format)."""
    url = f"{base_url}/load_auto_pid_car_data"
    try:
        resp = requests.get(url, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        n_entries = len(data.get("cars", [{}])[0].get("pids", []))
        print(f"  Downloaded from {url} ({n_entries} entries)", file=sys.stderr)
        return data
    except requests.RequestException as e:
        messages.error(f"failed to download from {url}: {e}")
        return None


def upload_profile(base_url: str, device_payload: dict, reboot: bool = False) -> None:
    """Upload vehicle profile to WiCAN device via POST /store_car_data.

    Expects device_payload in the firmware's format:
      {"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}
    with parameters as array-of-objects. Use to_device_format() to convert.
    """
    n_pids = len(device_payload.get("cars", [{}])[0].get("pids", []))
    n_params = sum(
        len(p.get("parameters", [])) for p in device_payload.get("cars", [{}])[0].get("pids", [])
    )

    url = f"{base_url}/store_car_data"
    try:
        resp = requests.post(url, json=device_payload, timeout=WICAN_TIMEOUT)
        resp.raise_for_status()
        print(f"  Uploaded to {url} — {resp.status_code} ({n_pids} PIDs, {n_params} signals)")
    except requests.RequestException as e:
        messages.error(f"failed to upload to {url}: {e}")
        sys.exit(1)

    if reboot:
        time.sleep(0.3)
        reboot_device(base_url, timeout=WICAN_TIMEOUT)
