"""Drawing primitives for ``decode --plot``: the braille canvas and its chrome.

The pure, stateless half of the plot: turn a list of floats into a Unicode braille
line chart with axes, work out which signal already maps a byte offset, and format
the info/legend lines. Knows nothing about :class:`~canlib.commands.decode.plot.PlotModel`
or the Textual app — they call *down* into this, never the reverse, which is what
keeps the chart testable without a terminal.

The byte-interpretation primitives themselves (INSPECT_TYPES, interpret_bytes,
wican_expr, ...) live one level further down in :mod:`canlib.inspect_bytes`.
"""

from __future__ import annotations

from canlib.byteindex import extract_byte_indices, payload_to_wican_frame
from canlib.inspect_bytes import norm01
from canlib.states import join_states as _join_states
from canlib.stats import fmt_num as _fmt_num
from canlib.stats import mean as _mean

# Terminal colors — mirror decode's palette (see decode.format for the shared set;
# these stay local because this module is a leaf the renderers must not depend on).
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RED = "\033[91m"
_RESET = "\033[0m"

_V_AXIS = "\u2502"  # box vertical
_CORNER = "\u2514"  # box corner
_HLINE = "\u2500"  # box horizontal


class _Braille:
    """A 2x4-dots-per-cell Unicode braille drawing surface (w x h *cells*)."""

    _DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.g = [[0] * w for _ in range(h)]

    def _set(self, px: int, py: int) -> None:
        if 0 <= px < 2 * self.w and 0 <= py < 4 * self.h:
            self.g[py // 4][px // 2] |= self._DOTS[py % 4][px % 2]

    def plot(self, points: list[tuple[int, int]]) -> None:
        """Plot points, connecting consecutive ones with straight segments."""
        prev = None
        for px, py in points:
            if prev is not None:
                x0, y0 = prev
                steps = max(abs(px - x0), abs(py - y0), 1)
                for s in range(steps + 1):
                    self._set(round(x0 + (px - x0) * s / steps), round(y0 + (py - y0) * s / steps))
            else:
                self._set(px, py)
            prev = (px, py)

    def char_grid(self) -> list[list[int]]:
        return [[(0x2800 + c) if c else 0 for c in row] for row in self.g]


def _to_pixels(values: list[float], w: int, h: int, lo: float, hi: float) -> list[tuple[int, int]]:
    span = hi - lo or 1.0
    px_max, py_max = 2 * w - 1, 4 * h - 1
    den = max(len(values) - 1, 1)
    return [
        (round(i / den * px_max), round((1 - (v - lo) / span) * py_max))
        for i, v in enumerate(values)
    ]


def render_plot(
    values: list[float],
    ref: list[float] | None = None,
    width: int = 74,
    height: int = 16,
    caption: str | None = None,
) -> list[str]:
    """Render a braille line chart (list of rows) with a y-axis min/max gutter.

    When ``ref`` is given, both series are normalized to [0,1] and overlaid
    (``values`` bright, ``ref`` dim) so their shapes can be compared. ``caption``
    overrides the default bottom label (used to show the visible x-range).
    """
    if not values:
        return ["  (no data to plot)"]
    overlay = bool(ref)
    if overlay:
        mv, rv, lo, hi = norm01(values), norm01(ref), 0.0, 1.0
    else:
        mv, lo, hi = list(values), min(values), max(values)

    main = _Braille(width, height)
    main.plot(_to_pixels(mv, width, height, lo, hi))
    mg = main.char_grid()
    rg = None
    if overlay:
        refg = _Braille(width, height)
        refg.plot(_to_pixels(rv, width, height, lo, hi))
        rg = refg.char_grid()

    gutter = 12
    lines = []
    for r in range(height):
        ylab = _fmt_num(hi) if r == 0 else _fmt_num(lo) if r == height - 1 else ""
        cells = []
        for c in range(width):
            if mg[r][c]:
                cells.append(f"{_GREEN}{chr(mg[r][c])}{_RESET}")
            elif rg and rg[r][c]:
                cells.append(f"{_DIM}{chr(rg[r][c])}{_RESET}")
            else:
                cells.append(" ")
        lines.append(f"{ylab:>{gutter}} {_V_AXIS}{''.join(cells)}")
    lines.append(f"{'':>{gutter}} {_CORNER}{_HLINE * width}")
    if caption is None:
        caption = "normalized 0-1" if overlay else f"{len(values)} captures"
    lines.append(f"{'':>{gutter}}  {_DIM}{caption}{_RESET}")
    return lines


def _window(seq: list, xlo: float, xhi: float) -> tuple[list, int, int]:
    """Slice ``seq`` to the fractional x-window ``[xlo, xhi]``.

    Returns ``(view, i0, i1)`` with ``i0``/``i1`` the resolved integer bounds.
    Fractions (rather than indices) keep the zoom stable as the underlying
    series length changes between offsets/types.
    """
    n = len(seq)
    if n == 0:
        return [], 0, 0
    i0 = max(0, min(n - 1, int(xlo * n)))
    i1 = max(i0 + 1, min(n, round(xhi * n)))
    return seq[i0:i1], i0, i1


def _mapping_for_offset(
    defined_params: dict, offset: int, width: int, current_expr: str | None
) -> tuple[list, list]:
    """Which defined parameters read the byte range ``[offset, offset+width)``.

    Returns ``(exact, overlap)`` lists of ``(name, expression, verified)``:
    ``exact`` matches the current interpretation's expression byte-for-byte;
    ``overlap`` merely reads one of the same bytes. Lets the plot flag bytes
    that are already decoded (and by what) while sweeping.
    """
    cur_bytes = set(range(offset, offset + width))
    norm_cur = current_expr.replace(" ", "") if current_expr else None
    exact, overlap = [], []
    for name, pdef in (defined_params or {}).items():
        expr = (pdef or {}).get("expression", "")
        if not expr:
            continue
        try:
            bs = extract_byte_indices(expr)
        except Exception:
            bs = set()
        if bs & cur_bytes:
            entry = (name, expr, bool((pdef or {}).get("verified", False)))
            if norm_cur and expr.replace(" ", "") == norm_cur:
                exact.append(entry)
            else:
                overlap.append(entry)
    return exact, overlap


def _pci_positions(payload_hex: str) -> set[int]:
    """WiCAN byte indices that are ISO-TP PCI bytes for a payload (role is None)."""
    ph = payload_hex.replace(" ", "")
    try:
        pb = [int(ph[i : i + 2], 16) for i in range(0, len(ph), 2)]
        frame = payload_to_wican_frame(pb)
    except Exception:
        return set()
    return {i for i, (_, role) in enumerate(frame) if role is None}


def _series_stats_str(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values)}  min={_fmt_num(min(values))} max={_fmt_num(max(values))} "
        f"mean={_fmt_num(_mean(values))}"
    )


