"""Multi-ECU pipeline mode.

Executes a sequence of sub-commands within a single WebSocket session,
managing extended diagnostic sessions across multiple ECUs with interleaved
TesterPresent keepalives.

Sub-commands:
    skm-wake [level]                Wake SKM + activate relay (acc/ign1/ign2)
    session <ECU|TX_ID> [--wake]    Enter extended session on ECU
    query <ECU> [PID ...]           Query ECU parameters (like --ecu/--param)
    raw <TX:PID>                    Raw UDS request
    scan <TX> <SVC> <RANGE> [APPEND]  Scan PID range
    sleep <seconds>                 Pause between steps
    repl                            Drop into interactive REPL (explicit)

After all sub-commands complete, drops into REPL by default (unless --no-repl
was specified or an explicit 'repl' step was already executed).
"""

import asyncio
import re
import shlex

from ..session_manager import SessionManager
from ..pids import build_ecu_index, build_param_index, load_pids
from ..formatting import print_decoded_params, print_hexdump, print_json_result
from ..expression import evaluate_expression
from ..elm327 import parse_elm_response, elm_hex_to_wican_bytes
from ..terminal import WiCANTerminal


def resolve_tx_id(name_or_hex: str, ecu_index: dict) -> int | None:
    """Resolve an ECU name or hex TX ID to an integer.

    Accepts: 'IGPM', 'igpm', '770', '0x770', '7A0'.
    """
    upper = name_or_hex.upper()
    if upper in ecu_index:
        return ecu_index[upper]["tx_id"]

    # Try as hex
    cleaned = upper.removeprefix("0X")
    try:
        return int(cleaned, 16)
    except ValueError:
        return None


def parse_sub_commands(args: list[str]) -> list[dict]:
    """Parse multi-mode sub-command strings into structured dicts.

    Each string is a mini-command like 'skm-wake acc' or 'raw 770:22BC03'.
    """
    commands = []
    for arg in args:
        parts = shlex.split(arg)
        if not parts:
            continue

        verb = parts[0].lower().replace("_", "-")

        if verb == "skm-wake":
            level = parts[1] if len(parts) > 1 else "acc"
            commands.append({"type": "skm-wake", "level": level})

        elif verb == "session":
            if len(parts) < 2:
                raise ValueError(f"'session' requires an ECU name or TX ID: session IGPM")
            wake = "--wake" in parts
            target = parts[1]
            commands.append({"type": "session", "target": target, "wake": wake})

        elif verb == "query":
            if len(parts) < 2:
                raise ValueError(f"'query' requires an ECU name: query IGPM BC03 BC06")
            ecu = parts[1]
            pids = parts[2:] if len(parts) > 2 else []
            commands.append({"type": "query", "ecu": ecu, "pids": pids})

        elif verb == "raw":
            if len(parts) < 2:
                raise ValueError(f"'raw' requires TX:PID: raw 770:22BC03")
            commands.append({"type": "raw", "spec": parts[1], "hold": "--hold" in parts})

        elif verb == "scan":
            # scan <TX> <SVC> <RANGE> [APPEND]
            if len(parts) < 4:
                raise ValueError(f"'scan' requires: scan <TX> <SERVICE> <RANGE> [APPEND]")
            commands.append({
                "type": "scan",
                "tx": parts[1],
                "service": parts[2],
                "range": parts[3],
                "append": parts[4] if len(parts) > 4 else "",
            })

        elif verb == "sleep":
            seconds = float(parts[1]) if len(parts) > 1 else 1.0
            commands.append({"type": "sleep", "seconds": seconds})

        elif verb == "repl":
            commands.append({"type": "repl"})

        else:
            raise ValueError(f"Unknown sub-command: {verb!r}. "
                             f"Available: skm-wake, session, query, raw, scan, sleep, repl")

    return commands


async def _exec_skm_wake(sm: SessionManager, level: str, verbose: bool):
    """Execute skm-wake sub-command using the existing mode_skm_wakeup logic."""
    from .skm_wakeup import mode_skm_wakeup
    terminal = sm.terminal

    success = await mode_skm_wakeup(terminal, level, verbose)
    if success:
        # Track the SKM session so keepalives are sent
        sm._sessions[0x7A5] = __import__("time").monotonic()
    return success


