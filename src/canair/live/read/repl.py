"""Multi-ECU pipeline — the interactive REPL.

The session-aware interactive prompt entered after (or during) a multi pipeline.
Composes the parsing (:mod:`canair.query.steps`) and execution
(:mod:`canair.live.read.exec`) layers; orchestration lives in
:mod:`canair.live.read.multi`.

The background keepalive runs for the whole REPL. Every command that sets a header and
sends holds ``terminal.transaction()`` across both, so a keepalive never re-points one.
"""

import asyncio
import re
import signal
import sys
from dataclasses import dataclass

from canair.profile.model import Profile
from canair.query.steps import query_selectors, resolve_tx_id
from canair.render import messages
from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.session_manager import SessionManager
from canair.uds.parse import parse_uds_response

from ..elm.skm_wakeup import SKM_SESSION_MODE, SKM_TX_ID, mode_skm_wakeup
from .exec import _exec_raw, exec_query

_ATSH = re.compile(r"^ATSH\s*([0-9A-Fa-f]{3})$", re.IGNORECASE)

_HELP = """
Multi-ECU REPL -- sessions are kept alive automatically
{sessions}
Commands:
  AT commands      ATZ, ATSH7E4, etc.
  UDS requests     2101, 22BC03, etc.
  session <ECU>    Open extended session on ECU
  sessions         List active sessions
  skm [level]      SKM wakeup
  query <ECU> [PID ...]  Query ECU signals
  raw <TX:PID>     Raw UDS request
  quit / Ctrl+C    Exit REPL
  (! prefix optional: !query = query)
"""


@dataclass
class ReplCommands:
    """What one REPL line does. :meth:`handle` returns False when the line ends the REPL."""

    sm: SessionManager
    ecu_index: dict
    pids_data: dict
    verbose: bool
    include_static: bool
    profile: Profile
    # The ECU a typed ATSH named: a bare request goes there, whatever a keepalive set since.
    last_tx_id: int | None = None

    async def handle(self, cmd: str) -> bool:
        cmd_lower = cmd.lower().lstrip("!")  # the ! prefix is optional for built-ins
        if cmd_lower in ("quit", "exit", "q"):
            return False
        if cmd_lower == "sessions":
            self._list_sessions()
        elif cmd_lower.startswith("session "):
            await self._open(cmd.split()[1])
        elif cmd_lower.startswith("skm"):
            await self._skm(cmd.split())
        elif cmd_lower.startswith("query "):
            await self._query(cmd.split()[1:])
        elif cmd_lower.startswith("raw "):
            spec = cmd.split(None, 1)[1].lstrip("!")
            await _exec_raw(self.sm, spec, hold=False, verbose=self.verbose, profile=self.profile)
        else:
            await self._send(cmd)
        return True

    def _list_sessions(self) -> None:
        if not self.sm.active_sessions:
            print("  No active sessions.")
            return
        names = {info["tx_id"]: name for name, info in self.ecu_index.items()}
        for tx_id in self.sm.active_sessions:
            print(f"  0x{tx_id:03X} ({names.get(tx_id, '?')})")

    async def _open(self, target: str) -> None:
        tx_id = resolve_tx_id(target, self.ecu_index, profile=self.profile)
        if tx_id is None:
            messages.error(f"unknown ECU: {target}")
            return
        outcome = await self.sm.open_session(tx_id)
        if outcome.ok:
            print(f"  Session opened on 0x{tx_id:03X}")

    async def _skm(self, parts: list[str]) -> None:
        terminal = self.sm.terminal
        if not isinstance(terminal, Elm327Terminal):
            messages.error("skm needs an ELM327 transport (wican-ws / elm327-tcp).")
            return
        level = parts[1] if len(parts) > 1 else "acc"
        async with terminal.transaction():
            await mode_skm_wakeup(terminal, level, self.verbose)
        self.sm.track(SKM_TX_ID, SKM_SESSION_MODE)

    async def _query(self, tokens: list[str]) -> None:
        try:
            selectors = query_selectors(tokens)
        except ValueError as ex:
            messages.error(f"invalid query: {ex}")
            return
        for ecu, pids in selectors:
            await exec_query(
                self.sm,
                ecu,
                pids,
                self.ecu_index,
                self.pids_data,
                self.verbose,
                include_static=self.include_static,
                profile=self.profile,
            )

    async def _send(self, cmd: str) -> None:
        """A typed AT command or UDS request, sent as written."""
        terminal = self.sm.terminal
        atsh = _ATSH.match(cmd)
        if atsh:
            self.last_tx_id = int(atsh.group(1), 16)
        try:
            async with terminal.transaction():
                if self.last_tx_id is not None and not atsh:
                    await terminal.set_header(self.last_tx_id)
                raw = await terminal.send_command(cmd)
        except Exception as e:
            messages.error(str(e))
            return
        print(raw)
        response = parse_uds_response(raw, request=cmd)
        if response.get("nrc") is not None:
            print(f"  [NRC] 0x{response['nrc']:02X} ({response.get('nrc_desc', 'unknown')})")


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
    commands = ReplCommands(sm, ecu_index, pids_data, verbose, include_static, profile)
    sm.start_background_keepalive()

    sessions = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
    print(_HELP.format(sessions=f"  Active sessions: {sessions}\n" if sessions else ""))

    repl_quit = asyncio.Event()
    old_handler = signal.getsignal(signal.SIGINT)
    try:
        # An asyncio stdin reader rather than run_in_executor(input()): input() blocks a
        # thread-pool thread that Ctrl+C cannot interrupt.
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        signal.signal(signal.SIGINT, lambda _sig, _frame: repl_quit.set())

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
            if repl_quit.is_set() or read_task not in done:
                print("\nExiting REPL…")
                break
            line = read_task.result()
            if not line:  # EOF
                print("\nExiting REPL…")
                break
            cmd = line.decode("utf-8", errors="replace").strip()
            if cmd and not await commands.handle(cmd):
                break
    finally:
        signal.signal(signal.SIGINT, old_handler)
        await sm.stop_background_keepalive()
