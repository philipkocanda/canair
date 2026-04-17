#!/usr/bin/env python3
"""Send custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket terminal.

Uses the WiCAN ELM327 terminal mode over WebSocket (ws://<ip>/ws) to send
ELM327 AT commands and UDS requests. The firmware handles ISO-TP internally,
so multi-frame responses are reassembled automatically.

IMPORTANT: Using the WebSocket terminal overrides AutoPID mode. The WiCAN
must be rebooted after a terminal session for AutoPID (MQTT data feed to
Home Assistant) to resume. Use --reboot to reboot automatically on exit.

Modes:
    Interactive     python3 canreq.py
    Query params    python3 canreq.py --param SOC_BMS SOC_DISP
    Query ECU       python3 canreq.py --ecu BMS [--pid 2101]
    Raw request     python3 canreq.py --raw 7E4:2101
    Scan PIDs       python3 canreq.py --scan --tx 7E4 --service 21 --range 01-FF
    Scan IOControl  python3 canreq.py --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session
    Multi-ECU       python3 canreq.py --multi "skm-wake acc" "query IGPM BC03 BC06"
    Monitor         python3 canreq.py --multi "query BMS 2101" --monitor [INTERVAL]
    SKM wakeup      python3 canreq.py --skm-wakeup [--level acc|ign1|ign2]
    TesterPresent   python3 canreq.py --tester-present [--target 7A5]

    Add --session to any mode (except interactive) to enter extended diagnostic
    session (10 03) before sending requests. Required for ECUs like IGPM (0x770).

    Add --wake to wake ECUs from deep sleep before entering extended session.
    Sends 10 01 (default session) as a CAN wake-up frame. Implies --session.

Requires: websockets, pyyaml (requests optional, for --reboot)
"""

import argparse
import asyncio
import re
import sys

# Force line-buffered stdout so output appears immediately when piped
if not sys.stdout.isatty():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

from canlib import (
    DEFAULT_WICAN,
    WICAN_ADDRESSES,
    WiCANTerminal,
    init_logging,
    load_pids,
    log_command,
    reboot_wican,
)
from canlib.lock import WiCANLock
from canlib.modes import (
    mode_ecu,
    mode_identity,
    mode_interactive,
    mode_monitor,
    mode_multi,
    mode_param,
    mode_raw,
    mode_scan,
    mode_skm_wakeup,
    mode_tester_present,
)

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip3 install websockets", file=sys.stderr)
    sys.exit(1)


def parse_range(range_str: str) -> tuple[int, int]:
    """Parse a PID/DID range like '01-FF', 'E000-E0FF', or 'BC01-BC0B'."""
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid range: {range_str}. Expected format: 01-FF or E000-E0FF"
        )
    return int(match.group(1), 16), int(match.group(2), 16)


async def async_main(args):
    """Main async entry point."""
    host = args.wican
    if host in WICAN_ADDRESSES:
        host = WICAN_ADDRESSES[host]

    init_logging()
    log_command(
        f"--- SESSION START (host={host}, mode={'interactive' if not any([args.param, args.ecu, args.raw, args.scan, args.skm_wakeup, args.tester_present]) else 'batch'}, unsafe={args.unsafe}, session={getattr(args, 'session', False)}) ---"
    )

    if args.unsafe:
        print("!! WARNING: --unsafe mode active. Dangerous command blocklist is bypassed.")
        print("!! Each blocked command will require explicit user consent before execution.")
        print()

    pids_data = load_pids()
    init_string = pids_data.get("init", "ATSP6;ATS0;ATAL;ATST96;")

    terminal = WiCANTerminal(
        host=host,
        timeout=args.timeout,
        verbose=args.verbose,
        unsafe=args.unsafe,
    )

    try:
        print(f"Connecting to WiCAN at {host}...")
        await terminal.connect()
        print("Connected. Initializing ELM327...")
        await terminal.init_elm(init_string)

        if args.elm_timeout is not None:
            atst_val = max(1, min(255, round(args.elm_timeout / 4.096)))
            atst_cmd = f"ATST{atst_val:02X}"
            await terminal.send_command(atst_cmd)
            terminal.elm_timeout_cmd = atst_cmd
            actual_ms = atst_val * 4.096
            print(f"  ELM327 timeout: {atst_cmd} ({actual_ms:.0f}ms)")

        print("Ready.")

        if args.wake:
            args.session = True

        # Dispatch to mode
        if args.multi and args.monitor:
            # Monitor mode: split pipeline into setup steps + query steps
            from canlib.modes.multi import parse_sub_commands

            commands = parse_sub_commands(args.multi)
            session_steps = [c for c in commands if c["type"] in ("session", "skm-wake", "sleep")]
            query_steps = [c for c in commands if c["type"] == "query"]
            if not query_steps:
                print(
                    "Error: --monitor requires at least one 'query' step in --multi",
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
            )
        elif args.multi:
            await mode_multi(terminal, args.multi, pids_data, args.verbose, no_repl=not args.repl)
        elif args.skm_wakeup:
            await mode_skm_wakeup(terminal, args.level, args.verbose)
        elif args.tester_present:
            await mode_tester_present(terminal, args.target, args.interval, args.verbose)
        elif args.identity:
            if not args.tx:
                print("Error: --identity requires --tx (ECU TX ID)", file=sys.stderr)
                sys.exit(1)
            tx_id = int(args.tx, 16)
            await mode_identity(
                terminal, tx_id, session=args.session, wake=args.wake, as_json=args.json
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
            )
        elif args.scan:
            if not args.tx:
                print("Error: --scan requires --tx (ECU TX ID)", file=sys.stderr)
                sys.exit(1)
            tx_id = int(args.tx, 16)
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
            )
        else:
            await mode_interactive(terminal, pids_data, args.verbose)

    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except websockets.exceptions.InvalidURI as e:
        print(f"Invalid WebSocket URI: {e}", file=sys.stderr)
        sys.exit(1)
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"WebSocket closed: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await terminal.close()
        log_command("--- SESSION END ---")

        if args.reboot:
            reboot_wican(host)


