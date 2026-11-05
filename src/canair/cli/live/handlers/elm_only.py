"""Handler for the path that needs ELM327 text semantics: the interactive REPL.

An ELM327 command prompt depends on behaviour the raw SLCAN terminal does not provide (it uses
``set_header()``, not ``ATSH`` parsing), so it guards on the shared
:class:`~canair.transport.elm327.terminal.Elm327Terminal` engine and reports a clear error on
``slcan-tcp`` rather than half-working.
"""

from __future__ import annotations

from canair.live.interactive import mode_interactive
from canair.render import messages
from canair.transport.elm327.terminal import Elm327Terminal
from canair.transport.protocol import Terminal


async def handle_interactive(args, terminal: Terminal, pids_data, host, *, reconnect=None) -> None:
    if not isinstance(terminal, Elm327Terminal):
        messages.fail(
            "the interactive REPL is only supported on the ELM327 "
            "transports (wican-ws / elm327-tcp), not slcan-tcp.",
            code=2,
        )
    await mode_interactive(terminal, pids_data, args.verbose)
