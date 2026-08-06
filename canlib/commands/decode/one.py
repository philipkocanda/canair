"""Decoding one ECU+PID: the per-PID pipeline behind every ``decode`` view.

The shape to keep in mind is a **pipeline, not a mode switch**:

1. resolve which signals to decode (definitions, ``--param``/``--verified``
   filters, ``--try`` candidates) — bail with guidance if there is nothing;
2. resolve the ``--corr`` reference (local signal, or a cross-signal series);
3. load the scoped captures and decode every one;
4. the three *terminal* exports — ``--dump-bytes``, ``--plot``, ``--json`` — each
   of which owns the whole run and returns;
5. otherwise: print the header, then a **sequence** of output sections
   (``--find-mirrors``, ``--stats``, ``--discriminate``, ``--corr``), any number of
   which may fire, falling back to the value-range view when none did.

Step 5 is why this is not a dispatch table: the sections are independent and
several can print in one run, so the ``printed`` fall-through is load-bearing.
Each section is its own function here; :func:`decode_one` is only their order.
"""

from __future__ import annotations

import json
import sys

from canlib.align import join_nearest
from canlib.byteindex import payload_to_wican_bytes
from canlib.capture_store import load_pid_captures
from canlib.commands._join import fill_policy_from_args
from canlib.decoding import decode_payload, ordered_signal_names
from canlib.keepmode import CHANGES_BANNER, scope_is_keep_changes, scope_is_keep_unique
from canlib.notation import resolve_notation, subfunction_bytes_for_pid
from canlib.stats import compute_stats
from canlib.stats import correlation as _correlation

from .calc import _local_series, _paired, _series, axis_group_keys, load_cross_ref_series
from .plot import build_plot_model, cmd_plot, plot_pid_options
from .query import resolve_ref, scope_captures, tolerates_missing_pid
from .render import (
    _dump_bytes,
    print_compact,
    print_correlations,
    print_discriminate,
    print_mirrors,
    print_stats_grouped,
    print_stats_table,
    print_value_ranges,
    scope_banner,
)

_BOLD = "\033[1m"
_DIM = "\033[2m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"


def _resolve_parameters(
    args, ecu_key: str, pid_key: str, ecu_index: dict, try_params: dict
) -> tuple[dict, dict, str | None]:
    """Which signals to decode: ``(parameters, defined_params, error)``.

    ``defined_params`` is the PID's full unfiltered definition set — ``--plot``
    needs it to flag already-mapped bytes regardless of the display filters.
    ``error`` is a ready-to-print message for an unresolvable ECU/PID.
    """
    parameters: dict = {}
    if ecu_key in ecu_index:
        ecu_pids = ecu_index[ecu_key]["pids"]
        if pid_key in ecu_pids:
            parameters = ecu_pids[pid_key]["parameters"]
        elif not tolerates_missing_pid(args):
            return (
                {},
                {},
                f"PID '{pid_key}' not found for {ecu_key}. Available: {', '.join(sorted(ecu_pids))}",
            )
    elif not tolerates_missing_pid(args):
        return (
            {},
            {},
            f"ECU '{ecu_key}' not found in ecus/. Available: {', '.join(sorted(ecu_index))}",
        )

    defined_params = dict(parameters)

    # Filter parameters (applies to *defined* params only; --try params are always shown)
    if args.param:
        filter_names = {n.upper() for n in args.param}
        parameters = {k: v for k, v in parameters.items() if k.upper() in filter_names}
        missing = filter_names - {k.upper() for k in parameters}
        if missing:
            print(f"Warning: parameters not found: {', '.join(sorted(missing))}")
    if args.verified:
        parameters = {k: v for k, v in parameters.items() if v.get("verified", False)}
    if args.unverified:
        parameters = {k: v for k, v in parameters.items() if not v.get("verified", False)}

    # Merge candidate expressions in (override a defined param on name clash).
    if try_params:
        parameters = {**parameters, **try_params}

    return parameters, defined_params, None


