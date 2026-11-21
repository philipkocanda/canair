"""Finalize a read pipeline's crash-safe capture journal."""

from __future__ import annotations

import sys

from canair.captures.metadata import resolve_metadata
from canair.captures.types import Quality


def finalize_journal(
    journal,
    count: int,
    label: str | None,
    vehicle_states=None,
    notes: str | None = None,
    prompt: bool = True,
    suggested_state: str | None = None,
    quality: Quality | None = None,
    non_answers: int = 0,
) -> None:
    """Resolve metadata and reconcile captured payloads or refusals on exit.

    Only a deliberate skip discards a nonempty journal; an unanswered non-interactive prompt
    saves under its suggested label. ``prompt=False`` reconciles interrupted pipelines without
    further interaction. ``quality`` records the transport's exchange/error footprint.
    """
    if journal is None:
        return
    if count == 0 and non_answers == 0:
        print("\n  --save: no payloads captured — nothing to save.", file=sys.stderr)
        journal.discard()
        return

    refused = f" and {non_answers} non-answer(s)" if non_answers else ""
    print(f"\n  --save: {count} payload(s){refused} captured.", file=sys.stderr)
    if prompt:
        meta = resolve_metadata(
            label,
            vehicle_states,
            notes,
            suggested_label="Multi query session",
            last_state=suggested_state,
        )
        if meta is None:
            journal.discard()
            return
        lbl, states, nt = meta
        journal.update_meta(lbl, states, nt)
    if quality is not None:
        journal.update_meta(quality=quality)
    journal.reconcile()
