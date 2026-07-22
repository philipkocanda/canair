"""Command safety enforcement — shared across every transport.

The blocklist itself (``BLOCKED_UDS_SERVICES`` / :func:`check_command_safety`
in :mod:`canlib.elm327`) is a pure, transport-independent policy. This module
*applies* that policy uniformly: every terminal routes outbound commands through
:func:`enforce_command_safety` before they reach the bus, so ``wican-ws`` and
``slcan-tcp`` (and any future transport) get identical protection and identical
``--unsafe`` confirmation behavior. Never re-implement the guard per transport.
"""

from __future__ import annotations

import asyncio
import sys

from .elm327 import check_command_safety
from .log import log_command


async def enforce_command_safety(cmd: str, unsafe: bool) -> None:
    """Enforce the command blocklist before a command is sent.

    Raises :class:`ValueError` if ``cmd`` is blocked and not permitted. In
    default (safe) mode a blocked command is refused outright. With
    ``unsafe=True`` the user is prompted for interactive confirmation and must
    type ``YES`` to proceed; anything else declines and raises.
    """
    blocked = check_command_safety(cmd)
    if not blocked:
        return

    if not unsafe:
        log_command(f"{cmd}  !! {blocked}")
        raise ValueError(blocked)

    print(f"\n  !! WARNING: {blocked}", file=sys.stderr)
    print("  !! --unsafe mode is active. The user MUST be consulted and", file=sys.stderr)
    print("  !! must explicitly give consent before this command is executed.", file=sys.stderr)
    print("  !! This command can cause irreversible damage to vehicle ECUs.", file=sys.stderr)
    try:
        confirm = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: input("  !! Type 'YES' to execute, anything else to skip: "),
        )
    except (EOFError, KeyboardInterrupt):
        confirm = ""
    if confirm.strip() != "YES":
        log_command(f"{cmd}  !! {blocked} -- user declined")
        raise ValueError(f"Command declined by user: {cmd}")
    log_command(f"{cmd}  !! {blocked} -- user confirmed (unsafe mode)")
