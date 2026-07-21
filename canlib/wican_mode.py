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
import sys
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


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


@contextlib.contextmanager
def protocol_mode(
    wican: str,
    protocol: str,
    *,
    assume_yes: bool = False,
    restore: bool = True,
):
    """Context manager: ensure the device runs ``protocol`` for the block.

    If a switch is needed, asks for consent (unless ``assume_yes``), waits out
    the reboot, yields, then restores the previous protocol on exit (best-effort,
    even on error/Ctrl+C). If already in ``protocol``, does nothing and does not
    reboot on exit.
    """
    base_url = resolve_wican_url(wican)
    previous = current_protocol(base_url)

    if previous == protocol:
        yield base_url  # already there — nothing to switch or restore
        return

    prompt = (
        f"Switch WiCAN from '{previous}' to '{protocol}' mode? This reboots the "
        f"device (~5s) and pauses ELM327/AutoPID (Home Assistant) until restored."
    )
    if not _confirm(prompt, assume_yes):
        raise ModeError(
            f"Declined mode switch to '{protocol}'. Re-run with --yes to auto-confirm, "
            f"or set the device to '{protocol}' manually."
        )

    print(f"  Switching WiCAN to '{protocol}' mode (rebooting)...")
    set_protocol(base_url, protocol)
    print(f"  WiCAN is up in '{protocol}' mode.")
    try:
        yield base_url
    finally:
        if restore:
            print(f"  Restoring WiCAN to '{previous}' mode (rebooting)...")
            try:
                set_protocol(base_url, previous, wait=False)
            except Exception as e:  # restore is best-effort — never mask the body
                print(
                    f"  WARNING: failed to restore '{previous}' mode: {e}\n"
                    f"  The device may still be in '{protocol}' mode — set it back "
                    f"via the web UI if needed.",
                    file=sys.stderr,
                )
