#!/usr/bin/env python3
"""Cross-signal analysis engine: correlation matrix + byte hunting.

Shared core for ``canair correlate`` (rank every relationship in a drive) and
``canair hunt`` (which byte on ECU X *is* known signal Y?). Both reduce to:
build time-stamped series, time-align them (``canlib.align``), Pearson-correlate,
and for hunt additionally fit a line + sniff a physical unit.

Pure analysis over ``captures/`` — no device, no numpy.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .align import (
    DEFAULT_JOIN_TOL_S,
    DEFAULT_SESSION_GAP_S,
    LoadedPid,
    SignalRef,
    TimePoint,
    detrend_by_session,
    extract_series,
    join_nearest,
    load_signal_captures,
)
from .corrmatrix import (
    CorrHit,
    colinear_clusters,
    correlate_matrix,
    signal_group_key,
)
from .fill import FillPolicy
from .inspect_bytes import (
    INSPECT_TYPES,
    NO_EXPR,
    apply_transform,
    float_series_is_noise,
    interpret_bytes,
    read_indices,
    wican_expr,
    wican_expr_indices,
)
from .physical_bands import DEFAULT_PHYSICAL_BANDS
from .stats import correlation, linear_fit, pearson
from .unit_guess import DEFAULT_UNIT_CANDIDATES as _UNIT_CANDIDATES

__all__ = [
    "PHYSICAL_BANDS",
    "CorrHit",
    "LagHit",
    "PhysicalHit",
    "build_bit_series",
    "build_byte_series",
    "build_param_series",
    "byte_state_buckets",
    "colinear_clusters",
    "correlate_matrix",
    "correlation",
    "discriminability",
    "hunt_byte",
    "lag_scan",
    "linear_fit",
    "pearson",
    "physical_scan",
    "reference_is_absolute_level",
    "reference_is_bimodal",
    "signal_group_key",
    "sniff_unit",
    "transform_ref",
    "unit_dimension",
]


# ---------------------------------------------------------------------------
# Reference-quality guard
# ---------------------------------------------------------------------------
def reference_is_bimodal(
    values: Sequence[float | None],
    *,
    min_n: int = 15,
    min_cluster_frac: float = 0.05,
    gap_ratio: float = 3.0,
) -> bool:
    """True when a reference collapses into ~2 well-separated value clusters.

    On such a reference — e.g. a 12 V bus that sits at ~14.5 V while charging and
    ~12.2 V otherwise — *any* candidate that merely differs between the two
    regimes correlates near-perfectly (a two-cluster / point-biserial artifact),
    so |r| ranks *cluster separation*, not a real signal match. Detected by a
    single dominant gap (``> gap_ratio ×`` the wider cluster's own spread — which
    is what spares a *continuously*-varying reference like speed, whose "moving"
    cluster is wide) that splits the values into two clusters, the smaller
    holding at least ``min_cluster_frac`` of the samples (so a lone outlier
    doesn't trigger it).
    """
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n < min_n:
        return False
    uniq = sorted(set(vals))
    if len(uniq) < 2 or uniq[-1] == uniq[0]:
        return False  # constant
    gap, k = max((uniq[i + 1] - uniq[i], i) for i in range(len(uniq) - 1))
    within = max(uniq[k] - uniq[0], uniq[-1] - uniq[k + 1])
    if within > 0 and gap <= gap_ratio * within:
        return False  # the gap doesn't dominate — looks continuous
    split = (uniq[k] + uniq[k + 1]) / 2.0
    n_low = sum(1 for v in vals if v <= split)
    frac = min(n_low, n - n_low) / n
    return frac >= min_cluster_frac


def reference_is_absolute_level(
    values: list[float],
    *,
    min_n: int = 10,
    baseline_floor: float = 5.0,
    max_rel_span: float = 0.15,
) -> bool:
    """True if a series looks like a slowly-varying absolute *level*.

    A signal sitting on a large non-zero baseline with only a small relative
    swing (a pack/12V/mains voltage, a temperature held near a setpoint) is the
    case where Pearson |r| misleads: the swing is dwarfed by the baseline and by
    cross-session DC offsets, so ``hunt --against`` / ``correlate`` rank noise.
    The fix is band/anchor reasoning (``hunt --physical`` + per-state absolute
    comparison), which this flag points the user toward.

    Deliberately conservative to avoid false positives on genuinely dynamic
    signals (speed/RPM sweep from ~0, wide relative span): requires a baseline
    of at least ``baseline_floor`` and a peak-to-peak span under
    ``max_rel_span`` of ``|mean|``.
    """
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n < min_n:
        return False
    mean = sum(vals) / n
    if abs(mean) < baseline_floor:
        return False  # near-zero baseline — DC offset doesn't dominate
    span = max(vals) - min(vals)
    return (span / abs(mean)) < max_rel_span


# ---------------------------------------------------------------------------
# Stats: unit sniffing (pearson/spearman/linear_fit live in canlib.stats)
# ---------------------------------------------------------------------------
# Common physical scalings for the unit sniffer. The candidate table + resolver
# live in canlib.unit_guess (make-neutral built-ins + profile extension); this
# module just consumes the resolved list (imported above as _UNIT_CANDIDATES for
# the callers/tests that referenced it from xanalysis historically).


def unit_dimension(unit: str | None) -> str | None:
    """Coarse physical dimension of a reference unit string (or None if unknown).

    Used to gate the unit-guess domain hint: a candidate's flavour (voltage /
    temperature / speed) is only shown when it agrees with the reference's own
    dimension. Deliberately conservative — an unrecognized unit returns None so
    the hint is left as-is rather than wrongly suppressed.
    """
    if not unit:
        return None
    u = unit.strip().lower()
    if u in {"v", "mv", "kv", "volt", "volts"}:
        return "voltage"
    if u in {"°c", "c", "degc", "celsius", "°f", "f", "degf", "k", "kelvin"}:
        return "temperature"
    if u in {"km/h", "kmh", "kph", "mph", "m/s", "mps"}:
        return "speed"
    return None


def sniff_unit(
    xs: list[float],
    ys: list[float],
    ref_unit: str | None = None,
    candidates: list[tuple[float, float, str, str | None, str | None]] | None = None,
) -> str | None:
    """Guess the physical scaling of candidate ``ys`` vs reference ``xs``.

    For each known scaling ``physical = raw*factor + offset`` (``xs`` is the
    reference in physical units, ``ys`` the raw candidate byte), measure how well
    that formula reproduces the reference and pick the closest. Using the
    ``offset`` — not just the slope — is what lets a ``raw−40`` temperature byte
    be identified as a temperature rather than a plain ``×1`` scaling.

    ``candidates`` is the resolved scaling list (see
    :func:`canlib.unit_guess.resolve_unit_candidates`); ``None`` uses the
    make-neutral built-ins.

    ``ref_unit`` (the reference signal's unit, when known) gates the domain hint:
    a candidate's flavour (e.g. "cell V") is suppressed when the reference is a
    *different* dimension (e.g. a speed), leaving just the numeric scale — so a
    speed reference no longer tags an RPM slope as a cell voltage. An unknown
    ``ref_unit`` leaves the hint untouched (conservative).

    Advisory only — returns a short human string (e.g. "slope≈0.6214 ⇒ raw×0.621
    (km/h→mph)") or None when nothing fits well.
    """
    if candidates is None:
        candidates = _UNIT_CANDIDATES
    fit = linear_fit(xs, ys)
    if fit is None:
        return None
    m, _c, _ = fit
    if m == 0:
        return None
    n = len(xs)
    ref_span = (max(xs) - min(xs)) or 1.0
    ref_dim = unit_dimension(ref_unit)
    best = None  # (normalised_residual, numeric_label, hint, dimension)
    for factor, offset, numeric_label, hint, dimension in candidates:
        resid = sum(abs((y * factor + offset) - x) for x, y in zip(xs, ys, strict=True)) / n
        norm = resid / ref_span
        if best is None or norm < best[0]:
            best = (norm, numeric_label, hint, dimension)
    if best is None or best[0] > 0.05:  # >5% of the reference range — no confident unit
        return None
    _norm, numeric_label, hint, dimension = best
    label = numeric_label
    # Show the domain hint unless a *known* reference dimension contradicts it.
    if hint is not None and not (ref_dim is not None and ref_dim != dimension):
        label = f"{numeric_label} ({hint})"
    return f"slope≈{m:.4f} ⇒ {label}"


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

    ordered = sorted(ref, key=lambda tp: tp.dt)
    vals = apply_transform([tp.value for tp in ordered], mode)
    return [TimePoint(tp.dt, v, tp.hold_until) for tp, v in zip(ordered, vals, strict=True)]


def build_param_series(
    loaded: LoadedPid, parameters: dict, *, fill: FillPolicy | None = None
) -> dict[str, list[TimePoint]]:
    """One time series per defined (non-empty-expression) param on this PID."""
    out: dict[str, list[TimePoint]] = {}
    for name, pdef in parameters.items():
        if not pdef.get("expression"):
            continue
        series = extract_series(loaded, name, parameters=parameters, fill=fill)
        if series:
            out[f"{loaded.ecu}:{loaded.pid}:{name}"] = series
    return out


def build_byte_series(
    loaded: LoadedPid,
    *,
    min_distinct: int = 4,
    skip_offsets: set[int] | None = None,
    skip_pci: bool = True,
    fill: FillPolicy | None = None,
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

    from .byteindex import wican_to_isotp

    skip_offsets = set(skip_offsets or set())
    # Each capture's WiCAN frame + timestamp is decoded ONCE by LoadedPid.decoded
    # (shared with every other pass over this PID), then every byte offset is read
    # by indexing the frame. Calling extract_series per offset would re-parse
    # (payload_to_wican_bytes + evaluate_expression + entry_datetime) every capture
    # once per byte — O(bytes * captures) redundant parsing.
    rows = list(zip(loaded.timed_frames(), loaded.timed_holds(fill), strict=True))
    max_len = max((len(fr) for (_dt, fr), _h in rows), default=0)
    if not max_len:
        return {}
    if skip_pci:
        skip_offsets |= {i for i in range(max_len) if wican_to_isotp(i) is None}
    out: dict[str, list[TimePoint]] = {}
    for bn in range(max_len):
        if bn in skip_offsets:
            continue
        series = [TimePoint(dt, float(fr[bn]), h) for (dt, fr), h in rows if bn < len(fr)]
        if len({tp.value for tp in series}) < min_distinct:
            continue
        out[f"{loaded.ecu}:{loaded.pid}:B{bn}"] = series
    return out


def build_bit_series(
    loaded: LoadedPid, *, skip_pci: bool = True, fill: FillPolicy | None = None
) -> dict[str, list[TimePoint]]:
    """One 0/1 series per individual data bit (``Bn:k``) that actually toggles.

    The bit-level companion to :func:`build_byte_series`. Only bits with ≥2
    distinct values are kept (a constant bit can't correlate), which bounds the
    otherwise-large bit space. Feeding a 0/1 series into the same Pearson yields
    the point-biserial coefficient (bit vs analog) or φ (bit vs bit). PCI framing
    bytes are skipped by default.
    """

    from .byteindex import wican_to_isotp

    rows = list(zip(loaded.timed_frames(), loaded.timed_holds(fill), strict=True))
    max_len = max((len(fr) for (_dt, fr), _h in rows), default=0)

    out: dict[str, list[TimePoint]] = {}
    for off in range(max_len):
        if skip_pci and wican_to_isotp(off) is None:
            continue
        for k in range(8):
            series = [
                TimePoint(dt, float((fr[off] >> k) & 1), h) for (dt, fr), h in rows if off < len(fr)
            ]
            if len({tp.value for tp in series}) >= 2:
                out[f"{loaded.ecu}:{loaded.pid}:B{off}:{k}"] = series
    return out


# ---------------------------------------------------------------------------
# State discrimination (F-like separation of a signal across power states)
# ---------------------------------------------------------------------------
def discriminability(groups: dict[str, list[float]], *, min_group_n: int = 2) -> float | None:
    """F-like score: between-group variance / within-group (pooled) variance.

    High when a signal is nearly constant within each state but differs across
    states (a mode/thermal/relay signal). ``None`` when undefined (too few
    groups/points).

    ``min_group_n`` is the sample floor a group must clear to take part. The
    default 2 is the statistical minimum (a single sample has no within-group
    variance to pool), but a group sitting *at* the floor can manufacture a
    perfect separation: two identical samples in their own bucket contribute zero
    within-group spread, so ``msw`` collapses to 0 and the byte is ranked as an
    infinitely good discriminator. Raise the floor to demand real evidence.
    """
    pops = [vals for vals in groups.values() if len(vals) >= max(2, min_group_n)]
    if len(pops) < 2:
        return None
    all_vals = [v for vals in pops for v in vals]
    n = len(all_vals)
    grand = sum(all_vals) / n
    between = sum(len(vals) * (sum(vals) / len(vals) - grand) ** 2 for vals in pops)
    within = sum((v - sum(vals) / len(vals)) ** 2 for vals in pops for v in vals)
    df_between = len(pops) - 1
    df_within = n - len(pops)
    if df_between <= 0 or df_within <= 0:
        return None
    msb = between / df_between
    msw = within / df_within
    if msw == 0:
        # Every group internally constant. Genuine perfect separation is possible,
        # but so is an artifact of thin buckets, and `inf` outranks every measured
        # byte either way. Score it on between-group spread relative to the pooled
        # scale instead, so a real 0/1 relay still ranks high while a two-sample
        # singleton cannot beat it on a technicality.
        if msb <= 0:
            return None
        scale = (sum((v - grand) ** 2 for v in all_vals) / n) or 1.0
        return msb / scale * n
    return msb / msw


def byte_state_buckets(
    all_results: list[dict],
    field: str,
    *,
    min_distinct: int = 2,
    include_bits: bool = False,
    group_of: Callable[[dict], str | None] | None = None,
) -> dict[str, dict[str, list[float]]]:
    """Bucket each varying, non-PCI raw byte value by session ``field``.

    The raw-byte analogue of the param buckets in ``decode.print_discriminate``.
    Uses a low ``min_distinct`` (2) on purpose: the highest-value discrimination
    targets are near-binary relay/mode bytes (e.g. 0x00/0x34) that the default
    correlation floor (4) would drop. Reads every capture (incl. untimed —
    discrimination buckets by state, not time) and skips PCI framing bytes via
    the canonical :func:`byteindex.wican_to_isotp` detector. With ``include_bits``,
    each varying bit ``Bn:k`` is also bucketed (the body-status/relay finder).

    ``group_of`` overrides the grouping key per result (the arbitrary-axis
    generalization: e.g. bucket by a cross-signal's discretized value instead of
    the vehicle state); when it returns ``None`` for a capture, that capture is
    skipped. Defaults to the session vehicle-state key.
    """
    from .byteindex import payload_to_wican_bytes, wican_to_isotp
    from .states import load_states, state_bucket_key

    rules = load_states() if group_of is None else []
    key_cache: dict[tuple[str, ...], str] = {}

    def _state_key(cap: dict) -> str:
        raw = tuple(str(s).upper() for s in cap.get("vehicle_states") or [])
        hit = key_cache.get(raw)
        if hit is None:
            hit = state_bucket_key(raw, rules)
            key_cache[raw] = hit
        return hit

    frames: list[tuple[bytes, str]] = []
    max_len = 0
    for r in all_results:
        cap = r["capture"]
        try:
            payload = cap.get("payload")
            if not payload:
                continue
            fr = payload_to_wican_bytes(payload)
        except Exception:
            continue
        if group_of is not None:
            grp = group_of(r)
            if grp is None:
                continue
        else:
            grp = _state_key(cap)
        frames.append((fr, grp))
        max_len = max(max_len, len(fr))

    buckets: dict[str, dict[str, list[float]]] = {}
    for off in range(max_len):
        if wican_to_isotp(off) is None:
            continue  # PCI framing byte, not data
        per_state: dict[str, list[float]] = {}
        distinct: set[float] = set()
        for fr, grp in frames:
            if off < len(fr):
                v = float(fr[off])
                per_state.setdefault(grp, []).append(v)
                distinct.add(v)
        if len(distinct) >= min_distinct:
            buckets[f"B{off}"] = per_state
        if include_bits:
            for k in range(8):
                bit_state: dict[str, list[float]] = {}
                bit_distinct: set[float] = set()
                for fr, grp in frames:
                    if off < len(fr):
                        b = float((fr[off] >> k) & 1)
                        bit_state.setdefault(grp, []).append(b)
                        bit_distinct.add(b)
                if len(bit_distinct) >= 2:
                    buckets[f"B{off}:{k}"] = bit_state
    return buckets


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
        shift = timedelta(seconds=k * step)
        shifted = [
            TimePoint(
                tp.dt + shift,
                tp.value,
                None if tp.hold_until is None else tp.hold_until + shift,
            )
            for tp in cand
        ]
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
# The pairwise ranking engine lives in canlib.corrmatrix (bucketed join + hoisted
# per-signal statistics); re-exported here because `correlate`/`investigate` and
# the tests have always reached for it through xanalysis.


# ---------------------------------------------------------------------------
# Byte hunt — "which byte/interpretation on ECU:PID is this reference signal?"
# ---------------------------------------------------------------------------
@dataclass
class HuntHit:
    expr: str  # WiCAN expression, or NO_EXPR for a float reinterpretation
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
    control: list[TimePoint] | None = None,
    ref_unit: str | None = None,
    candidates: list[tuple[float, float, str, str | None, str | None]] | None = None,
    per_session: bool = False,
    session_gap_s: float = DEFAULT_SESSION_GAP_S,
    fill: FillPolicy | None = None,
) -> list[HuntHit]:
    """Sweep every byte offset × interpretation, rank by |r| vs ``ref``.

    Uses the shared byte-interpretation primitives (``INSPECT_TYPES``,
    ``interpret_bytes``, ``wican_expr`` from ``canlib.inspect_bytes``) so hunt and
    plot agree on how bytes are
    read and expressed. PCI-crossing multi-byte reads are skipped. Ranking uses
    ``method`` (pearson/spearman); the reported linear fit is always least-squares
    on the raw values regardless.

    With ``control`` (a nuisance/confounder series), each candidate is ranked by
    the **partial** correlation ``r_(ref,cand)·control`` over the three-way
    time-aligned points — surfacing a byte whose relationship to ``ref`` only
    appears once the dominant driver is regressed out (and demoting bytes that
    merely track the control). The linear fit stays on the raw (ref, cand) pairs.
    """
    from .align import join_nearest_triple
    from .byteindex import wican_to_isotp
    from .stats import partial_correlation

    # (datetime, frame) per timed capture, decoded once by LoadedPid.decoded. A
    # non-hex payload (a stored "NO DATA", a mis-transcribed capture) is dropped
    # there rather than aborting the sweep — every sibling series builder agrees.
    frames = loaded.timed_frames()
    holds = loaded.timed_holds(fill)
    max_len = max((len(fr) for _dt, fr in frames), default=0)
    if not frames:
        return []

    # PCI (ISO-TP framing) byte positions in the WiCAN frame: a read spanning one
    # is garbage. Use the canonical detector (not `i % 8 == 0`, which misses the
    # first frame's second PCI byte at index 1) so hunt matches build_byte_series,
    # coverage, validate, etc.
    pci = {i for i in range(max_len) if wican_to_isotp(i) is None}

    ref_used = detrend_by_session(ref, session_gap_s) if per_session else ref
    control_used = (
        detrend_by_session(control, session_gap_s)
        if (per_session and control is not None)
        else control
    )

    hits: list[HuntHit] = []

    def _consider(cand: list[TimePoint], expr: str, interp: str, offset: int, width: int) -> None:
        c = detrend_by_session(cand, session_gap_s) if per_session else cand
        if control_used is not None:
            xs, ys, zs, n = join_nearest_triple(ref_used, c, control_used, tol_s=tol_s)
            if n < min_n:
                return
            r = partial_correlation(xs, ys, zs, method)
        else:
            xs, ys, n = join_nearest(ref_used, c, tol_s=tol_s)
            if n < min_n:
                return
            r = correlation(xs, ys, method)
        if r is None:
            return
        fit = linear_fit(xs, ys)
        if fit is None:
            return
        m, ic, resid = fit
        hits.append(
            HuntHit(
                expr=expr,
                interp=interp,
                offset=offset,
                r=r,
                n=n,
                slope=m,
                intercept=ic,
                resid=resid,
                unit_guess=sniff_unit(xs, ys, ref_unit, candidates),
                width=width,
            )
        )

    # Contiguous byte-window sweep: every interpretation x both endians.
    for spec in INSPECT_TYPES:
        _, width, kind, _signed = spec
        for endian_little in (False, True) if width > 1 else (False,):
            for off in range(max_len):
                if off + width > max_len:
                    continue
                if any((off + k) in pci for k in range(width)):
                    continue
                cand = [
                    TimePoint(dt, v, hold)
                    for (dt, frame), hold in zip(frames, holds, strict=True)
                    if (v := interpret_bytes(frame, off, spec, little=endian_little)) is not None
                ]
                if len({tp.value for tp in cand}) < 3:
                    continue
                if kind == "float" and float_series_is_noise([tp.value for tp in cand]):
                    continue  # implausible float reinterpretation (denormal/huge) — skip
                expr = wican_expr(off, spec, little=endian_little) or NO_EXPR
                interp = spec[0] + (" LE" if endian_little and width > 1 else "")
                _consider(cand, expr, interp, off, width)

    # PCI-skip sweep: big-endian multi-byte values whose data bytes are contiguous
    # in ISO-TP order but straddle a framing (PCI) byte in the WiCAN frame — e.g. a
    # signed 16-bit pack current with its low byte in the next consecutive frame
    # (high byte B15, PCI at B16, low byte B17 → `S15*256 + B17`). The contiguous
    # sweep above skips any window containing a PCI byte, so without this these
    # frame-straddling words are undiscoverable.
    data_idx = [i for i in range(max_len) if i not in pci]  # WiCAN indices, ISO-TP order
    for width in (2, 3, 4):
        for p in range(len(data_idx) - width + 1):
            idxs = data_idx[p : p + width]
            if idxs[-1] - idxs[0] == width - 1:
                continue  # contiguous — already covered by the sweep above
            for signed in (False, True):
                cand = [
                    TimePoint(dt, v, hold)
                    for (dt, frame), hold in zip(frames, holds, strict=True)
                    if (v := read_indices(frame, idxs, signed)) is not None
                ]
                if len({tp.value for tp in cand}) < 3:
                    continue
                expr = wican_expr_indices(idxs, signed)
                interp = f"{'i' if signed else 'u'}{width * 8} skip-PCI"
                _consider(cand, expr, interp, idxs[0], width)

    # Rank: strongest |r| first; among near-equal r, prefer the narrowest read
    # (a single byte that *is* the signal beats any wider window that merely
    # contains it) and the lowest relative residual. Also demote reads with no
    # expression (a float reinterpretation) — not directly usable as a param.
    return _rank_and_collapse(hits, top=top, all_interps=all_interps)


def _rank_and_collapse(hits: list[HuntHit], *, top: int, all_interps: bool) -> list[HuntHit]:
    """Shared hit ranking + per-offset collapse for byte/frame hunts.

    Sorts strongest |r| first; among near-equal r prefers the narrowest read and
    lowest relative residual, and demotes ``<no-expr>`` (a float reinterpretation)
    hits. Collapses to the best hit per starting offset (``all_interps`` keeps every
    ``offset:interp``), then trims to ``top``.
    """

    def _rank(h: HuntHit) -> tuple[float, int, bool, float]:
        rel_resid = h.resid / (abs(h.slope) or 1.0)
        return (-round(abs(h.r), 3), h.width, h.expr == NO_EXPR, rel_resid)

    hits.sort(key=_rank)
    seen: set[int | str] = set()
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
    since: date | datetime | None = None,
    until: date | datetime | None = None,
    state: str | Sequence[str] | None = None,
    label: str | None = None,
    fill: FillPolicy | None = None,
) -> tuple[list[TimePoint], str]:
    """Load an ``ECU:PID:PARAM|EXPR`` reference series (shared by hunt/correlate).

    Raises ``ValueError`` with a clean message when the reference can't be built.
    ``fill`` stamps the samples with their validity windows, so a run-length
    reference can still be joined onto instants it has no sample at.
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
    series = extract_series(lp, sref.name_or_expr, parameters=params, fill=fill)
    if not series:
        raise ValueError(f"reference {sref.label} decoded no numeric values in scope")
    return series, sref.label


def ref_unit_for(ref_spec: str) -> str | None:
    """The declared ``unit`` of an ``ECU:PID:PARAM`` reference, or None.

    Used to gate :func:`sniff_unit`'s domain hint to the reference's dimension.
    Returns None for an expression reference (no named param) or when anything
    can't be resolved — the sniffer treats None as "don't gate".
    """
    from .pids import build_ecu_index, load_pids

    try:
        sref = SignalRef.parse(ref_spec)
        ecu_pids = build_ecu_index(load_pids()).get(sref.ecu.upper(), {}).get("pids", {})
        params = ecu_pids.get(sref.pid.upper(), {}).get("parameters", {})
        return params.get(sref.name_or_expr, {}).get("unit")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Physical-value band scan — find bytes whose value lands in a known band
# ---------------------------------------------------------------------------
# Named physical bands: (label, low, high) in the band's natural unit. A byte's
# raw value is multiplied by a candidate scaling before the band test, so e.g. a
# 16-bit centivolt word (raw ~22200) is caught by the mains-RMS band at x0.01.
# Bands are car-class + grid-region assumptions (mains/HV/12V are EV/region
# -typical). The built-in defaults live in canlib.physical_bands; a profile
# (vehicle axis) and the user's grid_region (grid axis) override them via
# resolve_physical_bands(). This constant is the default when no bands are
# threaded in (preserves the historical no-config behaviour).
PHYSICAL_BANDS: list[tuple[str, float, float]] = list(DEFAULT_PHYSICAL_BANDS.values())

# Candidate scalings (factor, offset, label, kind): physical = raw * factor + offset.
# ``kind`` pairs a scaling with the bands it is meaningful for: "ratio" scalings
# (offset 0) test the voltage/frequency bands; "temp" scalings (the -40/-50 sensor
# offsets, HK-style) test ONLY the temperature band. Keeping them separate stops a
# -40 offset from manufacturing spurious voltage-band hits (and a ratio scaling
# from hitting the temp band), and bounds the inherently-broad temperature band's
# false positives. NB temperature bands are far less selective than the narrow
# mains/HV/12V bands (a single byte spans most of the range) -- treat a temp hit as
# a triage hint, not proof.
_PHYSICAL_SCALINGS: list[tuple[float, float, str, str]] = [
    (1.0, 0.0, "×1", "ratio"),
    (0.1, 0.0, "/10", "ratio"),
    (0.01, 0.0, "/100", "ratio"),
    (2.0, 0.0, "×2", "ratio"),
    (0.5, 0.0, "/2", "ratio"),
    (2.0**0.5, 0.0, "×√2", "ratio"),
    (0.5, -40.0, "/2−40", "temp"),
    (1.0, -40.0, "−40", "temp"),
    (0.5, -50.0, "/2−50", "temp"),
]


def _band_kind(label: str) -> str:
    """Band category for scaling/band kind-matching ("temp" vs "ratio")."""
    return "temp" if "temp" in label.lower() else "ratio"


# Interpretations swept for the physical scan — the common WiCAN-expressible
# integer widths (BE). Floats/LE are excluded: a physical sensor field is a
# scaled integer, and they only add noise.
_PHYSICAL_INTERPS = ("u8", "u16", "u24", "i16")


@dataclass
class PhysicalHit:
    expr: str  # WiCAN expression, or NO_EXPR for a float reinterpretation
    interp: str
    offset: int
    scaling: str
    band: str
    frac: float  # fraction of samples landing in the band
    median: float  # median scaled value
    n: int
    width: int = 1


def physical_scan(
    loaded: LoadedPid,
    *,
    min_frac: float = 0.6,
    min_n: int = 10,
    top: int = 12,
    bands: list[tuple[str, float, float]] | None = None,
) -> list[PhysicalHit]:
    """Find bytes whose (scaled) value lands in a named physical band.

    Sweeps every byte offset × interpretation × candidate scaling and reports the
    ones a majority (``min_frac``) of whose samples fall inside a physical band
    range — a plausibility hunt that needs **no reference signal**, which is the
    only way to find a signal (like AC mains voltage) that has no correlate on
    the bus. Collapses to the best (highest-fraction) hit per starting offset;
    ranks by fraction.

    ``bands`` is the resolved ``(label, low, high)`` list (see
    :func:`canlib.physical_bands.resolve_physical_bands`); ``None`` falls back to
    the built-in :data:`PHYSICAL_BANDS` defaults.
    """
    from .byteindex import mappable_data_indices
    from .notation import subfunction_bytes_for_pid

    if bands is None:
        bands = PHYSICAL_BANDS

    sfb = subfunction_bytes_for_pid(loaded.pid)
    frames: list[bytes] = []
    max_len = 0
    # Scan only bytes that carry real data — never ISO-TP framing (PCI) or the
    # UDS header (SID + PID/DID echo): a physical sensor value is never in those,
    # and letting a constant echo byte join a data byte into a wide 16-bit word
    # manufactures false band hits.
    #
    # The roles must be derived per capture via the length-aware
    # `mappable_data_indices`, because a single-frame response has ONE PCI byte
    # and a multi-frame response TWO. Filtering with wican_to_isotp/isotp_to_wican
    # (which hardcode the multi-frame layout) shifted the header by one on
    # single-frame PIDs and silently excluded their first real data byte.
    #
    # Take the INTERSECTION across captures: an index is only safe to interpret
    # if it is a data byte in *every* capture, otherwise a PID that answered with
    # both layouts would mix a data byte with a SID at the same offset.
    #
    # Unlike the time-joined passes this keeps *untimed* captures too — a band hit
    # is a plausibility check on the value, not a correlation, so it needs no clock.
    data_idx: set[int] | None = None
    for dec in loaded.decoded:
        try:
            mappable = set(mappable_data_indices(dec.payload, sfb))
        except Exception:
            continue
        frames.append(dec.frame)
        max_len = max(max_len, len(dec.frame))
        data_idx = mappable if data_idx is None else (data_idx & mappable)
    if not frames or not data_idx:
        return []
    skip = {i for i in range(max_len) if i not in data_idx}

    hits: list[PhysicalHit] = []
    for spec in INSPECT_TYPES:
        name, width, _kind, _signed = spec
        if name not in _PHYSICAL_INTERPS:
            continue
        for off in range(max_len):
            if off + width > max_len:
                continue
            if any((off + k) in skip for k in range(width)):
                continue
            vals = [
                v
                for fr in frames
                if (v := interpret_bytes(fr, off, spec, little=False)) is not None
            ]
            if len(vals) < min_n or len({round(v, 6) for v in vals}) < 3:
                continue
            # Best hit per band *kind* at this offset: a byte can be a plausible
            # voltage AND a plausible temperature (a real ambiguity), so keep both
            # rather than letting one mask the other — the per-offset collapse
            # below is per (offset, kind).
            best_by_kind: dict[str, tuple[float, PhysicalHit]] = {}
            for factor, offset, slabel, kind in _PHYSICAL_SCALINGS:
                scaled = [v * factor + offset for v in vals]
                for blabel, lo, hi in bands:
                    if _band_kind(blabel) != kind:
                        continue
                    inband = sum(1 for v in scaled if lo <= v <= hi)
                    frac = inband / len(scaled)
                    if frac < min_frac:
                        continue
                    prev = best_by_kind.get(kind)
                    if prev is None or frac > prev[0]:
                        expr = wican_expr(off, spec, little=False) or NO_EXPR
                        best_by_kind[kind] = (
                            frac,
                            PhysicalHit(
                                expr=expr,
                                interp=name,
                                offset=off,
                                scaling=slabel,
                                band=blabel,
                                frac=frac,
                                median=_median_scaled(scaled),
                                n=len(scaled),
                                width=width,
                            ),
                        )
            for _frac, hit in best_by_kind.values():
                hits.append(hit)

    # Collapse to the best hit per (starting offset, band-kind) — so a voltage and
    # a temperature reading of the same byte both survive — then rank by fraction,
    # with ratio-band hits ahead of temp-band hits at equal fraction (temp is a
    # broad, lower-confidence triage band).
    hits.sort(key=lambda h: (-h.frac, _band_kind(h.band) == "temp", h.width))
    seen: set[tuple[int, str]] = set()
    unique: list[PhysicalHit] = []
    for h in hits:
        key = (h.offset, _band_kind(h.band))
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
        if len(unique) >= top:
            break
    return unique


def _median_scaled(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