def _report_nothing_to_decode(
    ecu_key: str, pid_key: str, defined_params: dict, caps: list, *, multi: bool
) -> int:
    """Explain *why* there is nothing to show, distinguishing the four causes.

    These used to collapse into one misleading message. The cases are: filters
    excluded everything / captured but undecoded / neither / and the terse
    one-liner a multi-PID query wants so its report stays scannable.
    """
    if multi:
        note = (
            "filtered out"
            if defined_params
            else (f"no params defined ({len(caps)} captures)" if caps else "no params/captures")
        )
        print(f"\n{_BOLD}{ecu_key} {pid_key}{_RESET} — {_DIM}{note}{_RESET}")
        return 1
    if defined_params:
        # Params exist for this PID; the active --param/--verified/--unverified
        # filters just excluded them all.
        print(
            f"No parameters match the filter criteria "
            f"({len(defined_params)} defined for {ecu_key} {pid_key})."
        )
    elif caps:
        # Captured but not yet decoded — signpost the byte-level tools.
        print(
            f"{_BOLD}{ecu_key} {pid_key}{_RESET} — no parameters defined yet, "
            f"but {len(caps)} capture(s) exist."
        )
        print(f"  {_DIM}·{_RESET} Inspect raw bytes:  canair captures {ecu_key} {pid_key} --diff")
        print(f"  {_DIM}·{_RESET} Explore signals:    canair decode {ecu_key}:{pid_key} --plot")
        print(
            f"  {_DIM}·{_RESET} Test a candidate:   "
            f'canair decode {ecu_key}:{pid_key} --try "NAME:unit=EXPR"'
        )
    else:
        # Neither defined nor captured.
        print(f"No parameters defined and no captures found for {ecu_key} {pid_key}.")
    return 1


def _resolve_corr_reference(args, parameters: dict, scope: dict, fill):
    """Resolve ``--corr`` to ``(ref_label, series, series_label, error)``.

    A reference containing ``:`` is a cross-signal ``ECU:PID:PARAM|EXPR`` loaded
    from another ECU/PID and time-aligned; otherwise it is a local signal name
    paired by shared payload (so no join is needed).

    ``error`` is ``None`` or ``(message, to_stderr)`` — the two failures were
    reported on different streams before this was extracted, and that is
    preserved deliberately rather than unified, so ``--help``/output stays
    byte-identical.
    """
    if not args.corr:
        return None, None, None, None
    if ":" in args.corr:
        try:
            series, label = load_cross_ref_series(
                args.corr, scope=scope, tol_s=args.join_tol, fill=fill
            )
        except ValueError as e:
            return None, None, None, (f"--corr error: {e}", True)
        return label, series, label, None
    ref = resolve_ref(args.corr, ordered_signal_names(parameters))
    if ref is None:
        msg = f"--corr reference '{args.corr}' not found. Available: {', '.join(parameters)}"
        return None, None, None, (msg, False)
    return ref, None, None, None


def _decode_captures(captures: list[dict], parameters: dict) -> list[dict]:
    """Decode every capture, recording a per-capture parse error rather than raising."""
    all_results: list[dict] = []
    for cap in captures:
        try:
            wican_bytes = payload_to_wican_bytes(cap["payload"])
        except Exception as e:
            all_results.append(
                {"capture": cap, "decoded": {}, "error": f"payload parse error: {e}"}
            )
            continue
        all_results.append({"capture": cap, "decoded": decode_payload(wican_bytes, parameters)})
    return all_results


def _emit_aggregate_json(args, all_results: list[dict], param_names, corr_ref, cross_ref_series):
    """``--json`` with ``--stats``/``--corr``: per-signal statistics and/or correlations."""
    out: dict = {}
    if args.stats:
        out["stats"] = {
            name: {
                k: v for k, v in compute_stats(_series(all_results, name)).items() if k != "values"
            }
            for name in param_names
            if _series(all_results, name)
        }
    if corr_ref:
        out["reference"] = corr_ref
        out["method"] = args.method
        out["correlations"] = {}
        if cross_ref_series is not None:
            out["join_tol_s"] = args.join_tol
            for name in param_names:
                local = _local_series(all_results, name)
                xs, ys, n = join_nearest(cross_ref_series, local, tol_s=args.join_tol)
                out["correlations"][name] = {"r": _correlation(xs, ys, args.method), "n": n}
        else:
            for name in param_names:
                if name == corr_ref:
                    continue
                xs, ys = _paired(all_results, corr_ref, name)
                out["correlations"][name] = {
                    "r": _correlation(xs, ys, args.method),
                    "n": len(xs),
                }
    json.dump(out, sys.stdout, indent=2, default=str)
    print()
    return 0


