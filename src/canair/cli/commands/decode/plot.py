#!/usr/bin/env python3
"""Entry point for ``canair decode --plot``: build the explorer's model, pick an output path.

A TTY gets the Textual app (:mod:`canair.tui.apps.decode_plot`); otherwise one static frame
is printed. The model and its chart (:mod:`canair.tui.apps.decode_plot_model`) interpret raw
payload bytes at an offset as a type, post-process the series and draw it as a braille line
chart with an optional reference signal overlaid.
"""

from __future__ import annotations

import sys

from canair.captures.store import load_pid_captures
from canair.cli.args.scope import resolve_scope_bounds
from canair.ecus.pids import build_ecu_index, load_pids
from canair.expression.decoding import decode_payload, ordered_signal_names
from canair.tui.apps.decode_plot_model import PlotModel
from canair.uds.byteindex import payload_to_wican_bytes

from .query import scope_captures


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

    from canair.tui.apps.decode_plot import run_plot_app

    run_plot_app(model, reload_pid=reload_pid, pid_options=pid_options)


def plot_pid_options() -> list[tuple[str, str]]:
    """Distinct ``(ECU, PID)`` pairs that have payload captures, for the --plot switcher."""
    from canair.captures.store import load_all_captures

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
