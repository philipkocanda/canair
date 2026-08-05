"""``canair captures uds --backfill-states`` — offline state inference back-fill.

Infers each session's ``vehicle_states`` from its decoded captures (see
:mod:`canlib.state_infer`) and writes them back into ``captures/`` via the
session-level state writer. By default it only *fills* sessions that have no
recorded state; a session whose recorded state contradicts the decoded evidence
is reported but left untouched unless ``--overwrite`` is given.

Mirrors the ``cmd_delete`` mutating-mode pattern: operate on the already
scope-filtered entries, preview before writing, confirm on a TTY unless
``--yes``, and never write on ``--dry-run``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from canlib.state_infer import (
    DEFAULT_CYCLE_TOL_S,
    SessionInference,
    infer_session_states,
)
from canlib.states import parse_states

if TYPE_CHECKING:
    from collections.abc import Sequence

    from canlib.capture_types import CaptureEntry

# Verdict for a session after comparing inferred vs recorded states.
_FILL = "fill"  # recorded empty, inference available → write
_AGREE = "agree"  # inferred ⊆ recorded → nothing to do
_EXTRA = "extra"  # inference adds states not recorded (no contradiction)
_CONFLICT = "conflict"  # a recorded state is provably false given the evidence
_UNDETERMINED = "undetermined"  # nothing inferable


def _classify(recorded: list[str], inf: SessionInference) -> tuple[str, list[str]]:
    """Return ``(verdict, new_states)`` for a session.

    ``new_states`` is what would be written under the relevant policy (fill, or
    overwrite). For a conflict/extra it merges the inferred states with the
    recorded ones that aren't provably false, so human-supplied states the
    inference can't detect (e.g. SLEEP) survive an overwrite while a proven
    contradiction is corrected.
    """
    rec = {s.upper() for s in recorded}
    inferred = set(inf.inferred)
    false = set(inf.definitely_false)

    if not inf.inferred:
        return _UNDETERMINED, list(recorded)
    if not rec:
        return _FILL, inf.inferred
    if rec & false:
        merged = inferred | (rec - false)
        return _CONFLICT, _order(merged)
    if inferred - rec:
        merged = inferred | rec
        return _EXTRA, _order(merged)
    return _AGREE, list(recorded)


def _order(tokens: set[str]) -> list[str]:
    from canlib.states import _order_states

    return _order_states(list(tokens))


def cmd_backfill_states(
    entries: Sequence[CaptureEntry],
    *,
    captures_dir: Path | None = None,
    overwrite: bool = False,
    cycle_tol: float = DEFAULT_CYCLE_TOL_S,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Infer and back-fill session vehicle_states over the scope-filtered entries."""
    import json as _json

    from canlib.captures import set_session_states
    from canlib.pids import build_ecu_index, load_pids
    from canlib.states import StatePredicateError, join_states, load_states

    from .backfill_render import print_report
    from .query import group_sessions

    cdir = _resolve_captures_dir(captures_dir)

    try:
        rules = load_states()
    except StatePredicateError as ex:
        print(f"error: invalid vehicle_states.yaml predicate: {ex}", file=sys.stderr)
        return 2
    if not rules or not any(r.predicate is not None for r in rules):
        print(
            "  No state predicates defined (vehicle_states.yaml has no `when:` rules) "
            "— nothing to infer from.",
            file=sys.stderr,
        )
        return 1

    ecu_index = build_ecu_index(load_pids())

    # Payload captures grouped by session identity for decoding.
    payloads_by_session: dict[tuple[str, int], list[CaptureEntry]] = {}
    for e in entries:
        if not e.get("payload"):
            continue
        key = (e["file"], e.get("_session_idx", 0))
        payloads_by_session.setdefault(key, []).append(e)

    sessions = group_sessions(entries)

    rows: list[dict] = []
    for g in sessions:
        key = (g["file"], g["session_idx"])
        caps = payloads_by_session.get(key, [])
        recorded = parse_states(g.get("vehicle_states") or [])
        inf = infer_session_states(caps, rules, ecu_index, cycle_tol=cycle_tol)
        verdict, new_states = _classify(recorded, inf)

        writeable = verdict == _FILL or (overwrite and verdict in (_CONFLICT, _EXTRA))
        will_write = writeable and parse_states(new_states) != recorded

        rows.append(
            {
                "file": g["file"],
                "session_idx": g["session_idx"],
                "date": g["date"],
                "label": g["label"],
                "times": g["times"],
                "recorded": recorded,
                "inferred": inf.inferred,
                "definitely_false": inf.definitely_false,
                "verdict": verdict,
                "new_states": new_states,
                "timed": inf.timed,
                "n_cycles": inf.n_cycles,
                "n_params": inf.n_decoded_params,
                "will_write": will_write,
            }
        )

    to_write = [r for r in rows if r["will_write"]]

    if as_json and dry_run:
        print(_json.dumps(rows, indent=2))
        return 0

    print_report(rows, overwrite=overwrite, dry_run=dry_run)

    if not to_write:
        if as_json:
            print(_json.dumps(rows, indent=2))
        return 0

    if dry_run:
        print("  (--dry-run: nothing written)")
        return 0

    if not assume_yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "  error: refusing to write states without confirmation "
                "(pass --yes for non-interactive use, or --dry-run to preview).",
                file=sys.stderr,
            )
            return 2
        resp = input(f"  Write states to these {len(to_write)} session(s)? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("  Cancelled — nothing written.")
            return 1

    written = 0
    for r in to_write:
        try:
            set_session_states(cdir / r["file"], r["session_idx"], r["new_states"])
            written += 1
            print(
                f"    \u2192 {r['file']} [{r['session_idx']}] "
                f"{r['date']} = {join_states(r['new_states'])}"
            )
        except Exception as ex:  # keep going; report the failure
            print(f"    ! {r['file']} [{r['session_idx']}]: {ex}")

    print(f"  Wrote states to {written} session(s).")
    if as_json:
        print(_json.dumps(rows, indent=2))
    return 0 if written == len(to_write) else 1


def _resolve_captures_dir(explicit: Path | None) -> Path:
    """Captures dir from --dir, else the active profile's captures/."""
    if explicit is not None:
        return explicit
    from canlib.profile import active

    return active().captures_dir
