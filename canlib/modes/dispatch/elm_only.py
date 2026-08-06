"""Dispatch for the two paths that need ELM327 text semantics.

The SKM relay-wake (raw ``ATSH`` sends and frame collection) and the interactive
REPL (an ELM327 command prompt) both depend on behaviour the raw SLCAN terminal
does not provide — it uses ``set_header()``, not ``ATSH`` parsing — so each guards
on the shared :class:`~canlib.transport.elm327_terminal.Elm327Terminal` engine and
reports a clear error on ``slcan-tcp`` rather than half-working.
"""

from __future__ import annotations

import sys

from canlib.modes import mode_interactive, mode_skm_wakeup
from canlib.transport.elm327_terminal import Elm327Terminal
from canlib.transport.protocol import Terminal


async def handle_skm_wake(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
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


async def handle_interactive(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if not isinstance(terminal, Elm327Terminal):
        print(
            "Error: the interactive REPL is only supported on the ELM327 "
            "transports (wican-ws / elm327-tcp), not slcan-tcp.",
            file=sys.stderr,
        )
        sys.exit(1)
    await mode_interactive(terminal, pids_data, args.verbose)
