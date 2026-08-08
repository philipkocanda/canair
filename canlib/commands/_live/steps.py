"""Helpers for the multi mini-language STEPs the read/monitor surfaces take.

A positional STEP is either a verb-led command or a bare selector that gets the
implicit ``query`` verb; ``@group`` references are expanded textually before the
mini-language parser ever sees them, so a group composes with ad-hoc selectors.
"""

from __future__ import annotations

# Leading verbs recognised by the multi mini-language. A positional STEP whose
# first token is one of these is passed through verbatim; anything else is a
# bare selector and gets the implicit ``query`` verb prepended.
STEP_VERBS = ("skm-wake", "session", "query", "raw", "scan", "iocontrol", "sleep", "repl")


def to_step(selector: str) -> str:
    """Prefix a bare selector with the ``query`` verb unless it already has one."""
    first = selector.strip().split(maxsplit=1)
    if first and first[0].lower() in STEP_VERBS:
        return selector
    return f"query {selector}"


def expand_step_groups(steps: list[str]) -> list[str]:
    """Expand ``@group`` references in positional STEPs into their selectors.

    Loads the active profile's ``groups.yaml`` and rewrites each step textually
    (see :func:`canlib.ecu_groups.expand_group_refs`) *before* the mini-language
    parser sees it, so a group composes with ad-hoc selectors and other groups.
    Raises ``GroupError`` (a ``ValueError``) on a bad/unknown reference — callers
    already guard step parsing with a ``ValueError`` handler.
    """
    from canlib.ecu_groups import expand_group_refs, load_groups

    return expand_group_refs(steps, load_groups())


def report_merged_selectors(commands: list[dict]) -> None:
    """Print a note for each ECU whose redundant selectors were coalesced.

    An overlap is easy to create without noticing — ``@driving`` already contains
    ``IGPM`` and ``AAF:2180``, so ``mon IGPM OBC AAF @driving`` names them twice.
    The normaliser merges them (see
    :func:`canlib.modes.multi_parse.normalize_query_steps`); saying so teaches the
    group's contents instead of silently rewriting the command. No output when
    nothing overlapped.
    """
    from canlib.modes.multi_parse import merged_selector_notes

    notes = merged_selector_notes(commands)
    if not notes:
        return
    print("Merged overlapping selectors (each ECU is polled once):")
    for note in notes:
        print(f"  {note}")