def main():
    parser = argparse.ArgumentParser(
        prog="canreq",
        description="Send custom CAN/UDS requests via WiCAN WebSocket terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                  Interactive REPL
  %(prog)s --param SOC_BMS SOC_DISP         Query specific parameters
  %(prog)s --ecu BMS                        Query all BMS parameters
  %(prog)s --ecu BMS --pid 2101             Query BMS PID 2101 only
  %(prog)s --raw 7E4:2101                   Raw UDS request
  %(prog)s --scan --tx 7E4 --service 21 --range 01-FF
                                            Scan PID range
  %(prog)s --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session
                                            IOControl scan (extended session + suffix)
  %(prog)s --scan --tx 7E4 --service 22 --range BC01-BC0B
                                            Scan 0x22 DID range (auto 2-byte DIDs)

  %(prog)s --raw 770:22BC03 --session       Raw request with extended session
  %(prog)s --ecu IGPM --session             Query ECU that needs extended session
  %(prog)s --param DOOR_DRV_OPEN --session  Query parameter with extended session
  %(prog)s --raw 770:2FBC0103 --hold        IOControl: hold low beams on (Ctrl+C to release)
  %(prog)s --raw 770:2FBC0103 --hold --wake IOControl with deep sleep wake-up
  %(prog)s --ecu IGPM --session --wake      Query IGPM after waking from deep sleep

  %(prog)s --skm-wakeup                     Wake sleeping ECUs via SKM (ACC)
  %(prog)s --skm-wakeup --level ign1        Wake with IGN1 (more ECUs)
  %(prog)s --tester-present                 Send 3E00 broadcast at 1 Hz
  %(prog)s --tester-present --target 7A5    Send 3E00 to SKM only

  %(prog)s --wican vpn --param SOC_BMS      Use VPN address
  %(prog)s --verbose --ecu VCU              Show raw WebSocket traffic
  %(prog)s --json --param SOC_BMS           JSON output
  %(prog)s --reboot --param SOC_BMS         Query + reboot to restore AutoPID

  Multi-ECU pipeline (sessions managed automatically):
  %(prog)s --multi "skm-wake acc" "query IGPM BC03 BC06"
                                            Wake SKM, query IGPM, exit
  %(prog)s --multi "skm-wake acc" "session BCM --wake" "raw 7A0:22B00E"
                                            Wake SKM+BCM, raw query, exit
  %(prog)s --multi "session IGPM --wake" "query IGPM" --repl
                                            Wake IGPM, query all PIDs, REPL
  %(prog)s --multi "skm-wake acc" "sleep 1" "query BCM B00E" "repl"
                                            Pipeline with explicit sleep and REPL
  %(prog)s --multi "query BMS 2101" --monitor
                                            Live monitor: refresh BMS 2101 every 5s
  %(prog)s --multi "session IGPM --wake" "query IGPM BC03 BC06" --monitor 2
                                            Wake IGPM, then poll BC03+BC06 every 2s
""",
    )

    # Mode selection (mutually exclusive)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--param",
        nargs="+",
        metavar="NAME",
        help="Query named parameters (e.g., SOC_BMS SOC_DISP)",
    )
    mode.add_argument(
        "--ecu", metavar="NAME", help="Query all parameters for an ECU (e.g., BMS, VCU)"
    )
    mode.add_argument("--raw", metavar="TX:PID", help="Raw UDS request (e.g., 7E4:2101)")
    mode.add_argument(
        "--scan",
        action="store_true",
        help="Scan a range of PIDs (requires --tx). "
        "One scan at a time only -- parallel scans lock up the device. "
        "Scan gently: use small ranges first, wait between scans.",
    )
    mode.add_argument(
        "--skm-wakeup",
        action="store_true",
        help="Wake sleeping ECUs via SKM relay control (requires active CAN bus)",
    )
    mode.add_argument(
        "--tester-present",
        action="store_true",
        help="Send TesterPresent (3E00) at regular intervals (Ctrl+C to stop)",
    )
    mode.add_argument(
        "--identity",
        action="store_true",
        help="Query standard UDS identity DIDs (F100, F18x, F190, F19x) from --tx ECU "
        "and print decoded part number, serial, manufacture date, VIN, etc.",
    )
    mode.add_argument(
        "--multi",
        nargs="+",
        metavar="CMD",
        help="Multi-ECU pipeline: execute sub-commands in sequence with shared "
        "session management. Each CMD is a quoted string. Sub-commands: "
        "skm-wake [level], session <ECU> [--wake], query <ECU> [PID ...], "
        "raw <TX:PID>, scan <TX> <SVC> <RANGE> [APPEND], security <ECU> [algo ...], "
        "sleep <N>, repl",
    )

    # ECU/PID mode options
    parser.add_argument("--pid", metavar="PID", help="Filter by PID code (for --ecu mode)")

    # Scan mode options
    parser.add_argument("--tx", metavar="ID", help="ECU TX ID for --scan (hex, e.g., 7E4)")
    parser.add_argument(
        "--service",
        metavar="SVC",
        default="21",
        help="UDS service for --scan (hex, default: 21)",
    )
    parser.add_argument(
        "--range",
        metavar="START-END",
        default="01-FF",
        help="PID range for --scan (hex, default: 01-FF)",
    )
    parser.add_argument(
        "--append",
        metavar="HEX",
        help="Hex bytes to append after each DID in --scan (e.g., 03 for IOControl "
        "ShortTermAdjustment). Makes scan send e.g. 2F{DID}03 instead of 2F{DID}.",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Enter extended diagnostic session (10 03) before the request and send "
        "periodic TesterPresent (3E 00) in the background to keep it alive. "
        "Required for some ECUs (e.g. IGPM 0x770) that only respond to 0x22 "
        "DID reads in extended session.",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Keep session alive after command completes (Ctrl+C to release). "
        "Useful for IOControl (2F) commands where the actuator releases when "
        "the diagnostic session drops. Implies --session. Only for --raw mode.",
    )
    parser.add_argument(
        "--wake",
        action="store_true",
        help="Send a wake-up frame (10 01) before entering extended session to "
        "rouse ECUs from deep sleep. The IGPM (0x770) goes into deep sleep "
        "when the car is off and unplugged -- this wakes it via CAN. "
        "Implies --session.",
    )
    parser.add_argument(
        "--repl",
        action="store_true",
        help="For --multi: drop into REPL after pipeline completes",
    )
    parser.add_argument(
        "--monitor",
        nargs="?",
        const=5.0,
        default=None,
        type=float,
        metavar="INTERVAL",
        help="For --multi: instead of running the pipeline once, repeatedly poll "
        "all 'query' steps and refresh the display in-place (live monitor). "
        "Non-query steps (session, skm-wake, sleep) run once as setup. "
        "Optional poll interval in seconds (default: 5.0).",
    )

    # SKM wakeup options
    parser.add_argument(
        "--level",
        default="acc",
        choices=["acc", "ign1", "ign2", "start"],
        help="Relay level for --skm-wakeup (default: acc)",
    )

    # TesterPresent options
    parser.add_argument(
        "--target",
        metavar="TX_ID",
        help="ECU TX ID for --tester-present (hex, e.g., 7A5). Default: broadcast 7DF",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Interval in seconds for --tester-present (default: 1.0)",
    )

    # Connection options
    parser.add_argument(
        "--wican",
        default=DEFAULT_WICAN,
        help=f"WiCAN address: {', '.join(WICAN_ADDRESSES.keys())} or IP (default: {DEFAULT_WICAN})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="WebSocket response timeout in seconds -- max wait for ELM327 to reply (default: 3.0). "
        "In practice, the ELM327's own ATST timeout governs how long it waits for an ECU response.",
    )
    parser.add_argument(
        "--elm-timeout",
        type=int,
        default=None,
        metavar="MS",
        help="ELM327 ECU response timeout in milliseconds (default: ~614ms from ATST96). "
        "Sent as ATSTxx after init. Useful for slow ECUs or scanning.",
    )

    # Output options
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show raw WebSocket traffic and expressions",
    )
    parser.add_argument(
        "--reboot",
        action="store_true",
        help="Reboot WiCAN after session to restore AutoPID mode",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Bypass dangerous command blocklist (requires explicit per-command consent)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Steal the connection lock if another session is still running (use after a killed session)",
    )

    args = parser.parse_args()

    lock = WiCANLock()
    lock.acquire(force=args.force)
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
