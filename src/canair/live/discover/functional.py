"""``canair discover --functional``: one broadcast request, every routed responder.

Sends an allowed read (default ``3E 00``) on the functional id and lists the ECUs
that answer, grouped by the bus the profile puts them on, with the known ECUs that
stayed silent. A gateway car only answers on the buses it forwards the functional
id to, and only for responders whose reply ids it routes back, so the result is
that car's routing map. ``--repeat`` sends it again ``--interval`` seconds later
(at least 1 s; faster bursts silence some buses). One round prints as that routing map.
Several rounds print one progress line each, then the same map once with one mark per
round before every responder, so a bus that drops out shows as a ``-``. The classification is
the pure :mod:`canair.live.discover.sweep`; the reception is the terminal's ``send_functional``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any, TextIO

from canair.captures.build import build_manual_session
from canair.captures.metadata import resolve_metadata
from canair.captures.oneshot import save_session_journaled
from canair.ecus.registry import load_ecus
from canair.profile.model import Profile
from canair.render import ansi, messages
from canair.render.discover import ABSENT, NO_ANSWER, POSITIVE, REFUSED, status_cell
from canair.render.paint import GAP, INDENT, Cell, Painter, column_widths, render_row
from canair.transport.functional import DEFAULT_WINDOW_S
from canair.transport.protocol import Terminal
from canair.transport.stats import client_transport
from canair.uds.addressing import functional_request_id, resolve_mode, resolve_rx_offset
from canair.uds.services import TESTER_PRESENT_REQUEST

from .physical import _offer_identify, _register_discovered
from .sweep import (
    NOT_IN_PROFILE,
    UNKNOWN_BUS,
    OtherMessage,
    Responder,
    SilentEcu,
    SweepRound,
    classify_round,
    group_by_bus,
    hex_id,
    round_to_capture,
    round_to_json,
    silent_by_bus,
)

DEFAULT_REQUEST = TESTER_PRESENT_REQUEST
# The spacing ES95486 asks for between functional requests; closer bursts were
# seen to silence the B-CAN and MM-CAN responders on a Kona EV.
MIN_INTERVAL_S = 1.0


_PHYSICAL_HINT = "Some ECUs answer only a physical request; ask one with `canair raw ECU:3E00`."
_MARKS_LEGEND = "one mark per round: + positive, ~ negative response, - no reply"


def _bus_heading(bus: str, paint: Painter) -> str:
    if bus == NOT_IN_PROFILE:
        return f"  {paint(bus, ansi.raw.BOLD + ansi.raw.YELLOW)}"
    if bus == UNKNOWN_BUS:
        return f"  {paint(bus, ansi.raw.DIM)}"
    return f"  {paint(bus, ansi.raw.BOLD + ansi.raw.CYAN)}"


def _mark(r: Responder | None) -> Cell:
    if r is None:
        return ABSENT
    return POSITIVE if r.positive else NO_ANSWER if not r.answered else REFUSED


def _responder_row(r: Responder, marks: Sequence[Cell]) -> list[Cell]:
    name: Cell = (r.name, ansi.raw.BOLD) if r.name else ("NEW", ansi.raw.BOLD + ansi.raw.YELLOW)
    extra = [r.detail] if r.detail else []
    if r.elapsed_ms is not None:
        extra.append(f"{r.elapsed_ms} ms")
    if r.pending:
        extra.append(f"{r.pending}x pending")
    return [
        *marks,
        (hex_id(r.rx_id), ""),
        name,
        status_cell(r.status),
        (r.payload_hex, ansi.raw.DIM),
        ("  ".join(extra), ansi.raw.DIM),
    ]


def _print_buses(rows: dict[str, list[list[Cell]]], paint: Painter, out: TextIO) -> None:
    # One set of widths for every bus, so the columns line up down the whole report.
    widths = column_widths([row for bus_rows in rows.values() for row in bus_rows])
    for bus, bus_rows in rows.items():
        print(_bus_heading(bus, paint), file=out)
        for row in bus_rows:
            print(render_row(row, widths, paint), file=out)


def _print_silent(silent: Sequence[SilentEcu], scope: str, paint: Painter, out: TextIO) -> None:
    if not silent:
        return
    by_bus = silent_by_bus(silent)
    print(paint.heading("Silent", f"{len(silent)} in the profile did not answer {scope}"), file=out)
    width = max(len(bus) for bus in by_bus)
    for bus, members in by_bus.items():
        names = ", ".join(s.name for s in members)
        print(f"{INDENT}{paint(bus.ljust(width), ansi.raw.DIM)}{GAP}{names}", file=out)
    print(f"{INDENT}{paint(_PHYSICAL_HINT, ansi.raw.DIM)}", file=out)


def _print_other(other: Sequence[OtherMessage], paint: Painter, out: TextIO) -> None:
    if not other:
        return
    print(paint.heading("Other traffic", "did not answer the request"), file=out)
    rows: list[list[Cell]] = [[(hex_id(o.rx_id), ""), (o.payload_hex, ansi.raw.DIM)] for o in other]
    widths = column_widths(rows)
    for row in rows:
        print(render_row(row, widths, paint), file=out)


def _print_round(sweep: SweepRound, out: TextIO) -> None:
    """The report of a single round: responders by bus, the silent ECUs, other traffic."""
    paint = Painter(out)
    print(paint.heading("Responders", sweep.time, f"{len(sweep.responders)} answered"), file=out)
    groups = group_by_bus(sweep.responders)
    _print_buses(
        {bus: [_responder_row(r, [_mark(r)]) for r in members] for bus, members in groups.items()},
        paint,
        out,
    )
    _print_silent(sweep.silent, "the functional request", paint, out)
    _print_other(sweep.other, paint, out)
    if sweep.lost or sweep.notes:
        print(file=out)
    if sweep.lost:
        messages.warning(f"{sweep.lost} reply/replies missing frames (not shown)")
    for note in sweep.notes:
        messages.note(note)


def _print_progress(sweep: SweepRound, total: int, out: TextIO) -> None:
    """One line for a finished round of several, so a long run shows it is alive."""
    paint = Painter(out)
    width = len(str(total))
    count = f"{len(sweep.responders)} answered"
    count = paint(count, ansi.raw.YELLOW) if not sweep.responders else count
    stamp = paint(sweep.time, ansi.raw.DIM)
    print(f"{INDENT}round {sweep.index:>{width}}/{total}  {stamp}  {count}", file=out)
    if sweep.lost:
        messages.warning(f"round {sweep.index}: {sweep.lost} reply/replies missing frames")


def _print_rounds(rounds: Sequence[SweepRound], out: TextIO) -> None:
    """The report of several rounds: one map, one mark per round before every responder."""
    paint = Painter(out)
    heard: dict[int, list[Responder | None]] = {}
    for position, sweep in enumerate(rounds):
        for r in sweep.responders:
            heard.setdefault(r.rx_id, [None] * len(rounds))[position] = r
    # The last reply a responder gave stands for its row; the marks carry the rest.
    latest = [next(r for r in reversed(replies) if r) for replies in heard.values()]
    groups = group_by_bus(latest)

    print(paint.heading("Responders", f"{len(heard)} answered in at least one round"), file=out)
    print(f"{INDENT}{paint(_MARKS_LEGEND, ansi.raw.DIM)}", file=out)
    _print_buses(
        {
            bus: [_responder_row(r, [_mark(x) for x in heard[r.rx_id]]) for r in members]
            for bus, members in groups.items()
        },
        paint,
        out,
    )

    answered = set(heard)
    silent = [s for s in rounds[0].silent if s.rx_id not in answered]
    _print_silent(silent, "in any round", paint, out)
    other = list(dict.fromkeys(o for sweep in rounds for o in sweep.other))
    _print_other(other, paint, out)
    notes = list(dict.fromkeys(n for sweep in rounds for n in sweep.notes))
    if notes:
        print(file=out)
    for note in notes:
        messages.note(note)


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
    vehicle_states: list[str] | str | None = None,
    notes: str | None = None,
    register: bool = False,
    dry_run: bool = False,
    identify: bool = False,
    profile: Profile,
) -> list[SweepRound]:
    """Broadcast ``request`` ``repeat`` times and report every responder."""

    if interval < MIN_INTERVAL_S:
        raise ValueError(f"--interval must be at least {MIN_INTERVAL_S:g} s")
    request = request.replace(" ", "").upper()
    mode = resolve_mode(pids_data)
    tx_id = functional_request_id(mode)
    ecus = load_ecus(profile=profile)
    out = sys.stderr if as_json else sys.stdout

    paint = Painter(out)
    settings = [
        ("request", f"{request} on {hex_id(tx_id)}"),
        ("mode", mode.value),
        ("window", f"{window * 1000:.0f} ms"),
    ]
    print(paint.heading("Functional discovery"), file=out)
    print("\n".join(paint.fields(settings)), file=out)
    if repeat > 1:
        print(paint.heading("Rounds", f"{repeat}, {interval:g} s apart"), file=out)

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
        )
        rounds.append(sweep)
        if repeat > 1:
            _print_progress(sweep, repeat, out)
        else:
            _print_round(sweep, out)

    if repeat > 1:
        _print_rounds(rounds, out)

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
        _save(terminal, rounds, tx_id, request, label, vehicle_states, notes, profile=profile)

    # Everyone who answered at least once, in the shape the physical sweep's
    # register/identify steps take.
    alive: dict[int, tuple[int, str, str]] = {}
    for sweep in rounds:
        for r in sweep.responders:
            alive.setdefault(r.tx_id, (r.tx_id, r.status, r.payload_hex))

    if register:
        _register_discovered(
            list(alive.values()),
            dry_run=dry_run,
            via=f"functional {request}",
            out=out,
            profile=profile,
        )
    await _offer_identify(terminal, list(alive.values()), identify, as_json, profile=profile)
    print(file=out)
    return rounds


def _save(
    terminal: Terminal,
    rounds: Sequence[SweepRound],
    tx_id: int,
    request: str,
    label: str | None,
    vehicle_states: list[str] | str | None,
    notes: str | None,
    *,
    profile: Profile,
) -> None:
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
    save_session_journaled(session, profile=profile)
