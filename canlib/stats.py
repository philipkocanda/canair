#!/usr/bin/env python3
"""Correlation statistics — the single home for the hand-rolled (numpy-free)
coefficients used across the analysis suite (``decode``, ``correlate``,
``hunt``, the plot overlay).

Kept dependency-free and leaf (imports nothing from ``canlib``) so every caller
can import it without a cycle. Consolidates what were three separate ``pearson``
copies.
"""

from __future__ import annotations

CORRELATION_METHODS = ("pearson", "spearman")


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson product-moment correlation, or None if undefined (<2 points or a
    zero-variance series)."""
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
