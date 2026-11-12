"""Live monitor — the per-PID rendered-block cache and the signatures that key it."""

from rich.text import Text

from canair.expression.notation import ByteNotation
from canair.expression.results import ResultEntry

from .history import HistoryRun


class RenderCache:
    """Per-PID rendered-block cache for the live monitor.

    A monitor repaints its whole body on every poll cycle *and* on every
    mid-cycle partial resolve, but between paints most PIDs are unchanged. Each
    PID entry's rendered :class:`~rich.text.Text` is deterministic in its
    inputs, so we key the block on a signature of everything that affects its
    output and reuse the cached block on a hit — skipping the per-byte
    ``Text.append`` churn and the expression byte-index re-parsing that
    dominated render cost.

    A cached ``Text`` is only ever appended into a parent via
    ``Text.append_text`` (which copies, not mutates), so sharing one cached
    instance across many renders is safe. Held by the monitor controller so it
    persists across paints; a fresh cache renders everything once.
    """

    def __init__(self) -> None:
        self._blocks: dict[tuple[str, str], tuple[tuple, Text]] = {}

    def get(self, key: tuple[str, str], sig: tuple) -> Text | None:
        hit = self._blocks.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        return None

    def put(self, key: tuple[str, str], sig: tuple, block: Text) -> None:
        self._blocks[key] = (sig, block)

    def prune(self, live_keys: set[tuple[str, str]]) -> None:
        """Drop cached blocks for PIDs no longer polled (e.g. after a filter change)."""
        for key in [k for k in self._blocks if k not in live_keys]:
            del self._blocks[key]


def entry_signature(
    entry: ResultEntry,
    *,
    changed: bool,
    verbose: bool,
    show_rulers: bool,
    is_selected: bool,
    sel_name: str | None,
    row_cap: int,
    prev_raw: str,
    prev_values: dict[str, object] | None,
    history: list[HistoryRun] | None,
    view_mode: str = "full",
    stat_sig: tuple | None = None,
    notation: ByteNotation = ByteNotation.WICAN,
    stale_label: str = "",
    note: str = "",
    width: int | None = None,
) -> tuple:
    """A hashable signature of everything that affects one PID entry's render.

    Two renders with equal signatures produce byte-identical output, so a match
    lets :class:`RenderCache` reuse the prior block. ``params`` are flattened to
    a tuple (name/value/unit/expr/error/verified/display) because their values
    and verification state drive both the table and the per-byte colours.
    ``view_mode`` and ``stat_sig`` (the ranges-view accumulated span) are part of
    the signature so a view switch or a moving range invalidates the cache.
    """
    params = tuple(tuple(row) for row in entry.get("params", []))
    return (
        entry.get("pid", ""),
        entry.get("raw_hex", ""),
        entry.get("map_bytes", ()),
        entry.get("error"),
        entry.get("decode"),
        bool(entry.get("unmapped", False)),
        bool(entry.get("stale", False)),
        stale_label,
        changed,
        verbose,
        show_rulers,
        is_selected,
        sel_name if is_selected else None,
        row_cap,
        prev_raw,
        tuple(sorted(prev_values.items())) if prev_values is not None else None,
        # The rendered tail plus the run count: the rows above the cap only show as
        # the "earlier entries hidden" count, so hashing them is wasted work.
        (len(history), tuple(history[-(row_cap + 1) :])) if history is not None else None,
        params,
        view_mode,
        stat_sig,
        notation,
        note,
        width,
    )


def stale_age(entry: ResultEntry, now: float) -> str:
    """How long a stale entry's last-good value has been held, e.g. ``12s``, ``3m``.

    Coarse on purpose: the label is part of the render-cache signature, so it should
    change no more often than a reader can use.
    """
    acquired = entry.get("acquired_at")
    if not entry.get("stale") or not acquired:
        return ""
    secs = max(0, int(now - acquired))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m"


def stat_signature(stat: dict) -> tuple:
    """A hashable signature of a signal's accumulated range (for the ranges view)."""
    return (
        stat.get("unit", ""),
        bool(stat.get("verified")),
        stat.get("n", 0),
        stat.get("min"),
        stat.get("max"),
        tuple(stat.get("values") or ()),
        int(stat.get("overflow", 0)),
    )


def pid_stat_signature(pid_stats: dict) -> tuple:
    """Signature over all signals' ranges for one PID (order-stable)."""
    return tuple((name, stat_signature(s)) for name, s in pid_stats.items())
