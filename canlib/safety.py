"""Command safety policy and enforcement — shared across every transport.

The blocklist (:data:`BLOCKED_UDS_SERVICES` / :func:`check_command_safety`) is a
pure, transport-independent policy. :func:`enforce_command_safety` *applies* that
policy uniformly: every terminal routes outbound commands through it before they
reach the bus, so ``wican-ws`` and ``slcan-tcp`` (and any future transport) get
identical protection and identical ``--unsafe`` confirmation behavior. Never
re-implement the guard per transport.
"""

from __future__ import annotations

import asyncio
import sys

from .log import log_command

# UDS services that can write to ECU memory, reflash firmware, or actuate
# physical outputs. Blocked by default to prevent accidental damage.
BLOCKED_UDS_SERVICES = {
    0x2E: "WriteDataByIdentifier (write to ECU memory)",
    0x34: "RequestDownload (ECU reflash)",
    0x35: "RequestUpload (ECU memory dump)",
    0x36: "TransferData (flash data transfer)",
    0x37: "RequestTransferExit (finalize flash)",
    0x38: "RequestFileTransfer",
}


def check_command_safety(cmd: str) -> str | None:
    """Check if a command is potentially dangerous.

    Returns an error message if the command is blocked, or None if safe.
    Checks both raw UDS hex commands and AT commands.
    """
    clean = cmd.strip().upper()

    if clean.startswith("AT"):
        return None

    hex_only = clean.replace(" ", "")
    if not hex_only or not all(c in "0123456789ABCDEF" for c in hex_only):
        return None

    if len(hex_only) < 2:
        return None

    service_byte = int(hex_only[:2], 16)

    if service_byte in BLOCKED_UDS_SERVICES:
        desc = BLOCKED_UDS_SERVICES[service_byte]
        return f"BLOCKED: UDS service 0x{service_byte:02X} -- {desc}"

    if service_byte == 0x10 and len(hex_only) >= 4:
        sub = int(hex_only[2:4], 16)
        if sub == 0x02:
            return (
                "BLOCKED: DiagnosticSessionControl sub 0x02 "
                "(programmingSession) -- required for flash/write operations"
            )
        if sub == 0x85:
            return (
                "BLOCKED: StartDiagnosticSession sub 0x85 "
                "(KWP2000 ECU programming mode) -- required for flash/write operations"
            )
        # KWP2000 uses OEM-defined session modes in the 0x80-0xFF band; some are
        # development/programming modes that are unsafe to enter blind. Allow the
        # well-known safe diagnostic sessions and require --unsafe for the rest of
        # the 0x8x range. Safe: 0x81 standardDiagnosticSession, 0x82 (Hyundai/Kia
        # periodic/EOL diagnostic session), 0x83 extendedDiagnosticSession — all
        # read-only diagnostic sessions used by scan tools. 0x85 (programming)
        # stays blocked above.
        if 0x80 <= sub <= 0xFF and sub not in (0x81, 0x82, 0x83):
            return (
                f"BLOCKED: StartDiagnosticSession sub 0x{sub:02X} "
                "(unrecognized KWP2000 session mode) -- may be a programming/development "
                "mode; re-run with --unsafe if you are certain it is safe"
            )

    return None


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
