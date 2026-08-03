"""Payload decoding — evaluate PID parameter expressions against a response.

Shared by the live monitor (``modes.multi._exec_query``), the historical capture
viewer (``query-captures.py``), and anything else that needs decoded parameter
rows. Decoded values are never persisted; they are regenerated on demand from the
payload + PID definitions.
"""

from functools import lru_cache

from .autopid_layout import uds_hex_to_wican_bytes
from .expression import evaluate_expression

# A decoded parameter row, as consumed by ``formatting.render_param_table`` and
# ``formatting._build_byte_colors`` / ``_render_hex_line``:
#   (name, value, unit, expression, error, verified, display)
# ``value`` is None when the expression errored (``error`` holds the message).
# For a ``type: ascii/date/struct`` param the value is the rendered text ``str``
# (there is no numeric scalar); numeric/enum/bitmask/bcd carry the ``float``.
ParamRow = tuple[str, float | str | None, str, str, str | None, bool, str]

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


def _decode_typed_rows(payload_hex: str, parameters: dict) -> list[ParamRow]:
    """Decode when at least one parameter declares a ``type:`` (enum/bitmask/…).

    Typed params can't ride the numeric LRU cache (their ``values``/``bits`` maps
    aren't hashable and their rendering is payload-dependent), so this path
    decodes them directly and folds the typed label into the row's ``display``
    slot — rendering as ``{raw} ({label})``, consistent with the ``display:``
    based flags already shown in these views. Numeric params behave as before.
    """
    from .decode_value import BCD, BITMASK, DATE, ENUM, NUMERIC, decode_typed, render

    try:
        wican_bytes = uds_hex_to_wican_bytes(payload_hex)
    except Exception:
        return []

    rows: list[ParamRow] = []
    for name, pdef in parameters.items():
        ptype = (pdef.get("type") or NUMERIC).lower()
        expr = pdef.get("expression", "")
        unit = pdef.get("unit", "")
        verified = bool(pdef.get("verified", False))
        display = pdef.get("display", "")

        if ptype == NUMERIC:
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                rows.append((name, value, unit, expr, None, verified, display))
            except Exception as ex:
                rows.append((name, None, unit, expr, str(ex), verified, display))
            continue

        # Typed parameter: decode the typed interpretation.
        try:
            dv = decode_typed(pdef, wican_bytes)
        except Exception as ex:
            rows.append((name, None, unit, expr, str(ex), verified, display))
            continue

        if dv.error and ptype != DATE:
            rows.append((name, None, unit, expr, dv.error, verified, display))
            continue

        if ptype in (ENUM, BITMASK):
            # Keep the raw float as the value; surface the label via ``display``
            # so it renders as ``{raw} ({label})``. ``display`` is eval'd by
            # ``format_value`` as an expression, so pass a string *literal*.
            label = dv.category()
            disp = repr(label) if label else display
            rows.append((name, dv.raw, unit, expr, None, verified, disp))
        elif ptype == BCD:
            rows.append((name, dv.raw, unit, expr, None, verified, display))
        else:
            # ascii/date/struct read a byte run — carry the rendered text as the
            # (non-numeric) value; ``format_value`` renders a string as-is.
            rows.append((name, render(dv), unit, "", None, verified, ""))
    return rows


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
    # Typed (enum/bitmask/ascii/date/bcd/struct) params take a slower, uncached
    # path so their label/text interpretation is rendered (not just the float).
    if any((p.get("type") or "numeric").lower() != "numeric" for p in parameters.values()):
        return _decode_typed_rows(payload_hex, parameters)
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
