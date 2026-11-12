"""On-demand decode of a stored payload for display."""

from canair.ecus.pids import build_ecu_index
from canair.expression import evaluate_expression
from canair.expression.decode_value import NUMERIC, decode_typed, render
from canair.uds.wican_layout import uds_hex_to_wican_bytes


def _decode_payload(
    ecu_name: str,
    pid: str,
    hex_payload: str,
    pids_data: dict,
    ecu_index: dict | None = None,
) -> dict | None:
    """Try to decode a payload using PID definitions. Returns decoded dict or None.

    Decoded values are never persisted to capture files; this helper is used to
    regenerate them on demand for display (see query-captures.py). Pass a
    prebuilt ``ecu_index`` to avoid rebuilding it on every call.
    """

    if ecu_index is None:
        ecu_index = build_ecu_index(pids_data)
    ecu_key = ecu_name.upper()
    if ecu_key not in ecu_index:
        return None

    ecu_def = ecu_index[ecu_key]
    pid_info = ecu_def.get("pids", {}).get(str(pid).upper())
    if not pid_info or not pid_info.get("parameters"):
        return None

    try:
        wican_bytes = uds_hex_to_wican_bytes(hex_payload)
        decoded = {}
        for pname, pdef in pid_info["parameters"].items():
            expr = pdef.get("expression", "")
            ptype = (pdef.get("type") or NUMERIC).lower()
            unit = pdef.get("unit", "")
            # Typed (enum/bitmask/ascii/date/bcd/struct) params: render the typed
            # interpretation (e.g. an enum label) rather than the bare float.
            if ptype != NUMERIC:
                try:
                    decoded[pname] = render(decode_typed(pdef, wican_bytes), unit)
                except Exception:
                    pass
                continue
            if not expr:
                continue
            try:
                value = evaluate_expression(expr, wican_bytes)
                value = round(value * 100) / 100
                display = pdef.get("display", "")
                if display:
                    try:
                        v = value  # noqa: F841 — used by eval(display)
                        formatted = eval(display)
                        decoded[pname] = f"{value} {unit} ({formatted})".strip()
                    except Exception:
                        decoded[pname] = f"{value} {unit}".strip()
                else:
                    decoded[pname] = f"{value} {unit}".strip()
            except Exception:
                pass
        return decoded if decoded else None
    except Exception:
        return None
