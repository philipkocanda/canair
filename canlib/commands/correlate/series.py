"""Assembling the signal series a correlation run ranks.

Which ECU/PID specs are in scope, what their keep-modes imply about the data, and
the signal/byte/bit series built from them — plus the ``fill`` block every
``--json`` report carries, so a machine consumer can tell measured rows from
forward-filled ones.
"""

from __future__ import annotations

from canlib.align import (
    load_signal_captures,
    payload_lengths,
)
from canlib.commands._join import (
    fill_summaries,
)
from canlib.fill import FillPolicy
from canlib.keepmode import scope_is_keep_changes, scope_is_keep_unique
from canlib.xanalysis import (
    build_bit_series,
    build_byte_series,
    build_param_series,
)

NAME = "correlate"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"
_RESET = "\033[0m"


def _fill_json(policy: FillPolicy, fills) -> dict:
    """The ``fill`` block every ``--json`` report carries.

    Machine consumers need the same "measured vs reconstructed" distinction the
    human report gets, or an agent reading a strong correlation cannot tell whether
    its rows were sampled or carried forward.
    """
    return {
        "mode": policy.mode,
        "max_hold_s": policy.max_hold_s,
        "signals": [f.as_json() for f in fills],
    }


def _scope_keep_flags(specs, since, until, state, label) -> tuple[bool, bool]:
    """(has keep:unique, has keep:changes) across the captures in scope."""
    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    has_unique = any(scope_is_keep_unique(lp.captures) for lp in loaded.values())
    has_changes = any(scope_is_keep_changes(lp.captures) for lp in loaded.values())
    return has_unique, has_changes


def _gather_series(
    specs, since, until, state, label, want_bytes, want_bits=False, fill=None, tol_s=0.0
):
    """Build all signal series (params + optionally varying bytes/bits) for specs.

    Returns ``(series, payload_lens, fills)``. The ranked output mixes labels from
    every PID in scope, so the render layer needs each PID's payload length to
    resolve a WiCAN offset against the right frame layout (see
    notation.relabel_signal); ``fills`` names which PIDs contribute forward-filled
    run-length values, for the report header.
    """
    from canlib.pids import build_ecu_index, load_pids

    loaded = load_signal_captures(specs, since=since, until=until, state=state, label=label)
    ecu_index = build_ecu_index(load_pids())
    series: dict = {}
    for (ecu, pid), lp in loaded.items():
        if not lp.captures:
            continue
        params = ecu_index.get(ecu, {}).get("pids", {}).get(pid, {}).get("parameters", {})
        series.update(build_param_series(lp, params, fill=fill))
        if want_bytes:
            series.update(build_byte_series(lp, fill=fill))
        if want_bits:
            series.update(build_bit_series(lp, fill=fill))
    fills = fill_summaries(loaded.values(), fill, tol_s) if fill is not None else []
    return series, payload_lengths(loaded), fills
