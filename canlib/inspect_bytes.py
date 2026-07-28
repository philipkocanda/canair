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
explorer (``commands/_decode_plot``) — so they live in this neutral library leaf
(depending only on the library layer) and every consumer imports *down* from
here. This is a single-purpose module, mirroring the ``uds_parse``/``notation``
leaf style.
"""

from __future__ import annotations

import struct

# name, byte width, kind ("int"/"float"), signed (ints only)
INSPECT_TYPES = [
    ("u8", 1, "int", False),
    ("i8", 1, "int", True),
    ("u16", 2, "int", False),
    ("i16", 2, "int", True),
    ("u24", 3, "int", False),
    ("i24", 3, "int", True),
    ("u32", 4, "int", False),
    ("i32", 4, "int", True),
    ("u64", 8, "int", False),
    ("i64", 8, "int", True),
    ("f16", 2, "float", True),
    ("f32", 4, "float", True),
    ("f64", 8, "float", True),
]

POST_TRANSFORMS = ("raw", "delta", "abs", "cumsum", "normalize", "smooth")


def interpret_bytes(frame: bytes, offset: int, spec: tuple, little: bool = False) -> float | None:
    """Read ``frame`` at ``offset`` as one INSPECT_TYPES spec, or None if OOB.

    ``spec`` is ``(name, width, kind, signed)``. Endianness applies to
    multi-byte types; single bytes ignore it.
    """
    _, width, kind, signed = spec
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


def wican_expr(offset: int, spec: tuple, little: bool = False) -> str | None:
    """Equivalent WiCAN expression for an interpretation, or None if not expressible.

    Big-endian ints map to the ``[Bnn:Bmm]`` / ``[Snn:Smm]`` forms; little-endian
    unsigned ints to a shift-composition; floats and little-endian *signed* ints
    have no direct expression in the WiCAN language.
    """
    _, width, kind, signed = spec
    if kind == "float":
        return None
    c = "S" if signed else "B"
    if width == 1:
        return f"{c}{offset}"
    if not little:
        return f"[{c}{offset}:{c}{offset + width - 1}]"
    if signed:
        return None
    terms = [f"B{offset}"] + [f"(B{offset + k} << {8 * k})" for k in range(1, width)]
    return " | ".join(terms)


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
