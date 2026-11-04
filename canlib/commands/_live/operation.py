"""What a live subcommand does once it has a terminal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from canlib.modes.monitor_options import MonitorOptions

# A handler owns the whole subcommand once connected: ``(args, terminal, pids_data, host, *,
# reconnect=None)``. It reads the flags it needs and calls one ``mode_*`` coroutine.
Handler = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class LiveOperation:
    """The operation a command picked, and what the runtime must know before handing over.

    The command picks its own operation, so nothing selects a handler by looking at which flags
    are set.
    """

    handler: Handler
    # Set for ``monitor``: the raw transport polls with its pipelined client rather than the
    # handler's terminal, and takes these options to do it.
    monitor: MonitorOptions | None = None
    # ``--save``/``--label``/``--state``/``--notes`` mean something to this run.
    records: bool = False
    # The scan types whose aborted previous run is worth a notice ("scan", "iocontrol", ...).
    scan_types: frozenset[str] = frozenset()
