"""Payload decoding — evaluate PID parameter expressions against a response.

Shared by the live monitor (``modes.multi._exec_query``), the historical capture
viewer (``query-captures.py``), and anything else that needs decoded parameter
rows. Decoded values are never persisted; they are regenerated on demand from the
payload + PID definitions.
"""

from functools import lru_cache

from .expression import evaluate_expression
from .wican_bytes import uds_hex_to_wican_bytes

# A decoded parameter row, as consumed by ``formatting.render_param_table`` and
# ``formatting._build_byte_colors`` / ``_render_hex_line``:
#   (name, value, unit, expression, error, verified, display)
# ``value`` is None when the expression errored (``error`` holds the message).
ParamRow = tuple[str, float | None, str, str, str | None, bool, str]

# One parameter's identity for the decode cache key — everything that affects the
# decoded row (name/expr/unit/verified/display). Hashable so it can key an LRU.
_ParamSig = tuple[str, str, str, bool, str]


@lru_cache(maxsize=8192)
def _decode_cached(payload_hex: str, params_sig: tuple[_ParamSig, ...]) -> tuple[ParamRow, ...]:
    """Decode from a hashable ``(payload_hex, param-signature)`` key.

    Memoized: the monitor re-decodes the *same* payload every cycle (most Ioniq
    PIDs are static between polls) and capture analysis replays many identical
    payloads — caching skips the per-parameter ``evaluate_expression`` work on a
    repeat. Pure function of its args, so the cache is always correct.
    """
    try:
        wican_bytes = uds_hex_to_wican_bytes(payload_hex)
    except Exception:
        return ()

    rows: list[ParamRow] = []
    for name, expr, unit, verified, display in params_sig:
        if not expr:
            continue
        try:
            value = evaluate_expression(expr, wican_bytes)
            value = round(value * 100) / 100
            rows.append((name, value, unit, expr, None, verified, display))
        except Exception as ex:  # surface decode errors in the table
            rows.append((name, None, unit, expr, str(ex), verified, display))
    return tuple(rows)


def decode_param_rows(payload_hex: str, parameters: dict) -> list[ParamRow]:
    """Decode a UDS response payload into parameter rows.

    Args:
        payload_hex: Raw UDS response hex (ELM327 form, PCI stripped), e.g.
            ``"6101FFE0..."``. Converted to a WiCAN byte frame internally.
        parameters: The ``parameters`` mapping from a PID definition
            (``name -> {expression, unit, verified, display, ...}``).

    Returns:
        A list of ``(name, value, unit, expression, error, verified, display)``
        tuples — one per parameter that has an expression. Empty if there are no
        parameters or the payload can't be parsed.
    """
    if not parameters:
        return []
    params_sig = tuple(
        (
            name,
            pdef.get("expression", ""),
            pdef.get("unit", ""),
            bool(pdef.get("verified", False)),
            pdef.get("display", ""),
        )
        for name, pdef in parameters.items()
    )
    # Copy the cached tuple into a fresh list so callers can't mutate the cache.
    return list(_decode_cached(payload_hex, params_sig))
