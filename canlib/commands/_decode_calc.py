#!/usr/bin/env python3
"""Analysis/series math for ``canair decode`` (extracted from decode.py).

These helpers operate on decode's ``all_results`` shape — a list of
``{capture, decoded, error}`` dicts — turning decoded parameter values into the
plain/time-stamped series the correlation, mirror, and stats views consume. They
are the decode-shaped glue over the generic primitives in ``canlib.align`` /
``canlib.stats`` / ``canlib.xanalysis``; keeping them here leaves decode.py as
argparse + orchestration and lets the renderers (``_decode_render``) stay pure
presentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from canlib.inspect_bytes import apply_transform
from canlib.mirrors import DEFAULT_MIRROR_MATCH

if TYPE_CHECKING:
    from canlib.align import TimePoint
    from canlib.fill import FillPolicy


def _series(all_results: list[dict], name: str) -> list[float]:
    """Decoded values for one param across captures (capture order, None dropped)."""
    return [
        r["decoded"][name]["value"]
        for r in all_results
        if name in r["decoded"] and r["decoded"][name].get("value") is not None
    ]


def _paired(all_results: list[dict], ref: str, name: str) -> tuple[list[float], list[float]]:
    """Time-aligned (ref, name) value pairs across captures where both are present."""
    xs: list[float] = []
    ys: list[float] = []
    for r in all_results:
        d = r["decoded"]
        rv = d.get(ref, {}).get("value")
        pv = d.get(name, {}).get("value")
        if rv is not None and pv is not None:
            xs.append(rv)
            ys.append(pv)
    return xs, ys


def _paired_timed(all_results: list[dict], ref: str, name: str) -> tuple[list[float], list[float]]:
    """Like :func:`_paired` but ordered by capture timestamp.

    Needed for order-sensitive reference transforms (``delta``/``cumsum``): the
    capture-list order is not guaranteed to be chronological (recovered journals,
    edits), so a positional pairing would corrupt a rate. Undated captures sort
    last (``datetime.max``) so they never split a dated run.
    """
    from datetime import datetime

    from canlib.capture_dates import entry_datetime

    triples: list[tuple[datetime, float, float]] = []
    for r in all_results:
        d = r["decoded"]
        rv = d.get(ref, {}).get("value")
        pv = d.get(name, {}).get("value")
        if rv is None or pv is None:
            continue
        triples.append((entry_datetime(r.get("capture") or {}) or datetime.max, rv, pv))
    triples.sort(key=lambda t: t[0])
    return [t[1] for t in triples], [t[2] for t in triples]


def _local_series(all_results: list[dict], name: str) -> list:
    """A time-stamped series (list[TimePoint]) for one local param.

    Used to time-align local params against a cross-signal reference on a
    different ECU/PID. Captures with no usable ``datetime`` are dropped.
    """
    from canlib.align import TimePoint
    from canlib.capture_dates import entry_datetime

    out = []
    for r in all_results:
        v = r["decoded"].get(name, {}).get("value")
        if v is None:
            continue
        dt = entry_datetime(r["capture"])
        if dt is None:
            continue
        out.append(TimePoint(dt, float(v)))
    return out


def load_cross_ref_series(ref: str, *, scope: dict, tol_s: float, fill: FillPolicy | None = None):
    """Load an external ``ECU:PID:PARAM|EXPR`` reference as a TimePoint series.

    Returns ``(series, resolved_label)`` or raises ``ValueError`` with a clean
    message. Applies the same date/state/label scope as the local decode so the
    reference is drawn from the same drive/window, and the same ``fill`` policy so a
    run-length reference can be carried onto the local captures' instants.
    """
    from canlib.align import SignalRef, extract_series, load_signal_captures
    from canlib.pids import build_ecu_index, load_pids

    sref = SignalRef.parse(ref)
    loaded = load_signal_captures(
        [(sref.ecu, sref.pid)],
        since=scope.get("since"),
        until=scope.get("until"),
        state=scope.get("state"),
        label=scope.get("label"),
    )
    lp = loaded[(sref.ecu.upper(), sref.pid.upper())]
    if not lp.captures:
        raise ValueError(
            f"no timed captures for reference {sref.ecu}:{sref.pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
    # Resolve a defined param name to its expression when possible.
    params: dict = {}
    ecu_index = build_ecu_index(load_pids())
    ecu_pids = ecu_index.get(sref.ecu.upper(), {}).get("pids", {})
    if sref.pid.upper() in ecu_pids:
        params = ecu_pids[sref.pid.upper()]["parameters"]
    series = extract_series(lp, sref.name_or_expr, parameters=params, fill=fill)
    if not series:
        raise ValueError(f"reference {sref.label} decoded no numeric values in scope")
    return series, sref.label


def axis_group_keys(
    all_results: list[dict],
    axis_spec: str,
    *,
    scope: dict,
    tol_s: float,
    fill: FillPolicy | None = None,
) -> tuple[dict[int, str | None], str]:
    """Map each result to a group key from a cross-signal ``ECU:PID:PARAM`` axis.

    The generalization of ``--discriminate state`` to an arbitrary signal: load
    the axis series (same scope as the local decode), nearest-join it onto each
    capture within ``tol_s``, and discretize its value into a group label. Returns
    ``(keys_by_id, axis_label)`` where ``keys_by_id[id(result)]`` is the group key
    (or ``None`` when no axis sample lands within tolerance).

    Raises ``ValueError`` when the axis can't be built, aligns to nothing, or is
    too high-cardinality to be a sensible grouping (``--discriminate`` wants an
    enum/flag/mode axis, not a continuous analog).
    """
    from canlib.align import join_indices, prepare_series
    from canlib.capture_dates import entry_datetime

    series, label = load_cross_ref_series(axis_spec, scope=scope, tol_s=tol_s, fill=fill)
    axis = prepare_series(series)

    # Nearest-join the axis onto the captures' own clock. The captures arrive in
    # file order, so sort a *view* of them for the join and scatter the results back
    # by identity — the shared join primitive requires a sorted reference, and
    # reusing it is what keeps this axis honouring the same tie-breaking (and
    # forward-fill) rule as every other join.
    timed: list[tuple[float, dict]] = []
    for r in all_results:
        dt = entry_datetime(r["capture"])
        if dt is not None:
            timed.append((dt.timestamp(), r))
    timed.sort(key=lambda t: t[0])

    keys: dict[int, str | None] = {id(r): None for r in all_results}
    ref_idx, cand_idx = join_indices([t for t, _ in timed], axis.ts, tol_s, axis.hold_ts)
    for k, j in zip(ref_idx, cand_idx, strict=True):
        v = axis.values[j]
        keys[id(timed[k][1])] = str(int(v)) if float(v).is_integer() else f"{v:.3f}"

    distinct = {k for k in keys.values() if k is not None}
    if not distinct:
        raise ValueError(f"axis {label} aligned to no captures in scope (join ≤{tol_s}s)")
    if len(distinct) > 12:
        raise ValueError(
            f"axis {label} has {len(distinct)} distinct values in scope; --discriminate "
            "expects a low-cardinality enum/flag/mode axis (or use 'state')"
        )
    return keys, label


def find_mirrors(
    all_results: list[dict],
    *,
    bits: bool = False,
    min_fraction: float = DEFAULT_MIRROR_MATCH,
    allow_offset: bool = False,
):
    """Byte/bit positions on this PID that mirror each other — redundant signals.

    Extracts each capture's reconstructed WiCAN frame from decode's
    ``all_results`` and delegates to the generic matcher in
    :mod:`canlib.mirrors`. Rows are aligned by capture index (all the frames come
    from the same captures), so no timestamp join is involved — the intra-PID
    counterpart of ``correlate --find-mirrors``.
    """
    from canlib.byteindex import payload_to_wican_bytes
    from canlib.mirrors import byte_owns_bit, find_column_mirrors, frame_columns

    frames: list[bytes] = []
    for r in all_results:
        cap = r["capture"]
        try:
            frames.append(payload_to_wican_bytes(cap["payload"]))
        except Exception:
            continue
    return find_column_mirrors(
        frame_columns(frames, bits=bits),
        min_fraction=min_fraction,
        allow_offset=allow_offset,
        same_source=byte_owns_bit,
    )


def _transform_series(series: list[TimePoint], mode: str) -> list[TimePoint]:
    """Apply a POST_TRANSFORMS mode to a TimePoint series (preserving times)."""
    from canlib.align import TimePoint

    vals = apply_transform([tp.value for tp in series], mode)
    return [TimePoint(tp.dt, v) for tp, v in zip(series, vals, strict=True)]
