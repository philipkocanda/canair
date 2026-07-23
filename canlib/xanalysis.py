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

__all__ = [
    "CorrHit",
    "build_byte_series",
    "build_param_series",
    "correlate_matrix",
    "hunt_byte",
    "linear_fit",
    "pearson",
    "sniff_unit",
]


# ---------------------------------------------------------------------------
# Stats (hand-rolled, no numpy — matches decode.py conventions)
# ---------------------------------------------------------------------------
def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return cov / (sx**0.5 * sy**0.5)


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

    Fits ``y = m*x + c`` then reports which common scaling best explains the
    reference→candidate slope. Advisory only — returns a short human string
    (e.g. "≈ km/h ÷ 1.609 ⇒ mph") or None when nothing fits well.
    """
    fit = linear_fit(xs, ys)
    if fit is None:
        return None
    m, _c, _ = fit
    if m == 0:
        return None
    # The candidate ≈ m·ref. So ref ≈ candidate/m; a unit factor f means
    # candidate = ref/f (candidate in ref-units scaled by 1/f). Compare 1/m to
    # each known factor.
    inv = 1.0 / m
    best = None
    for factor, _off, label in _UNIT_CANDIDATES:
        err = abs(inv - factor) / (abs(factor) or 1.0)
        if best is None or err < best[0]:
            best = (err, label, factor)
    if best is None or best[0] > 0.05:  # >5% off — no confident unit
        return None
    return f"slope≈{m:.4f} ⇒ {best[1]}"


# ---------------------------------------------------------------------------
# Series construction
# ---------------------------------------------------------------------------
def build_param_series(
    loaded: LoadedPid, parameters: dict
) -> dict[str, list[TimePoint]]:
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
) -> dict[str, list[TimePoint]]:
    """One series per single raw data byte (``Bn``) that varies enough to matter.

    Skips near-constant bytes (``distinct < min_distinct``) — they can't
    correlate and only add noise. Uses raw unsigned bytes (``Bn``); the byte-hunt
    (:func:`hunt_byte`) sweeps richer interpretations.
    """

    skip_offsets = skip_offsets or set()
    # Determine the frame length from the longest capture.
    max_len = 0
    for cap in loaded.captures:
        pl = cap["payload"].replace(" ", "")
        max_len = max(max_len, len(pl) // 2)
    if not max_len:
        return {}
    out: dict[str, list[TimePoint]] = {}
    for bn in range(max_len):
        if bn in skip_offsets:
            continue
        series = extract_series(loaded, f"B{bn}")
        if len({tp.value for tp in series}) < min_distinct:
            continue
        out[f"{loaded.ecu}:{loaded.pid}:B{bn}"] = series
    return out


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
) -> list[CorrHit]:
    """Pairwise Pearson across all series, time-aligned by nearest timestamp.

    Returns hits with ``|r| >= min_r`` and ``n >= min_n``, strongest first.
    Same-(ECU,PID) pairs are dropped unless ``include_intra`` (they're already
    covered by ``decode --corr`` and dominate the ranking).
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
            r = pearson(xs, ys)
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
) -> list[HuntHit]:
    """Sweep every byte offset × interpretation, rank by |r| vs ``ref``.

    Reuses the plot explorer's interpretation machinery (``INSPECT_TYPES``,
    ``interpret_bytes``, ``wican_expr``) so hunt and plot agree on how bytes are
    read and expressed. PCI-crossing multi-byte reads are skipped. For each top
    hit, reports the best linear fit + a unit guess.
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
                r = pearson(xs, ys)
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
    # De-dupe by (offset, interp) keeping the strongest, then trim.
    seen: set[str] = set()
    unique: list[HuntHit] = []
    for h in hits:
        key = f"{h.offset}:{h.interp}"
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
        params = ecu_pids[sref.pid.upper()]["parameters"]
    series = extract_series(lp, sref.name_or_expr, parameters=params)
    if not series:
        raise ValueError(f"reference {sref.label} decoded no numeric values in scope")
    return series, sref.label
