"""Cell-level formatting shared by every ``decode`` view.

The leaf of decode's presentation layer: one value, one range check, one column
mark, one scope banner. No table structure and no ``all_results`` — the view
modules compose these. Also the single home for the ANSI codes decode's renderers
use, so the three of them cannot drift apart.
"""

from __future__ import annotations

from canair.render import ansi
from canair.states.selectors import format_state_selector


def format_value(v: float | None, unit: str) -> str:
    """Format a decoded value with unit."""
    if v is None:
        return "ERROR"
    if v == int(v):
        return f"{int(v)}{unit}"
    return f"{v:.2f}{unit}"


def check_range(value: float | None, param_result: dict) -> str | None:
    """Return warning if value is outside min/max range."""
    if value is None:
        return None
    mn = param_result.get("min")
    mx = param_result.get("max")
    try:
        if mn is not None and value < float(mn):
            return f"< min({mn})"
        if mx is not None and value > float(mx):
            return f"> max({mx})"
    except (ValueError, TypeError):
        pass
    return None


def scope_banner(since, until, state, label, first, last) -> str:
    """Human-readable summary of active scope filters (empty when none active)."""
    parts = []
    if since or until:
        lo = since.isoformat() if since else "earliest"
        hi = until.isoformat() if until else "latest"
        parts.append(f"{lo} .. {hi}")
    if state:
        parts.append(f"state={format_state_selector(state)}")
    if label:
        parts.append(f"label~'{label}'")
    if first is not None:
        parts.append(f"first {first}")
    if last is not None:
        parts.append(f"last {last}")
    return "  ·  ".join(parts)


def _compact_cell(decoded: dict) -> str:
    """Format one decoded signal for a compact column (no unit; units go in header).

    A typed signal with no numeric raw (ascii, date, struct) shows its rendered text.
    """
    v = decoded.get("value")
    if v is None:
        display = decoded.get("display")
        return "ERR" if display is None or decoded.get("error") else display
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}"


def _mark_for(name: str, parameters: dict, candidate_names: set[str]) -> str:
    if name in candidate_names:
        return f"{ansi.DIM}»{ansi.RESET}"
    verified = parameters.get(name, {}).get("verified", False)
    return f"{ansi.GREEN}✓{ansi.RESET}" if verified else f"{ansi.YELLOW}?{ansi.RESET}"