async def _exec_session(sm: SessionManager, target: str, wake: bool, ecu_index: dict):
    """Execute session sub-command."""
    tx_id = resolve_tx_id(target, ecu_index)
    if tx_id is None:
        print(f"  ERROR: Unknown ECU '{target}'. Use a name (IGPM) or hex ID (770).")
        return False
    print(f"  Opening extended session on 0x{tx_id:03X} ({target})...")
    return await sm.open_session(tx_id, wake=wake)


async def _exec_query(sm: SessionManager, ecu_name_str: str, pid_filter: list[str],
                      ecu_index: dict, pids_data: dict, verbose: bool):
    """Execute query sub-command — query ECU parameters."""
    upper = ecu_name_str.upper()
    if upper not in ecu_index:
        print(f"  ERROR: Unknown ECU '{ecu_name_str}'. Available: {', '.join(ecu_index.keys())}")
        return

    ecu_info = ecu_index[upper]
    tx_id = ecu_info["tx_id"]

    # Refresh stale sessions before switching
    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    # Open session on this ECU if not already tracked
    if not sm.has_session(tx_id):
        # Check if ECU needs session (heuristic: try without first)
        pass

    pids_to_query = ecu_info["pids"]
    if pid_filter:
        # Match filter values flexibly: "BC03" matches key "22BC03", and "22BC03" matches too
        filter_upper = [p.upper() for p in pid_filter]
        pids_to_query = {k: v for k, v in pids_to_query.items()
                         if k.upper() in filter_upper
                         or any(k.upper().endswith(f) for f in filter_upper)}
        if not pids_to_query:
            print(f"  No matching PIDs for filter: {pid_filter}")
            print(f"  Available: {', '.join(sorted(ecu_info['pids'].keys()))}")
            return

    print(f"\n  Querying {upper} (0x{tx_id:03X}) -- {len(pids_to_query)} PIDs")

    for pid_code, pid_info in sorted(pids_to_query.items()):
        await sm.keepalive_stale()
        await sm.terminal.set_header(tx_id)

        resp = await sm.terminal.send_uds(pid_code)
        if not resp.get("ok"):
            error = resp.get("error") or resp.get("nrc_desc", "unknown")
            nrc = resp.get("nrc")
            if nrc is not None:
                print(f"    {pid_code}: NRC 0x{nrc:02X} ({resp['nrc_desc']})")
            else:
                print(f"    {pid_code}: {error}")
            continue

        wican_bytes = elm_hex_to_wican_bytes(resp["hex"])
        params = pid_info["parameters"]
        results = []
        for pname, pdef in params.items():
            expr = pdef.get("expression", "")
            unit = pdef.get("unit", "")
            verified = pdef.get("verified", False)
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                results.append((pname, value, unit, expr, None, verified))
            except Exception as e:
                results.append((pname, None, unit, expr, str(e), verified))

        print(f"    PID {pid_code}:")
        if results:
            print_decoded_params(results, verbose=verbose)
        if not results or verbose:
            # No mapped parameters (unmapped PID) or verbose — always show raw bytes
            print(f"      {resp['hex']}  ({len(resp['bytes'])} bytes)")


async def _exec_raw(sm: SessionManager, spec: str, hold: bool, verbose: bool):
    """Execute raw sub-command."""
    match = re.match(r"^([0-9A-Fa-f]{3})[:\s]([0-9A-Fa-f]+)$", spec)
    if not match:
        print(f"  ERROR: Invalid raw format: {spec}. Expected: TX:PID (e.g., 770:22BC03)")
        return

    tx_id = int(match.group(1), 16)
    service_pid = match.group(2).upper()

    await sm.keepalive_stale()
    await sm.terminal.set_header(tx_id)

    print(f"\n  TX: 0x{tx_id:03X}  Request: {service_pid}")
    response = await sm.terminal.send_uds(service_pid)

    if not response["ok"]:
        error = response.get("error") or response.get("nrc_desc", "unknown error")
        if response.get("nrc") is not None:
            print(f"  NRC: 0x{response['nrc']:02X} -- {response['nrc_desc']}")
        else:
            print(f"  Error: {error}")
    else:
        print(f"  Response ({len(response['bytes'])} bytes): {response['hex']}")
        print()
        print_hexdump(response["bytes"])

    if hold:
        print("\n  Holding session (Ctrl+C to continue pipeline)...")
        bg = sm.start_background_keepalive()
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("  Continuing...")
        finally:
            sm.stop_background_keepalive()