def _emit_capture_json(all_results: list[dict], ecu_key, pid_key, json_collect):
    """``--json``: per-capture decoded values (payload-level views live in ``captures``)."""
    out = []
    for r in all_results:
        entry = {
            "date": r["capture"]["date"],
            "vehicle_states": r["capture"].get("vehicle_states") or [],
            "file": r["capture"]["file"],
        }
        if r["capture"].get("time"):
            entry["time"] = r["capture"]["time"]
        entry["parameters"] = {}
        for name, d in r["decoded"].items():
            entry["parameters"][name] = {
                "value": d["value"],
                "unit": d["unit"],
                "verified": d["verified"],
            }
            if d.get("error"):
                entry["parameters"][name]["error"] = d["error"]
        if r.get("error"):
            entry["error"] = r["error"]
        out.append(entry)
    if json_collect is not None:
        # Multi-PID --json: contribute one tagged block to the shared array.
        json_collect.append({"ecu": ecu_key, "pid": pid_key, "captures": out})
        return 0
    json.dump(out, sys.stdout, indent=2, default=str)
    print()
    return 0


def _print_header(args, ecu_key, pid_key, parameters, candidate_names, captures, since, until):
    """The signal-count header, the scope banner, and the keep-mode reliability warnings."""
    n_verified = sum(1 for p in parameters.values() if p.get("verified", False))
    n_total = len(parameters)
    try_note = (
        f", {_CYAN}{len(candidate_names)} candidate (--try){_RESET}" if candidate_names else ""
    )
    print(
        f"\n{_BOLD}{ecu_key} PID {pid_key}{_RESET} — "
        f"{n_total} parameters ({n_verified} verified, {n_total - n_verified} unverified){try_note}, "
        f"{len(captures)} captures\n"
    )
    banner = scope_banner(since, until, args.state, args.label, args.first, args.last)
    if banner:
        print(f"  {_DIM}scope: {banner}{_RESET}\n")

    if scope_is_keep_unique(captures) and args.corr_transform in ("delta", "cumsum"):
        print(
            f"  {_YELLOW}⚠ --corr-transform {args.corr_transform} on keep:unique data is "
            f"unreliable — the time gaps between stored rows are dedup artifacts, not "
            f"real sampling intervals.{_RESET}"
        )
        print()
    if scope_is_keep_changes(captures):
        print(f"  {_YELLOW}⚠ {CHANGES_BANNER}.{_RESET}")
        if args.corr_transform in ("delta", "cumsum"):
            print(
                f"  {_YELLOW}  ⚠ --corr-transform {args.corr_transform} on keep:changes data is "
                f"unreliable — stored rows are value-transitions, not fixed-rate samples.{_RESET}"
            )
        print()


def _print_discriminate_section(
    args, all_results, param_names, parameters, candidate_names, scope, fill, notation, sub_bytes
) -> int | None:
    """The ``--discriminate`` section. Returns an exit code only on a bad axis."""
    disc_field = args.discriminate
    group_of = None
    if ":" in args.discriminate:
        try:
            axis_keys, disc_field = axis_group_keys(
                all_results, args.discriminate, scope=scope, tol_s=args.join_tol, fill=fill
            )
        except ValueError as e:
            print(f"--discriminate error: {e}", file=sys.stderr)
            return 1
        group_of = lambda r: axis_keys.get(id(r))  # noqa: E731
    elif args.discriminate != "state":
        print(
            f"--discriminate: unknown axis {args.discriminate!r} — use 'state' or a "
            "cross-signal ECU:PID:PARAM",
            file=sys.stderr,
        )
        return 1
    print_discriminate(
        all_results,
        param_names,
        parameters,
        candidate_names,
        disc_field,
        include_bytes=args.bytes,
        include_bits=args.bits,
        notation=notation,
        sub_bytes=sub_bytes,
        group_of=group_of,
    )
    return None


