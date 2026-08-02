#!/usr/bin/env python3
"""Helpers for reasoning about a capture's ``keep_mode``.

The monitor records with one of two dedup policies (per ECU/PID), both of which
keep a capture file compact for a stationary signal:

- ``keep_mode: changes`` (the recording default, ``canair monitor --keep-changes``)
  — **run-length**: only payloads that differ from the *immediately preceding*
  one are stored. Genuine oscillation (``A→B→A→B``) is preserved and each stored
  row is a real value-transition, so dwell durations are recoverable from the
  timestamps. What's lost is the intra-run sampling cadence and the final run's
  end time.
- ``keep_mode: unique`` (legacy, ``canair monitor --keep-unique``) — **global**:
  only payloads that differ from *any* seen before are stored. A return to any
  prior value is dropped, so return-to-previous transitions and dwell durations
  are absent, and stored-row time gaps are dedup artifacts, not real intervals.

Analysis tools use these helpers to caveat results that would otherwise be
misread. Pure inspection over already-loaded capture entries; no I/O.
"""

from __future__ import annotations

# Strong caveat — legacy global dedup (``keep_mode: unique``).
BANNER = (
    "scope includes keep:unique sessions — only distinct values were kept (global "
    "dedup); return-to-previous transitions and durations are absent, and stored-row "
    "time gaps are not real sampling intervals"
)

# Mild caveat — run-length recording (``keep_mode: changes``).
CHANGES_BANNER = (
    "scope includes keep:changes sessions — stored rows are value-transitions "
    "(run-length); the intra-run sampling cadence and the final run's duration are "
    "not captured"
)


def scope_is_keep_unique(captures) -> bool:
    """True if any capture entry in scope came from a ``keep_mode: unique`` session.

    Accepts the flat capture-entry dicts produced by ``load_all_captures`` (which
    copies the session's ``keep_mode`` onto each entry).
    """
    for c in captures:
        if isinstance(c, dict) and str(c.get("keep_mode") or "") == "unique":
            return True
    return False


def scope_is_keep_changes(captures) -> bool:
    """True if any capture entry in scope came from a ``keep_mode: changes`` session."""
    for c in captures:
        if isinstance(c, dict) and str(c.get("keep_mode") or "") == "changes":
            return True
    return False


def keep_mode_from_args(args) -> str:
    """Resolve the monitor keep-mode from parsed ``--keep*`` flags.

    The default is ``"changes"`` (run-length dedup) so a ``canair monitor --save``
    session preserves real value-transitions without ballooning its capture file
    with polled duplicates. Explicit overrides: ``--keep-unique`` → ``"unique"``
    (legacy global dedup), ``--keep-all`` → ``"all"`` (full time-series),
    ``--keep N`` → ``"last"``. Shared by both the ELM and raw-CAN monitor paths so
    the default can't drift between transports.
    """
    if getattr(args, "keep_all", False):
        return "all"
    if getattr(args, "keep", None):
        return "last"
    if getattr(args, "keep_unique", False):
        return "unique"
    return "changes"
