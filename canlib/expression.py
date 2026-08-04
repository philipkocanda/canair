"""WiCAN expression evaluator — the public entry point.

Faithful Python port of wican-fw/main/expression_parser.c evaluate_expression().

**Byte-space contract (important):** this evaluator is *byte-space-agnostic* — it
indexes the ``data`` argument literally (``data[idx]``) with no PCI-awareness.
The WiCAN indexing convention (``B0=PCI``, ``B9=first consecutive-frame data
byte``, …) is therefore imposed entirely by the *caller*, which must pass bytes
in the WiCAN AutoPID layout (PCI re-inserted) — see
:func:`canlib.byteindex.payload_to_wican_bytes` /
:func:`canlib.autopid_layout.uds_hex_to_wican_bytes`. Feeding a raw ISO-TP payload
(PCI stripped) would silently misalign every index past the first PCI byte.

**Parsing is separate from evaluation.** An expression is constant across a whole
series, so it is parsed once into a tree of closures and
:func:`evaluate_expression` is a thin, cached wrapper around that. Bulk consumers
that evaluate one expression against many payloads may call
:func:`compile_expression` directly to skip the per-call cache lookup.

The implementation is split by concern, both halves re-exported here:

- :mod:`canlib.expression_nodes` — the runtime semantics: closure factories for
  byte/bit/range loads and the arithmetic operators, plus the ``[Sn:Sm]``
  sign-extension container ladder and its :func:`signed_range_is_exact` guard.
- :mod:`canlib.expression_compile` — the grammar: the shunting-yard scan that
  assembles those nodes into a tree.
"""

from functools import lru_cache

from .expression_compile import compile_expression
from .expression_nodes import (
    SIGNED_RANGE_WIDTHS,
    CompiledExpression,
    signed_range_is_exact,
)

__all__ = [
    "SIGNED_RANGE_WIDTHS",
    "CompiledExpression",
    "compile_expression",
    "evaluate_expression",
    "signed_range_is_exact",
]

_compile_cached = lru_cache(maxsize=512)(compile_expression)


def evaluate_expression(expression: str, data: bytes, V: float = 0.0) -> float:
    """Evaluate a WiCAN expression against a byte array.

    ``data`` must be in the WiCAN AutoPID layout (PCI bytes re-inserted); indices
    are read literally (see the module docstring's byte-space contract).

    The expression is parsed by :func:`compile_expression` and the result cached,
    so re-evaluating the same expression against many payloads re-parses nothing.

    Supported syntax:
        Bn      — unsigned byte at index n
        Sn      — signed byte at index n (int8)
        Bn:m    — bit m of byte n (0=LSB)
        [Bn:Bm] — big-endian unsigned multi-byte (up to 8 bytes)
        [Sn:Sm] — big-endian signed multi-byte (auto-sized: 8/16/32/64-bit)
        V       — external value parameter (default 0)
        + - * / — arithmetic
        << >>   — bit shift
        & | ^   — bitwise AND, OR, XOR
        ( )     — grouping
        numeric — integer or float literals
    """
    return _compile_cached(expression)(data, V)
