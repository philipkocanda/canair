"""Handler for the path that needs ELM327 text semantics: the interactive REPL.

An ELM327 command prompt depends on behaviour the raw SLCAN terminal does not provide (it uses
``set_header()``, not ``ATSH`` parsing), so it guards on the shared
:class:`~canlib.transport.elm327.terminal.Elm327Terminal` engine and reports a clear error on
``slcan-tcp`` rather than half-working.
"""

from __future__ import annotations

import sys

from canlib.live.interactive import mode_interactive
from canlib.transport.elm327.terminal import Elm327Terminal
from canlib.transport.protocol import Terminal


async def handle_interactive(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if not isinstance(terminal, Elm327Terminal):
        print(
            "Error: the interactive REPL is only supported on the ELM327 "
            "transports (wican-ws / elm327-tcp), not slcan-tcp.",
            file=sys.stderr,
        )
        sys.exit(1)
    await mode_interactive(terminal, pids_data, args.verbose)
