"""``canair status`` — show the configured transport, device state, and profile.

Read-only and side-effect-free: it never connects a session, never changes the
device mode. Designed to answer "what am I talking to, in what mode, and is it
reachable/usable?" at a glance, with actionable hints and Unix-style exit codes.
Also reports the running canair version — which, when canair is running from a
git checkout rather than an installed release, names that checkout's branch and
short commit (``1.15.0+main.343b244``) — and, when the WiCAN HTTP API answers,
the device's firmware generation, version and hardware version. On WiCAN
firmware v6 it also reports battery, sleep and OBD-chip state, checks the
selected transport's path through the device's settings, and exits 2 when that
transport cannot work there.

It reports the device a live command would actually use: like ``read``/
``monitor`` it resolves the whole candidate list and runs the same connect-time
fallback probe, so a setup where the selected device is down but another
configured one answers reads as ready here too (``--no-fallback`` to pin the
selected device).
"""

from __future__ import annotations

import argparse
import contextlib
import socket

from .render import _render

NAME = "status"
ALIASES = ["st"]

# Exit codes.
_OK = 0
_UNREACHABLE = 1
_MISCONFIGURED = 2

# Floor for the read-only reachability probes (s). The effective timeout is the
# larger of this and the configured `transport.connect_timeout`, so a user who
# raised it for a slow VPN/cellular link isn't told "unreachable" by a probe
# stricter than the one the live commands use.
_PROBE_TIMEOUT = 4.0


def _valid_transports():
    from ...transport.config import VALID_TRANSPORTS

    return VALID_TRANSPORTS


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        aliases=ALIASES,
        help="Show the configured transport, device mode, and reachability",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair status                       # what am I talking to, in what mode, is it up?
  canair status --wican vpn           # check the device at the 'vpn' address
  canair status --no-fallback         # only the selected device, no fallback probe
  canair status --json                # machine-readable (for scripts/CI)

exit codes: 0 = reachable & usable, 1 = unreachable, 2 = misconfigured.
""",
    )
    parser.add_argument(
        "--transport",
        choices=_valid_transports(),
        default=None,
        help="Override the configured transport type",
    )
    parser.add_argument("--wican", default=None, help="Override device host (alias or IP)")
    parser.add_argument(
        "--no-fallback",
        dest="no_fallback",
        action="store_true",
        help="Report only the selected device — don't fall back to the other "
        "configured devices when it's unreachable (see config transport.fallback). "
        "Without it, status reports the device a live command would use.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=run)
    return parser


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _load_device_config(host: str, timeout: float) -> dict | None:
    """Best-effort GET /load_config; None if the WiCAN HTTP API isn't reachable."""
    try:
        from ...wican_mode import load_config

        return load_config(f"http://{host}", timeout=timeout)
    except Exception:
        return None


