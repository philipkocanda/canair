#!/usr/bin/env python3
"""Cross-signal analysis engine: correlation matrix + byte hunting.

Shared core for ``canair correlate`` (rank every relationship in a drive) and
``canair hunt`` (which byte on ECU X *is* known signal Y?). Both reduce to:
build time-stamped series, time-align them (``canlib.align``), Pearson-correlate,
and for hunt additionally fit a line + sniff a physical unit.

Pure analysis over ``captures/`` — no device, no numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

from .align import (
    DEFAULT_JOIN_TOL_S,
    LoadedPid,
    SignalRef,
    TimePoint,
    extract_series,
    join_nearest,
    load_signal_captures,
)
from .stats import correlation, pearson

__all__ = [
    "CorrHit",
    "LagHit",
    "build_bit_series",
    "build_byte_series",
    "build_param_series",
    "correlate_matrix",
    "correlation",
    "hunt_byte",
    "lag_scan",
    "linear_fit",
    "pearson",
    "sniff_unit",
    "transform_ref",
]


# ---------------------------------------------------------------------------
# Stats: linear fit + unit sniffing (pearson/spearman live in canlib.stats)
# ---------------------------------------------------------------------------
def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Least-squares fit ``y = m*x + c``; returns ``(m, c, mean_abs_resid)``.

    ``xs`` is the reference (e.g. known speed), ``ys`` the candidate byte. None
    if degenerate.
    """
    n = len(xs)
    if n < 2:
        return None
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    m = (n * sxy - sx * sy) / denom
    c = (sy - m * sx) / n
    resid = sum(abs(y - (m * x + c)) for x, y in zip(xs, ys, strict=True)) / n
    return m, c, resid


# Common physical scalings for the unit sniffer. ``factor`` multiplies the raw
# byte; ``offset`` is added after. Label describes the resulting unit.
_UNIT_CANDIDATES = [
    (1.0, 0.0, "raw (×1)"),
    (0.5, 0.0, "raw/2"),
    (0.1, 0.0, "raw/10"),
    (0.01, 0.0, "raw/100"),
    (0.02, 0.0, "raw×0.02 (cell V)"),
    (1.0, -40.0, "raw−40 (°C offset)"),
    (0.5, -40.0, "raw/2−40 (HK temp)"),
    (1.609344, 0.0, "raw×1.609 (mph→km/h)"),
    (0.621371, 0.0, "raw×0.621 (km/h→mph)"),
]


def sniff_unit(xs: list[float], ys: list[float]) -> str | None:
    """Guess the physical scaling of candidate ``ys`` vs reference ``xs``.

    For each known scaling ``physical = raw*factor + offset`` (``xs`` is the
    reference in physical units, ``ys`` the raw candidate byte), measure how well
    that formula reproduces the reference and pick the closest. Using the
    ``offset`` — not just the slope — is what lets a Hyundai/Kia ``raw−40`` temp
    byte be identified as a temperature rather than a plain ``×1`` scaling.
    Advisory only — returns a short human string (e.g. "≈ km/h ÷ 1.609 ⇒ mph")
    or None when nothing fits well.
    """
    fit = linear_fit(xs, ys)
    if fit is None:
        return None
    m, _c, _ = fit
    if m == 0:
        return None
    n = len(xs)
    ref_span = (max(xs) - min(xs)) or 1.0
    best = None  # (normalised_residual, label)
    for factor, offset, label in _UNIT_CANDIDATES:
        resid = sum(abs((y * factor + offset) - x) for x, y in zip(xs, ys, strict=True)) / n
        norm = resid / ref_span
        if best is None or norm < best[0]:
            best = (norm, label)
    if best is None or best[0] > 0.05:  # >5% of the reference range — no confident unit
        return None
    return f"slope≈{m:.4f} ⇒ {best[1]}"


# ---------------------------------------------------------------------------
# Series construction
# ---------------------------------------------------------------------------
def transform_ref(ref: list[TimePoint], mode: str | None) -> list[TimePoint]:
    """Apply a post-transform (``delta``/``abs``/``cumsum``/…) to a reference
    series, preserving timestamps.

    Order-sensitive transforms (``delta``/``cumsum``) require time order, so the
    series is sorted by timestamp first. Lets ``hunt``/``correlate --against``
    test "is this byte the signal or its *rate*?" — e.g. torque vs acceleration.
    """
    if not mode or mode == "raw" or not ref:
        return ref
    from .commands._decode_plot import apply_transform

    ordered = sorted(ref, key=lambda tp: tp.dt)
    vals = apply_transform([tp.value for tp in ordered], mode)
    return [TimePoint(tp.dt, v) for tp, v in zip(ordered, vals, strict=True)]


