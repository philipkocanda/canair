"""``canair captures uds --set-state STATES`` — manual session-state tagging.

Sets the ``vehicle_states`` on the scope-selected sessions directly, for the
sessions whose state is known to the operator but not inferable from the decoded
data — e.g. a body/low-power ECU read whose label records the ignition position
(``ACC`` / ``SLEEP``) that no polled powertrain signal can prove. The
manual counterpart to ``--backfill-states`` (which only infers).

Mirrors the ``cmd_delete`` mutating-mode pattern: operate on the already
scope-filtered entries, preview before writing, confirm on a TTY unless
``--yes``, and never write on ``--dry-run``. The caller (``run``) refuses a
bare invocation with no scope filter, so this can never blanket-relabel every
session.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from canlib.states import allowed_states, join_states, parse_states

if TYPE_CHECKING:
    from collections.abc import Sequence

    from canlib.captures.types import CaptureEntry


def cmd_set_state(
    entries: Sequence[CaptureEntry],
    states_arg: str,
    *,
    captures_dir: Path | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Set ``vehicle_states`` on every scope-selected session to ``states_arg``."""
    import json as _json

    from canlib.captures import set_session_states

    from .query import group_sessions
    from .session_gate import refuse_write

    states = parse_states(states_arg)
    if not states:
        print(
            "  error: --set-state needs at least one state token "
            "(e.g. --set-state ACC). To clear a session's state, use the "
            "monitor/back-fill flow.",
            file=sys.stderr,
        )
        return 2

    # Soft vocabulary check (free text is still accepted, like validate captures).
    vocab = allowed_states()
    unknown = [s for s in states if s not in vocab]
    if unknown and not as_json:
        print(
            f"  warning: {', '.join(unknown)} not in the vehicle_states.yaml "
            "vocabulary (writing anyway; see `canair states`)."
        )

    sessions = group_sessions(entries)
    rows: list[dict] = [
        {
            "file": g["file"],
            "path": g["path"],
            "session_idx": g["session_idx"],
            "date": g["date"],
            "label": g["label"],
            "times": g["times"],
            "recorded": parse_states(g.get("vehicle_states") or []),
            "new_states": states,
            "will_write": parse_states(g.get("vehicle_states") or []) != states,
        }
        for g in sessions
    ]

    to_write = [r for r in rows if r["will_write"]]

    if as_json and dry_run:
        print(_json.dumps(rows, indent=2))
        return 0

    # Under --json stdout carries only the JSON; the human report goes to stderr.
    out = sys.stderr if as_json else sys.stdout
    _print_report(rows, states, out)

    if not to_write:
        if as_json:
            print(_json.dumps(rows, indent=2))
        return 0

    if dry_run:
        print("  (--dry-run: nothing written)", file=out)
        return 0

    stop = refuse_write(
        (Path(r["path"]) for r in to_write),
        verb="relabelled",
        prompt=f"Set state to {join_states(states)!r} on these {len(to_write)} session(s)?",
        what="set state",
        assume_yes=assume_yes,
        out=out,
    )
    if stop is not None:
        return stop

    written = 0
    for r in to_write:
        try:
            set_session_states(Path(r["path"]), r["session_idx"], states)
            written += 1
            print(
                f"    \u2192 {r['file']} [{r['session_idx']}] {r['date']} = {join_states(states)}",
                file=out,
            )
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {r['file']} [{r['session_idx']}]: {ex}", file=out)

    print(f"  Set state on {written} session(s).", file=out)
    if as_json:
        print(_json.dumps(rows, indent=2))
    return 0 if written == len(to_write) else 1


def _print_report(rows: list[dict], states: list[str], out: TextIO) -> None:
    print(
        f"  Setting state = {join_states(states)} on "
        f"{sum(1 for r in rows if r['will_write'])} of {len(rows)} "
        f"matched session(s):\n",
        file=out,
    )
    for r in rows:
        mark = "*" if r["will_write"] else " "
        span = r["times"][0][:8] if r["times"] else "--:--:--"
        recorded = join_states(r["recorded"]) or "(none)"
        label = (r["label"] or "")[:40]
        note = "" if r["will_write"] else "  (already)"
        print(
            f"  {mark} {r['date']} {span}  {recorded:24} → {join_states(states)}  {label}{note}",
            file=out,
        )
    print(file=out)
