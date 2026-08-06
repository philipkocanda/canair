#!/usr/bin/env python3
"""Byte-interpretation primitives: read raw payload bytes at an offset as a
typed value, and post-process a value series.

Two composable layers, like an ImHex data inspector plus post-processing:
  1. INTERPRETATION — read raw payload bytes at an offset as a type
     (u8/i8/u16/.../u64/i64/f16/f32/f64, big/little endian).
  2. TRANSFORM — post-process the per-capture series
     (raw/delta/abs/cumsum/normalize/smooth) to expose structure.

These pure primitives are the shared bedrock of the cross-signal analysis engine
(``xanalysis``), the frame-series sweep (``frame_series``), and the plot signal
explorer (``commands/decode/plot``) — so they live in this neutral library leaf
(depending only on the library layer) and every consumer imports *down* from
here. This is a single-purpose module, mirroring the ``uds_parse``/``notation``
leaf style.
"""

from __future__ import annotations

import struct
from typing import NamedTuple

from .expression import signed_range_is_exact


class InspectType(NamedTuple):
    """One data-inspector interpretation: read ``width`` bytes as ``kind``.

    A named view over the former ``(name, width, kind, signed)`` 4-tuple so the
    fields read as ``spec.width``/``spec.kind`` rather than opaque ``spec[1]``.
    Still a ``tuple`` at runtime, so positional access and plain-tuple equality
    keep working for existing callers.
    """

    name: str
    width: int
    kind: str  # "int" or "float"
    signed: bool  # ints only; floats are always signed


INSPECT_TYPES: list[InspectType] = [
    InspectType("u8", 1, "int", False),
    InspectType("i8", 1, "int", True),
    InspectType("u16", 2, "int", False),
    InspectType("i16", 2, "int", True),
    InspectType("u24", 3, "int", False),
    InspectType("i24", 3, "int", True),
    InspectType("u32", 4, "int", False),
    InspectType("i32", 4, "int", True),
    InspectType("u64", 8, "int", False),
    InspectType("i64", 8, "int", True),
    InspectType("f16", 2, "float", True),
    InspectType("f32", 4, "float", True),
    InspectType("f64", 8, "float", True),
]

POST_TRANSFORMS = ("raw", "delta", "abs", "cumsum", "normalize", "smooth")

#: Placeholder shown (and stored on a hit) when an interpretation has **no** WiCAN
#: expression. Only *float* reinterpretations reach this: since LE/PCI-straddling
#: signed ints gained arithmetic-composition forms (``B9 + S10*256``,
#: ``S15*256 + B17``), every integer read is expressible — see :func:`wican_expr`.
#: A hit carrying this cannot be promoted or written as a parameter.
NO_EXPR = "<no-expr>"


def interpret_bytes(
    frame: bytes, offset: int, spec: InspectType, little: bool = False
) -> float | None:
    """Read ``frame`` at ``offset`` as one :class:`InspectType`, or None if OOB.

    Endianness applies to multi-byte types; single bytes ignore it.
    """
    width, kind, signed = spec.width, spec.kind, spec.signed
    if offset < 0 or offset + width > len(frame):
        return None
    bs = frame[offset : offset + width]
    if kind == "int":
        order = "little" if (little and width > 1) else "big"
        return float(int.from_bytes(bs, order, signed=signed))
    fmt = {2: "e", 4: "f", 8: "d"}[width]
    try:
        return float(struct.unpack(("<" if little else ">") + fmt, bs)[0])
    except (struct.error, ValueError):
        return None


def float_series_is_noise(values: list[float]) -> bool:
    """True if a float-interpretation series is implausible reinterpretation noise.

    Reading integer-looking byte runs as IEEE floats routinely yields values with
    absurd magnitudes — a denormal-ish ``5e-36`` or a ``1e30`` — that vary
    nonlinearly and produce weak spurious correlations in the hunt sweep. A real
    physical float (temperature, speed, voltage, energy) sits in a sane range, so
    a series whose values are *all* essentially zero (``|v| < 1e-6``) or *all*
    astronomically large (``|v| > 1e15``) is treated as noise and skipped. Only
    applied to float interpretations — integer reads are never filtered.
    """
    mags = [abs(v) for v in values if v == v]  # drop NaN (v != v)
    if not mags:
        return True
    hi = max(mags)
    return hi < 1e-6 or hi > 1e15