async def _exec_scan(sm: SessionManager, tx_str: str, service_str: str,
                     range_str: str, append: str, verbose: bool):
    """Execute scan sub-command."""
    from .scan import mode_scan

    tx_id = int(tx_str, 16)
    service = int(service_str, 16)
    match = re.match(r"^([0-9A-Fa-f]+)-([0-9A-Fa-f]+)$", range_str)
    if not match:
        print(f"  ERROR: Invalid range: {range_str}")
        return
    pid_range = (int(match.group(1), 16), int(match.group(2), 16))

    await sm.keepalive_stale()

    # mode_scan handles its own header setting and session
    await mode_scan(sm.terminal, tx_id, service, pid_range, verbose, as_json=False,
                    append_bytes=append.upper(), session=False, wake=False)


async def mode_multi(terminal: WiCANTerminal, sub_commands: list[str], pids_data: dict,
                     verbose: bool, no_repl: bool = False):
    """Execute a multi-ECU pipeline and optionally drop into REPL.

    Args:
        terminal: Connected WiCANTerminal.
        sub_commands: List of sub-command strings (e.g., ["skm-wake acc", "query IGPM BC03"]).
        pids_data: Loaded PID definitions.
        verbose: Show debug output.
        no_repl: If True, don't drop into REPL after pipeline.
    """
    commands = parse_sub_commands(sub_commands)
    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)
    repl_executed = False

    try:
        for i, cmd in enumerate(commands):
            cmd_type = cmd["type"]
            step = f"[{i+1}/{len(commands)}]"

            if cmd_type == "skm-wake":
                print(f"\n{step} SKM wakeup ({cmd['level']})...")
                await _exec_skm_wake(sm, cmd["level"], verbose)

            elif cmd_type == "session":
                print(f"\n{step} Session on {cmd['target']}...")
                await _exec_session(sm, cmd["target"], cmd["wake"], ecu_index)

            elif cmd_type == "query":
                pids_str = " ".join(cmd["pids"]) if cmd["pids"] else "all"
                print(f"\n{step} Query {cmd['ecu']} ({pids_str})...")
                await _exec_query(sm, cmd["ecu"], cmd["pids"], ecu_index, pids_data, verbose)

            elif cmd_type == "raw":
                print(f"\n{step} Raw {cmd['spec']}...")
                await _exec_raw(sm, cmd["spec"], cmd["hold"], verbose)

            elif cmd_type == "scan":
                print(f"\n{step} Scan {cmd['tx']} service {cmd['service']} range {cmd['range']}...")
                await _exec_scan(sm, cmd["tx"], cmd["service"], cmd["range"],
                                 cmd["append"], verbose)

            elif cmd_type == "sleep":
                print(f"\n{step} Sleeping {cmd['seconds']}s...")
                await asyncio.sleep(cmd["seconds"])

            elif cmd_type == "repl":
                print(f"\n{step} Entering REPL...")
                repl_executed = True
                await _multi_repl(sm, ecu_index, pids_data, verbose)

        # Auto-REPL if no explicit repl step and not suppressed
        if not repl_executed and not no_repl:
            sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
            if sessions_str:
                print(f"\n  Active sessions: {sessions_str}")
            print(f"\n  Pipeline complete. Entering REPL (use --no-repl to skip)...")
            await _multi_repl(sm, ecu_index, pids_data, verbose)

    except KeyboardInterrupt:
        print("\n  Interrupted.")

    finally:
        sm.stop_background_keepalive()
        print("  Closing all sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (asyncio.TimeoutError, KeyboardInterrupt, Exception):
            pass


async def _multi_repl(sm: SessionManager, ecu_index: dict, pids_data: dict, verbose: bool):
    """Interactive REPL with multi-ECU session awareness.

    Extends the standard REPL with session keepalives and multi-ECU commands.
    """
    from .interactive import mode_interactive
    from .skm_wakeup import mode_skm_wakeup
    from .tester import mode_tester_present
    from .identity import mode_identity

    terminal = sm.terminal
    param_index = build_param_index(pids_data)
    last_response = None
    last_tx_id = None

    # Start background keepalive for all tracked sessions
    bg_task = sm.start_background_keepalive(interval=2.0)

    print()
    print("Multi-ECU REPL -- sessions are kept alive automatically")
    sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
    if sessions_str:
        print(f"  Active sessions: {sessions_str}")
    print()
    print("Commands:")
    print("  AT commands      ATZ, ATSH7E4, etc.")
    print("  UDS requests     2101, 22BC03, etc.")
    print("  session <ECU>    Open extended session on ECU")
    print("  sessions         List active sessions")
    print("  skm [level]      SKM wakeup")
    print("  query <ECU> [PID ...]  Query ECU parameters")
    print("  raw <TX:PID>     Raw UDS request")
    print("  quit / Ctrl+C    Exit REPL")
    print("  (! prefix optional: !query = query)")
    print()

    try:
        # Use asyncio stdin reader instead of run_in_executor(input()) —
        # input() blocks a thread pool thread that can't be interrupted by Ctrl+C
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), __import__("sys").stdin)

        import sys, signal

        # Set up SIGINT to cancel the current readline gracefully
        repl_quit = asyncio.Event()
        old_handler = signal.getsignal(signal.SIGINT)

        def _sigint_handler(sig, frame):
            repl_quit.set()

        signal.signal(signal.SIGINT, _sigint_handler)

        while not repl_quit.is_set():
            sys.stdout.write("multi> ")
            sys.stdout.flush()

            # Race: readline vs quit signal
            read_task = asyncio.ensure_future(reader.readline())
            quit_task = asyncio.ensure_future(repl_quit.wait())
            done, pending = await asyncio.wait(
                [read_task, quit_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            if repl_quit.is_set():
                print("\nExiting REPL...")
                break

            if read_task in done:
                line = read_task.result()
            else:
                break

            if not line:  # EOF
                print("\nExiting REPL...")
                break

            cmd = line.decode("utf-8", errors="replace").strip()
            if not cmd:
                continue

            # Strip optional ! prefix for built-in commands
            cmd_lower = cmd.lower().lstrip("!")

            if cmd_lower in ("quit", "exit", "q"):
                break

            if cmd_lower == "sessions":
                if sm.active_sessions:
                    for tx_id in sm.active_sessions:
                        name = "?"
                        for n, info in ecu_index.items():
                            if info["tx_id"] == tx_id:
                                name = n
                                break
                        print(f"  0x{tx_id:03X} ({name})")
                else:
                    print("  No active sessions.")
                continue

            if cmd_lower.startswith("session "):
                target = cmd.split()[1]
                tx_id = resolve_tx_id(target, ecu_index)
                if tx_id is None:
                    print(f"  Unknown ECU: {target}")
                else:
                    sm.stop_background_keepalive()
                    await sm.open_session(tx_id)
                    bg_task = sm.start_background_keepalive(interval=2.0)
                    print(f"  Session opened on 0x{tx_id:03X}")
                continue

            if cmd_lower.startswith("skm"):
                parts = cmd.split()
                level = parts[1] if len(parts) > 1 else "acc"
                sm.stop_background_keepalive()
                await mode_skm_wakeup(terminal, level, verbose)
                sm._sessions[0x7A5] = __import__("time").monotonic()
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("query "):
                parts = cmd.split()
                # First token might be "query" or "!query"
                ecu = parts[1]
                pids = parts[2:] if len(parts) > 2 else []
                sm.stop_background_keepalive()
                await _exec_query(sm, ecu, pids, ecu_index, pids_data, verbose)
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("raw "):
                spec = cmd.split(None, 1)[1]
                # Strip leading ! if present
                if spec.startswith("!"):
                    spec = spec.lstrip("!")
                sm.stop_background_keepalive()
                await _exec_raw(sm, spec, hold=False, verbose=verbose)
                bg_task = sm.start_background_keepalive(interval=2.0)
                continue

            # Track ATSH commands
            atsh_match = re.match(r"^ATSH\s*([0-9A-Fa-f]{3})$", cmd, re.IGNORECASE)
            if atsh_match:
                last_tx_id = int(atsh_match.group(1), 16)

            # Pause background keepalive during manual command
            sm.stop_background_keepalive()
            await sm.keepalive_stale()

            # Restore header if we know one
            if last_tx_id and not atsh_match:
                await terminal.set_header(last_tx_id)

            try:
                raw = await terminal.send_command(cmd)
                print(raw)

                response = parse_elm_response(raw)
                if response.get("ok") or response.get("nrc") is not None:
                    last_response = response
                    if response.get("nrc") is not None:
                        nrc = response["nrc"]
                        desc = response.get("nrc_desc", "unknown")
                        print(f"  [NRC] 0x{nrc:02X} ({desc})")
            except ValueError as e:
                print(f"  !! {e}")
            except Exception as e:
                print(f"  Error: {e}")

            bg_task = sm.start_background_keepalive(interval=2.0)

    finally:
        sm.stop_background_keepalive()
