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
    """Dispatch to :func:`pearson` or :func:`spearman` by ``method`` name."""
    if method == "spearman":
        return spearman(xs, ys)
    return pearson(xs, ys)


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
