"""The warning every canair command prints while another session holds the device connection.

Only one canair process may talk to the device at a time (``canair.device.lock``). A command that
never touches the device (``decode``, ``captures``, ``pids``) still runs fine, but the user is
probably about to wonder why a live command in another terminal stalls or fails. So any command
that does not take the lock itself says, on stderr and before it starts, who holds it and how to
free it. A command that does take the lock (``takes_device_lock``) gets the full, actionable
refusal from ``WiCANLock.acquire`` instead, so it is skipped here.
"""

from __future__ import annotations

import argparse
import sys

from canair.device.lock import lock_state
from canair.render import ansi

#: Commands that report the lock themselves, or print a script for the shell to eat.
_SKIPPED = frozenset({"lock", "status", "st", "completion"})


def _applies(args: argparse.Namespace) -> bool:
    if getattr(args, "takes_device_lock", False):
        return False
    return getattr(args, "command", None) not in _SKIPPED


def connection_warning() -> list[str] | None:
    """The warning lines, or None when no other session holds the connection."""
    state = lock_state()
    if not state.held or state.ours:
        return None
    holder = state.describe() or "an unknown process"
    return [
        ansi.cerr(
            f"warning: another canair session holds the device connection — {holder}",
            ansi.raw.YELLOW,
        ),
        f"{ansi.cerr('hint:', ansi.raw.DIM)} a live command will fail or wait. Free it with "
        f"{ansi.cerr('canair lock steal', ansi.raw.CYAN)} (asks it to release) or "
        f"{ansi.cerr('canair lock kill', ansi.raw.CYAN)}.",
    ]


def warn_if_connection_held(args: argparse.Namespace) -> None:
    """Print the warning to stderr when it applies. Never raises: it must not break a command."""
    if not _applies(args):
        return
    try:
        lines = connection_warning()
    except Exception:
        return
    for line in lines or ():
        print(line, file=sys.stderr)