def decode_one(
    args,
    ecu_key: str,
    pid_key: str,
    ecu_index: dict,
    try_params: dict,
    candidate_names: set,
    since,
    until,
    *,
    multi: bool = False,
    json_collect: list | None = None,
) -> int:
    """Decode a single ECU+PID and print whichever view(s) the flags selected.

    ``multi`` marks a call that is one of several in a multi-PID query (used to
    keep the "no parameters/captures" notes terse and to continue past a miss);
    ``json_collect`` (when set) collects this PID's per-capture JSON into a shared
    list instead of dumping it, so a multi-PID ``--json`` yields one array.
    """
    parameters, defined_params, perr = _resolve_parameters(
        args, ecu_key, pid_key, ecu_index, try_params
    )
    if perr:
        print(perr)
        return 1

    # Scope arguments shared by every capture load in this run (date/state/label
    # range + first/last slice). Resolved once so all paths stay consistent.
    scope = {
        "since": since,
        "until": until,
        "state": args.state,
        "label": args.label,
        "first": args.first,
        "last": args.last,
    }
    scoped = any(v is not None for v in scope.values())

    # Raw byte/bit analyses (--discriminate/--find-mirrors with --bytes/--bits)
    # rank straight off the captured payloads and need no defined parameters.
    raw_byte_mode = bool((args.discriminate or args.find_mirrors) and (args.bytes or args.bits))

    if not parameters and not args.plot and not args.dump_bytes and not raw_byte_mode:
        # Terminating path, so loading captures here doesn't double-load — the
        # normal load below is only reached when there are signals to decode.
        caps = scope_captures(load_pid_captures(ecu_key, pid_key), **scope)
        return _report_nothing_to_decode(ecu_key, pid_key, defined_params, caps, multi=multi)

    fill = fill_policy_from_args(args)
    corr_ref, cross_ref_series, cross_ref_label, cerr = _resolve_corr_reference(
        args, parameters, scope, fill
    )
    if cerr is not None:
        msg, to_stderr = cerr
        print(msg, file=sys.stderr if to_stderr else sys.stdout)
        return 1

    captures = scope_captures(load_pid_captures(ecu_key, pid_key), **scope)
    if not captures:
        if multi:
            print(f"\n{_BOLD}{ecu_key} {pid_key}{_RESET} — {_DIM}no captures in scope{_RESET}")
            return 1
        if scoped:
            print(f"No captures for {ecu_key} PID {pid_key} match the scope filters.")
        else:
            print(f"No captures found for {ecu_key} PID {pid_key}.")
        return 1

    all_results = _decode_captures(captures, parameters)

    # --- terminal exports: each owns the whole run ---------------------------
    if args.dump_bytes:
        return _dump_bytes(
            all_results,
            ecu_key,
            pid_key,
            as_json=args.json,
            include_pci=args.include_pci,
            notation=resolve_notation(args.notation),
            sub_bytes=subfunction_bytes_for_pid(pid_key),
            signed=args.dump_signed,
        )

    if args.plot:
        cmd_plot(
            all_results,
            ordered_signal_names(parameters),
            parameters,
            candidate_names,
            corr_ref,
            ecu_key,
            pid_key,
            defined_params=defined_params,
            reload_pid=lambda e, p: build_plot_model(args, e, p),
            pid_options=plot_pid_options(),
        )
        return 0

    if args.json:
        param_names = ordered_signal_names(parameters)
        if args.stats or corr_ref:
            return _emit_aggregate_json(args, all_results, param_names, corr_ref, cross_ref_series)
        return _emit_capture_json(all_results, ecu_key, pid_key, json_collect)

    # --- human views --------------------------------------------------------
    # Signal column order: payload position (--try candidates appended last).
    param_names = ordered_signal_names(parameters)
    _print_header(args, ecu_key, pid_key, parameters, candidate_names, captures, since, until)

    if args.compact:
        print_compact(
            all_results, param_names, parameters, candidate_names, changes_only=args.changes_only
        )
        return 0

    # A sequence, not a choice: any number of these may print, and the
    # value-range view is the fallback when none did.
    notation = resolve_notation(args.notation)
    sub_bytes = subfunction_bytes_for_pid(pid_key)
    printed = False
    if args.find_mirrors:
        print_mirrors(
            all_results,
            bits=args.bits,
            notation=notation,
            sub_bytes=sub_bytes,
            min_fraction=args.mirror_match,
            allow_offset=args.allow_offset,
        )
        printed = True
    if args.stats:
        if args.group_by == "state":
            print_stats_grouped(all_results, param_names, parameters, candidate_names, "state")
        else:
            print_stats_table(all_results, param_names, parameters, candidate_names)
        printed = True
    if args.discriminate:
        rc = _print_discriminate_section(
            args,
            all_results,
            param_names,
            parameters,
            candidate_names,
            scope,
            fill,
            notation,
            sub_bytes,
        )
        if rc is not None:
            return rc
        printed = True
    if corr_ref:
        print_correlations(
            all_results,
            param_names,
            parameters,
            candidate_names,
            corr_ref,
            cross_ref_series=cross_ref_series,
            cross_ref_label=cross_ref_label,
            tol_s=args.join_tol,
            transform=args.corr_transform,
            method=args.method,
        )
        printed = True
    if not printed:
        print_value_ranges(all_results, param_names, parameters, candidate_names)
        if sys.stdout.isatty():
            print(
                f"\n  {_DIM}Tip: add --plot to interactively explore these signals "
                f"(byte interpretations, transforms, correlations).{_RESET}"
            )
    return 0
