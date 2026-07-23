#!/usr/bin/env python3
"""Time-aligned cross-signal analysis primitives.

The building blocks for correlating signals *across* different PIDs and ECUs —
``canair correlate``, ``canair hunt``, and cross-ECU ``decode --corr``.

canair polls one connection sequentially, so different ECUs are sampled with a
small (~0.3-3 s) skew. To compare a signal on ECU A against one on ECU B we
join by **nearest timestamp within a tolerance**, using the real ``datetime``
built by :func:`capture_dates.entry_datetime`. Captures with no usable ``time``
(one-shot scan/probe/identity reads) are dropped from time joins — but retained
by value/state views elsewhere.

Nothing here talks to the device; it is pure analysis over ``captures/``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime

from .byteindex import payload_to_wican_bytes as _payload_to_wican_bytes
from .capture_dates import entry_datetime, filter_by_date_range, filter_by_text
from .expression import evaluate_expression

__all__ = [
    "DEFAULT_JOIN_TOL_S",
    "SignalRef",
    "TimePoint",
    "align_many",
    "extract_series",
    "join_nearest",
    "load_signal_captures",
]

# Default nearest-neighbour join window. Chosen from the observed 0.3-3 s
# inter-ECU sampling skew of the sequential single-connection poller.
DEFAULT_JOIN_TOL_S = 2.5


@dataclass(frozen=True)
class SignalRef:
    """A reference to one decodable signal: ``ECU:PID:PARAM`` or ``ECU:PID:EXPR``.

    ``name_or_expr`` is either a defined parameter name (resolved against the
    PID's ``parameters``) or a raw WiCAN expression (e.g. ``[S10:S11]``,
    ``B22``). :func:`extract_series` decides which by asking the caller-supplied
    resolver, falling back to treating it as an expression.
    """

    ecu: str
    pid: str
    name_or_expr: str

    @property
    def label(self) -> str:
        return f"{self.ecu}:{self.pid}:{self.name_or_expr}"

    @classmethod
    def parse(cls, spec: str) -> SignalRef:
        """Parse ``ECU:PID:PARAM_OR_EXPR``.

        The expression part may itself contain colons (``[S10:S11]``), so we
        split only on the first two colons and keep the remainder intact.
        """
        parts = spec.split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts[:2]) or not parts[2].strip():
            raise ValueError(
                f"invalid signal reference {spec!r} "
                "(expected ECU:PID:PARAM or ECU:PID:EXPR, e.g. ESC:22C101:REAL_SPEED_KMH)"
            )
        return cls(parts[0].strip(), parts[1].strip(), parts[2].strip())


@dataclass
class TimePoint:
    """One decoded sample: a timestamp and a numeric value."""

    dt: datetime
    value: float


@dataclass
class LoadedPid:
    """All captures for one (ecu, pid), plus loader metadata."""

    ecu: str
    pid: str
    captures: list[dict] = field(default_factory=list)  # payload captures only
    n_no_time: int = 0  # payload captures dropped for lacking a usable time


def load_signal_captures(
    specs: list[tuple[str, str]],
    *,
    since=None,
    until=None,
    state: str | None = None,
    label: str | None = None,
    captures_dir=None,
) -> dict[tuple[str, str], LoadedPid]:
    """Load ``payload`` captures grouped by ``(ecu, pid)`` for a set of specs.

    ``specs`` is a list of ``(ECU, PID)`` (canonical short names, upper-cased for
    matching). Reuses the single canonical loader
    (:func:`commands.captures.load_all_captures`) and the shared scope filters so
    date/state/label narrowing behaves identically to ``decode``/``captures``.

    Scan/probe captures (``scan_results``, no ``payload``) are ignored — they are
    not time-series. Payload captures with no usable ``time`` are counted in
    ``n_no_time`` and excluded (their ``datetime`` would be ``None``).
    """
    from .commands.captures import load_all_captures

    wanted = {(e.upper(), str(p).upper()) for e, p in specs}
    result: dict[tuple[str, str], LoadedPid] = {
        (e.upper(), str(p).upper()): LoadedPid(e.upper(), str(p).upper()) for e, p in specs
    }

    entries = load_all_captures(captures_dir)
    entries = filter_by_date_range(entries, since, until)
    entries = filter_by_text(entries, state=state, label=label)

    for e in entries:
        key = (str(e.get("ecu", "")).upper(), str(e.get("pid", "")).upper())
        if key not in wanted:
            continue
        if not e.get("payload"):
            continue  # scan/probe/identity capture — not a time series
        lp = result[key]
        if entry_datetime(e) is None:
            lp.n_no_time += 1
            continue
        lp.captures.append(e)
    return result


def extract_series(
    loaded: LoadedPid,
    name_or_expr: str,
    *,
    parameters: dict | None = None,
) -> list[TimePoint]:
    """Decode one signal from a :class:`LoadedPid` into a time series.

    ``name_or_expr`` resolves to a defined parameter's expression when it matches
    a key in ``parameters`` (case-insensitively); otherwise it is treated as a
    raw WiCAN expression. Captures where the expression errors or yields a
    non-numeric value are skipped.
    """
    expr = name_or_expr
    if parameters:
        for pname, pdef in parameters.items():
            if pname.upper() == name_or_expr.upper():
                expr = pdef.get("expression", "") or name_or_expr
                break

    out: list[TimePoint] = []
    for cap in loaded.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            wb = _payload_to_wican_bytes(cap["payload"])
            val = evaluate_expression(expr, wb)
        except Exception:
            continue
        if isinstance(val, (int, float)):
            out.append(TimePoint(dt, float(val)))
    out.sort(key=lambda tp: tp.dt)
    return out


def join_nearest(
    ref: list[TimePoint],
    cand: list[TimePoint],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], list[float], int]:
    """Nearest-neighbour join ``cand`` onto ``ref`` within ``tol_s`` seconds.

    For each reference point, pick the candidate whose timestamp is closest and
    within tolerance; reference points with no candidate in range are dropped.
    Returns ``(ref_values, cand_values, n)`` aligned pairs — ``n`` is the
    realised overlap, always reported so a thin join is visible.
    """
    if not ref or not cand:
        return [], [], 0
    cand_sorted = sorted(cand, key=lambda tp: tp.dt)
    cand_ts = [tp.dt for tp in cand_sorted]
    xs: list[float] = []
    ys: list[float] = []
    for rp in ref:
        i = bisect.bisect_left(cand_ts, rp.dt)
        best_val: float | None = None
        best_dt = tol_s + 1.0
        for j in (i - 1, i):
            if 0 <= j < len(cand_sorted):
                delta = abs((cand_ts[j] - rp.dt).total_seconds())
                if delta < best_dt:
                    best_dt = delta
                    best_val = cand_sorted[j].value
        if best_val is not None and best_dt <= tol_s:
            xs.append(rp.value)
            ys.append(best_val)
    return xs, ys, len(xs)


def align_many(
    reference: list[TimePoint],
    others: dict[str, list[TimePoint]],
    tol_s: float = DEFAULT_JOIN_TOL_S,
) -> tuple[list[float], dict[str, list[float | None]]]:
    """Align every series in ``others`` onto ``reference`` by nearest timestamp.

    Returns ``(ref_values, columns)`` where ``ref_values`` is the reference
    series' values (in time order) and ``columns[name]`` is a same-length list of
    the nearest ``other`` value within ``tol_s`` (or ``None`` when out of range).

    Unlike :func:`join_nearest` this keeps *every* reference row (padding with
    ``None``) so a caller can build a rectangular table for a correlation matrix
    and decide per-pair how to drop the gaps.
    """
    ref_sorted = sorted(reference, key=lambda tp: tp.dt)
    ref_vals = [tp.value for tp in ref_sorted]
    columns: dict[str, list[float | None]] = {}
    for name, series in others.items():
        cand_sorted = sorted(series, key=lambda tp: tp.dt)
        cand_ts = [tp.dt for tp in cand_sorted]
        col: list[float | None] = []
        for rp in ref_sorted:
            best_val: float | None = None
            best_dt = tol_s + 1.0
            if cand_ts:
                i = bisect.bisect_left(cand_ts, rp.dt)
                for j in (i - 1, i):
                    if 0 <= j < len(cand_sorted):
                        delta = abs((cand_ts[j] - rp.dt).total_seconds())
                        if delta < best_dt:
                            best_dt = delta
                            best_val = cand_sorted[j].value
            col.append(best_val if best_dt <= tol_s else None)
        columns[name] = col
    return ref_vals, columns