def _device_status(host: str, timeout: float) -> dict | None:
    try:
        import requests

        r = requests.get(f"http://{host}/check_status", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _gather(args) -> dict:
    """Collect everything status reports into a plain dict (also used for --json)."""
    from ...build_info import full_version
    from ...config import fallback_settings
    from ...transport import (
        TransportError,
        resolve_transport_candidates,
        select_reachable_transport,
    )

    info: dict = {"exit": _OK, "warnings": [], "errors": [], "canair_version": full_version()}

    try:
        candidates = resolve_transport_candidates(args)
    except TransportError as e:
        info["errors"].append(str(e))
        info["exit"] = _MISCONFIGURED
        return info

    # Probe the whole candidate list exactly as a live command does, so status
    # reports the device `read`/`monitor` would actually connect to instead of
    # calling the setup unreachable because the *selected* device is down.
    _, connect_timeout, _ = fallback_settings()
    probe_timeout = max(_PROBE_TIMEOUT, connect_timeout)
    notes: list[str] = []
    t = select_reachable_transport(candidates, connect_timeout=connect_timeout, notice=notes.append)
    info["notes"] = notes

    info["transport"] = {
        "type": t.type,
        "host": t.host,
        "port": t.port,
        "bitrate": t.bitrate,
        "family": "raw" if t.is_raw else "elm",
        "summary": t.summary,
        "selected": candidates[0].describe(),
        "candidates": [c.describe() for c in candidates],
        "fell_back": t is not candidates[0],
    }

    # Active vehicle profile.
    profile_bitrate: int | None = None
    try:
        from ...profile import active

        prof = active()
        info["profile"] = {"name": prof.name, "root": str(prof.root)}
        with contextlib.suppress(TypeError, ValueError):
            profile_bitrate = (
                int(prof.meta["can_bitrate"]) if prof.meta.get("can_bitrate") else None
            )
    except Exception as e:
        info["profile"] = None
        info["warnings"].append(f"no active profile: {e}")

    host = t.host
    if not host:
        info["errors"].append("transport has no host configured")
        info["exit"] = _MISCONFIGURED
        return info

    # Firmware generation. A WiCAN transport is always probed here (status is the
    # diagnostic); elm327-tcp only when the check is on, since it is usually a
    # clone with no HTTP server. The verdict is enforced only when the check is
    # on, matching what a live command would do — including, on v6, reading the
    # device's settings to confirm the selected transport's data path.
    from ...config import firmware_check
    from ...wican_firmware import OK, UNKNOWN, probe
    from ...wican_v6 import assess, device_status

    check_on = firmware_check()
    fw = probe(host, timeout=probe_timeout) if t.is_wican_http or check_on else UNKNOWN
    v6 = fw.generation == "v6"
    # v6 has no /load_config: the SLCAN port and bitrate come from its settings.
    v6_port, v6_bitrate = (
        t.resolve_device_defaults(profile_bitrate) if v6 and t.is_raw else (None, None)
    )
    verdict = (
        assess(fw, t.type, host, port=v6_port, bitrate=v6_bitrate, timeout=probe_timeout)
        if check_on
        else OK
    )
    info["firmware"] = {
        **fw.as_dict(),
        "label": fw.label(),
        "verdict": verdict.level,
        "message": verdict.message or None,
        "checked": check_on,
    }

    # WiCAN 4.x HTTP config API — queryable only for WiCAN-backed transports
    # (slcan-tcp / wican-ws) on 4.x. A direct ELM327 adapter (elm327-tcp) has no
    # such API, and v6 removed these routes, so skip the probe and report
    # device: None.
    cfg = _load_device_config(host, probe_timeout) if t.is_wican_http and not v6 else None
    st = _device_status(host, probe_timeout) if cfg is not None else None
    device_protocol = str(cfg.get("protocol")) if cfg else None
    dev_port = None
    if cfg and cfg.get("port"):
        try:
            dev_port = int(cfg["port"])
        except (TypeError, ValueError):
            dev_port = None

    info["device"] = (
        {
            "http_reachable": cfg is not None,
            "protocol": device_protocol,
            "socket_port": dev_port,
            "sleep": cfg.get("sleep_status") if cfg else None,
            "sleep_volt": cfg.get("sleep_volt") if cfg else None,
            "battery": (st or {}).get("batt_voltage") if st else None,
            "ip": (st or {}).get("sta_ip") if st else None,
            "fw_version": (st or {}).get("fw_version") if st else None,
            "hw_version": (st or {}).get("hw_version") if st else None,
            "git_version": (st or {}).get("git_version") if st else None,
        }
        if t.is_wican_http and not v6
        else None
    )
    # v6 reports power and OBD-chip state on its own routes (none behind a gate).
    if v6 and fw.gated is None:
        info["device_v6"] = device_status(host, timeout=probe_timeout).as_dict()
        if v6_port is not None:
            dev_port = v6_port

    if verdict.level == "warn":
        info["warnings"].append(verdict.message)
    elif verdict.level == "note":
        notes.append(verdict.message)

    # Transport usability check.
    if verdict.refused:
        info["errors"].append(verdict.message)
        info["exit"] = _MISCONFIGURED
        info["transport"]["usable"] = False
    elif t.is_wican_http and t.is_elm:
        # The ELM327 WebSocket terminal lives on the HTTP port and, on 4.x, works
        # in any mode. A v6 answer to the firmware probe proves that port is up,
        # and assess() has already checked its /ws/obd channel.
        http_ok = cfg is not None or v6
        if not http_ok:
            info["errors"].append(f"WiCAN HTTP not reachable at {host} (is it online / on VPN?)")
            info["exit"] = _UNREACHABLE
        info["transport"]["usable"] = http_ok
    elif t.is_elm:
        # Direct ELM327 adapter (elm327-tcp): no HTTP API — usability is just
        # whether the ELM socket accepts a TCP connection.
        from ...transport.config import DEFAULT_ELM327_TCP_PORT

        elm_port = t.port or DEFAULT_ELM327_TCP_PORT
        info["transport"]["port"] = elm_port
        elm_ok = _tcp_open(host, elm_port, probe_timeout)
        info["transport"]["usable"] = elm_ok
        if not elm_ok:
            info["errors"].append(
                f"ELM327 adapter {host}:{elm_port} not reachable "
                f"(powered on? on its Wi-Fi? for offline testing: elm -n {elm_port})"
            )
            info["exit"] = _UNREACHABLE
    else:
        raw_port = t.port or dev_port or 3333
        info["transport"]["port"] = raw_port
        raw_ok = _tcp_open(host, raw_port, probe_timeout)
        mismatch = bool(device_protocol and device_protocol != "slcan")
        # Usable only if the port is open AND the device is actually serving SLCAN
        # (an open port in the wrong mode won't speak SLCAN).
        info["transport"]["usable"] = raw_ok and not mismatch
        if mismatch:
            info["warnings"].append(
                f"device is in '{device_protocol}' mode but the raw transport needs 'slcan' — "
                f"set it with: canair wican mode set slcan"
            )
            info["exit"] = _MISCONFIGURED
        if not raw_ok:
            if v6:
                hint = " (firmware v6 serves SLCAN only once set up — see above)"
            elif device_protocol == "slcan":
                hint = ""
            else:
                hint = " (likely because it's not in 'slcan' mode)"
            info["errors"].append(f"SLCAN port {host}:{raw_port} not reachable{hint}")
            if info["exit"] == _OK:
                info["exit"] = _UNREACHABLE

    # A liveness probe that rejected the primary and still handed it back means
    # every candidate was down; say so, or the verdict reads as if only the
    # selected device had been tried.
    if info["exit"] == _UNREACHABLE and notes and t is candidates[0]:
        others = ", ".join(c.describe() for c in candidates[1:])
        info["errors"].append(f"no other configured device answered either (tried {others})")

    # Connection mutex (local-only): who, if anyone, currently owns the device
    # connection. Read-only — it never takes or clears the lock.
    try:
        from ...lock import lock_state

        info["lock"] = lock_state().as_dict()
    except Exception:
        info["lock"] = None

    # Cached update notice (local-only; no network on the status hot path).
    try:
        from ...update_check import pending_notice

        info["update_notice"] = pending_notice()
    except Exception:
        info["update_notice"] = None

    # Install context (local-only): warn when a bare `canair` would run a
    # different version than `uv run canair` in the source clone.
    try:
        from ...install_context import describe as describe_install
        from ...install_context import source_clone

        install = describe_install(source_clone())
        info["install"] = install
        if install["out_of_sync"]:
            info["warnings"].append(
                f"installed `canair` ({install['tool_version']}) is out of sync with the "
                f"source clone ({install['clone_version']}) — run `canair update` to reinstall"
            )
        elif install["running_origin"] == "uv-tool":
            info["warnings"].append(
                "running the `uv tool install` copy — its bundled profiles are a frozen "
                "snapshot; edits to the repo's profiles/ won't appear until reinstall. Use "
                "`uv run canair` from the repo root for live profile edits"
            )
    except Exception:
        info["install"] = None

    return info


def run(args) -> int:
    info = _gather(args)
    if args.json:
        import json

        print(json.dumps(info, indent=2))
    else:
        _render(info)
    return info["exit"]
