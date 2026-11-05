"""Run one live operation over a connected terminal, under one error policy.

The operation is whatever the command chose to do (read a PID set, sweep a range, poll a
monitor ...): a coroutine over a :class:`~canair.transport.protocol.Terminal` and the loaded
definitions. This module knows nothing about flags or which command asked. It owns what every
live session shares: revision detection before the first read, the classified error message,
the ``--recover`` hint, and writing back the response lengths the session proved.

Typed against the ``Terminal`` protocol rather than a concrete class, so the same session serves
both transports (ELM327 over WebSocket/TCP, and raw SLCAN with client-side ISO-TP) and an
operation reaching for a transport-specific attribute is a ``ty`` error: the compiler-checked
form of the "keep the WiCAN replaceable" rule.

It lives in :mod:`canair.live` because :mod:`canair.live.read.raw_ops` runs the raw transport
through :func:`run_session_guarded` as well, and a mode importing upward into the command layer
would be a layering inversion. The CLI keeps only what is genuinely CLI: turning a parsed command
line into an operation and a :class:`SessionOptions`, and constructing the terminal.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from canair.core.prompt import PromptUnavailable
from canair.render import messages
from canair.transport.protocol import Terminal

__all__ = ["Operation", "SessionOptions", "run_session_guarded"]

# What a command asks of the session: it owns the whole run once started, and reports its own
# errors (several exit non-zero via sys.exit rather than returning). It receives the definitions
# as they are *after* revision detection, which can change which PIDs exist.
Operation = Callable[[Terminal, dict], Awaitable[None]]


@dataclass(frozen=True)
class SessionOptions:
    """The flags a session itself reads (the operation closes over its own)."""

    as_json: bool = False
    timings: bool = False
    verbose: bool = False
    # False under ``--no-learn-frames``: do not write response lengths into the profile.
    learn_frames: bool = True
    # The invocation records captures, so a dropped session points at ``--recover``.
    saving: bool = False
    # The read/monitor steps, which name the ECUs whose revision to detect first.
    steps: Sequence[str] | None = None


async def run_session_guarded(
    terminal: Terminal,
    pids_data,
    host,
    operation: Operation,
    *,
    options: SessionOptions,
    transport_label: str,
) -> int:
    """Run ``operation`` (+ optional timings) under unified error handling.

    The single, transport-agnostic home for "a live session hit a transport/IO
    failure" — shared by both the ELM (``async_main``) and raw (``run_raw``)
    entry points so a dropped/failed bus is always a clean, classified message
    (never a traceback), with a ``--recover`` hint when a ``--save`` session was
    in flight (its data is safe in the write-ahead journal). Returns a process
    exit code: 0 on success, 1 on a transport failure, 2 when the command
    blocklist (:mod:`canair.safety.blocklist`) refused a request or a confirmation had no
    terminal and no flag to answer it (1 when the user declined the ``--unsafe`` prompt).

    The caller owns the terminal lifecycle (construct/close) and any
    transport-specific ``finally`` (session-end logging, reboot). ``KeyboardInterrupt``
    is intentionally *not* caught here — the operations reconcile their journals
    on interrupt, and ``run_live`` reports the interrupt.
    """
    from canair.safety.blocklist import CommandDeclined, SafetyBlocked
    from canair.transport.errors import describe_transport_error, transport_error_types

    try:
        from canair.live.revision_probe import detect_revisions, targeted_ecus

        if await detect_revisions(terminal, targeted_ecus(options.steps)):
            from canair.ecus.pids import load_pids

            # A settled revision changes which PIDs exist; read them before the mode does.
            pids_data = load_pids()
        await operation(terminal, pids_data)
        if options.timings:
            from canair.transport.timing import print_timings

            print_timings(terminal.timings, as_json=options.as_json)
        return 0
    except CommandDeclined as e:
        # The user said no to an --unsafe consent prompt: the run ends, nothing was sent.
        messages.error(str(e))
        return 1
    except SafetyBlocked as e:
        # The command blocklist refused something this mode was about to send
        # (e.g. a programming session): a policy outcome, not a crash.
        messages.error(str(e))
        return 2
    except PromptUnavailable as e:
        # A confirmation no one can answer (no terminal, no --yes): a refusal, not a crash.
        messages.error(str(e))
        return 2
    except transport_error_types() as e:
        messages.error(
            describe_transport_error(
                e, host=host, transport_label=transport_label, saving=options.saving
            )
        )
        return 1
    finally:
        _persist_frame_counts(options, terminal, pids_data)


def _persist_frame_counts(options: SessionOptions, terminal: Terminal, pids_data) -> None:
    """Record what this session proved about response lengths into the profile.

    Here rather than in either entry point because ``terminal.frame_counts`` is
    part of the :class:`~canair.transport.protocol.Terminal` contract, so one
    implementation covers every transport — the same reason the error handling
    above lives here. In the ``finally`` so an interrupted or failed session still
    banks the counts it did confirm; the evidence is already collected, and a
    Ctrl-C during a long monitor run is the *normal* way it ends.
    """
    if not options.learn_frames:
        return
    from canair.yaml_edit.response_frames import persist

    try:
        persist(pids_data, terminal.frame_counts, verbose=options.verbose)
    except Exception:
        # Never let a bookkeeping by-product change the outcome of the command the
        # user actually ran, nor mask the exception this `finally` is unwinding.
        logging.getLogger(__name__).exception("failed to persist response frame counts")
