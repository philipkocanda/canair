#!/usr/bin/env python3
"""The ``keep_mode`` vocabulary, and helpers for reasoning about it.

This module owns the keep-mode types/constants (see :data:`KeepMode` and
:data:`PersistedKeepMode` below) so the monitor's recording path, the capture
writers, and the analysis commands all name the same values — no module compares
against its own bare string literal.

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

Analysis tools use the ``scope_is_*`` helpers to caveat results that would
otherwise be misread. Pure inspection over already-loaded capture entries; no I/O
and no canair imports, so any layer can depend on it.
"""

from __future__ import annotations

from typing import Literal, get_args

# Two related-but-distinct vocabularies share the ``keep_mode`` spelling, and
# conflating them is the bug this typing prevents:
#
# - ``KeepMode`` is the monitor's *recording policy* — what the poll loop does
#   with each payload. All four values are legitimate here.
# - ``PersistedKeepMode`` is the *provenance* a capture session can carry. Only
#   the two dedup policies are recordable: ``all``/``last`` mean "no dedup was
#   applied", so the field's **absence** is the honest record. Writing "all" into
#   a capture file would claim a dedup policy that was never applied.
#
# Both are `Literal`s (not `StrEnum`) so the runtime values stay plain `str` and
# serialise into YAML/JSON unchanged; the tuples below are derived from the types
# so `choices=`/membership guards have exactly one source of truth.
KeepMode = Literal["changes", "unique", "all", "last"]
PersistedKeepMode = Literal["changes", "unique"]

# The read-side spelling: ``load_all_captures`` denormalises the session's
# ``keep_mode`` onto every capture entry, using "" for a session that recorded
# none (``all``/``last``, or a session predating the field).
EntryKeepMode = Literal["changes", "unique", ""]

KEEP_CHANGES: PersistedKeepMode = "changes"
KEEP_UNIQUE: PersistedKeepMode = "unique"
KEEP_ALL: KeepMode = "all"
KEEP_LAST: KeepMode = "last"

KEEP_MODES: tuple[KeepMode, ...] = get_args(KeepMode)
PERSISTED_KEEP_MODES: tuple[PersistedKeepMode, ...] = get_args(PersistedKeepMode)


def parse_keep_mode(value: object) -> KeepMode | None:
    """Narrow an untrusted value (journal meta, loaded capture) to a keep mode.

    Returns None for anything outside :data:`KEEP_MODES`, so a hand-edited or
    stale file degrades to "no dedup policy known" instead of propagating a
    bogus mode into the dedup logic.
    """
    for mode in KEEP_MODES:
        if value == mode:
            return mode
    return None


def persisted_keep_mode(mode: str | None) -> PersistedKeepMode | None:
    """The value to store for ``mode``, or None when it must not be persisted.

    ``all``/``last`` mean "no dedup was applied", so the field is omitted rather
    than recorded — absence is the honest provenance. The single home for that
    rule, which was previously re-implemented as a bare
    ``in ("changes", "unique")`` guard at four call sites.
    """
    for persisted in PERSISTED_KEEP_MODES:
        if mode == persisted:
            return persisted
    return None


# Mild caveat — run-length recording (``keep_mode: changes``).
#
# There is deliberately no blanket ``keep:unique`` counterpart: most historical
# captures were recorded that way, so a scope banner fired on nearly every
# report and became noise. The unique caveat is now raised only where it changes
# a reading — the dwell classes (``investigate --events``) and the
# ``--transform``/``--lag-scan`` time-gap warnings in ``decode``/``correlate``.
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
        if isinstance(c, dict) and str(c.get("keep_mode") or "") == KEEP_UNIQUE:
            return True
    return False


def scope_is_keep_changes(captures) -> bool:
    """True if any capture entry in scope came from a ``keep_mode: changes`` session."""
    for c in captures:
        if isinstance(c, dict) and str(c.get("keep_mode") or "") == KEEP_CHANGES:
            return True
    return False


def keep_mode_from_args(args) -> KeepMode:
    """Resolve the monitor keep-mode from parsed ``--keep*`` flags.

    The default is ``"changes"`` (run-length dedup) so a ``canair monitor --save``
    session preserves real value-transitions without ballooning its capture file
    with polled duplicates. Explicit overrides: ``--keep-unique`` → ``"unique"``
    (legacy global dedup), ``--keep-all`` → ``"all"`` (full time-series),
    ``--keep N`` → ``"last"``. Shared by both the ELM and raw-CAN monitor paths so
    the default can't drift between transports.
    """
    if getattr(args, "keep_all", False):
        return KEEP_ALL
    if getattr(args, "keep", None):
        return KEEP_LAST
    if getattr(args, "keep_unique", False):
        return KEEP_UNIQUE
    return KEEP_CHANGES