def build_param_series(loaded: LoadedPid, parameters: dict) -> dict[str, list[TimePoint]]:
    """One time series per defined (non-empty-expression) param on this PID."""
    out: dict[str, list[TimePoint]] = {}
    for name, pdef in parameters.items():
        if not pdef.get("expression"):
            continue
        series = extract_series(loaded, name, parameters=parameters)
        if series:
            out[f"{loaded.ecu}:{loaded.pid}:{name}"] = series
    return out


def build_byte_series(
    loaded: LoadedPid,
    *,
    min_distinct: int = 4,
    skip_offsets: set[int] | None = None,
    skip_pci: bool = True,
) -> dict[str, list[TimePoint]]:
    """One series per single raw data byte (``Bn``) that varies enough to matter.

    Skips near-constant bytes (``distinct < min_distinct``) — they can't
    correlate and only add noise. Uses raw unsigned bytes (``Bn``); the byte-hunt
    (:func:`hunt_byte`) sweeps richer interpretations.

    ``Bn`` indexes the reconstructed WiCAN frame (with ISO-TP PCI bytes
    re-inserted), which is longer than the raw payload, so the frame length is
    taken from :func:`payload_to_wican_bytes` — not the raw payload hex — or the
    tail bytes of a multi-frame response (e.g. BMS 2101 B62+) would never be
    generated. PCI byte offsets are framing, not data, and are skipped by default
    (``skip_pci``) using the canonical :func:`byteindex.wican_to_isotp` detector
    (which handles the first-frame 2-byte PCI and every consecutive-frame PCI).
    """
    from .byteindex import payload_to_wican_bytes, wican_to_isotp

    skip_offsets = set(skip_offsets or set())
    # Frame length = longest reconstructed WiCAN frame across captures.
    max_len = 0
    for cap in loaded.captures:
        try:
            max_len = max(max_len, len(payload_to_wican_bytes(cap["payload"])))
        except Exception:
            continue
    if not max_len:
        return {}
    if skip_pci:
        skip_offsets |= {i for i in range(max_len) if wican_to_isotp(i) is None}
    out: dict[str, list[TimePoint]] = {}
    for bn in range(max_len):
        if bn in skip_offsets:
            continue
        series = extract_series(loaded, f"B{bn}")
        if len({tp.value for tp in series}) < min_distinct:
            continue
        out[f"{loaded.ecu}:{loaded.pid}:B{bn}"] = series
    return out


def build_bit_series(loaded: LoadedPid, *, skip_pci: bool = True) -> dict[str, list[TimePoint]]:
    """One 0/1 series per individual data bit (``Bn:k``) that actually toggles.

    The bit-level companion to :func:`build_byte_series`. Only bits with ≥2
    distinct values are kept (a constant bit can't correlate), which bounds the
    otherwise-large bit space. Feeding a 0/1 series into the same Pearson yields
    the point-biserial coefficient (bit vs analog) or φ (bit vs bit). PCI framing
    bytes are skipped by default.
    """
    from datetime import datetime

    from .byteindex import payload_to_wican_bytes, wican_to_isotp
    from .capture_dates import entry_datetime

    frames: list[tuple[datetime, bytes]] = []
    max_len = 0
    for cap in loaded.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        try:
            fr = payload_to_wican_bytes(cap["payload"])
        except Exception:
            continue
        frames.append((dt, fr))
        max_len = max(max_len, len(fr))

    out: dict[str, list[TimePoint]] = {}
    for off in range(max_len):
        if skip_pci and wican_to_isotp(off) is None:
            continue
        for k in range(8):
            series = [
                TimePoint(dt, float((fr[off] >> k) & 1)) for dt, fr in frames if off < len(fr)
            ]
            if len({tp.value for tp in series}) >= 2:
                out[f"{loaded.ecu}:{loaded.pid}:B{off}:{k}"] = series
    return out


# ---------------------------------------------------------------------------
# Lead/lag cross-correlation
# ---------------------------------------------------------------------------
@dataclass
class LagHit:
    """Result of a lead/lag sweep: the sample offset that maximises |r|."""

    lag_samples: int
    lag_seconds: float
    r: float
    n: int


