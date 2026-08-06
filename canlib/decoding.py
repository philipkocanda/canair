"""Payload decoding — evaluate PID signal expressions against a response.

Shared by the live monitor (``modes.multi._exec_query``), the historical capture
viewer (``query-captures.py``), and anything else that needs decoded signal
rows. Decoded values are never persisted; they are regenerated on demand from the
payload + PID definitions.

Rows come out ordered by **where the signal sits in the payload** (see
:func:`signal_order_key`), not by definition order, so every view that decodes a
payload lists its signals in the same order the hex bytes are printed in.
"""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from .autopid_layout import uds_hex_to_wican_bytes
from .byteindex import extract_bit_indices, extract_byte_indices
from .expression import evaluate_expression

# A decoded signal row, as consumed by ``formatting.render_param_table`` and
# ``formatting._build_byte_colors`` / ``_render_hex_line``:
#   (name, value, unit, expression, error, verified, display)
# ``value`` is None when the expression errored (``error`` holds the message).
# For a ``type: ascii/date/struct`` param the value is the rendered text ``str``
# (there is no numeric scalar); numeric/enum/bitmask/bcd carry the ``float``.
ParamRow = tuple[str, float | str | None, str, str, str | None, bool, str]

# One signal's identity for the decode cache key — everything that affects the
# decoded row (name/expr/unit/verified/display). Hashable so it can key an LRU.
_ParamSig = tuple[str, str, str, bool, str]


def decodes_to_number(param_def: Mapping[str, Any] | None) -> bool:
    """Whether a signal definition can produce a *numeric* decoded value.

    The typed layer renders ``ascii``/``date``/``struct`` as text, and a signal
    with no ``expression`` is skipped entirely — so neither ever reaches a
    consumer that only reads numbers (state-predicate evaluation, correlation).
    Lives here beside :data:`ParamRow` because this is the invariant the decoders
    below implement; callers that check it themselves drift from it silently.
    """
    from .decode_value import ASCII, DATE, NUMERIC, STRUCT

    pdef = param_def or {}
    if str(pdef.get("type") or NUMERIC).lower() in (ASCII, DATE, STRUCT):
        return False
    return bool(pdef.get("expression"))


def signal_order_key(expression: str) -> tuple[int, int, int]:
    """Sort key placing a signal at the byte it reads first.

    Definition order in ``ecus/`` reflects how a PID was reverse-engineered, not
    the payload's layout, so a table in definition order forces the reader to
    hunt back and forth between a value and the hex byte it came from. Ordering by
    first byte (then by bit within it, whole-byte reads before the bits inside
    them) makes the table read top-to-bottom in the same direction as the hex.

    WiCAN indices are monotonic in the ISO-TP payload position, so no payload
    length is needed. A signal that references no byte (a constant, or a typed
    read with no expression) sorts last.
    """
    byte_indices = extract_byte_indices(expression)
    if not byte_indices:
        return (1, 0, 0)
    first = min(byte_indices)
    bits = [bit for offset, bit in extract_bit_indices(expression) if offset == first]
    return (0, first, min(bits) if bits else -1)


def order_signals(parameters: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """``parameters`` as ``(name, definition)`` pairs in payload-position order.

    A stable sort, so signals reading the same byte keep their definition order.
    """
    return sorted(
        parameters.items(), key=lambda kv: signal_order_key((kv[1] or {}).get("expression", ""))
    )


def ordered_signal_names(parameters: Mapping[str, Any]) -> list[str]:
    """Signal names in payload-position order (see :func:`order_signals`)."""
    return [name for name, _ in order_signals(parameters)]


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


def _decode_typed_rows(payload_hex: str, ordered: list[tuple[str, Any]]) -> list[ParamRow]:
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
    for name, pdef in ordered:
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
    """Decode a UDS response payload into signal rows.

    Args:
        payload_hex: Raw UDS response hex (ELM327 form, PCI stripped), e.g.
            ``"6101FFE0..."``. Converted to a WiCAN byte frame internally.
        parameters: The ``parameters`` mapping from a PID definition
            (``name -> {expression, unit, verified, display, ...}``).

    Returns:
        A list of ``(name, value, unit, expression, error, verified, display)``
        tuples — one per signal that has an expression, ordered by the payload
        byte each reads first (see :func:`signal_order_key`). Empty if there are
        no signals or the payload can't be parsed.
    """
    if not parameters:
        return []
    ordered = order_signals(parameters)
    # Typed (enum/bitmask/ascii/date/bcd/struct) signals take a slower, uncached
    # path so their label/text interpretation is rendered (not just the float).
    if any((p.get("type") or "numeric").lower() != "numeric" for p in parameters.values()):
        return _decode_typed_rows(payload_hex, ordered)
    params_sig = tuple(
        (
            name,
            pdef.get("expression", ""),
            pdef.get("unit", ""),
            bool(pdef.get("verified", False)),
            pdef.get("display", ""),
        )
        for name, pdef in ordered
    )
    # Copy the cached tuple into a fresh list so callers can't mutate the cache.
    return list(_decode_cached(payload_hex, params_sig))


def decode_payload(wican_bytes: bytes, parameters: dict) -> dict[str, dict]:
    """Evaluate every signal expression against a WiCAN frame, keyed by name.

    The richer sibling of :func:`decode_param_rows`: where that returns display
    rows for a table, this returns one entry per signal carrying the pieces the
    analysis verbs need — ``value`` plus ``expression``/``unit``/``verified``/
    ``min``/``max``, or ``error`` when the expression raised.

    For a signal declaring a ``type:`` (enum/bitmask/ascii/date/bcd/struct) the
    entry also carries ``display`` (the rendered typed string) and ``category`` (a
    nominal key for categorical stats). ``value`` stays the raw float so every
    numeric consumer (min/max/corr/stats) is unaffected.
    """
    from .decode_value import decode_typed, render

    results: dict[str, dict] = {}
    for name, param in parameters.items():
        expr = param.get("expression", "")
        ptype = (param.get("type") or "numeric").lower()
        if not expr and ptype in ("numeric", "enum", "bitmask", "bcd"):
            continue
        try:
            entry: dict = {
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "min": param.get("min"),
                "max": param.get("max"),
            }
            if ptype != "numeric":
                dv = decode_typed(param, wican_bytes)
                entry["value"] = dv.raw
                entry["type"] = ptype
                entry["display"] = render(dv, param.get("unit", ""))
                entry["category"] = dv.category()
            else:
                entry["value"] = evaluate_expression(expr, wican_bytes)
            results[name] = entry
        except Exception as e:
            results[name] = {
                "value": None,
                "expression": expr,
                "unit": param.get("unit", ""),
                "verified": param.get("verified", False),
                "error": str(e),
            }
    return results
