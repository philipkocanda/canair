#!/usr/bin/env python3
"""Interactive signal explorer for ``canair decode --plot`` (extracted from
decode.py to keep that module focused).

Two composable layers, like an ImHex data inspector plus post-processing:
  1. INTERPRETATION — read raw payload bytes at an offset as a type
     (u8/i8/u16/.../u64/i64/f16/f32/f64, big/little endian).
  2. TRANSFORM — post-process the per-capture series
     (raw/delta/abs/cumsum/normalize/smooth) to expose structure.
The series is drawn as a Unicode braille line chart; an optional reference
parameter can be overlaid with a live Pearson r.

The pure primitives here (INSPECT_TYPES, interpret_bytes, wican_expr,
apply_transform, POST_TRANSFORMS) are reused by the cross-signal analysis engine
(xanalysis) and decode's correlation transforms, so they live in this leaf
module and are re-exported by decode.
"""

from __future__ import annotations

import math
import shutil
import sys

from canlib.byteindex import (
    extract_byte_indices,
    payload_to_wican_bytes,
    payload_to_wican_frame,
)
from canlib.states import join_states as _join_states
from canlib.stats import fmt_num as _fmt_num
from canlib.stats import mean as _mean
from canlib.stats import pearson as _pearson

# Terminal colors — mirror decode's palette. Kept local (not imported from
# decode) so this leaf module has no import-time dependency on decode, which
# imports the plot primitives back at its own module top.
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RED = "\033[91m"
_RESET = "\033[0m"


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

