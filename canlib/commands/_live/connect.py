"""Constructing and connecting the ELM327 terminal, and re-homing it mid-session.

The single build path shared by the initial connect and the monitor's mid-session
reconnect, so a re-home behaves exactly like the original connect. Also the WiCAN
sleep/battery banner, which is presentation and so stays in the command layer.
"""

from __future__ import annotations

import contextlib
import re

from canlib import (
    WiCANTerminal,
)
from canlib.transport.protocol import Terminal


def _print_sleep_banner(host: str, timeout: int = 5) -> None:
    """Fetch WiCAN sleep status and battery voltage, print a status line."""
    try:
        from canlib.wican_api import get_config, get_status

        base_url = f"http://{host}"
        status = get_status(base_url, timeout)
        config = get_config(base_url, timeout)
    except Exception:
        return  # silently skip if REST API unreachable

    batt = status.get("batt_voltage", "?")
    sleep_on = config.get("sleep_status", "disable") == "enable"
    sleep_volt = config.get("sleep_volt", "?")

    try:
        batt_f = float(str(batt).rstrip("V"))
        thresh_f = float(sleep_volt)
        margin = batt_f - thresh_f
    except (ValueError, TypeError):
        batt_f = None
        thresh_f = None
        margin = None

    from rich.console import Console

    console = Console()

    if sleep_on:
        sleep_str = f"[red]ON[/red] (threshold {sleep_volt}V)"
        if margin is not None and margin < 0.5:
            console.print(
                f"  [bold red]⚠ Sleep: ON  |  Battery: {batt}  |  Threshold: {sleep_volt}V"
                f"  — {margin:.2f}V above cutoff — may shut down soon![/bold red]"
            )
            return
    else:
        sleep_str = "[green]OFF[/green]"

    console.print(f"  [WiCAN] Sleep: {sleep_str}  |  Battery: {batt}")


async def connect_elm_terminal(transport, pids_data: dict, args) -> Terminal:
    """Construct, connect, and ELM-initialise the ELM327 terminal for ``transport``.

    Builds a :class:`WiCANTerminal` (WebSocket) for ``wican-ws`` or a
    :class:`~canlib.transport.elm327_terminal.Elm327TcpTerminal` (plain TCP) for
    ``elm327-tcp`` — the ELM init + ATST response-timeout + per-ECU budgets are
    identical because they live in the shared engine. The single build path
    shared by the initial connect (``async_main``) and the monitor's mid-session
    reconnect. Raises a transport error on failure (closing the partially-opened
    terminal first); the caller classifies it.
    """
    from canlib import config
    from canlib.quirks import HK_F1XX_MINUS_ONE, has_quirk
    from canlib.response_frames import seed_counts
    from canlib.timeouts import cli_timeout, ecu_timeouts_by_tx
    from canlib.transport import DEFAULT_ELM327_TCP_PORT, Elm327TcpTerminal

    init_string = pids_data.get("init")
    assert init_string, "profile init string must be validated by the caller"

    host = transport.host
    assert host is not None  # ELM transports always resolve a host

    _cli_timeout = cli_timeout(args)
    _ws_timeout = _cli_timeout if _cli_timeout is not None else 3.0
    hk = has_quirk(pids_data, HK_F1XX_MINUS_ONE)
    counts = config.expected_responses()
    # Counts previous sessions proved, so the first read of each PID is already
    # fast instead of paying the adapter's full ATST wait to re-learn it.
    seeds = seed_counts(pids_data) if counts else {}
    terminal: Terminal
    if transport.type == "elm327-tcp":
        port = transport.port or DEFAULT_ELM327_TCP_PORT
        terminal = Elm327TcpTerminal(
            host,
            port,
            timeout=_ws_timeout,
            verbose=args.verbose,
            unsafe=args.unsafe,
            hk_f1xx_offset=hk,
            expected_responses=counts,
            response_frames=seeds,
        )
        connecting = f"Connecting to ELM327 adapter at {host}:{port}..."
    else:
        terminal = WiCANTerminal(
            host=host,
            timeout=_ws_timeout,
            verbose=args.verbose,
            unsafe=args.unsafe,
            hk_f1xx_offset=hk,
            expected_responses=counts,
            response_frames=seeds,
        )
        connecting = f"Connecting to WiCAN at {host}..."
    # Per-ECU response budgets apply only when the user didn't force --timeout.
    if _cli_timeout is None:
        terminal.ecu_timeouts = ecu_timeouts_by_tx(pids_data)

    try:
        print(connecting)
        await terminal.connect()
        print("Connected. Initializing ELM327...")
        await terminal.init_elm(init_string)

        atst_cmd: str | None = None
        label = ""
        if args.elm_timeout is not None:
            atst_val = max(1, min(255, round(args.elm_timeout / 4.096)))
            atst_cmd = f"ATST{atst_val:02X}"
        elif pids_data.get("response_timeout_ms") is not None:
            atst_val = max(1, min(255, round(pids_data["response_timeout_ms"] / 4.096)))
            candidate = f"ATST{atst_val:02X}"
            # Skip if the init string already applied this exact ATST (avoid a
            # redundant round-trip on connect).
            _init_atst = re.search(r"ATST([0-9A-Fa-f]{2})", init_string)
            if not (_init_atst and f"ATST{_init_atst.group(1).upper()}" == candidate):
                atst_cmd = candidate
                label = ", from profile"
        if atst_cmd is not None:
            await terminal.send_command(atst_cmd)
            terminal.elm_timeout_cmd = atst_cmd
            actual_ms = int(atst_cmd[4:], 16) * 4.096
            print(f"  ELM327 timeout: {atst_cmd} ({actual_ms:.0f}ms{label})")

        print("Ready.")
        return terminal
    except BaseException:
        with contextlib.suppress(Exception):
            await terminal.close()
        raise


def build_elm_reconnector(args, pids_data: dict):
    """A :class:`MonitorReconnector` that re-homes to a reachable ELM device.

    Restricts the candidate list to ELM (``wican-ws`` / ``elm327-tcp``) devices
    and reuses :func:`connect_elm_terminal` as the per-candidate connect step, so
    a mid-session drop reconnects exactly the way the initial connect did.
    """
    from canlib.modes.monitor_reconnect import MonitorReconnector, reconnect_policy
    from canlib.transport import resolve_transport_candidates
    from canlib.transport.config import TransportConfig

    candidates = [c for c in resolve_transport_candidates(args) if c.is_elm]

    async def connect(cand: TransportConfig):
        assert cand.host is not None  # wait_for_reachable only yields hosted candidates
        return await connect_elm_terminal(cand, pids_data, args)

    return MonitorReconnector(candidates, connect, reconnect_policy(args))
