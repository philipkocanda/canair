"""``canair captures uds --backfill-state-spans`` — reconstruct state timelines.

``--backfill-states`` answers *which* states a session was in; this answers
*when*. It re-decodes each session's captures per pseudo-cycle (see
:func:`canair.captures.state_inference.infer_state_spans`) and writes the resulting
``state_spans`` block, so every analysis command can resolve a capture's state at
its own timestamp instead of inheriting the session-wide union.

The write is deliberately **narrowing-only for evidence it actually has**. A
recorded state the predicates cannot place in time — one with no ``when:`` rule at
all, or one recorded but never matched — is carried into *every* span, so a
back-fill can never make a capture stop matching a state it used to match. When
that leaves every span identical there is nothing temporal to record and the
session is skipped rather than bloated with a redundant block.

Mirrors the ``cmd_backfill_states`` mutating-mode pattern: operate on the
scope-filtered entries, preview, confirm on a TTY unless ``--yes``, never write on
``--dry-run``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from canair.captures.state_inference import DEFAULT_CYCLE_TOL_S, session_state_spans
from canair.captures.store import entry_path
from canair.core.prompt import confirm
from canair.profile import active
from canair.render import messages
from canair.states.predicates import StatePredicateError
from canair.states.tokens import join_states, parse_states
from canair.states.vocabulary import load_states

from .echo import echo_write

if TYPE_CHECKING:
    from collections.abc import Sequence

    from canair.captures.types import CaptureEntry

# Verdict for a session after attempting to reconstruct its timeline.
_SPANS = "spans"  # a real timeline (states change mid-session) → write
_FLAT = "flat"  # states never changed → nothing temporal to record
_SINGLE = "single"  # ≤1 recorded state → already unambiguous
_LIVE = "live"  # live-observed spans already present → don't clobber
#
# Only "live" is protected. A "record"-sourced timeline was derived from the same
# stored payloads this back-fill reads, so re-deriving it is idempotent — and when
# the definitions have since changed, re-deriving is exactly the point. A "live"
# timeline is the one that saw signals the stored captures may not.
_NO_EVIDENCE = "no-evidence"  # nothing decodable/timed to reconstruct from


def _existing_span_sources(paths: set[str]) -> dict[tuple[str, int], str]:
    """Map ``(path, session_idx) → state_spans.source`` for the files in scope.

    Read straight off disk rather than from the flattened entries: a capture row
    carries its *resolved* states, not the provenance of the block they came from,
    and only this command needs that provenance.
    """
    from canair.captures import io as capture_io

    sources: dict[tuple[str, int], str] = {}
    for path in sorted(paths):
        try:
            data = capture_io.load_capture_file(Path(path))
        except Exception:
            continue
        for idx, session in enumerate(data.get("sessions") or []):
            block = session.get("state_spans")
            if isinstance(block, dict) and block.get("source"):
                sources[(path, idx)] = str(block["source"])
    return sources


def cmd_backfill_state_spans(
    entries: Sequence[CaptureEntry],
    *,
    captures_dir: Path | None = None,
    overwrite: bool = False,
    cycle_tol: float = DEFAULT_CYCLE_TOL_S,
    dry_run: bool = False,
    assume_yes: bool = False,
    as_json: bool = False,
) -> int:
    """Reconstruct and write per-session state_spans over the scoped entries."""
    import json as _json

    from canair.captures import layers
    from canair.captures.entries import group_sessions
    from canair.captures.session_edit import set_session_state_spans
    from canair.ecus.pids import build_ecu_index, load_pids
    from canair.install.build_info import full_version

    from .backfill_spans_render import print_span_report

    try:
        rules = load_states(profile=active())
    except StatePredicateError as ex:
        messages.error(f"invalid vehicle_states.yaml predicate: {ex}")
        return 2
    inferable = {r.name for r in rules if r.predicate is not None}
    if not inferable:
        messages.error(
            "no state predicates defined (vehicle_states.yaml has no `when:` rules); "
            "nothing to reconstruct a timeline from"
        )
        return 1

    ecu_index = build_ecu_index(load_pids(profile=active()))

    payloads_by_session: dict[tuple[str, int], list[CaptureEntry]] = {}
    for e in entries:
        if not e.get("payload"):
            continue
        key = (str(entry_path(e, captures_dir, profile=active())), e.get("_session_idx", 0))
        payloads_by_session.setdefault(key, []).append(e)

    span_source = _existing_span_sources(
        {str(entry_path(e, captures_dir, profile=active())) for e in entries}
    )

    rows: list[dict] = []
    for g in group_sessions(entries, profile=active()):
        key = (g["path"], g["session_idx"])
        recorded = parse_states(g.get("vehicle_states") or [])
        caps = payloads_by_session.get(key, [])

        result = session_state_spans(
            recorded, caps, rules, ecu_index, cycle_tol=cycle_tol, profile=active()
        )
        inf = result.inference
        spans = result.spans

        if span_source.get(key) == "live":
            verdict = _LIVE
        elif len(recorded) <= 1:
            verdict = _SINGLE
        elif not spans:
            verdict = _NO_EVIDENCE
        elif not result.is_timeline:
            verdict = _FLAT
        else:
            verdict = _SPANS

        rows.append(
            {
                "file": g["file"],
                "path": g["path"],
                "session_idx": g["session_idx"],
                "date": g["date"],
                "label": g["label"],
                "times": g["times"],
                "recorded": recorded,
                "verdict": verdict,
                "n_spans": len(spans),
                "n_cycles": inf.n_cycles,
                "n_informative": inf.n_informative,
                "n_untimed": inf.n_untimed,
                "placed": inf.union,
                "carried": result.carried,
                "spans": spans,
                "will_write": verdict == _SPANS or (overwrite and verdict == _LIVE),
            }
        )

    to_write = [r for r in rows if r["will_write"]]

    report = sys.stderr if as_json else sys.stdout
    print_span_report(rows, dry_run=dry_run, out=report)

    if as_json and dry_run:
        print(_json.dumps(rows, indent=2))
        return 0
    if not to_write:
        if as_json:
            print(_json.dumps(rows, indent=2))
        return 0
    if dry_run:
        print("  (--dry-run: nothing written)", file=report)
        return 0

    if blocked := layers.refusal(
        (Path(r["path"]) for r in to_write), "back-filled", profile=active()
    ):
        print(blocked, file=sys.stderr)
        return 1

    if not confirm(f"Write spans to these {len(to_write)} session(s)?", yes=assume_yes):
        return 1

    version = full_version()
    written = 0
    for r in to_write:
        try:
            set_session_state_spans(
                Path(r["path"]),
                r["session_idx"],
                r["spans"],
                source="backfill",
                version=version,
            )
            written += 1
            print(
                f"    \u2192 {r['file']} [{r['session_idx']}] {r['date']} "
                f"{r['n_spans']} span(s) over {join_states(r['recorded'])}",
                file=report,
            )
        except Exception as ex:  # keep going; report the failure
            messages.error(f"{r['file']} [{r['session_idx']}]: {ex}")

    echo_write(f"Wrote spans to {written} session(s)", (Path(r["path"]) for r in to_write), report)
    if as_json:
        print(_json.dumps(rows, indent=2))
    return 0 if written == len(to_write) else 1