_V_AXIS = "\u2502"  # box vertical
_CORNER = "\u2514"  # box corner
_HLINE = "\u2500"  # box horizontal


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
    import struct

    fmt = {2: "e", 4: "f", 8: "d"}[width]
    try:
        return float(struct.unpack(("<" if little else ">") + fmt, bs)[0])
    except (struct.error, ValueError):
        return None


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
        return _norm01(values)
    if mode == "smooth":
        w, out = 5, []
        for i in range(len(values)):
            a, b = max(0, i - w // 2), min(len(values), i + w // 2 + 1)
            out.append(sum(values[a:b]) / (b - a))
        return out
    return list(values)


def _norm01(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    return [(v - lo) / span for v in values]


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
        mv, rv, lo, hi = _norm01(values), _norm01(ref), 0.0, 1.0
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


def cmd_plot(
    all_results: list[dict],
    param_names: list[str],
    parameters: dict,
    candidate_names: set[str],
    corr_ref: str | None,
    ecu_key: str,
    pid_key: str,
    defined_params: dict | None = None,
    reload_pid=None,
    pid_options: list[tuple[str, str]] | None = None,
) -> None:
    """Interactive signal explorer: sweep byte interpretations / params and plot.

    Byte mode is the ImHex-style inspector (offset x type x endianness over the
    raw payload); param mode plots a defined/--try parameter's decoded series.
    Both feed a post-transform and an optional reference overlay; byte mode also
    shows the equivalent WiCAN expression and flags bytes already mapped by a
    defined parameter. The x-axis can be zoomed/panned. Falls back to a single
    static chart when stdin/stdout is not a TTY.

    ``reload_pid``/``pid_options`` (optional) enable in-TUI PID switching: a
    callback ``(ecu, pid) -> PlotModel | None`` and the list of switchable
    ``(ecu, pid)`` pairs.
    """
    model = PlotModel(
        all_results,
        param_names,
        parameters,
        candidate_names,
        corr_ref,
        ecu_key,
        pid_key,
        defined_params=defined_params,
    )
    if model.empty:
        print("  Nothing to plot (no decodable payloads or numeric params).")
        return

    # Non-interactive: print one static frame and return.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\n".join(model.render_lines()))
        print("  (interactive --plot needs a TTY for navigation)")
        return

    from canlib.commands._decode_plot_tui import run_plot_app

    run_plot_app(model, reload_pid=reload_pid, pid_options=pid_options)


class PlotModel:
    """State + rendering for the ``decode --plot`` signal explorer.

    Holds the capture-derived data and the mutable view state (byte/param mode,
    offset, interpretation type, endianness, transform, overlay reference, x-axis
    window, info modal). :meth:`render_lines` produces the ANSI-colored frame; the
    Textual app (``_decode_plot_tui``) binds keys to the mutator methods. Kept
    UI-framework-free so the non-TTY path can render one frame without Textual.
    """

    def __init__(
        self,
        all_results: list[dict],
        param_names: list[str],
        parameters: dict,
        candidate_names: set[str],
        corr_ref: str | None,
        ecu_key: str,
        pid_key: str,
        defined_params: dict | None = None,
    ) -> None:
        self.all_results = all_results
        self.param_names = param_names
        self.parameters = parameters
        self.candidate_names = candidate_names
        self.ecu_key = ecu_key
        self.pid_key = pid_key
        self.defined_params = defined_params or {}

        # WiCAN AutoPID frames per capture (ISO-TP + re-inserted PCI; offset space
        # matches Bnn / expressions) — not raw CAN frames (no arbitration ID/DLC).
        self.frames: list[bytes | None] = []
        for r in all_results:
            payload = r["capture"].get("payload")
            try:
                self.frames.append(payload_to_wican_bytes(payload) if payload else None)
            except Exception:
                self.frames.append(None)
        self.valid = [f for f in self.frames if f]
        longest_payload = max(
            (r["capture"]["payload"] for r in all_results if r["capture"].get("payload")),
            key=len,
            default="",
        )
        self.pci = _pci_positions(longest_payload)
        self.max_off = (max((len(f) for f in self.valid), default=1)) - 1

        self.plottable_params = [
            n
            for n in param_names
            if len([1 for r in all_results if r["decoded"].get(n, {}).get("value") is not None])
            >= 2
        ]

        # Overlay reference is selectable at runtime (cycled with `o`), seeded by
        # --corr when given. Any numeric param can be overlaid — no --corr required.
        self.ov_cycle = [
            None,
            *dict.fromkeys(([corr_ref] if corr_ref else []) + self.plottable_params),
        ]

        # ---- view state ----
        self.mode = "bytes" if self.valid else "param"
        self.offset = min(self.max_off, 3)  # skip PCI/SID/echo by default
        self.ti = 0  # INSPECT_TYPES index
        self.little = False
        self.tmode = "raw"  # post-transform
        self.pi = 0  # param index (param mode)
        self.overlay_ref = corr_ref  # overlay reference param (None = off)
        self.xlo, self.xhi = 0.0, 1.0  # fractional x-axis window (zoom/pan)
        self.show_info = False  # captures-in-view modal

    @property
    def empty(self) -> bool:
        return not self.valid and not self.plottable_params

    # -- rendering ---------------------------------------------------------
    def render_lines(self) -> list[str]:
        spec = INSPECT_TYPES[self.ti]
        warn = ""
        map_line = None
        if self.mode == "bytes":
            per_cap = [
                interpret_bytes(f, self.offset, spec, self.little) if f else None
                for f in self.frames
            ]
            expr = wican_expr(self.offset, spec, self.little)
            width = spec[1]
            if any((self.offset + k) in self.pci for k in range(width)):
                warn = "crosses PCI byte — likely garbage"
            endian = "" if width == 1 else ("  LE" if self.little else "  BE")
            src = f"B{self.offset} as {spec[0]}{endian}"
            expr_line = f"expr: {expr}" if expr else "expr: (no direct WiCAN expression)"
            # Feature: flag bytes already mapped by a defined parameter.
            exact, overlap = _mapping_for_offset(self.defined_params, self.offset, width, expr)
            if exact:
                n_, e_, v_ = exact[0]
                mk = f"{_GREEN}✓{_RESET}" if v_ else f"{_YELLOW}?{_RESET}"
                map_line = f"  {_GREEN}= mapped: {n_}{_RESET} {mk} {_DIM}({e_}){_RESET}"
            elif overlap:
                shown = "  ".join(f"{n_} {_DIM}({e_}){_RESET}" for n_, e_, _ in overlap[:3])
                more = f" +{len(overlap) - 3}" if len(overlap) > 3 else ""
                map_line = f"  {_YELLOW}~ reads B{self.offset}:{_RESET} {shown}{more}"
            else:
                map_line = f"  {_DIM}unmapped{_RESET}"
        else:
            if not self.plottable_params:
                return [
                    f"{_BOLD}{self.ecu_key} {self.pid_key}{_RESET}",
                    "  No numeric parameters to plot — press m for byte mode.",
                ]
            name = self.plottable_params[self.pi % len(self.plottable_params)]
            per_cap = [r["decoded"].get(name, {}).get("value") for r in self.all_results]
            expr_line = f"expr: {self.parameters.get(name, {}).get('expression', '')}"
            src = name

        # Overlay reference resolved from runtime state (cycled with `o`).
        overlay = self.overlay_ref is not None
        ref_per_cap = (
            [r["decoded"].get(self.overlay_ref, {}).get("value") for r in self.all_results]
            if overlay
            else None
        )

        # Drop missing (None) and non-finite (NaN/Inf) values — float byte
        # interpretations routinely yield NaN/Inf, which can't be plotted or
        # averaged. Keep each retained value's capture aligned for the modal.
        caps_all = [r["capture"] for r in self.all_results]
        if overlay and ref_per_cap is not None:
            triples = [
                (cap, rf, cv)
                for cap, rf, cv in zip(caps_all, ref_per_cap, per_cap, strict=True)
                if rf is not None and cv is not None and math.isfinite(rf) and math.isfinite(cv)
            ]
            caps_full = [t[0] for t in triples]
            ref_full = [t[1] for t in triples]
            cur_full = apply_transform([t[2] for t in triples], self.tmode)
        else:
            kept = [
                (cap, v)
                for cap, v in zip(caps_all, per_cap, strict=True)
                if v is not None and math.isfinite(v)
            ]
            caps_full = [k[0] for k in kept]
            ref_full = None
            cur_full = apply_transform([k[1] for k in kept], self.tmode)

        # Apply the x-axis window (zoom/pan), keeping ref + captures aligned.
        series, i0, i1 = _window(cur_full, self.xlo, self.xhi)
        caps_view = caps_full[i0:i1]
        refseries = ref_full[i0:i1] if ref_full is not None else None

        # Date/time span of the *visible* window (accounts for zoom).
        lo_ts, hi_ts = _view_time_range(caps_view)
        ts_range = f"{lo_ts} → {hi_ts}" if lo_ts else "no timestamps"

        total = len(cur_full)

        # Captures-in-view modal takes over the frame when toggled.
        if self.show_info:
            max_rows = max(4, shutil.get_terminal_size((80, 24)).lines - 8)
            return _info_lines(self.ecu_key, self.pid_key, caps_view, i0, total, ts_range, max_rows)

        if overlay and refseries is not None:
            r = _pearson(refseries, series)
            rstr = (
                f"  {_CYAN}r={r:+.3f} vs {self.overlay_ref}{_RESET}"
                if r is not None
                else f"  {_DIM}r=n/a vs {self.overlay_ref}{_RESET}"
            )
        else:
            rstr = ""

        zoomed = (i0, i1) != (0, total)
        caption = (
            (f"captures {i0}-{i1 - 1} of {total}" if total else "no data")
            + f"  ·  {ts_range}"
            + ("  (zoomed)" if zoomed else "")
            + ("  · normalized 0-1" if overlay else "")
        )

        out = [
            f"{_BOLD}{self.ecu_key} {self.pid_key}{_RESET}  {_DIM}·  {self.mode} mode{_RESET}",
            f"  {_CYAN}{src}{_RESET}   {_DIM}{expr_line}{_RESET}",
        ]
        if map_line:
            out.append(map_line)
        out.append(
            f"  transform={_YELLOW}{self.tmode}{_RESET}  {_series_stats_str(series)}{rstr}"
            + (f"   {_RED}\u26a0 {warn}{_RESET}" if warn else "")
        )
        out.append("")
        out.extend(render_plot(series, ref=refseries if overlay else None, caption=caption))
        return out

    def hint(self) -> str:
        """The one-line key hint for the current mode."""
        common = "  +/- zoom · ,/. pan · 0 reset-x · f transform · o overlay · i captures · ? help · q quit"
        if self.mode == "bytes":
            return "←/→ offset · t/T type · e endian · m param" + common
        return "←/→ param · m bytes" + common

    # -- current-interpretation accessors (for annotation / promotion) -----
    def current_expr(self) -> str | None:
        """The WiCAN expression for the current byte-mode interpretation, if any."""
        if self.mode != "bytes":
            return None
        return wican_expr(self.offset, INSPECT_TYPES[self.ti], self.little)

    def current_param_name(self) -> str | None:
        """The parameter currently plotted in param mode, if any."""
        if self.mode != "param" or not self.plottable_params:
            return None
        return self.plottable_params[self.pi % len(self.plottable_params)]

    # -- mutators (return an optional status message) ----------------------
    def move_left(self) -> None:
        if self.mode == "bytes":
            self.offset = max(0, self.offset - 1)
        elif self.plottable_params:
            self.pi = (self.pi - 1) % len(self.plottable_params)

    def move_right(self) -> None:
        if self.mode == "bytes":
            self.offset = min(self.max_off, self.offset + 1)
        elif self.plottable_params:
            self.pi = (self.pi + 1) % len(self.plottable_params)

    def type_next(self) -> None:
        self.ti = (self.ti + 1) % len(INSPECT_TYPES)

    def type_prev(self) -> None:
        self.ti = (self.ti - 1) % len(INSPECT_TYPES)

    def toggle_endian(self) -> None:
        self.little = not self.little

    def cycle_transform(self) -> None:
        self.tmode = POST_TRANSFORMS[(POST_TRANSFORMS.index(self.tmode) + 1) % len(POST_TRANSFORMS)]

    def toggle_mode(self) -> None:
        self.mode = "param" if self.mode == "bytes" else "bytes"

    def cycle_overlay(self) -> str:
        if len(self.ov_cycle) > 1:
            self.overlay_ref = _cycle_overlay(self.overlay_ref, self.ov_cycle)
            return f"overlay: {self.overlay_ref}" if self.overlay_ref else "overlay: off"
        return "no numeric param to overlay (define one or use --try)"

    def zoom_in(self) -> None:
        c, half = (self.xlo + self.xhi) / 2, (self.xhi - self.xlo) / 4
        if (self.xhi - self.xlo) > 0.02:
            self.xlo, self.xhi = max(0.0, c - half), min(1.0, c + half)

    def zoom_out(self) -> None:
        c, half = (self.xlo + self.xhi) / 2, (self.xhi - self.xlo)
        self.xlo, self.xhi = max(0.0, c - half), min(1.0, c + half)

    def pan_left(self) -> None:
        d = min(self.xlo, 0.1 * (self.xhi - self.xlo))
        self.xlo, self.xhi = self.xlo - d, self.xhi - d

    def pan_right(self) -> None:
        d = min(1.0 - self.xhi, 0.1 * (self.xhi - self.xlo))
        self.xlo, self.xhi = self.xlo + d, self.xhi + d

    def reset_x(self) -> None:
        self.xlo, self.xhi = 0.0, 1.0

    def toggle_info(self) -> None:
        self.show_info = not self.show_info