def lag_scan(
    ref: list[TimePoint],
    cand: list[TimePoint],
    *,
    tol_s: float = DEFAULT_JOIN_TOL_S,
    max_lag: int = 3,
    method: str = "pearson",
) -> LagHit | None:
    """Shift ``cand`` by ±k sample-intervals and return the lag maximising |r|.

    ``lag_seconds`` is the time shift *applied to the candidate* to best align it
    with the reference (interval unit = the reference's median inter-sample
    spacing). A negative applied shift means the candidate's events occur *later*
    than the reference's (candidate lags). **Apparent lag only:** with sequential
    single-connection polling the result is ``true_lag + fixed_poll_offset`` — it
    shows ordering *relative to the acquisition offset*, not proven causality
    (needs a same-ECU pair or a synced capture). None if no lag yields a defined
    correlation.
    """
    from datetime import timedelta

    if not ref or not cand:
        return None
    ref_sorted = sorted(ref, key=lambda tp: tp.dt)
    diffs = sorted(
        (ref_sorted[i + 1].dt - ref_sorted[i].dt).total_seconds()
        for i in range(len(ref_sorted) - 1)
    )
    step = diffs[len(diffs) // 2] if diffs else 0.0
    if step <= 0:
        step = 1.0
    best: LagHit | None = None
    for k in range(-max_lag, max_lag + 1):
        shifted = [TimePoint(tp.dt + timedelta(seconds=k * step), tp.value) for tp in cand]
        xs, ys, n = join_nearest(ref, shifted, tol_s=tol_s)
        r = correlation(xs, ys, method)
        if r is None:
            continue
        if best is None or abs(r) > abs(best.r):
            best = LagHit(lag_samples=k, lag_seconds=k * step, r=r, n=n)
    return best


# ---------------------------------------------------------------------------
# Correlation matrix
# ---------------------------------------------------------------------------
@dataclass
class CorrHit:
    a: str
    b: str
    r: float
    n: int


def correlate_matrix(
    series: dict[str, list[TimePoint]],
    *,
    tol_s: float = DEFAULT_JOIN_TOL_S,
    min_r: float = 0.6,
    min_n: int = 15,
    include_intra: bool = False,
    method: str = "pearson",
) -> list[CorrHit]:
    """Pairwise correlation across all series, time-aligned by nearest timestamp.

    Returns hits with ``|r| >= min_r`` and ``n >= min_n``, strongest first.
    ``method`` selects Pearson (linear) or Spearman (monotone/rank). Same-(ECU,
    PID) pairs are dropped unless ``include_intra`` (they're already covered by
    ``decode --corr`` and dominate the ranking).
    """
    names = list(series)
    hits: list[CorrHit] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if not include_intra and _same_pid(a, b):
                continue
            xs, ys, n = join_nearest(series[a], series[b], tol_s=tol_s)
            if n < min_n:
                continue
            r = correlation(xs, ys, method)
            if r is None or abs(r) < min_r:
                continue
            hits.append(CorrHit(a, b, r, n))
    hits.sort(key=lambda h: -abs(h.r))
    return hits


def _same_pid(a: str, b: str) -> bool:
    """True if two ``ECU:PID:SIGNAL`` labels share the same ECU+PID."""
    pa = a.rsplit(":", 1)[0]
    pb = b.rsplit(":", 1)[0]
    return pa == pb


# ---------------------------------------------------------------------------
# Byte hunt — "which byte/interpretation on ECU:PID is this reference signal?"
# ---------------------------------------------------------------------------
@dataclass
class HuntHit:
    expr: str  # WiCAN expression (or "<no-expr>" for float/LE-signed)
    interp: str  # e.g. "u8", "i16 LE"
    offset: int
    r: float
    n: int
    slope: float
    intercept: float
    resid: float
    unit_guess: str | None
    width: int = 1


def hunt_byte(
    loaded: LoadedPid,
    ref: list[TimePoint],
    *,
    tol_s: float = DEFAULT_JOIN_TOL_S,
    min_n: int = 10,
    top: int = 12,
    method: str = "pearson",
    all_interps: bool = False,
) -> list[HuntHit]:
    """Sweep every byte offset × interpretation, rank by |r| vs ``ref``.

    Reuses the plot explorer's interpretation machinery (``INSPECT_TYPES``,
    ``interpret_bytes``, ``wican_expr``) so hunt and plot agree on how bytes are
    read and expressed. PCI-crossing multi-byte reads are skipped. Ranking uses
    ``method`` (pearson/spearman); the reported linear fit is always least-squares
    on the raw values regardless.
    """
    from .byteindex import payload_to_wican_bytes
    from .capture_dates import entry_datetime
    from .commands._decode_plot import INSPECT_TYPES, interpret_bytes, wican_expr

    # Precompute (datetime, frame) for each timed capture.
    frames: list[tuple] = []
    max_len = 0
    for cap in loaded.captures:
        dt = entry_datetime(cap)
        if dt is None:
            continue
        frame = payload_to_wican_bytes(cap["payload"])
        frames.append((dt, frame))
        max_len = max(max_len, len(frame))
    if not frames:
        return []

    # PCI byte positions in the WiCAN frame (0, 8, 16, ...): a multi-byte read
    # spanning one is garbage.
    pci = {i for i in range(max_len) if i % 8 == 0}

    hits: list[HuntHit] = []
    for spec in INSPECT_TYPES:
        _, width, _kind, _signed = spec
        for endian_little in (False, True) if width > 1 else (False,):
            for off in range(max_len):
                if off + width > max_len:
                    continue
                if any((off + k) in pci for k in range(width)):
                    continue
                cand: list[TimePoint] = []
                for dt, frame in frames:
                    v = interpret_bytes(frame, off, spec, little=endian_little)
                    if v is not None:
                        cand.append(TimePoint(dt, v))
                if len({tp.value for tp in cand}) < 3:
                    continue
                xs, ys, n = join_nearest(ref, cand, tol_s=tol_s)
                if n < min_n:
                    continue
                r = correlation(xs, ys, method)
                if r is None:
                    continue
                fit = linear_fit(xs, ys)
                if fit is None:
                    continue
                m, c, resid = fit
                expr = wican_expr(off, spec, little=endian_little) or "<no-expr>"
                interp = spec[0] + (" LE" if endian_little and width > 1 else "")
                hits.append(
                    HuntHit(
                        expr=expr,
                        interp=interp,
                        offset=off,
                        r=r,
                        n=n,
                        slope=m,
                        intercept=c,
                        resid=resid,
                        unit_guess=sniff_unit(xs, ys),
                        width=width,
                    )
                )

    # Rank: strongest |r| first; among near-equal r, prefer the narrowest read
    # (a single byte that *is* the signal beats any wider window that merely
    # contains it) and the lowest relative residual. Also demote reads with no
    # WiCAN expression (float / LE-signed) — not directly usable as a param.
    def _rank(h: HuntHit) -> tuple:
        rel_resid = h.resid / (abs(h.slope) or 1.0)
        return (-round(abs(h.r), 3), h.width, h.expr == "<no-expr>", rel_resid)

    hits.sort(key=_rank)
    # Collapse to the best hit per starting offset (default) so one real signal
    # doesn't emit a wall of near-identical u8/i16/u24/… rows; --all-interps
    # (all_interps=True) keeps every (offset, interp). Then trim to ``top``.
    seen: set = set()
    unique: list[HuntHit] = []
    for h in hits:
        key = h.offset if not all_interps else f"{h.offset}:{h.interp}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
        if len(unique) >= top:
            break
    return unique


def load_ref(
    ref_spec: str,
    *,
    since=None,
    until=None,
    state=None,
    label=None,
) -> tuple[list[TimePoint], str]:
    """Load an ``ECU:PID:PARAM|EXPR`` reference series (shared by hunt/correlate).

    Raises ``ValueError`` with a clean message when the reference can't be built.
    """
    from .pids import build_ecu_index, load_pids

    sref = SignalRef.parse(ref_spec)
    loaded = load_signal_captures(
        [(sref.ecu, sref.pid)],
        since=since,
        until=until,
        state=state,
        label=label,
    )
    lp = loaded[(sref.ecu.upper(), sref.pid.upper())]
    if not lp.captures:
        raise ValueError(
            f"no timed captures for reference {sref.ecu}:{sref.pid} in scope"
            + (f" ({lp.n_no_time} untimed skipped)" if lp.n_no_time else "")
        )
    params: dict = {}
    ecu_pids = build_ecu_index(load_pids()).get(sref.ecu.upper(), {}).get("pids", {})
    if sref.pid.upper() in ecu_pids:
        params = ecu_pids[sref.pid.upper()].get("parameters", {})
    series = extract_series(lp, sref.name_or_expr, parameters=params)
    if not series:
        raise ValueError(f"reference {sref.label} decoded no numeric values in scope")
    return series, sref.label