def wican_expr(offset: int, spec: InspectType, little: bool = False) -> str | None:
    """Equivalent WiCAN expression for an interpretation, or None if not expressible.

    Big-endian ints map to the ``[Bnn:Bmm]`` / ``[Snn:Smm]`` forms — except a
    *signed* read whose width the range form can't sign-extend exactly (3/5/6/7
    bytes; see :func:`canlib.expression.signed_range_is_exact`), which falls back
    to the arithmetic composition below. A little-endian *signed* int has no
    ``<<``/``|`` form (OR-ing a negative high byte is wrong), so it too is emitted
    as an arithmetic composition with the most-significant byte signed — e.g.
    ``B9 + S10*256`` — which *is* promotable. Only floats remain inexpressible.
    """
    width, kind, signed = spec.width, spec.kind, spec.signed
    if kind == "float":
        return None
    c = "S" if signed else "B"
    if width == 1:
        return f"{c}{offset}"
    exact_range = not signed or signed_range_is_exact(width)
    if not little and exact_range:
        return f"[{c}{offset}:{c}{offset + width - 1}]"
    if signed:
        # Signed, non-range (little-endian, or a width the range form would
        # sign-extend from the wrong bit): MSB signed, lower bytes unsigned,
        # combined arithmetically (``+``/``*``) since ``<<``/``|`` mishandle the
        # negative high byte.
        msb_idx = width - 1 if little else 0
        terms = []
        for k in range(width):
            char = "S" if k == msb_idx else "B"
            shift = 8 * k if little else 8 * (width - 1 - k)
            mult = 1 << shift
            terms.append(f"{char}{offset + k}" if mult == 1 else f"{char}{offset + k}*{mult}")
        return " + ".join(terms)
    terms = [f"B{offset}"] + [f"(B{offset + k} << {8 * k})" for k in range(1, width)]
    return " | ".join(terms)


def read_indices(frame: bytes, indices: list[int], signed: bool) -> float | None:
    """Read bytes at the given frame indices (big-endian order) as one integer.

    ``indices`` are absolute WiCAN frame positions, most-significant byte first.
    They need **not** be contiguous: a value split across an ISO-TP framing (PCI)
    byte is read by passing only its data-byte positions, skipping the framing
    byte in between — the case a plain ``[Bnn:Bmm]`` read gets wrong. Returns None
    if any index is out of range.
    """
    if not indices:
        return None
    val = 0
    for idx in indices:
        if idx < 0 or idx >= len(frame):
            return None
        val = (val << 8) | frame[idx]
    if signed and val >= 1 << (8 * len(indices) - 1):
        val -= 1 << (8 * len(indices))
    return float(val)


def wican_expr_indices(indices: list[int], signed: bool) -> str:
    """WiCAN expression for a big-endian value at (possibly non-contiguous) indices.

    Emitted as an arithmetic composition (e.g. ``S15*256 + B17``) rather than the
    ``[Bnn:Bmm]`` slice form, so it stays correct when the bytes straddle a PCI
    framing byte (a slice would include the framing byte) and when the
    most-significant byte is signed (``<<``/``|`` mishandle a negative MSB). Fully
    promotable.
    """
    n = len(indices)
    terms = []
    for k, idx in enumerate(indices):
        char = "S" if (signed and k == 0) else "B"
        mult = 1 << (8 * (n - 1 - k))
        terms.append(f"{char}{idx}" if mult == 1 else f"{char}{idx}*{mult}")
    return " + ".join(terms)


def apply_transform(values: list[float], mode: str) -> list[float]:
    """Apply a post-processing transform to a value series (see POST_TRANSFORMS)."""
    if not values or mode == "raw":
        return list(values)
    if mode == "delta":
        return [0.0] + [values[i] - values[i - 1] for i in range(1, len(values))]
    if mode == "abs":
        return [abs(v) for v in values]
    if mode == "cumsum":
        out, run = [], 0.0
        for v in values:
            run += v
            out.append(run)
        return out
    if mode == "normalize":
        return norm01(values)
    if mode == "smooth":
        w, out = 5, []
        for i in range(len(values)):
            a, b = max(0, i - w // 2), min(len(values), i + w // 2 + 1)
            out.append(sum(values[a:b]) / (b - a))
        return out
    return list(values)


def norm01(values: list[float]) -> list[float]:
    """Normalize a series into [0, 1]; a zero-span series maps to all-zeros."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return [(v - lo) / span for v in values]
