#!/usr/bin/env python3
"""Entry point for ``captures --step``: build the model, pick an output path.

Three paths over one :class:`~canlib.commands.captures.step_model.StepModel`:

* a TTY gets the interactive Textual app (:mod:`step_tui`);
* piped output renders the most recent frames statically;
* ``--json`` emits the same frames as data.

The state and rendering live in the model/renderer modules; the join lives in
:mod:`query`. This module only wires them to the command line.
"""

import sys
from collections.abc import Sequence
from pathlib import Path

from canlib.capture_types import CaptureEntry

from .query import (
    _DIM,
    _RESET,
    _YELLOW,
    _capture_key,
    _dump_json,
    _gather_query,
)
from .step_model import (
    DEFAULT_STEP_JOIN_TOL_S,
    VIEW_AUTO,
    StepModel,
)


def _safe_alias_index() -> dict[str, str]:
    """Canonical-name -> alias map for display, or {} if the registry is unavailable."""
    from canlib.ecus import build_alias_index

    try:
        return build_alias_index()
    except Exception:
        return {}


def build_model(
    entries: Sequence[CaptureEntry],
    query,
    *,
    show_all: bool = False,
    captures_dir: Path | None = None,
    rulers: bool = False,
    view: str = VIEW_AUTO,
    tol_s: float = DEFAULT_STEP_JOIN_TOL_S,
    warn: bool = True,
) -> StepModel | None:
    """Select captures for ``query`` and build the step model (None if nothing matched).

    The model keeps *all* scoped ``entries`` (not just the queried ones) so PIDs
    can be added from inside the TUI without reloading from disk.
    """
    if captures_dir is None:
        from canlib.profile import active

        captures_dir = active().captures_dir

    captures, defs = _gather_query(entries, query, warn=warn)
    if not captures:
        return None

    # Keys in first-appearance order; the model sorts them for a stable block order.
    keys = list(dict.fromkeys(_capture_key(e) for e in captures))
    return StepModel.from_entries(
        entries,
        keys,
        defs,
        view=view,
        tol_s=tol_s,
        show_all=show_all,
        rulers=rulers,
        captures_dir=captures_dir,
        aliases=_safe_alias_index(),
    )


def cmd_step(
    entries: Sequence[CaptureEntry],
    query,
    show_all: bool = False,
    captures_dir: Path | None = None,
    rulers: bool = False,
    view: str = VIEW_AUTO,
    tol_s: float = DEFAULT_STEP_JOIN_TOL_S,
    as_json: bool = False,
    limit: int = 0,
) -> None:
    """Step through the captures matching ``query``, comparing several PIDs at once.

    ``query`` is a canlib.query selection (``"VCU"``, ``"HVAC:220100,2201A0"``,
    ``"VCU:2101 BMS:2101"``). When it resolves to more than one (ECU, PID) key the
    frames *stack* one block per key, time-joined within ``tol_s`` seconds, so the
    PIDs can be read against each other; a single key steps capture by capture.
    ``view`` selects (or ``auto``-resolves) the stacked/params-only/changed-only/
    interleaved rendering.

    Interactively (a TTY) this opens the Textual stepper, where the PID set, the
    join tolerance and the view are all editable, and the focused block's capture
    can be annotated or deleted. Piped, it renders the last ``limit`` frames
    (0 = all); with ``as_json`` it emits those frames as data instead.

    Steps through *unique* payloads per PID by default; ``show_all=True`` keeps
    every capture.
    """
    model = build_model(
        entries,
        query,
        show_all=show_all,
        captures_dir=captures_dir,
        rulers=rulers,
        view=view,
        tol_s=tol_s,
        warn=not as_json,
    )
    if model is None:
        if as_json:
            _dump_json({"frames": [], "frame_count": 0, "keys": []})
        return

    if as_json:
        _dump_json(model.to_json(limit=limit))
        return

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _print_frames(model, limit=limit)
        return

    from .step_tui import run_step_app

    run_step_app(model)


def _print_frames(model: StepModel, *, limit: int = 0) -> None:
    """Render the most recent frames to stdout (the non-interactive fallback)."""
    from rich.console import Console

    console = Console(highlight=False)
    total = model.frame_count()
    start = max(total - limit, 0) if limit > 0 else 0
    model.cursor = False  # nothing to focus in a static render
    print(f"  {_DIM}(not a TTY — rendering frames statically){_RESET}")
    for n in range(start, total):
        console.print(model.render(n), end="", soft_wrap=True)
    if start:
        hidden = start
        print(
            f"\n  {_YELLOW}{hidden} earlier frame(s) hidden{_RESET} "
            f"{_DIM}— widen with --limit N (0 = all){_RESET}"
        )
    print(f"  {_DIM}{model.status_line()}{_RESET}")
