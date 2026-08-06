#!/usr/bin/env python3
"""The shared CLI surface and reporting for cross-signal comparison.

Everything here answers "how are two signals compared against each other?" — the
knobs the analysis verbs (``align``/``correlate``/``hunt``/``investigate``/
``decode``) must agree on: how rows are matched in time (``--join-tol``), whether a
run-length value may be carried to a row it has no sample at (``--fill`` /
``--max-hold``), and what counts as one signal mirroring another
(``--mirror-match`` / ``--allow-offset``).

``align``/``correlate``/``hunt``/``investigate``/``decode`` all join one signal
onto another by nearest timestamp, and all need the same two knobs: how wide the
join window is (``--join-tol``) and whether a run-length signal's stored value may
be carried forward to a reference instant it has no sample at (``--fill`` /
``--max-hold``). Declaring them once here is what keeps the default from drifting
between commands — the mistake this replaces, where five commands each carried
their own copy-pasted ``--join-tol`` block (and ``decode``'s help had already
drifted to a default it no longer used).

It also owns the *reporting* half: a filled row is reconstructed rather than
measured, so a report that fills must say which signals it filled and how far —
from one spelling, so the phrasing and units can't diverge across five commands.

Mirrors :func:`canlib.capture_dates.add_scope_args` and
:func:`canlib.notation.add_notation_arg`: one helper owning one group of flags
shared across the analysis verbs.

``captures uds --step`` deliberately keeps its own ``--join-tol``: it is a
*viewer* join with different semantics (anchored on the union of all timestamps,
its own wider default sized for a full round-robin cycle, live-adjustable) and no
use for filling.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass

from canlib.align import DEFAULT_JOIN_TOL_S, LoadedPid
from canlib.fill import FILL_MODES, FillPolicy, format_hold_duration, parse_fill_mode
from canlib.mirrors import DEFAULT_MIRROR_MATCH

__all__ = [
    "FillSummary",
    "add_join_args",
    "add_mirror_args",
    "fill_policy_from_args",
    "fill_summaries",
    "fill_summary_line",
]


def add_join_args(
    parser: argparse.ArgumentParser,
    *,
    tol_default: float = DEFAULT_JOIN_TOL_S,
    tol_help: str | None = None,
) -> argparse._ArgumentGroup:
    """Add the shared ``--join-tol``/``--fill``/``--max-hold`` flags to ``parser``."""
    group = parser.add_argument_group("time joining")
    group.add_argument(
        "--join-tol",
        type=float,
        default=tol_default,
        metavar="SECONDS",
        help=tol_help or f"Nearest-timestamp join window (default {tol_default:g}s)",
    )
    group.add_argument(
        "--fill",
        choices=FILL_MODES,
        default="auto",
        help="Carry a run-length (keep:changes) value forward to reference instants it "
        "has no sample at: 'auto' (default) fills only keep:changes sessions, 'hold' "
        "forces it everywhere, 'none' keeps strict point semantics",
    )
    group.add_argument(
        "--max-hold",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Cap how long a filled value may be carried (default: until the next "
        "sample or the end of its recording session)",
    )
    return group


def add_mirror_args(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    """Add the shared ``--mirror-match``/``--allow-offset`` flags to ``parser``.

    Shared by ``decode --find-mirrors`` (intra-PID) and both ``correlate
    --find-mirrors`` kinds (cross-ECU, cross-arbitration-ID) so "what counts as a
    mirror" means the same thing in all three.
    """
    group = parser.add_argument_group("mirror matching")
    group.add_argument(
        "--mirror-match",
        type=float,
        default=DEFAULT_MIRROR_MATCH,
        metavar="FRACTION",
        help=f"Fraction of compared rows that must agree (default "
        f"{DEFAULT_MIRROR_MATCH}; use 1 to demand every row, which round-robin poll "
        f"skew alone is enough to defeat)",
    )
    group.add_argument(
        "--allow-offset",
        action="store_true",
        help="Also accept a mirror at a constant offset or scale (a == b + k, "
        "a == b × s) — real mirrors are frequently the same quantity in different "
        "units or with a different zero",
    )
    return group


def fill_policy_from_args(args) -> FillPolicy:
    """Build the :class:`~canlib.fill.FillPolicy` from parsed ``--fill``/``--max-hold``.

    Tolerant of a namespace that predates the flags (a command not yet wired
    through :func:`add_join_args`), which then gets the default policy.
    """
    return FillPolicy(
        mode=parse_fill_mode(getattr(args, "fill", None)),
        max_hold_s=getattr(args, "max_hold", None),
    )


@dataclass(frozen=True)
class FillSummary:
    """One PID whose stored rows are run-length segments a join may carry forward."""

    label: str  # "ECU:PID"
    n_held: int  # rows whose value extends past their own timestamp
    n_rows: int  # timed rows for this PID in scope
    max_hold_s: float  # the longest segment

    def as_json(self) -> dict:
        return {
            "signal": self.label,
            "held_rows": self.n_held,
            "rows": self.n_rows,
            "max_hold_s": round(self.max_hold_s, 3),
        }


def fill_summaries(
    loaded: Iterable[LoadedPid], policy: FillPolicy, tol_s: float
) -> list[FillSummary]:
    """Which of ``loaded``'s PIDs contribute forward-filled values, and how far.

    Reported per *PID* rather than per join, deliberately: every signal decoded
    from one PID shares one capture timeline, so the fill is a property of the PID's
    recording — and naming the PID tells a reader exactly which rows of the report
    are reconstructed, which a per-join count buried in hundreds of swept bytes
    would not.

    Only segments **longer than ``tol_s``** are counted, and a PID with none is
    omitted entirely. That is exact, not a heuristic: a hold no longer than the join
    window covers only instants the strict nearest-join already reached, so it can
    never contribute a row. Without the filter nearly every report grew a "values
    carried forward" note describing 2-second holds that changed nothing.
    """
    out: list[FillSummary] = []
    for lp in loaded:
        holds = lp.timed_holds(policy)
        if not holds:
            continue
        spans = [
            span
            for (dt, _frame), h in zip(lp.timed_frames(), holds, strict=True)
            if h is not None and (span := (h - dt).total_seconds()) > tol_s
        ]
        if not spans:
            continue
        out.append(
            FillSummary(
                label=f"{lp.ecu}:{lp.pid}",
                n_held=len(spans),
                n_rows=len(holds),
                max_hold_s=max(spans),
            )
        )
    return out


def fill_summary_line(
    summaries: list[FillSummary], policy: FillPolicy, *, top: int = 4
) -> str | None:
    """One line naming the run-length signals a report carried forward.

    Capped at the ``top`` longest carries: a whole-corpus scope can involve dozens
    of run-length PIDs, and listing them all produced a paragraph nobody reads.
    Ranked by hold length because that is what decides whether a fill is worth
    scrutinising — a 2-minute carry is unremarkable, a 2-hour one is the finding.
    """
    if not summaries:
        return None
    ranked = sorted(summaries, key=lambda s: -s.max_hold_s)
    shown = ranked[:top]
    detail = ", ".join(
        f"{s.label} ({s.n_held}/{s.n_rows} rows, up to {format_hold_duration(s.max_hold_s)})"
        for s in shown
    )
    if len(ranked) > len(shown):
        detail += f", +{len(ranked) - len(shown)} more"
    return (
        f"fill: {policy.mode} — run-length values carried forward from {detail}; "
        "rows at instants these signals were not sampled are reconstructed, "
        "not measured (--fill none to disable)"
    )
