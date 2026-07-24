#!/usr/bin/env python3
"""Helpers for reasoning about a capture's ``keep_mode``.

``keep_mode: unique`` (set by ``--monitor --keep-unique``) means the monitor
stored only payloads that differed from any seen before — so the capture holds
**rising-edge transitions only**; return-to-previous states (falling edges) and
dwell durations are absent. Analysis tools use these helpers to caveat results
that would otherwise be misread (e.g. a value "persisting" is an artifact of
dedup, and time gaps are not real sampling gaps).

Pure inspection over already-loaded capture entries; no I/O.
"""

from __future__ import annotations

BANNER = (
    "scope includes keep:unique sessions — only rising-edge transitions were "
    "stored; falling edges/durations are absent"
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
