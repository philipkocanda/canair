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

The byte-interpretation primitives (INSPECT_TYPES, interpret_bytes, wican_expr,
apply_transform, POST_TRANSFORMS, norm01) live in the neutral library leaf
``canlib.inspect_bytes``; this module imports them down and uses them here.
"""

from __future__ import annotations

import math
import shutil
import sys

from canlib.byteindex import payload_to_wican_bytes
from canlib.capture_dates import resolve_scope_bounds
from canlib.capture_store import load_pid_captures
from canlib.decoding import decode_payload, ordered_signal_names
from canlib.inspect_bytes import (
    INSPECT_TYPES,
    POST_TRANSFORMS,
    apply_transform,
    interpret_bytes,
    wican_expr,
)
from canlib.pids import build_ecu_index, load_pids
from canlib.stats import pearson as _pearson

from .plot_draw import (
    _cycle_overlay,
    _info_lines,
    _mapping_for_offset,
    _pci_positions,
    _series_stats_str,
    _view_time_range,
    _window,
    render_plot,
)
from .query import scope_captures

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


_V_AXIS = "\u2502"  # box vertical
_CORNER = "\u2514"  # box corner
_HLINE = "\u2500"  # box horizontal


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

    from .plot_tui import run_plot_app

    run_plot_app(model, reload_pid=reload_pid, pid_options=pid_options)


class PlotModel:
    """State + rendering for the ``decode --plot`` signal explorer.

    Holds the capture-derived data and the mutable view state (byte/param mode,
    offset, interpretation type, endianness, transform, overlay reference, x-axis
    window, info modal). :meth:`render_lines` produces the ANSI-colored frame; the
    Textual app (``decode.plot_tui``) binds keys to the mutator methods. Kept
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

    def hint_bits(self) -> list[str]:
        """Key hints for the current mode as separate segments, most useful first.

        Segments (not one joined string) so the TUI's status bar can drop the
        least essential ones on a narrow terminal rather than clipping the line;
        :meth:`hint` joins them for anything that wants the whole thing.
        """
        mode = (
            ["←/→ offset", "t/T type", "e endian", "m param"]
            if self.mode == "bytes"
            else ["←/→ param", "m bytes"]
        )
        return [*mode, "f transform", "o overlay", "i captures", "+/- zoom", ",/. pan", "0 reset-x"]

    def hint(self) -> str:
        """The one-line key hint for the current mode."""
        return " · ".join([*self.hint_bits(), "? help", "q quit"])

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


def plot_pid_options() -> list[tuple[str, str]]:
    """Distinct ``(ECU, PID)`` pairs that have payload captures, for the --plot switcher."""
    from canlib.capture_store import load_all_captures

    seen: dict[tuple[str, str], None] = {}
    try:
        for e in load_all_captures():
            if not e.get("payload"):
                continue
            ecu = str(e.get("ecu", "")).upper()
            pid = str(e.get("pid", "")).upper()
            if ecu and pid:
                seen.setdefault((ecu, pid), None)
    except Exception:
        return []
    return sorted(seen)


def build_plot_model(args, ecu: str, pid: str) -> PlotModel | None:
    """(Re)build a :class:`PlotModel` for ``ecu``/``pid`` reusing ``args`` scope.

    Used by the --plot TUI's in-place PID switch. Carries the date/state/label
    scope, but not --try/--corr (those were bound to the originally-selected PID).
    Returns None when the target has no plottable captures.
    """
    ecu_key = ecu.upper()
    pid_key = pid.upper()
    since, until, err = resolve_scope_bounds(args)
    if err:
        return None
    scope = {
        "since": since,
        "until": until,
        "state": args.state,
        "label": args.label,
        "first": args.first,
        "last": args.last,
    }
    pids_data = load_pids()
    ecu_index = build_ecu_index(pids_data)
    parameters: dict = {}
    if ecu_key in ecu_index:
        parameters = ecu_index[ecu_key]["pids"].get(pid_key, {}).get("parameters", {}) or {}
    defined_params = dict(parameters)

    captures = scope_captures(load_pid_captures(ecu_key, pid_key), **scope)
    all_results: list[dict] = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append({"capture": cap, "decoded": {}, "error": str(e)})
            continue
        all_results.append({"capture": cap, "decoded": decode_payload(wican_bytes, parameters)})

    model = PlotModel(
        all_results,
        ordered_signal_names(parameters),
        parameters,
        set(),
        None,
        ecu_key,
        pid_key,
        defined_params=defined_params,
    )
    return None if model.empty else model
