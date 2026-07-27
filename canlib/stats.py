#!/usr/bin/env python3
"""Statistics — the single home for the hand-rolled (numpy-free) coefficients
and descriptive stats used across the analysis suite (``decode``, ``correlate``,
``hunt``, the plot overlay).

Kept dependency-free and leaf (imports nothing from ``canlib``) so every caller
can import it without a cycle. Consolidates what were three separate ``pearson``
copies and the duplicated ``mean``/``median``/``stdev``/``fmt_num`` helpers.
"""

from __future__ import annotations

import math

CORRELATION_METHODS = ("pearson", "spearman")


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson product-moment correlation, or None if undefined (<2 points, a
    zero-variance series, or non-finite/overflowing values).

    Robust to the pathological inputs the byte-sweep hunters feed it: reading raw
    CAN bytes as ``f64``/``f32`` can yield ``inf``/``nan`` or values near the
    float max whose squared deviations overflow. Rather than raise
    ``OverflowError`` mid-sweep (which aborts the whole hunt), such a series is
    simply reported as undefined (``None``).
    """
    n = len(xs)
    if n < 2:
        return None
    if not all(math.isfinite(v) for v in xs) or not all(math.isfinite(v) for v in ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    try:
        sx = math.fsum((x - mx) ** 2 for x in xs)
        sy = math.fsum((y - my) ** 2 for y in ys)
        if sx == 0 or sy == 0:
            return None
        cov = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
        r = cov / (sx**0.5 * sy**0.5)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(r):
        return None
    return r


def rank(values: list[float]) -> list[float]:
    """Fractional ranks (1-based); tied values share their average rank.

    The basis for Spearman correlation (Pearson of the ranks).
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # mean of 0-based positions i..j, shifted to 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation — Pearson of the rank-transformed series.

    Catches monotone-but-nonlinear relationships (quantized/saturating signals)
    that Pearson under-scores. None if undefined (a fully-tied series has no rank
    variance).
    """
    n = len(xs)
    if n < 2:
        return None
    return pearson(rank(xs), rank(ys))


def correlation(xs: list[float], ys: list[float], method: str = "pearson") -> float | None:
    """Dispatch to a correlation/association coefficient by ``method`` name.

    ``pearson``/``spearman`` measure ordered/linear correlation (the default,
    for continuous signals). ``cramers_v``/``mutual_info`` measure *categorical*
    association: the series are treated as nominal categories (each distinct
    value is a level), which is the right question for a mode/flag/enum byte
    where numeric spacing is meaningless. All return a comparable 0..1-ish
    magnitude, so callers can rank uniformly.
    """
    if method == "spearman":
        return spearman(xs, ys)
    if method in CATEGORICAL_METHODS:
        return categorical_association(xs, ys, method)
    return pearson(xs, ys)


def partial_correlation(
    xs: list[float],
    ys: list[float],
    zs: list[float],
    method: str = "pearson",
) -> float | None:
    """Partial correlation of ``xs`` and ``ys`` controlling for ``zs``.

    The correlation that *remains* between ``xs`` (reference) and ``ys``
    (candidate) once the linear influence of the nuisance signal ``zs`` (the
    control/confounder) is removed from both. Uses the closed form
    ``r_xy·z = (r_xy − r_xz·r_yz) / √((1−r_xz²)(1−r_yz²))`` — three pairwise
    coefficients, so it stays numpy-free and exact.

    All three series must be **aligned** (same length, same sample points).
    ``method`` is ``pearson`` (product-moment) or ``spearman`` (rank); the
    categorical methods are undefined here. Returns ``None`` when a pairwise
    coefficient is undefined, or when the control is (near-)collinear with the
    reference or candidate (denominator → 0, so the partial is unidentifiable).
    """
    if method in CATEGORICAL_METHODS:
        raise ValueError(f"partial correlation is undefined for method {method!r}")
    r_xy = correlation(xs, ys, method)
    r_xz = correlation(xs, zs, method)
    r_yz = correlation(ys, zs, method)
    if r_xy is None or r_xz is None or r_yz is None:
        return None
    denom = ((1.0 - r_xz**2) * (1.0 - r_yz**2)) ** 0.5
    if denom <= 1e-9:
        return None  # control collinear with reference or candidate — unidentifiable
    r = (r_xy - r_xz * r_yz) / denom
    return max(-1.0, min(1.0, r))


# ── Categorical association ──────────────────────────────────────────────────
# Pearson/Spearman assume ordered, interval-scaled data — invalid for a nominal
# signal (an enum mode, a flag set, a gear). These measure association between
# *categorical* series (or a low-cardinality byte binned as categorical), which
# is the right question for "which byte encodes the fan setting / climate mode?".
# Both are leaf/numpy-free like the rest of this module.

CATEGORICAL_METHODS = ("cramers_v", "mutual_info")


def _contingency(xs: list, ys: list) -> tuple[dict, dict, dict, int]:
    """Build a contingency table over two aligned categorical series.

    Returns ``(joint, row_tot, col_tot, n)`` where ``joint[(a, b)]`` is the
    co-occurrence count. Values are compared by equality (hashable categories:
    ints, strings, tuples), so numeric levels and string labels both work.
    """
    joint: dict[tuple, int] = {}
    row_tot: dict = {}
    col_tot: dict = {}
    n = 0
    for a, b in zip(xs, ys, strict=False):
        joint[(a, b)] = joint.get((a, b), 0) + 1
        row_tot[a] = row_tot.get(a, 0) + 1
        col_tot[b] = col_tot.get(b, 0) + 1
        n += 1
    return joint, row_tot, col_tot, n


def cramers_v(xs: list, ys: list) -> float | None:
    """Bias-corrected Cramér's V — association between two categorical series.

    Ranges 0 (no association) to 1 (perfect). Symmetric. Uses the bias
    correction of Bergsma (2013) so small tables don't inflate. Returns None
    when undefined (fewer than 2 points, or a degenerate single-category
    series).
    """
    if len(xs) < 2 or len(ys) < 2:
        return None
    joint, row_tot, col_tot, n = _contingency(xs, ys)
    r, k = len(row_tot), len(col_tot)
    if r < 2 or k < 2 or n == 0:
        return None
    # Chi-square statistic.
    chi2 = 0.0
    for a, rt in row_tot.items():
        for b, ct in col_tot.items():
            expected = rt * ct / n
            observed = joint.get((a, b), 0)
            chi2 += (observed - expected) ** 2 / expected
    phi2 = chi2 / n
    # Bergsma bias correction.
    phi2corr = max(0.0, phi2 - (r - 1) * (k - 1) / (n - 1))
    rcorr = r - (r - 1) ** 2 / (n - 1)
    kcorr = k - (k - 1) ** 2 / (n - 1)
    denom = min(rcorr - 1, kcorr - 1)
    if denom <= 0:
        return None
    return (phi2corr / denom) ** 0.5


def mutual_information(xs: list, ys: list, *, normalized: bool = True) -> float | None:
    """Mutual information between two categorical series (in nats).

    With ``normalized=True`` (default) returns the symmetric normalized MI
    (0..1), so it is comparable across pairs like a correlation coefficient.
    Returns None for fewer than 2 points or a degenerate series.
    """
    if len(xs) < 2 or len(ys) < 2:
        return None
    joint, row_tot, col_tot, n = _contingency(xs, ys)
    if n == 0 or len(row_tot) < 2 or len(col_tot) < 2:
        return None
    mi = 0.0
    for (a, b), c in joint.items():
        pxy = c / n
        px = row_tot[a] / n
        py = col_tot[b] / n
        if pxy > 0:
            mi += pxy * math.log(pxy / (px * py))
    if not normalized:
        return mi
    hx = -sum((rt / n) * math.log(rt / n) for rt in row_tot.values())
    hy = -sum((ct / n) * math.log(ct / n) for ct in col_tot.values())
    denom = (hx * hy) ** 0.5
    if denom <= 0:
        return None
    return max(0.0, min(1.0, mi / denom))


def categorical_association(xs: list, ys: list, method: str = "cramers_v") -> float | None:
    """Dispatch to :func:`cramers_v` or :func:`mutual_information` by name."""
    if method == "mutual_info":
        return mutual_information(xs, ys)
    return cramers_v(xs, ys)


# ── Descriptive statistics ───────────────────────────────────────────────────
# Also numpy-free and leaf, shared by decode's stats table and the plot overlay.


def mean(xs: list[float]) -> float:
    """Arithmetic mean (0.0 for an empty series)."""
    return sum(xs) / len(xs) if xs else 0.0


def median(xs: list[float]) -> float:
    """Median (assumes a non-empty series)."""
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def stdev(xs: list[float]) -> float:
    """Sample standard deviation (0.0 for fewer than two points)."""
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def compute_stats(values: list[float]) -> dict:
    """Descriptive statistics for one parameter's value series."""
    distinct = sorted(set(values))
    return {
        "n": len(values),
        "distinct": len(distinct),
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "median": median(values),
        "stdev": stdev(values),
        "values": distinct,
    }


def fmt_num(x: float) -> str:
    """Compact numeric formatting: integers stay integral, else 2 decimals.

    Non-finite values (which float byte-interpretations routinely produce) are
    rendered as text rather than crashing ``int()``.
    """
    if not math.isfinite(x):
        return "nan" if math.isnan(x) else ("inf" if x > 0 else "-inf")
    return str(int(x)) if x == int(x) else f"{x:.2f}"
