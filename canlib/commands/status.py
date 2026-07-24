"""``canair status`` — show the configured transport, device state, and profile.

Read-only and side-effect-free: it never connects a session, never changes the
device mode. Designed to answer "what am I talking to, in what mode, and is it
reachable/usable?" at a glance, with actionable hints and Unix-style exit codes.
"""

from __future__ import annotations

import argparse
import socket

NAME = "status"

# Exit codes.
_OK = 0
_UNREACHABLE = 1
_MISCONFIGURED = 2

# Per-probe network timeout (s) for the read-only reachability checks.
_PROBE_TIMEOUT = 4.0


def _valid_transports():
    from ..transport.config import VALID_TRANSPORTS

    return VALID_TRANSPORTS


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Show the configured transport, device mode, and reachability",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair status                       # what am I talking to, in what mode, is it up?
  canair status --wican vpn           # check the device at the 'vpn' address
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
        from ..wican_mode import load_config

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
    from ..transport import TransportError, resolve_transport

    info: dict = {"exit": _OK, "warnings": [], "errors": []}

    try:
        t = resolve_transport(args)
    except TransportError as e:
        info["errors"].append(str(e))
        info["exit"] = _MISCONFIGURED
        return info

    info["transport"] = {
        "type": t.type,
        "host": t.host,
        "port": t.port,
        "bitrate": t.bitrate,
        "family": "raw" if t.is_raw else "elm",
        "summary": t.summary,
    }

    # Active vehicle profile.
    try:
        from ..profile import active

        prof = active()
        info["profile"] = {"name": prof.name, "root": str(prof.root)}
    except Exception as e:
        info["profile"] = None
        info["warnings"].append(f"no active profile: {e}")

    host = t.host
    if not host:
        info["errors"].append("transport has no host configured")
        info["exit"] = _MISCONFIGURED
        return info

    # WiCAN HTTP config API (works for both current transports when the device
    # is a WiCAN).
    cfg = _load_device_config(host, _PROBE_TIMEOUT)
    st = _device_status(host, _PROBE_TIMEOUT) if cfg is not None else None
    device_protocol = str(cfg.get("protocol")) if cfg else None
    dev_port = None
    if cfg and cfg.get("port"):
        try:
            dev_port = int(cfg["port"])
        except (TypeError, ValueError):
            dev_port = None

    info["device"] = {
        "http_reachable": cfg is not None,
        "protocol": device_protocol,
        "socket_port": dev_port,
        "sleep": cfg.get("sleep_status") if cfg else None,
        "sleep_volt": cfg.get("sleep_volt") if cfg else None,
        "battery": (st or {}).get("batt_voltage") if st else None,
        "ip": (st or {}).get("sta_ip") if st else None,
    }

    # Transport usability check.
    if t.is_elm:
        # The /ws ELM327 terminal lives on the HTTP port and works in any mode.
        if cfg is None:
            info["errors"].append(f"WiCAN HTTP not reachable at {host} (is it online / on VPN?)")
            info["exit"] = _UNREACHABLE
        info["transport"]["usable"] = cfg is not None
    else:
        raw_port = t.port or dev_port or 3333
        info["transport"]["port"] = raw_port
        raw_ok = _tcp_open(host, raw_port, _PROBE_TIMEOUT)
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
            hint = (
                "" if device_protocol == "slcan" else " (likely because it's not in 'slcan' mode)"
            )
            info["errors"].append(f"SLCAN port {host}:{raw_port} not reachable{hint}")
            if info["exit"] == _OK:
                info["exit"] = _UNREACHABLE

    return info


def _render(info: dict) -> None:
    from rich.console import Console

    c = Console()
    t = info.get("transport")
    if t:
        loc = t.get("host") or "?"
        if t.get("port"):
            loc = f"{loc}:{t['port']}"
        usable = t.get("usable")
        mark = "[green]✓[/green]" if usable else "[red]✗[/red]"
        c.print(f"\n  [bold]Transport[/bold]  {t['type']}  [dim]({loc})[/dim]  {mark}")
        summary = t.get("summary")
        if summary:
            c.print(f"             [dim]{summary}[/dim]")
        if t.get("bitrate"):
            c.print(f"             [dim]bitrate:[/dim]  {t['bitrate']}")

    p = info.get("profile")
    if p:
        c.print(f"  [bold]Profile[/bold]    {p['name']}  [dim]{p['root']}[/dim]")

    d = info.get("device")
    if d:
        if d["http_reachable"]:
            c.print("\n  [bold]WiCAN[/bold]")
            c.print(
                f"    protocol   [cyan]{d['protocol']}[/cyan]  [dim](socket port {d['socket_port']})[/dim]"
            )
            slp = d.get("sleep")
            if slp is not None:
                c.print(f"    sleep      {slp}  [dim](threshold {d.get('sleep_volt')}V)[/dim]")
            if d.get("battery") is not None:
                c.print(f"    battery    {d['battery']}")
            if d.get("ip"):
                c.print(f"    ip         {d['ip']}")
        else:
            c.print(
                "\n  [bold]WiCAN[/bold]   [yellow]HTTP config API not reachable[/yellow] "
                "[dim](non-WiCAN gateway, offline, or wrong host)[/dim]"
            )

    for w in info.get("warnings", []):
        c.print(f"  [yellow]⚠ {w}[/yellow]")
    for e in info.get("errors", []):
        c.print(f"  [red]✖ {e}[/red]")
    if not info.get("warnings") and not info.get("errors") and t and t.get("usable"):
        c.print("\n  [green]Ready.[/green]")
    c.print()


def run(args) -> int:
    info = _gather(args)
    if args.json:
        import json

        print(json.dumps(info, indent=2))
    else:
        _render(info)
    return info["exit"]
