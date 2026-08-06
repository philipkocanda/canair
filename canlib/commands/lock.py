"""``canair lock`` — inspect and clear the device connection mutex.

Only one canair session may talk to the device at a time. Normally the mutex
looks after itself: it is released when the holder exits, and a live holder
stands down when another run asks for the connection with ``--force``.

This command is for the case where you need to look at — or clear — the mutex
without starting a session:

- ``canair lock`` shows who holds it (PID, command line, how long).
- ``canair lock steal`` asks that session to release the connection and waits,
  so the next command can run. Nothing is signalled or killed.
- ``canair lock kill`` signals the holder, for a session too old (or too wedged)
  to release cooperatively. Confirmed first, and only ever aimed at a live
  canair process.
"""

from __future__ import annotations

import argparse

NAME = "lock"


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Show or clear the device connection lock (stuck/orphaned session)",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  canair lock                  # who holds the device connection?
  canair lock --json           # machine-readable
  canair lock steal            # ask the holder to release it, then wait
  canair lock steal --wait 30  # allow a slower teardown
  canair lock kill             # signal a holder that won't release (confirms)
  canair lock kill --hard -y   # SIGKILL it, no prompt (data is in the journal)

exit codes: 0 = lock is free (or was freed), 1 = still held.
""",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    sub = parser.add_subparsers(dest="lock_action", metavar="ACTION")

    steal = sub.add_parser(
        "steal",
        help="Ask the current session to release the connection, then wait",
        description=(
            "Records a release request the holder's watchdog picks up, waits for it to "
            "let go, and leaves the lock free. Never signals or kills anything."
        ),
    )
    from ..lock import FORCE_WAIT_S

    steal.add_argument(
        "--wait",
        type=float,
        default=FORCE_WAIT_S,
        metavar="SECONDS",
        help=f"How long to wait for the holder to release (default: {FORCE_WAIT_S:.0f})",
    )
    steal.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation")
    steal.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    kill = sub.add_parser(
        "kill",
        help="Signal a holder that won't release the connection cooperatively",
        description=(
            "Sends SIGTERM (a graceful stop: the session reconciles its --save journal "
            "on the way out), or SIGKILL with --hard. Only ever aimed at a live canair "
            "process."
        ),
    )
    kill.add_argument(
        "--hard", action="store_true", help="Send SIGKILL instead of SIGTERM (last resort)"
    )
    kill.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation")
    kill.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    parser.set_defaults(func=run, lock_action=None)
    return parser


def _emit(payload: dict, as_json: bool, lines: list[str]) -> None:
    if as_json:
        import json

        print(json.dumps(payload, indent=2))
    else:
        for line in lines:
            print(line)


def run(args) -> int:
    action = getattr(args, "lock_action", None)
    if action == "steal":
        return _steal(args)
    if action == "kill":
        return _kill(args)
    return _show(args)


def _show(args) -> int:
    from ..lock import lock_state

    state = lock_state()
    payload = state.as_dict()
    lines: list[str] = []
    if state.held:
        lines.append(f"  Connection lock: HELD by {state.describe()}")
        if state.ours:
            lines.append("    that's this process")
        if state.holder is not None and state.holder.looks_foreign:
            lines.append("    note: the command line does not look like canair (recycled PID?)")
        if state.steal is not None:
            lines.append(f"    release already requested by PID {state.steal.by}")
        lines.append("")
        lines.append("  Clear it with:  canair lock steal      (asks it to release)")
        lines.append("                  canair lock kill       (signals it)")
        lines.append("  Or just run your command with --force (it asks, then waits).")
    else:
        lines.append("  Connection lock: free")
        if state.steal is not None:
            lines.append(f"    stale release request from PID {state.steal.by} (harmless)")
    if state.error:
        lines.append(f"  {state.error}")
    lines.append(f"  file: {state.path}")
    _emit(payload, args.json, lines)
    return 1 if state.held else 0


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"  {prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _steal(args) -> int:
    import sys

    from ..lock import WiCANLock, describe_acquire_failure, lock_state

    state = lock_state()
    if not state.held:
        _emit(
            {"ok": True, "outcome": "free", "lock": state.as_dict()},
            args.json,
            ["  Connection lock is already free — nothing to do."],
        )
        return 0

    who = state.describe() or "the current session"
    if not args.json and not _confirm(f"Ask {who} to release the device connection?", args.yes):
        print("  Aborted.")
        return 1

    lock = WiCANLock()
    report = None if args.json else (lambda msg: print(f"  {msg}", file=sys.stderr))
    result = lock.try_acquire(force=True, wait=args.wait, report=report)
    if result.ok:
        # We only wanted it *freed*, not held — hand it straight back so the next
        # command can take it.
        lock.release()
        _emit(
            {"ok": True, "outcome": result.outcome, "waited_s": round(result.waited_s, 2)},
            args.json,
            [f"  Connection released after {result.waited_s:.1f}s — the lock is now free."],
        )
        return 0

    _emit(
        {
            "ok": False,
            "outcome": result.outcome,
            "waited_s": round(result.waited_s, 2),
            "pid": result.holder.pid if result.holder else None,
        },
        args.json,
        [describe_acquire_failure(result)],
    )
    return 1


def _kill(args) -> int:
    import os
    import signal
    import time

    from ..lock import holder_info, lock_state

    state = lock_state()
    if not state.held or state.pid is None:
        _emit(
            {"ok": True, "outcome": "free"},
            args.json,
            ["  Connection lock is already free — nothing to signal."],
        )
        return 0
    if state.ours:
        _emit(
            {"ok": False, "outcome": "self"},
            args.json,
            ["  The lock is held by this very process — refusing to signal myself."],
        )
        return 1

    holder = state.holder or holder_info(state.pid)
    if not holder.alive:
        _emit(
            {"ok": False, "outcome": "gone", "pid": holder.pid},
            args.json,
            [f"  PID {holder.pid} is no longer running, yet the lock reads as held — retry."],
        )
        return 1
    if holder.looks_foreign:
        _emit(
            {"ok": False, "outcome": "not_canair", "pid": holder.pid, "cmdline": holder.cmdline},
            args.json,
            [
                f"  Refusing to signal {holder.describe()}: that does not look like a canair "
                "session (a recycled PID?).",
                "  If you are sure, signal it yourself: "
                f"kill {'-9 ' if args.hard else ''}{holder.pid}",
            ],
        )
        return 1

    sig = signal.SIGKILL if args.hard else signal.SIGTERM
    name = "SIGKILL" if args.hard else "SIGTERM"
    if not args.json and not _confirm(f"Send {name} to {holder.describe()}?", args.yes):
        print("  Aborted.")
        return 1

    try:
        os.kill(holder.pid, sig)
    except OSError as e:
        _emit(
            {"ok": False, "outcome": "signal_failed", "pid": holder.pid, "error": str(e)},
            args.json,
            [f"  Could not signal PID {holder.pid}: {e}"],
        )
        return 1

    # Give it a moment to unwind, then report whether the connection is free.
    deadline = time.monotonic() + (3.0 if args.hard else 8.0)
    while time.monotonic() < deadline:
        if not lock_state().held:
            _emit(
                {"ok": True, "outcome": "killed", "pid": holder.pid, "signal": name},
                args.json,
                [
                    f"  Sent {name} to PID {holder.pid} — the connection lock is now free.",
                    "  Any --save data it recorded: canair captures uds --recover",
                ],
            )
            return 0
        time.sleep(0.2)

    _emit(
        {"ok": False, "outcome": "still_held", "pid": holder.pid, "signal": name},
        args.json,
        [
            f"  Sent {name} to PID {holder.pid} but the lock is still held.",
            "  Try again with --hard (SIGKILL)."
            if not args.hard
            else "  It may be unkillable (stuck in the kernel); a reboot may be the only option.",
        ],
    )
    return 1
