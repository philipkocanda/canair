"""``canair discover --functional``: one broadcast request, every routed responder.

Sends an allowed read (default ``3E 00``) on the functional id and lists the ECUs
that answer, grouped by the bus the profile puts them on, with the known ECUs that
stayed silent. A gateway car only answers on the buses it forwards the functional
id to, and only for responders whose reply ids it routes back, so the result is
that car's routing map. ``--repeat`` sends it again ``--interval`` seconds later
(at least 1 s; faster bursts silence some buses). The classification is the pure
:mod:`canlib.modes.functional_sweep`; the reception is the terminal's
``send_functional``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Any

from canlib.transport.functional import DEFAULT_WINDOW_S
from canlib.transport.protocol import Terminal

from .functional_sweep import (
    SweepRound,
    classify_round,
    group_by_bus,
    hex_id,
    round_to_capture,
    round_to_json,
    silent_by_bus,
)

DEFAULT_REQUEST = "3E00"
# The spacing ES95486 asks for between functional requests; closer bursts were
# seen to silence the B-CAN and MM-CAN responders on a Kona EV.
MIN_INTERVAL_S = 1.0


def _print_round(sweep: SweepRound, total: int, out) -> None:
    head = f"Round {sweep.index}/{total}" if total > 1 else "Responders"
    print(f"\n  --- {head} ({sweep.time}): {len(sweep.responders)} answered ---", file=out)
    for bus, rows in group_by_bus(sweep.responders).items():
        print(f"  {bus}", file=out)
        for r in rows:
            mark = "+" if r.status == "positive" else "~"
            tag = r.name or "NEW"
            timing = f"  {r.elapsed_ms} ms" if r.elapsed_ms is not None else ""
            pending = f"  ({r.pending}x pending)" if r.pending else ""
            detail = f" ({r.detail})" if r.detail else ""
            print(
                f"    {mark} {hex_id(r.rx_id)} [{tag}] {r.status}{detail}  {r.payload_hex}"
                f"{timing}{pending}",
                file=out,
            )
    if sweep.silent:
        print("  Silent (in the profile, did not answer):", file=out)
        for bus, rows in silent_by_bus(sweep.silent).items():
            print(f"    {bus}: {', '.join(s.name for s in rows)}", file=out)
    if sweep.other:
        print("  Other traffic (did not answer the request):", file=out)
        for o in sweep.other:
            print(f"    {hex_id(o.rx_id)}: {o.payload_hex}", file=out)
    if sweep.lost:
        print(f"  Lost: {sweep.lost} reply/replies missing frames (not shown)", file=out)


def _print_summary(rounds: list[SweepRound], out) -> None:
    """Per responder, how many rounds it answered (a dropped bus shows up here)."""
    answered: dict[int, tuple[str, int]] = {}
    for sweep in rounds:
        for r in sweep.responders:
            label, count = answered.get(r.rx_id, (r.name or f"NEW {hex_id(r.rx_id)}", 0))
            answered[r.rx_id] = (label, count + 1)
    print(f"\n  --- Answered per round ({len(rounds)} rounds) ---", file=out)
    for label, count in sorted(answered.values()):
        print(f"    {label}: {count}/{len(rounds)}", file=out)


async def mode_discover_functional(
    terminal: Terminal,
    pids_data: dict,
    *,
    request: str = DEFAULT_REQUEST,
    window: float = DEFAULT_WINDOW_S,
    repeat: int = 1,
    interval: float = MIN_INTERVAL_S,
    as_json: bool = False,
    save: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    register: bool = False,
    dry_run: bool = False,
    identify: bool = False,
) -> list[SweepRound]:
    """Broadcast ``request`` ``repeat`` times and report every responder."""
    from canlib.addressing import functional_request_id, resolve_mode, resolve_rx_offset
    from canlib.ecus import load_ecus
    from canlib.quirks import HK_F1XX_MINUS_ONE, has_quirk

    if interval < MIN_INTERVAL_S:
        raise ValueError(f"--interval must be at least {MIN_INTERVAL_S:g} s")
    request = request.replace(" ", "").upper()
    mode = resolve_mode(pids_data)
    tx_id = functional_request_id(mode)
    ecus = load_ecus()
    out = sys.stderr if as_json else sys.stdout

    print(
        f"\n  Functional discovery: {request} on {hex_id(tx_id)}"
        f"\n  Mode: {mode.value}"
        f"\n  Window: {window * 1000:.0f} ms"
        + (f"\n  Rounds: {repeat}, {interval:g} s apart" if repeat > 1 else ""),
        file=out,
    )

    rounds: list[SweepRound] = []
    for i in range(1, repeat + 1):
        if i > 1:
            await asyncio.sleep(interval)
        stamp = datetime.now().strftime("%H:%M:%S")
        result = await terminal.send_functional(tx_id, request, window)
        sweep = classify_round(
            result,
            ecus,
            index=i,
            time=stamp,
            rx_offset=resolve_rx_offset(pids_data),
            mode=mode,
            hk_f1xx_offset=has_quirk(pids_data, HK_F1XX_MINUS_ONE),
        )
        rounds.append(sweep)
        _print_round(sweep, repeat, out)

    if repeat > 1:
        _print_summary(rounds, out)

    if as_json:
        doc: dict[str, Any] = {
            "request": request,
            "tx": hex_id(tx_id),
            "mode": mode.value,
            "window_ms": round(window * 1000),
            "rounds": [round_to_json(r) for r in rounds],
        }
        print(json.dumps(doc, indent=2))

    if save and any(r.responders for r in rounds):
        _save(terminal, rounds, tx_id, request, label, vehicle_states, notes)

    # Everyone who answered at least once, in the shape the physical sweep's
    # register/identify steps take.
    alive: dict[int, tuple[int, str, str]] = {}
    for sweep in rounds:
        for r in sweep.responders:
            alive.setdefault(r.tx_id, (r.tx_id, r.status, r.payload_hex))
    from .discover import _offer_identify, _register_discovered

    if register:
        _register_discovered(list(alive.values()), dry_run=dry_run, via=f"functional {request}")
    await _offer_identify(terminal, list(alive.values()), identify, as_json)
    print(file=out)
    return rounds


def _save(terminal, rounds, tx_id, request, label, vehicle_states, notes) -> None:
    from canlib.captures import build_manual_session, resolve_metadata, save_session_journaled
    from canlib.transport.stats import client_transport

    suggested = f"Functional discovery {request}"
    meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
    if not meta:
        return
    label, vehicle_states, notes = meta
    session = build_manual_session(
        [round_to_capture(r, tx_id, request) for r in rounds],
        label=label,
        vehicle_states=vehicle_states,
        notes=notes,
        transport=client_transport(terminal),
    )
    save_session_journaled(session)
