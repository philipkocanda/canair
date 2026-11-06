"""Multi-ECU pipeline — the interactive REPL.

The session-aware interactive prompt entered after (or during) a multi pipeline.
Composes the parsing (:mod:`canair.query.steps`) and execution
(:mod:`canair.live.read.exec`) layers; orchestration lives in
:mod:`canair.live.read.multi`.
"""

import asyncio
import re

from canair.ecus.pids import build_param_index
from canair.profile.model import Profile
from canair.query.steps import query_selectors, resolve_tx_id
from canair.render import messages
from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.parse import parse_uds_response

from ..elm.skm_wakeup import SKM_SESSION_MODE, SKM_TX_ID, mode_skm_wakeup
from .exec import _exec_query, _exec_raw


async def _multi_repl(
    sm: SessionManager,
    ecu_index: dict,
    pids_data: dict,
    verbose: bool,
    include_static: bool = False,
    *,
    profile: Profile,
):
    """Interactive REPL with multi-ECU session awareness.

    Extends the standard REPL with session keepalives and multi-ECU commands.
    """
    terminal = sm.terminal
    _param_index = build_param_index(pids_data)
    last_tx_id = None

    # Start background keepalive for all tracked sessions
    sm.start_background_keepalive(interval=2.0)

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
    print("  query <ECU> [PID ...]  Query ECU signals")
    print("  raw <TX:PID>     Raw UDS request")
    print("  quit / Ctrl+C    Exit REPL")
    print("  (! prefix optional: !query = query)")
    print()

    try:
        # Use asyncio stdin reader instead of run_in_executor(input()) —
        # input() blocks a thread pool thread that can't be interrupted by Ctrl+C
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), __import__("sys").stdin
        )

        import signal
        import sys

        # Set up SIGINT to cancel the current readline gracefully
        repl_quit = asyncio.Event()
        _old_handler = signal.getsignal(signal.SIGINT)

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
                print("\nExiting REPL…")
                break

            if read_task in done:
                line = read_task.result()
            else:
                break

            if not line:  # EOF
                print("\nExiting REPL…")
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
                tx_id = resolve_tx_id(target, ecu_index, profile=profile)
                if tx_id is None:
                    messages.error(f"unknown ECU: {target}")
                else:
                    await sm.stop_background_keepalive()
                    await sm.open_session(tx_id)
                    sm.start_background_keepalive(interval=2.0)
                    print(f"  Session opened on 0x{tx_id:03X}")
                continue

            if cmd_lower.startswith("skm"):
                parts = cmd.split()
                level = parts[1] if len(parts) > 1 else "acc"
                if not isinstance(terminal, Elm327Terminal):
                    messages.error("skm needs an ELM327 transport (wican-ws / elm327-tcp).")
                    continue
                await sm.stop_background_keepalive()
                await mode_skm_wakeup(terminal, level, verbose)
                sm.track(SKM_TX_ID, SKM_SESSION_MODE)
                sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("query "):
                parts = cmd.split()
                # First token might be "query" or "!query"; rest is a mini-language query.
                try:
                    selectors = query_selectors(parts[1:])
                except ValueError as ex:
                    messages.error(f"invalid query: {ex}")
                    continue
                await sm.stop_background_keepalive()
                for ecu, pids in selectors:
                    await _exec_query(
                        sm,
                        ecu,
                        pids,
                        ecu_index,
                        pids_data,
                        verbose,
                        include_static=include_static,
                        profile=profile,
                    )
                sm.start_background_keepalive(interval=2.0)
                continue

            if cmd_lower.startswith("raw "):
                spec = cmd.split(None, 1)[1]
                # Strip leading ! if present
                if spec.startswith("!"):
                    spec = spec.lstrip("!")
                await sm.stop_background_keepalive()
                await _exec_raw(sm, spec, hold=False, verbose=verbose, profile=profile)
                sm.start_background_keepalive(interval=2.0)
                continue

            # Track ATSH commands
            atsh_match = re.match(r"^ATSH\s*([0-9A-Fa-f]{3})$", cmd, re.IGNORECASE)
            if atsh_match:
                last_tx_id = int(atsh_match.group(1), 16)

            # Pause background keepalive during manual command
            await sm.stop_background_keepalive()
            await sm.keepalive_stale()

            # Restore header if we know one
            if last_tx_id and not atsh_match:
                await terminal.set_header(last_tx_id)

            try:
                raw = await terminal.send_command(cmd)
                print(raw)

                response = parse_uds_response(raw, request=cmd)
                if response.get("ok") or response.get("nrc") is not None:
                    _last_response = response
                    if response.get("nrc") is not None:
                        nrc = response["nrc"]
                        desc = response.get("nrc_desc", "unknown")
                        print(f"  [NRC] 0x{nrc:02X} ({desc})")
            except Exception as e:
                messages.error(str(e))

            sm.start_background_keepalive(interval=2.0)

    finally:
        await sm.stop_background_keepalive()
