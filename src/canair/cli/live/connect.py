"""Constructing and connecting the ELM327 terminal, and re-homing it mid-session.

The single build path shared by the initial connect and the monitor's mid-session
reconnect, so a re-home behaves exactly like the original connect. Also the WiCAN
sleep/battery banner, which is presentation and so stays in the command layer.
"""

from __future__ import annotations

import contextlib
import sys

from canair.render.console import console, escape
from canair.render.glyphs import ELLIPSIS, WARN
from canair.transport.config import ConnectionOptions
from canair.transport.elm327.wican import WiCANTerminal
from canair.transport.protocol import Terminal


def _print_sleep_banner(host: str, timeout: int = 5) -> None:
    """Fetch WiCAN sleep status and battery voltage, print a status line.

    Purely informational and printed into an already-connected session, so any
    failure (an HTTP error, a timeout, a v6 device without these routes) skips
    the banner rather than ending the session.
    """
    try:
        from canair.device.wican.api import get_config, get_status

        base_url = f"http://{host}"
        status = get_status(base_url, timeout)
        config = get_config(base_url, timeout)
    except Exception:
        return
    if not isinstance(status, dict) or not isinstance(config, dict):
        return

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

    out = console(stderr=True)

    if sleep_on:
        sleep_str = f"[red]ON[/red] (threshold {escape(str(sleep_volt))}V)"
        if margin is not None and margin < 0.5:
            out.print(
                f"  [bold yellow]{WARN} Sleep: ON  |  Battery: {escape(str(batt))}  |  "
                f"Threshold: {escape(str(sleep_volt))}V"
                f"  — {margin:.2f}V above cutoff — may shut down soon![/bold yellow]"
            )
            return
    else:
        sleep_str = "[green]OFF[/green]"

    out.print(f"  \\[WiCAN] Sleep: {sleep_str}  |  Battery: {escape(str(batt))}")


async def connect_elm_terminal(transport, pids_data: dict, conn: ConnectionOptions) -> Terminal:
    """Construct, connect, and ELM-initialise the ELM327 terminal for ``transport``.

    Builds a :class:`WiCANTerminal` (WebSocket) for ``wican-ws`` or a
    :class:`~canair.transport.elm327.terminal.Elm327TcpTerminal` (plain TCP) for
    ``elm327-tcp`` — the ELM init + ATST response-timeout + per-ECU budgets are
    identical because they live in the shared engine. The single build path
    shared by the initial connect (``async_main``) and the monitor's mid-session
    reconnect. Raises a transport error on failure (closing the partially-opened
    terminal first); the caller classifies it.
    """
    from canair.config import transport as config_transport
    from canair.ecus.response_frames import seed_counts, variable_length_keys
    from canair.safety.blocklist import SessionPolicy
    from canair.transport import DEFAULT_ELM327_TCP_PORT, Elm327TcpTerminal, Elm327Terminal
    from canair.transport.elm327.addressing import flow_control_data
    from canair.transport.timeouts import cli_timeout, ecu_timeouts_by_tx
    from canair.uds.addressing import AddressBook

    init_string = pids_data.get("init")
    assert init_string, "profile init string must be validated by the caller"

    host = transport.host
    assert host is not None  # ELM transports always resolve a host

    _cli_timeout = cli_timeout(conn.timeout)
    _ws_timeout = _cli_timeout if _cli_timeout is not None else 3.0
    counts = config_transport.expected_responses()
    # Counts previous sessions proved, so the first read of each PID is already
    # fast instead of paying the adapter's full ATST wait to re-learn it.
    seeds = seed_counts(pids_data) if counts else {}
    variable = variable_length_keys(pids_data) if counts else frozenset()
    terminal: Elm327Terminal
    if transport.type == "elm327-tcp":
        port = transport.port or DEFAULT_ELM327_TCP_PORT
        terminal = Elm327TcpTerminal(
            host,
            port,
            timeout=_ws_timeout,
            verbose=conn.verbose,
            unsafe=conn.unsafe,
            expected_responses=counts,
            response_frames=seeds,
            variable_length=variable,
        )
        connecting = f"Connecting to ELM327 adapter at {host}:{port}{ELLIPSIS}"
    else:
        terminal = WiCANTerminal(
            host=host,
            timeout=_ws_timeout,
            verbose=conn.verbose,
            unsafe=conn.unsafe,
            expected_responses=counts,
            response_frames=seeds,
            variable_length=variable,
        )
        connecting = f"Connecting to WiCAN at {host}{ELLIPSIS}"
    # Per-ECU response budgets apply only when the user didn't force --timeout.
    if _cli_timeout is None:
        terminal.ecu_timeouts = ecu_timeouts_by_tx(pids_data)
    # Each ECU's resolved addressing, so a non-+0x08 or functional-request ECU gets
    # its receive filter and flow-control id set along with its header.
    terminal.addresses = AddressBook.from_profile(pids_data)
    terminal.flow_control_data = flow_control_data(pids_data.get("isotp"))
    terminal.session_policy = SessionPolicy.from_profile(pids_data)

    try:
        print(connecting, file=sys.stderr)
        await terminal.connect()
        print(f"Connected. Initializing ELM327{ELLIPSIS}", file=sys.stderr)
        await terminal.init_elm(init_string)

        atst_cmd: str | None = None
        label = ""
        if conn.elm_timeout is not None:
            atst_val = max(1, min(255, round(conn.elm_timeout / 4.096)))
            atst_cmd = f"ATST{atst_val:02X}"
        elif pids_data.get("response_timeout_ms") is not None:
            atst_val = max(1, min(255, round(pids_data["response_timeout_ms"] / 4.096)))
            candidate = f"ATST{atst_val:02X}"
            # Skip it when the init already left the adapter there.
            if terminal.adapter_timeout_cmd != candidate:
                atst_cmd = candidate
                label = ", from profile"
        if atst_cmd is not None:
            await terminal.send_command(atst_cmd)
            terminal.elm_timeout_cmd = atst_cmd
            actual_ms = int(atst_cmd[4:], 16) * 4.096
            print(f"  ELM327 timeout: {atst_cmd} ({actual_ms:.0f}ms{label})", file=sys.stderr)

        print("Ready.", file=sys.stderr)
        return terminal
    except BaseException:
        with contextlib.suppress(Exception):
            await terminal.close()
        raise


def build_elm_reconnector(candidates, pids_data: dict, conn: ConnectionOptions):
    """A :class:`MonitorReconnector` that re-homes to a reachable ELM device.

    ``candidates`` is the resolved transport list, restricted here to the ELM
    (``wican-ws`` / ``elm327-tcp``) devices. It reuses :func:`connect_elm_terminal` as the
    per-candidate connect step, so a mid-session drop reconnects exactly the way the initial
    connect did.
    """
    from canair.live.monitor.reconnect import MonitorReconnector, reconnect_policy
    from canair.transport.config import TransportConfig

    elm_candidates = [c for c in candidates if c.is_elm]

    async def connect(cand: TransportConfig):
        assert cand.host is not None  # wait_for_reachable only yields hosted candidates
        return await connect_elm_terminal(cand, pids_data, conn)

    return MonitorReconnector(elm_candidates, connect, reconnect_policy(wait=conn.wait))