def _cap_ts(cap: dict) -> str:
    """A capture's timestamp as ``YYYY-MM-DD HH:MM:SS`` (date and/or time, trimmed)."""
    return f"{cap.get('date', '')!s} {cap.get('time', '')!s}".strip()


def _view_time_range(caps: list[dict]) -> tuple[str, str]:
    """Earliest/latest timestamp across captures (ISO strings sort chronologically)."""
    tss = [t for t in (_cap_ts(c) for c in caps) if t]
    return (min(tss), max(tss)) if tss else ("", "")


def _cycle_overlay(overlay_ref: str | None, ov_cycle: list) -> str | None:
    """Advance the overlay reference to the next entry in ``ov_cycle`` (wraps).

    ``ov_cycle`` is ``[None, param, param, …]``; returns ``overlay_ref`` unchanged
    when there is nothing to cycle (only the ``None`` entry).
    """
    if len(ov_cycle) <= 1:
        return overlay_ref
    idx = ov_cycle.index(overlay_ref) if overlay_ref in ov_cycle else 0
    return ov_cycle[(idx + 1) % len(ov_cycle)]


def _info_lines(
    ecu_key: str,
    pid_key: str,
    caps_view: list[dict],
    i0: int,
    total: int,
    ts_range: str,
    max_rows: int,
) -> list[str]:
    """Modal body: list the captures backing the current view (date/state/label/notes/file)."""
    out = [
        f"{_BOLD}{ecu_key} {pid_key}{_RESET}  {_DIM}·  captures in view{_RESET}",
        f"  {_DIM}{len(caps_view)} capture(s)  ·  {ts_range or 'no timestamps'}  ·  "
        f"i/Esc to close{_RESET}",
        "",
    ]
    for n, cap in enumerate(caps_view[:max_rows]):
        state = _join_states(cap.get("vehicle_states"))
        label = cap.get("label", "")
        meta = "  ".join(x for x in [f"[{state}]" if state else "", label] if x)
        out.append(
            f"  {_CYAN}{i0 + n:>4}{_RESET}  {_BOLD}{_cap_ts(cap) or '?':<20}{_RESET}  "
            f"{_DIM}{cap.get('file', '')}{_RESET}" + (f"  {meta}" if meta else "")
        )
        notes = (cap.get("notes", "") or "").replace("\n", " ").strip()
        if notes:
            out.append(f"        {_DIM}{notes[:100]}{_RESET}")
    if len(caps_view) > max_rows:
        out.append(
            f"  {_DIM}... and {len(caps_view) - max_rows} more — "
            f"zoom in (+ or ,/.) to narrow the window{_RESET}"
        )
    return out
