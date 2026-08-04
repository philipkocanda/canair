"""Multi-ECU pipeline mode.

Executes a sequence of sub-commands within a single transport session,
managing extended diagnostic sessions across multiple ECUs with interleaved
TesterPresent keepalives.

Sub-commands:
    skm-wake [level]                Wake SKM + activate relay (acc/ign1/ign2)
    session <ECU|TX_ID> [--wake]    Enter extended session on ECU
    query <QUERY>                   Query ECUs/PIDs via the mini-language
    raw <TX:PID>                    Raw UDS request
    scan <TX> <SVC> <RANGE> [APPEND]  Scan PID range
    sleep <seconds>                 Pause between steps
    repl                            Drop into interactive REPL (explicit)

The 'query' sub-command uses the ECU/PID selection mini-language (see
canlib.query): whitespace-separated ``ECU[:PID,PID,...]`` selectors. A bare
ECU queries all its PIDs; a cross-ECU query fans out to one query per ECU.

    query BMS                       all BMS PIDs
    query IGPM:BC03,BC06            two IGPM DIDs
    query VCU:2101 BMS:2101         cross-ECU (two ECUs)

After all sub-commands complete, exits by default. Use --repl to drop into
an interactive REPL, or include an explicit 'repl' step in the pipeline.

This module is the orchestrator; the pipeline is split into focused layers:

    * ``multi_parse`` — sub-command / ECU-PID token parsing (device-free)
    * ``multi_exec``  — per-step execution primitives (session/query/raw/…)
    * ``multi_repl``  — the interactive REPL
    * ``multi_batch`` — the multi-DID batching/result kernel

The names that layers below use are re-exported here so existing callers
(``from canlib.modes.multi import build_query_plan`` etc.) keep working.
"""

import asyncio

from ..capture_types import Quality
from ..formatting import print_ecu_results
from ..pids import build_ecu_index
from ..session_manager import SessionManager
from ..transport.protocol import Terminal
from .multi_batch import (
    BatchState,
    ResultEntry,
    _capture_stamp,
)
from .multi_exec import (
    _exec_iocontrol,
    _exec_query,
    _exec_raw,
    _exec_scan,
    _exec_session,
    _exec_skm_wake,
    _read_batch,
    _read_single,
    _run_query_plan,
    _rx_addr_for_ecu_label,
    _rx_addr_for_tx,
    build_query_plan,
)
from .multi_parse import (
    _looks_like_pid,
    _query_selectors,
    parse_sub_commands,
    resolve_tx_id,
)
from .multi_repl import _multi_repl

__all__ = [
    "BatchState",
    "_exec_iocontrol",
    "_exec_query",
    "_exec_raw",
    "_exec_scan",
    "_exec_session",
    "_exec_skm_wake",
    "_finalize_journal",
    "_looks_like_pid",
    "_multi_repl",
    "_query_selectors",
    "_read_batch",
    "_read_single",
    "_run_query_plan",
    "_rx_addr_for_ecu_label",
    "_rx_addr_for_tx",
    "build_query_plan",
    "mode_multi",
    "parse_sub_commands",
    "resolve_tx_id",
]


def _finalize_journal(
    journal,
    count: int,
    label: str | None,
    vehicle_states=None,
    notes: str | None = None,
    prompt: bool = True,
    suggested_state: str | None = None,
    quality: Quality | None = None,
) -> None:
    """Resolve metadata (optionally prompting) and reconcile the write-ahead journal.

    ``journal`` is a :class:`~canlib.capture_journal.CaptureJournal` (or None).
    On a cancelled interactive prompt the journal is discarded. When ``prompt``
    is False (e.g. an interrupted pipeline) the journal is reconciled with the
    metadata already on it — no stdin interaction. ``suggested_state`` pre-fills
    the interactive state prompt (auto-suggested from decoded PID values).
    ``quality`` (the transport exchange/error footprint) is stamped onto the
    session before reconcile.
    """
    from ..captures import resolve_metadata

    if journal is None:
        return
    if count == 0:
        print("\n  --save: no payloads captured — nothing to save.")
        journal.discard()
        return

    print(f"\n  --save: {count} payload(s) captured.")
    if prompt:
        meta = resolve_metadata(
            label,
            vehicle_states,
            notes,
            suggested_label="Multi query session",
            last_state=suggested_state,
        )
        if meta is None:
            journal.discard()
            return
        lbl, states, nt = meta
        journal.update_meta(lbl, states, nt)
    if quality is not None:
        journal.update_meta(quality=quality)
    journal.reconcile()


