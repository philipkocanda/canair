"""Shared runtime for live-device subcommands (query, scan, discover, io, ...).

All live subcommands talk to the vehicle over the configured transport (raw
SLCAN-over-TCP by default, or the WebSocket ELM327 terminal — see
:mod:`canlib.transport.config`). They share one connection lifecycle and one
dispatcher (``async_main``, the shared live-command runtime for the CLI).
Each subcommand is a thin argparse surface that populates the same attribute
names ``async_main`` expects, then calls :func:`run_live`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import re
import signal
import sys

# Force line-buffered stdout so output appears immediately when piped.
# reconfigure() only exists on TextIOWrapper; guard so it's safe (and
# type-correct) when stdout/stderr are redirected to a plain stream.
if not sys.stdout.isatty():
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(line_buffering=True)

from canlib import (
    WiCANTerminal,
    init_logging,
    load_pids,
    log_command,
    reboot_wican,
)
from canlib.keepmode import keep_mode_from_args
from canlib.lock import WiCANLock
from canlib.modes import (
    mode_discover,
    mode_ecu,
    mode_identity,
    mode_interactive,
    mode_monitor,
    mode_multi,
    mode_param,
    mode_raw,
    mode_scan,
    mode_skm_wakeup,
)
from canlib.modes.iocontrol import mode_iocontrol_execute, mode_iocontrol_list
from canlib.modes.iocontrol_scan import mode_iocontrol_scan
from canlib.modes.routines import mode_routines_execute, mode_routines_list
from canlib.modes.routines_scan import mode_routines_scan
from canlib.states import parse_states
from canlib.transport.config import DEFAULT_TRANSPORT, VALID_TRANSPORTS
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.transport.protocol import Terminal

if importlib.util.find_spec("websockets") is None:  # pragma: no cover
    print("ERROR: websockets not installed. Run: pip3 install websockets", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# canair live default namespace — every attribute async_main reads, with the
# same defaults the original flat parser used. Live subcommands only expose a
# subset of these as real arguments; finalize_live_parser fills in the rest.
# ---------------------------------------------------------------------------

CANAIR_DEFAULTS: dict = {
    # mode selectors
    "param": None,
    "ecu": None,
    "raw": None,
    "scan": False,
    "skm_wakeup": False,
    "identity": False,
    "discover": False,
    "dtc": None,
    "dtc_all": False,
    "iocontrol": None,
    "routines": None,
    "routines_scan": None,
    "iocontrol_scan": None,
    "sessions_scan": None,
    "multi": None,
    # options
    "pid": None,
    "did": None,
    "off": False,
    "rid": None,
    "sf": "results",
    "tx": None,
    "service": "21",
    "range": "01-FF",
    "append": None,
    "session": False,
    "hold": False,
    "wake": False,
    "repl": False,
    "protocol": "auto",
    "monitor": None,
    "keep_changes": False,
    "keep_unique": False,
    "keep_all": False,
    "keep": None,
    "save": False,
    "label": None,
    "state": None,
    "notes": None,
    "rulers": False,
    "rid_range": "F000-F0FF",
    "did_range": None,
    "throttle_ms": 150,
    "level": "acc",
    "target": None,
    "interval": 1.0,
    "delay": 0.2,
    # `None`, not a resolved device address: every live subcommand exposes its
    # own `--wican` (default `None`, see add_connection_args below) via
    # add_connection_args, so this entry is only a fallback for callers (tests)
    # that build an argparse.Namespace straight from CANAIR_DEFAULTS. It must
    # match that real default and must NOT eagerly resolve a config-backed
    # value here — this dict is built at import time, and a config-derived
    # default previously forced every import of this module to read the user's
    # ~/.config/canair/config.yaml (a live liveness-lookup done purely because
    # the module was imported, not because a device was ever contacted).
    "wican": None,
    "no_fallback": False,
    "wait": False,
    "timeout": 3.0,  # WebSocket response timeout (s); fixed default, no CLI flag
    "elm_timeout": None,
    "json": False,
    "verbose": False,
    "timings": False,
    "reboot": False,
    "unsafe": False,
    "force": False,
}


def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF"
        )
    return int(match.group(1), 16), int(match.group(2), 16)


def split_ecus_by_protocol(names: list[str]) -> tuple[list[str], list[str]]:
    """Partition ECU names into (uds, kwp) by their registry ``id_protocol``.

    KWP2000 ECUs (BMS/VCU/MCU/LDC/AAF) need the KWP variants of the discovery
    scanners: InputOutputControlByLocalIdentifier (0x30) instead of UDS 0x2F, and
    RequestRoutineResultsByLocalIdentifier (0x33) instead of UDS RoutineControl
    (0x31) — sending 0x31 to a KWP2000 ECU means StartRoutine and can actuate, so
    this split is a safety boundary, not just a convenience. ECUs whose protocol
    can't be resolved fall in the UDS bucket (the historical default).
    """
    from canlib.ecus import ecu_id_protocol, resolve_tx

    uds: list[str] = []
    kwp: list[str] = []
    for name in names:
        tx_id = resolve_tx(name)
        proto = ecu_id_protocol(tx_id) if tx_id is not None else None
        if str(proto or "").upper().startswith("KWP"):
            kwp.append(name)
        else:
            uds.append(name)
    return uds, kwp


# ---------------------------------------------------------------------------
# Shell completion helpers (argcomplete)
# ---------------------------------------------------------------------------


def _load_pids_for_completion():
    try:
        return load_pids()
    except Exception:
        return None


def ecu_completer(prefix, parsed_args=None, **kwargs):
    """Complete ECU names from ecus/*.yaml (e.g. BMS, IGPM, HVAC)."""
    data = _load_pids_for_completion()
    if not data:
        return []
    names = list(data.get("ecus", {}).keys())
    up = prefix.upper()
    return [n for n in names if n.upper().startswith(up)]


def pid_completer(prefix, parsed_args=None, **kwargs):
    """Complete PID codes. Narrows to --ecu's PIDs if that arg is set."""
    data = _load_pids_for_completion()
    if not data:
        return []
    ecus = data.get("ecus", {})
    ecu_filter = getattr(parsed_args, "ecu", None)
    pids = set()
    if ecu_filter and ecu_filter.upper() in {k.upper() for k in ecus}:
        target = next(k for k in ecus if k.upper() == ecu_filter.upper())
        pids.update(ecus[target].get("pids", {}).keys())
    else:
        for info in ecus.values():
            pids.update(info.get("pids", {}).keys())
    codes = [str(p).upper().removeprefix("0X") for p in pids]
    up = prefix.upper()
    return sorted(c for c in codes if c.startswith(up))


def param_completer(prefix, parsed_args=None, **kwargs):
    """Complete parameter names from all ECUs' pids."""
    data = _load_pids_for_completion()
    if not data:
        return []
    names = set()
    for info in data.get("ecus", {}).values():
        for pid_info in info.get("pids", {}).values():
            if not isinstance(pid_info, dict):
                continue
            for param in pid_info.get("params", []) or []:
                if isinstance(param, dict) and "name" in param:
                    names.add(param["name"])
    up = prefix.upper()
    return sorted(n for n in names if n.upper().startswith(up))


def step_completer(prefix, parsed_args=None, **kwargs):
    """Complete a query STEP: ``@group`` refs when it starts with ``@``, else ECUs."""
    if prefix.startswith("@"):
        try:
            from canlib.ecu_groups import GROUP_SIGIL, load_groups

            names = [f"{GROUP_SIGIL}{g}" for g in load_groups()]
        except Exception:
            return []
        return [n for n in names if n.startswith(prefix)]
    return ecu_completer(prefix, parsed_args, **kwargs)


# ---------------------------------------------------------------------------
# Multi mini-language step helpers (shared by the query + monitor surfaces)
# ---------------------------------------------------------------------------

# Leading verbs recognised by the multi mini-language. A positional STEP whose
# first token is one of these is passed through verbatim; anything else is a
# bare selector and gets the implicit ``query`` verb prepended.
STEP_VERBS = ("skm-wake", "session", "query", "raw", "scan", "iocontrol", "sleep", "repl")


def to_step(selector: str) -> str:
    """Prefix a bare selector with the ``query`` verb unless it already has one."""
    first = selector.strip().split(maxsplit=1)
    if first and first[0].lower() in STEP_VERBS:
        return selector
    return f"query {selector}"


def expand_step_groups(steps: list[str]) -> list[str]:
    """Expand ``@group`` references in positional STEPs into their selectors.

    Loads the active profile's ``groups.yaml`` and rewrites each step textually
    (see :func:`canlib.ecu_groups.expand_group_refs`) *before* the mini-language
    parser sees it, so a group composes with ad-hoc selectors and other groups.
    Raises ``GroupError`` (a ``ValueError``) on a bad/unknown reference — callers
    already guard step parsing with a ``ValueError`` handler.
    """
    from canlib.ecu_groups import expand_group_refs, load_groups

    return expand_group_refs(steps, load_groups())


# ---------------------------------------------------------------------------
# Parser helpers shared by live subcommands
# ---------------------------------------------------------------------------


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add the connection/output flags common to every live subcommand."""
    from canlib.constants import DEFAULT_WICAN, WICAN_ADDRESSES

    parser.add_argument(
        "--wican",
        default=None,
        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or IP "
        f"(default: config transport.host / default_wican={DEFAULT_WICAN})",
    )
    parser.add_argument(
        "--transport",
        choices=VALID_TRANSPORTS,
        default=None,
        help="CAN transport: slcan-tcp (raw CAN), wican-ws (WiCAN ELM327 "
        "WebSocket), or elm327-tcp (direct ELM327 adapter over TCP). "
        f"Overrides the config `transport.type` (default: {DEFAULT_TRANSPORT}).",
    )
    parser.add_argument(
        "--no-fallback",
        dest="no_fallback",
        action="store_true",
        help="Don't auto-fall-back to other configured devices when the selected "
        "one is unreachable (see config transport.fallback).",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Keep retrying to reach the device indefinitely, then start as soon "
        "as it comes online (Ctrl-C to stop). For 'monitor', also reconnects "
        "forever if the connection drops mid-session (auto-failover to another "
        "same-transport device is bounded by default; --wait makes it unbounded).",
    )
    parser.add_argument(
        "--elm-timeout",
        type=int,
        default=None,
        metavar="MS",
        help="ELM327 ECU response timeout in ms (sent as ATSTxx after init)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Overall UDS response timeout in seconds (default 3.0 ELM / 2.0 raw). "
        "Overrides any per-ECU response_timeout_ms for the whole run.",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show raw transport traffic and expressions"
    )
    parser.add_argument(
        "--timings",
        action="store_true",
        help="Print per-ECU/PID round-trip timing stats on exit (to stderr)",
    )
    parser.add_argument(
        "--reboot", action="store_true", help="Reboot WiCAN after session to restore AutoPID mode"
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Bypass dangerous command blocklist (requires explicit per-command consent)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Steal the connection lock if another session is still running",
    )


def finalize_live_parser(parser: argparse.ArgumentParser, **active_mode) -> None:
    """Fill in every canair live default attribute the parser does not already expose.

    ``active_mode`` sets the mode selector(s) for this subcommand (e.g.
    ``scan=True`` or ``discover=True``). Also wires ``func=run``.
    """
    exposed = {a.dest for a in parser._actions}
    for dest, default in CANAIR_DEFAULTS.items():
        if dest in exposed or dest in active_mode:
            continue
        parser.set_defaults(**{dest: default})
    parser.set_defaults(**active_mode, func=run)


# ---------------------------------------------------------------------------
# Connection lifecycle + dispatcher (the shared live-command runtime)
# ---------------------------------------------------------------------------


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
    from canlib.quirks import HK_F1XX_MINUS_ONE, has_quirk
    from canlib.timeouts import cli_timeout, ecu_timeouts_by_tx
    from canlib.transport import DEFAULT_ELM327_TCP_PORT, Elm327TcpTerminal

    init_string = pids_data.get("init")
    assert init_string, "profile init string must be validated by the caller"

    host = transport.host
    assert host is not None  # ELM transports always resolve a host

    _cli_timeout = cli_timeout(args)
    _ws_timeout = _cli_timeout if _cli_timeout is not None else 3.0
    hk = has_quirk(pids_data, HK_F1XX_MINUS_ONE)
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
        )
        connecting = f"Connecting to ELM327 adapter at {host}:{port}..."
    else:
        terminal = WiCANTerminal(
            host=host,
            timeout=_ws_timeout,
            verbose=args.verbose,
            unsafe=args.unsafe,
            hk_f1xx_offset=hk,
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


async def async_main(args):
    """Main async entry point."""
    from canlib.config import fallback_settings
    from canlib.transport import (
        resolve_transport_candidates,
        select_reachable_transport,
        wait_for_reachable,
    )

    candidates = resolve_transport_candidates(args)
    _, _connect_timeout, _ = fallback_settings()
    if getattr(args, "wait", False):
        # --wait: block until a candidate comes online (Ctrl-C to stop). No custom
        # signal handler is installed yet, so a SIGINT here raises KeyboardInterrupt
        # which run_live reports cleanly.
        transport = (
            wait_for_reachable(
                candidates,
                connect_timeout=_connect_timeout,
                deadline=None,
                notice=lambda m: print(f"note: {m}", file=sys.stderr),
            )
            or candidates[0]
        )
    else:
        transport = select_reachable_transport(candidates, connect_timeout=_connect_timeout)
    host = transport.host

    init_logging()
    log_command(
        f"--- SESSION START (host={host}, mode={'interactive' if not any([args.param, args.ecu, args.raw, args.scan, args.discover, args.skm_wakeup, args.identity, args.dtc, args.iocontrol, args.routines, args.routines_scan is not None, args.iocontrol_scan is not None, getattr(args, 'sessions_scan', None) is not None]) else 'batch'}, unsafe={args.unsafe}, session={getattr(args, 'session', False)}) ---"
    )

    if args.unsafe:
        print("!! WARNING: --unsafe mode active. Dangerous command blocklist is bypassed.")
        print("!! Each blocked command will require explicit user consent before execution.")
        print()

    pids_data = load_pids()

    # Raw transport (slcan-tcp): route to the client-side ISO-TP/UDS path instead
    # of the ELM327 WebSocket. The device must already be in slcan mode.
    if transport.is_raw:
        from canlib.modes.raw_ops import run_raw

        return await run_raw(args, transport, pids_data)

    # Past the raw early-return we're on the WebSocket (WiCANTerminal) path,
    # which always resolves a host (via --wican/config/default_wican).
    assert host is not None

    # Warn about any aborted scans from a previous interrupted session — but
    # only in the scan subcommand that produces that scan type, so unrelated
    # commands (query, io, identity, ...) stay quiet.
    _scan_types: set[str] = set()
    if args.scan:
        _scan_types.add("scan")
    if args.iocontrol_scan is not None:
        _scan_types.add("iocontrol")
    if args.routines_scan is not None:
        _scan_types.add("routines")

    if _scan_types:
        from canlib.scan_state import find_aborted_scans

        _aborted = [s for s in find_aborted_scans() if s.get("type") in _scan_types]
        if _aborted:
            print("!! Aborted scan(s) detected from a previous session:")
            for _s in _aborted:
                print(
                    f"   [{_s['type'].upper()} scan  {_s['ecu']} @ {_s['tx_id']}]"
                    f"  range {_s['range']}"
                    f"  last probe: {_s['current']}"
                    f"  ({_s['hits']} hits / {_s['total']} total)"
                    f"  started {_s.get('started', '?')}"
                )
            print("!! To resume, re-run with the same ECU and range starting at the last probe.")
            print()

    # List-only mode: no CAN connection needed (--json or explicit list)
    if args.iocontrol and not args.did and args.json:
        mode_iocontrol_list(pids_data, args.iocontrol, as_json=True)
        return
    if args.routines and not args.rid and args.json:
        mode_routines_list(pids_data, args.routines, as_json=True)
        return

    # ELM327 init string from the active profile. Required (validate enforces
    # it); a profile missing it fails loud rather than silently using a stale
    # Ioniq-flavoured fallback.
    init_string = pids_data.get("init")
    if not init_string:
        print(
            "error: the active profile has no `init:` string (profile.yaml). "
            'Add one, e.g. `init: "ATSP6;ATS0;ATAL;"` (ISO 15765-4 11-bit).',
            file=sys.stderr,
        )
        sys.exit(1)

    # Fail loud: --save (and metadata flags) only apply to capture-producing modes.
    _wants_save = wants_save(args)
    if _wants_save:
        _save_ok = bool(args.scan or args.raw or args.discover)
        if not _save_ok and args.multi:
            from canlib.modes.multi import parse_sub_commands

            _save_ok = any(c["type"] in ("query", "raw") for c in parse_sub_commands(args.multi))
        if not _save_ok:
            print(
                "Error: --save/--label/--state/--notes only apply to --scan, --raw, "
                "--discover, or --multi with a 'query'/'raw' step.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Surface any orphaned journals from a previously killed --save session.
        from canlib.commands.captures import orphan_notice

        orphan_notice()

    # Pre-flight reachability check so a silent host fails fast with guidance
    # instead of hanging out a connect timeout deep in the stack. For a WiCAN the
    # ELM327 WebSocket lives on the HTTP port (require_ws_reachable); a direct
    # ELM327 adapter (elm327-tcp) has no HTTP API, so we probe its data port.
    from canlib.wican_mode import ModeError, require_elm327_tcp_reachable, require_ws_reachable

    is_elm_tcp = transport.type == "elm327-tcp"
    transport_label = "ELM327/TCP" if is_elm_tcp else "WebSocket"
    try:
        if is_elm_tcp:
            from canlib.transport import DEFAULT_ELM327_TCP_PORT

            require_elm327_tcp_reachable(host, transport.port or DEFAULT_ELM327_TCP_PORT)
        else:
            require_ws_reachable(host)
    except ModeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    from canlib.transport.errors import describe_transport_error, transport_error_types

    rc = 0
    terminal: Terminal | None = None
    try:
        try:
            terminal = await connect_elm_terminal(transport, pids_data, args)
            if transport.is_wican_http:
                _print_sleep_banner(host)

            if args.wake:
                args.session = True
        except transport_error_types() as e:
            # Connection setup failed (connect / ELM init). Classify + report
            # cleanly rather than dumping a traceback; no session ran yet.
            print(
                "error: "
                + describe_transport_error(
                    e, host=host, transport_label=transport_label, saving=_wants_save
                ),
                file=sys.stderr,
            )
            return 1

        # Session dispatch under the shared, transport-agnostic error guard.
        rc = await run_session_guarded(
            args, terminal, pids_data, host, transport_label=transport_label
        )
    finally:
        if terminal is not None:
            await terminal.close()
        log_command("--- SESSION END ---")

        if args.reboot and transport.is_wican_http:
            reboot_wican(host)
    return rc


def wants_save(args) -> bool:
    """True when the invocation intends to record captures (``--save`` or metadata flags).

    Shared by the ``--save`` validation and the session error guard (so a dropped
    session knows whether to point the user at ``--recover``).
    """
    return bool(
        getattr(args, "save", False)
        or getattr(args, "label", None) is not None
        or getattr(args, "state", None) is not None
        or getattr(args, "notes", None) is not None
    )


async def run_session_guarded(
    args, terminal: Terminal, pids_data, host, *, transport_label: str
) -> int:
    """Run :func:`dispatch_mode` (+ optional timings) under unified error handling.

    The single, transport-agnostic home for "a live session hit a transport/IO
    failure" — shared by both the ELM (``async_main``) and raw (``run_raw``)
    entry points so a dropped/failed bus is always a clean, classified message
    (never a traceback), with a ``--recover`` hint when a ``--save`` session was
    in flight (its data is safe in the write-ahead journal). Returns a process
    exit code: 0 on success, 1 on a transport failure.

    The caller owns the terminal lifecycle (construct/close) and any
    transport-specific ``finally`` (session-end logging, reboot). ``KeyboardInterrupt``
    is intentionally *not* caught here — the mode handlers reconcile their journals
    on interrupt, and ``run_live`` reports the interrupt.
    """
    from canlib.transport.errors import describe_transport_error, transport_error_types

    try:
        await dispatch_mode(args, terminal, pids_data, host)
        if getattr(args, "timings", False):
            from canlib.timing import print_timings

            print_timings(terminal.timings, as_json=getattr(args, "json", False))
        return 0
    except transport_error_types() as e:
        print(
            "error: "
            + describe_transport_error(
                e, host=host, transport_label=transport_label, saving=wants_save(args)
            ),
            file=sys.stderr,
        )
        return 1


def run_live(args) -> int:
    """Acquire the device lock and run ``async_main`` for a live subcommand."""
    lock = WiCANLock()
    lock.acquire(force=args.force)

    # Map SIGTERM onto the same graceful path as Ctrl-C (SIGINT already raises
    # KeyboardInterrupt). So a `kill`/`pkill -f canair` of a live session — e.g.
    # an orphan that another run just --force'd past — unwinds cleanly (closes
    # the terminal, reconciles any --save journal, releases the device
    # connection) instead of the default abrupt terminate. Modes that install
    # their own SIGTERM handler (the monitor) override this for their duration.
    def _on_sigterm(_sig, _frame):
        raise KeyboardInterrupt

    prev_term = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        result = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_term)
        lock.release()
    return result if isinstance(result, int) else 0


def run(args) -> int:
    """Default live dispatch used by most subcommands (set via finalize_live_parser)."""
    return run_live(args)


async def dispatch_mode(args, terminal: Terminal, pids_data, host):
    """Dispatch a live subcommand to its mode handler over ``terminal``.

    Shared by the ELM (WiCANTerminal) and raw (RawTerminal) transports so the
    same commands work on either — the transport differs, the dispatch does not.
    Typed against the :class:`~canlib.transport.protocol.Terminal` contract, not
    a concrete class, so a mode reaching for a transport-specific attribute is a
    ``ty`` error (the "keep the WiCAN replaceable" rule, compiler-checked).
    """
    if args.multi and args.monitor:
        from canlib.modes.multi import parse_sub_commands

        commands = parse_sub_commands(args.multi)
        session_steps = [c for c in commands if c["type"] in ("session", "skm-wake", "sleep")]
        query_steps = [c for c in commands if c["type"] == "query"]
        if not query_steps:
            print(
                "Error: monitor requires at least one 'query' step",
                file=sys.stderr,
            )
            sys.exit(1)
        await mode_monitor(
            terminal,
            query_steps,
            pids_data,
            args.verbose,
            interval=args.monitor,
            session_steps=session_steps,
            keep_mode=keep_mode_from_args(args),
            keep_n=args.keep,
            save=args.save,
            show_rulers=args.rulers,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
            include_static=getattr(args, "include_static", False),
            reconnect=build_elm_reconnector(args, pids_data),
        )
    elif args.multi:
        await mode_multi(
            terminal,
            args.multi,
            pids_data,
            args.verbose,
            no_repl=not args.repl,
            save=args.save,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
            include_static=getattr(args, "include_static", False),
        )
    elif args.skm_wakeup:
        # The SKM relay-wake is a make-specific Ioniq capability (relay DIDs /
        # magic bytes / addresses are Ioniq-particular), gated behind the profile
        # `skm_wakeup` quirk. Reading a fast-sleeping ECU is the make-neutral
        # per-ECU `wake:` block (canlib.wake) — not this command.
        from canlib.quirks import SKM_WAKEUP, has_quirk

        if not has_quirk(pids_data, SKM_WAKEUP):
            prof_name = getattr(getattr(args, "_profile", None), "name", None) or "this profile"
            print(
                f"Error: skm-wake is not supported by {prof_name} — it requires the "
                "`skm_wakeup` capability (declare it under `quirks:` in profile.yaml). "
                "To merely wake a fast-sleeping ECU, declare a per-ECU `wake:` block "
                "(`canair pids set-wake`) and use `session <ECU> --wake`.",
                file=sys.stderr,
            )
            sys.exit(1)
        # skm-wake relies on ELM327 text semantics (raw ATSH/frame collection)
        # provided by the shared Elm327Terminal engine — not the raw slcan-tcp
        # RawTerminal. Guard on the engine type so raw reports a clear error.
        if not isinstance(terminal, Elm327Terminal):
            print(
                "Error: skm-wake is only supported on the ELM327 transports "
                "(wican-ws / elm327-tcp), not slcan-tcp.",
                file=sys.stderr,
            )
            sys.exit(1)
        await mode_skm_wakeup(terminal, args.level, args.verbose)
    elif args.identity:
        from canlib.ecus import resolve_tx

        tx_id = resolve_tx(args.tx)
        if tx_id is None:
            print(
                f"Error: could not resolve ECU '{args.tx}' "
                "(use a name like IGPM or a hex TX id like 770)",
                file=sys.stderr,
            )
            sys.exit(1)
        from canlib.quirks import resolve_quirks

        await mode_identity(
            terminal,
            tx_id,
            session=args.session,
            wake=args.wake,
            as_json=args.json,
            protocol=getattr(args, "protocol", "auto"),
            quirks=resolve_quirks(pids_data),
        )
    elif args.dtc or getattr(args, "dtc_all", False):
        from canlib.ecus import resolve_tx
        from canlib.modes.dtc import mode_dtc_clear, mode_dtc_read, mode_dtc_scan_all

        if getattr(args, "dtc_all", False):
            try:
                mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
            except ValueError:
                print(f"Error: --mask must be hex (e.g. FF), got {args.mask!r}", file=sys.stderr)
                sys.exit(1)
            await mode_dtc_scan_all(
                terminal,
                mask=mask,
                protocol=args.protocol,
                as_json=args.json,
                verbose=args.verbose,
                retry=getattr(args, "dtc_retry", True),
                log=getattr(args, "dtc_log", True),
                label=args.label,
                vehicle_states=parse_states(getattr(args, "state", None)),
            )
            return

        tx_id = resolve_tx(args.dtc)
        if tx_id is None:
            print(
                f"Error: could not resolve ECU '{args.dtc}' "
                "(use a name like BMS or a hex TX id like 7E4)",
                file=sys.stderr,
            )
            sys.exit(1)
        if getattr(args, "clear", False):
            try:
                group = int(str(args.group).removeprefix("0x").removeprefix("0X"), 16)
            except ValueError:
                print(
                    f"Error: --group must be hex (e.g. FFFFFF), got {args.group!r}", file=sys.stderr
                )
                sys.exit(1)
            if not getattr(args, "yes", False):
                from canlib.ecus import ecu_display

                print(
                    f"!! About to CLEAR DTCs on {ecu_display(tx_id)} "
                    f"(group 0x{group & 0xFFFFFF:06X}). This erases stored fault memory.",
                    file=sys.stderr,
                )
                print("!! Continue? [y/N] ", end="", flush=True, file=sys.stderr)
                answer = sys.stdin.readline().strip().lower()
                if answer not in ("y", "yes"):
                    print("Aborted.", file=sys.stderr)
                    return
            await mode_dtc_clear(
                terminal,
                tx_id,
                group=group,
                protocol=args.protocol,
                session=args.session,
                wake=args.wake,
                as_json=args.json,
                verbose=args.verbose,
                log=getattr(args, "dtc_log", True),
                label=args.label,
            )
        else:
            try:
                mask = int(str(args.mask).removeprefix("0x").removeprefix("0X"), 16)
            except ValueError:
                print(f"Error: --mask must be hex (e.g. FF), got {args.mask!r}", file=sys.stderr)
                sys.exit(1)
            await mode_dtc_read(
                terminal,
                tx_id,
                mask=mask,
                protocol=args.protocol,
                session=args.session,
                wake=args.wake,
                as_json=args.json,
                verbose=args.verbose,
                log=getattr(args, "dtc_log", True),
                label=args.label,
                vehicle_states=parse_states(getattr(args, "state", None)),
            )
    elif args.param:
        await mode_param(
            terminal,
            pids_data,
            args.param,
            args.verbose,
            args.json,
            session=args.session,
            wake=args.wake,
        )
    elif args.ecu:
        await mode_ecu(
            terminal,
            pids_data,
            args.ecu,
            args.pid,
            args.verbose,
            args.json,
            session=args.session,
            wake=args.wake,
        )
    elif args.raw:
        await mode_raw(
            terminal,
            args.raw,
            args.verbose,
            args.json,
            session=args.session,
            hold=args.hold,
            wake=args.wake,
            save=args.save,
            pids_data=pids_data,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
        )
    elif args.scan:
        from canlib.ecus import resolve_tx

        tx_id = resolve_tx(args.tx)
        if tx_id is None:
            print(
                f"Error: could not resolve ECU '{args.tx}' "
                "(use a name like BMS or a hex TX id like 7E4)",
                file=sys.stderr,
            )
            sys.exit(1)
        service = int(args.service, 16) if args.service else 0x21
        pid_range = parse_range(args.range) if args.range else (0x01, 0xFF)
        append_bytes = ""
        if args.append:
            cleaned = args.append.replace(" ", "").upper()
            if not all(c in "0123456789ABCDEF" for c in cleaned) or len(cleaned) % 2 != 0:
                print(
                    "Error: --append must be valid hex bytes (e.g., 03 or 030A0A05)",
                    file=sys.stderr,
                )
                sys.exit(1)
            append_bytes = cleaned
        await mode_scan(
            terminal,
            tx_id,
            service,
            pid_range,
            args.verbose,
            args.json,
            append_bytes=append_bytes,
            session=args.session,
            wake=args.wake,
            save=args.save,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
        )
    elif args.iocontrol:
        if args.did:
            await mode_iocontrol_execute(
                terminal,
                pids_data,
                args.iocontrol,
                args.did,
                off=args.off,
                verbose=args.verbose,
                as_json=args.json,
            )
        else:
            from canlib.modes.iocontrol import mode_iocontrol_tui

            await mode_iocontrol_tui(
                terminal,
                pids_data,
                args.iocontrol,
                verbose=args.verbose,
                poll=getattr(args, "poll", False),
            )
    elif args.routines:
        if args.rid:
            from canlib.modes.routines import SF_RESULTS, SF_START, SF_STOP

            sf_map = {"results": SF_RESULTS, "start": SF_START, "stop": SF_STOP}
            sf_name = (args.sf or "results").lower()
            if sf_name not in sf_map:
                print(
                    f"Error: --sf must be one of: results, start, stop (got {args.sf!r})",
                    file=sys.stderr,
                )
                sys.exit(1)
            sub_function = sf_map[sf_name]
            if sub_function == SF_START:
                print(
                    f"!! WARNING: --sf start will send startRoutine (SF 0x01) to {args.routines} RID {args.rid}.",
                    file=sys.stderr,
                )
                print(
                    "!! This may actuate hardware. Continue? [y/N] ",
                    end="",
                    flush=True,
                    file=sys.stderr,
                )
                answer = sys.stdin.readline().strip().lower()
                if answer not in ("y", "yes"):
                    print("Aborted.", file=sys.stderr)
                    sys.exit(0)
            await mode_routines_execute(
                terminal,
                pids_data,
                args.routines,
                args.rid,
                sub_function=sub_function,
                verbose=args.verbose,
                as_json=args.json,
            )
        else:
            from canlib.modes.routines import mode_routines_tui

            await mode_routines_tui(
                terminal,
                pids_data,
                args.routines,
                verbose=args.verbose,
            )
    elif args.routines_scan is not None:
        from canlib.modes.kwp_routines_scan import mode_kwp_routines_scan

        rid_range = parse_range(args.rid_range)

        # Auto-select by id_protocol. UDS ECUs use RoutineControl (0x31 SF03,
        # requestRoutineResults). KWP2000 ECUs (BMS/VCU/MCU/LDC/AAF) MUST NOT
        # receive 0x31 — there it means StartRoutineByLocalIdentifier (actuates);
        # they use the read-only 0x33 RequestRoutineResultsByLocalIdentifier.
        uds_ecus, kwp_ecus = split_ecus_by_protocol(args.routines_scan)

        _session = getattr(args, "session", False)
        _wake = getattr(args, "wake", False)
        _mode = getattr(args, "session_mode", "03")

        if uds_ecus:
            await mode_routines_scan(
                terminal,
                pids_data,
                ecus=uds_ecus,
                rid_range=rid_range,
                throttle_ms=args.throttle_ms,
                verbose=args.verbose,
                write_yaml=True,
                session=_session,
                wake=_wake,
                session_mode=_mode,
            )
        if kwp_ecus:
            # For KWP2000 ECUs the id is an 8-bit LID; only pass an explicit range
            # if the user gave one that fits a single byte, else use the 00-FF default.
            lid_range = rid_range if rid_range[1] <= 0xFF else None
            await mode_kwp_routines_scan(
                terminal,
                pids_data,
                ecus=kwp_ecus,
                lid_range=lid_range,
                throttle_ms=args.throttle_ms,
                verbose=args.verbose,
                write_yaml=True,
                session=_session,
                wake=_wake,
                session_mode=_mode,
            )
    elif args.iocontrol_scan is not None:
        from canlib.modes.kwp_iocontrol_scan import mode_kwp_iocontrol_scan

        did_range = parse_range(args.did_range) if args.did_range else None

        # Auto-select the service by the ECU's identity protocol: KWP2000 ECUs
        # (BMS/VCU/MCU/LDC/AAF) use IOControlByLocalIdentifier (0x30); the rest
        # use UDS IOControlByIdentifier (0x2F).
        uds_ecus, kwp_ecus = split_ecus_by_protocol(args.iocontrol_scan)

        _session = getattr(args, "session", False)
        _wake = getattr(args, "wake", False)
        _mode = getattr(args, "session_mode", "03")

        if uds_ecus:
            await mode_iocontrol_scan(
                terminal,
                pids_data,
                ecus=uds_ecus,
                did_range=did_range,
                throttle_ms=args.throttle_ms,
                verbose=args.verbose,
                write_yaml=True,
                session=_session,
                wake=_wake,
                session_mode=_mode,
            )
        if kwp_ecus:
            await mode_kwp_iocontrol_scan(
                terminal,
                pids_data,
                ecus=kwp_ecus,
                lid_range=did_range,
                throttle_ms=args.throttle_ms,
                verbose=args.verbose,
                write_yaml=True,
                session=_session,
                wake=_wake,
                session_mode=_mode,
            )
    elif getattr(args, "sessions_scan", None) is not None:
        from canlib.modes.sessions_scan import mode_sessions_scan

        modes = None
        if getattr(args, "modes", None):
            modes = tuple(
                int(tok, 16) for tok in str(args.modes).replace(" ", "").split(",") if tok
            )
        await mode_sessions_scan(
            terminal,
            pids_data,
            ecus=args.sessions_scan,
            modes=modes,
            throttle_ms=args.throttle_ms,
            verbose=args.verbose,
            write_yaml=True,
        )
    elif args.discover:
        from canlib.addressing import is_extended, resolve_mode
        from canlib.modes.discover import DEFAULT_TESTER_ADDRESS

        _addr_mode = resolve_mode(pids_data)
        # 11-bit sweeps arbitration ids (default 0x700-0x7EF); 29-bit sweeps the
        # target-address byte (default 0x00-0xFF), formed into 0x18DA{target}{tester}.
        if args.range is not None:
            addr_range = parse_range(args.range)
        elif is_extended(_addr_mode):
            addr_range = (0x00, 0xFF)
        else:
            addr_range = (0x700, 0x7EF)
        await mode_discover(
            terminal,
            addr_range,
            args.verbose,
            args.json,
            delay=args.delay,
            save=args.save,
            label=args.label,
            vehicle_states=args.state,
            notes=args.notes,
            register=getattr(args, "register", False),
            dry_run=getattr(args, "dry_run", False),
            identify=getattr(args, "identify", False),
            mode=_addr_mode,
            tester=DEFAULT_TESTER_ADDRESS,
        )
    else:
        # The interactive REPL is an ELM327 command prompt: it sets the ECU
        # header via raw ``ATSH`` sends, which are no-ops on the raw transport
        # (RawTerminal uses set_header(), not ATSH parsing), so a UDS request
        # would fire with no header set. Guard on the ELM327 engine type so raw
        # reports a clear error instead of a half-working prompt.
        if not isinstance(terminal, Elm327Terminal):
            print(
                "Error: the interactive REPL is only supported on the ELM327 "
                "transports (wican-ws / elm327-tcp), not slcan-tcp.",
                file=sys.stderr,
            )
            sys.exit(1)
        await mode_interactive(terminal, pids_data, args.verbose)
