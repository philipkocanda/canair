#!/usr/bin/env python3
"""Gate parsing/application for ``canair correlate`` (extracted from correlate.py).

A ``--gate '[SIGNAL] OP VALUE'`` restricts a correlation to a regime (e.g.
``'> 0'`` = while-moving). Parsing the gate string and applying it to a reference
series are self-contained analysis helpers; keeping them here leaves correlate.py
as argparse + orchestration.
"""

from __future__ import annotations

import re

_GATE_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "!=": lambda a, b: a != b,
    "==": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _parse_gate(expr: str):
    """Parse a gate ``'[SIGNAL] OP VALUE'`` → ``(signal_or_None, op_fn, value, label)``.

    ``SIGNAL`` (empty ⇒ the reference itself) is a cross-signal ``ECU:PID:PARAM``.
    Raises ``ValueError`` on a malformed gate.
    """
    m = re.match(r"^\s*(.*?)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$", expr)
    if not m:
        raise ValueError(
            f"invalid --gate {expr!r} (expected '[SIGNAL] OP VALUE', e.g. '> 0' or "
            "'MCU:2102:MCU_MOTOR_RPM > 0')"
        )
    signal = m.group(1).strip() or None
    return signal, _GATE_OPS[m.group(2)], float(m.group(3)), expr.strip()


def _apply_gate(ref_series, gate_expr, tol, *, since, until, state, label):
    """Filter ``ref_series`` to the points where the gate predicate holds.

    An omitted signal gates on the reference's own value; a named
    ``ECU:PID:PARAM`` signal is loaded and aligned onto the reference by nearest
    timestamp. Returns the filtered (time-sorted) reference series.
    """
    from canlib.align import align_many
    from canlib.xanalysis import load_ref

    signal, op_fn, value, _lbl = _parse_gate(gate_expr)
    if signal is None:
        return [tp for tp in ref_series if op_fn(tp.value, value)]
    gate_series, _ = load_ref(signal, since=since, until=until, state=state, label=label)
    _, cols = align_many(ref_series, {"g": gate_series}, tol_s=tol)
    ref_sorted = sorted(ref_series, key=lambda tp: tp.dt)
    return [
        tp for tp, g in zip(ref_sorted, cols["g"], strict=True) if g is not None and op_fn(g, value)
    ]