async def mode_multi(
    terminal: Terminal,
    sub_commands: list[str],
    pids_data: dict,
    verbose: bool,
    no_repl: bool = False,
    save: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    include_static: bool = False,
):
    """Execute a multi-ECU pipeline and optionally drop into REPL.

    Args:
        terminal: Connected Terminal.
        sub_commands: List of sub-command strings (e.g., ["skm-wake acc", "query IGPM:BC03"]).
        pids_data: Loaded PID definitions.
        verbose: Show debug output.
        no_repl: If True, don't drop into REPL after pipeline.
        save: If True, collect payloads from query/raw steps and save them to
            captures/YYYY-MM-DD.yaml after the pipeline completes.
        label/state/notes: Session metadata. When ``label`` is provided, saving
            is non-interactive; otherwise the user is prompted.
    """
    commands = parse_sub_commands(sub_commands)
    ecu_index = build_ecu_index(pids_data)
    sm = SessionManager(terminal, verbose=verbose)
    repl_executed = False
    # Collected (ecu_ref, pid, hex, time) rows for --save (also counts for report).
    collected: list[tuple[str, str, str, str]] = []
    # Accumulated decoded values for state auto-suggestion at save time.
    pipe_values: dict[str, float] = {}
    pipe_responded: set[str] = set()
    # Write-ahead journal: payloads are appended as they arrive and reconciled at
    # the end. An exception mid-pipeline leaves it on disk for `--recover`.
    journal = None
    if save:
        from ..capture_journal import CaptureJournal
        from ..profile import active

        journal = CaptureJournal.open(
            active().captures_dir,
            label=label or "Multi query session",
            vehicle_states=vehicle_states,
            notes=notes,
            source="query",
            transport=getattr(getattr(terminal, "diag", None), "transport", None),
        )

    def _collect_query(ecu_label: str, pid_results: list[ResultEntry]) -> None:
        ecu_ref = _rx_addr_for_ecu_label(ecu_label, ecu_index)
        for entry in pid_results or []:
            raw_hex = entry.get("raw_hex", "")
            if raw_hex:
                # Preserve the per-PID acquisition timestamp (moment the response
                # arrived) at millisecond precision, mirroring the monitor — so
                # sequentially-polled PIDs keep their true sub-second skew that
                # cross-signal correlate/hunt/--corr rely on.
                cap_date, cap_time = _capture_stamp(entry.get("acquired_at"))
                collected.append((ecu_ref, entry["pid"], raw_hex, cap_time))
                if journal is not None:
                    journal.append(
                        ecu_ref,
                        entry["pid"],
                        raw_hex,
                        cap_time,
                        cap_date,
                        elapsed_ms=entry.get("elapsed_ms"),
                    )
        # Accumulate decoded values for end-of-pipeline state auto-suggestion.
        if save:
            from ..states import collect_values

            vals, resp = collect_values([(ecu_label, pid_results)])
            pipe_values.update(vals)
            pipe_responded.update(resp)

    def _suggest_pipeline_state() -> str | None:
        from ..states import StatePredicateError, join_states, load_states, suggest_states

        try:
            rules = load_states()
        except StatePredicateError:
            return None
        if not rules:
            return None
        matched, _false = suggest_states(rules, pipe_values, pipe_responded)
        return join_states(matched) if matched else None

    try:
        # Shared batch state so multi_did-capable ECUs batch service-22 DIDs in
        # the one-shot pipeline too (previously only the monitor did). Learns
        # per-DID lengths across steps and auto-falls back per-DID on rejection.
        from ..transport.isotp_params import resolve_tx_padding

        batch_state = BatchState(resolve_tx_padding(pids_data))
        for i, cmd in enumerate(commands):
            cmd_type = cmd["type"]
            step = f"[{i + 1}/{len(commands)}]"

            if cmd_type == "skm-wake":
                from ..quirks import SKM_WAKEUP, has_quirk

                if not has_quirk(pids_data, SKM_WAKEUP):
                    print(
                        f"\n{step} skm-wake skipped — not supported by this profile "
                        "(requires the `skm_wakeup` capability under quirks:). "
                        "For a plain wake, declare a per-ECU `wake:` block and use "
                        "`session <ECU> --wake`."
                    )
                    continue
                print(f"\n{step} SKM wakeup ({cmd['level']})...")
                await _exec_skm_wake(sm, cmd["level"], verbose)

            elif cmd_type == "session":
                print(f"\n{step} Session on {cmd['target']}...")
                await _exec_session(
                    sm, cmd["target"], cmd["wake"], ecu_index, cmd.get("mode", "03")
                )

            elif cmd_type == "query":
                pids_str = " ".join(cmd["pids"]) if cmd["pids"] else "all"
                print(f"\n{step} Query {cmd['ecu']} ({pids_str})...")
                if save:
                    result = await _exec_query(
                        sm,
                        cmd["ecu"],
                        cmd["pids"],
                        ecu_index,
                        pids_data,
                        verbose,
                        return_results=True,
                        batch_state=batch_state,
                        include_static=include_static,
                    )
                    if result:
                        ecu_label, pid_results = result
                        print_ecu_results(
                            ecu_label=ecu_label, pid_results=pid_results, verbose=verbose
                        )
                        _collect_query(ecu_label, pid_results)
                else:
                    await _exec_query(
                        sm,
                        cmd["ecu"],
                        cmd["pids"],
                        ecu_index,
                        pids_data,
                        verbose,
                        batch_state=batch_state,
                        include_static=include_static,
                    )

            elif cmd_type == "raw":
                print(f"\n{step} Raw {cmd['spec']}...")
                raw_result = await _exec_raw(sm, cmd["spec"], cmd["hold"], verbose)
                if save and raw_result:
                    tx_id, req, resp = raw_result
                    if resp.get("ok") and resp.get("hex"):
                        ecu_ref = _rx_addr_for_tx(tx_id)
                        cap_date, cap_time = _capture_stamp(None)
                        collected.append((ecu_ref, req, resp["hex"], cap_time))
                        if journal is not None:
                            journal.append(ecu_ref, req, resp["hex"], cap_time, cap_date)

            elif cmd_type == "scan":
                print(f"\n{step} Scan {cmd['tx']} service {cmd['service']} range {cmd['range']}...")
                await _exec_scan(
                    sm, cmd["tx"], cmd["service"], cmd["range"], cmd["append"], verbose
                )

            elif cmd_type == "sleep":
                print(f"\n{step} Sleeping {cmd['seconds']}s...")
                # Send keepalives during sleep to maintain active sessions
                remaining = cmd["seconds"]
                while remaining > 0:
                    chunk = min(remaining, 1.5)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                    if sm.active_sessions:
                        await sm.keepalive_stale()

            elif cmd_type == "repl":
                print(f"\n{step} Entering REPL...")
                repl_executed = True
                await _multi_repl(sm, ecu_index, pids_data, verbose, include_static=include_static)

            elif cmd_type == "iocontrol":
                action = "OFF" if cmd["off"] else "ON"
                print(f"\n{step} IOControl {cmd['ecu']} {cmd['did']} ({action})...")
                await _exec_iocontrol(
                    sm, cmd["ecu"], cmd["did"], cmd["off"], pids_data, ecu_index, verbose
                )

        # Save collected payloads before any REPL handoff
        if save:
            _finalize_journal(
                journal,
                len(collected),
                label,
                vehicle_states,
                notes,
                suggested_state=_suggest_pipeline_state(),
                quality=getattr(getattr(terminal, "diag", None), "quality", lambda: None)(),
            )
            journal = None

        # Auto-REPL if no explicit repl step and --repl was passed
        if not repl_executed and not no_repl:
            sessions_str = ", ".join(f"0x{tx:03X}" for tx in sm.active_sessions)
            if sessions_str:
                print(f"\n  Active sessions: {sessions_str}")
            print("\n  Pipeline complete. Entering REPL...")
            await _multi_repl(sm, ecu_index, pids_data, verbose, include_static=include_static)

    except KeyboardInterrupt:
        print("\n  Interrupted.")
        # Reconcile whatever was captured before the interrupt (no prompt).
        if save and journal is not None:
            _finalize_journal(
                journal,
                len(collected),
                label,
                vehicle_states,
                notes,
                prompt=False,
                quality=getattr(getattr(terminal, "diag", None), "quality", lambda: None)(),
            )
            journal = None

    finally:
        sm.stop_background_keepalive()
        print("  Closing all sessions...")
        try:
            await asyncio.wait_for(sm.close_all(), timeout=3.0)
        except (TimeoutError, KeyboardInterrupt, Exception):
            pass
