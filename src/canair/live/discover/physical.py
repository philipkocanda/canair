"""Discover ECUs by sweeping a range of TX addresses."""

import asyncio
import json
import sys
from typing import TextIO

from canair.captures.metadata import resolve_metadata
from canair.core.prompt import confirm
from canair.profile.model import Profile
from canair.render import ansi
from canair.render.glyphs import ELLIPSIS
from canair.render.paint import INDENT, Cell, Painter, column_widths, render_row
from canair.transport.stats import client_transport
from canair.uds.addressing import (
    DEFAULT_MODE,
    AddressingMode,
    functional_request_id,
    is_extended,
)
from canair.uds.services import DEFAULT_SESSION_REQUEST
from canair.yaml_edit.ecu import EcusEditError, register_ecu

from ...captures.build import build_discover_session, suggest_discover_label
from ...captures.oneshot import save_session_journaled
from ...ecus.registry import load_ecus, rx_for_tx
from ...render.discover import ABSENT, POSITIVE, PROBE_FAILED, REFUSED, status_cell
from ...render.progress import ProgressLine
from ...transport.protocol import Terminal
from ..identity.read import mode_identity

# Standard tester/source address used to form 29-bit physical request ids
# (0x18DA{target}{tester}); F1 is the ISO 15765-4 external-tester address.
DEFAULT_TESTER_ADDRESS = 0xF1


def fmt_id(can_id: int) -> str:
    """Format a CAN arbitration id: 3 hex digits for 11-bit, 8 for 29-bit."""
    return f"0x{can_id:08X}" if can_id > 0x7FF else f"0x{can_id:03X}"


def discovery_targets(
    addr_range: tuple[int, int],
    mode: AddressingMode = DEFAULT_MODE,
    tester: int = DEFAULT_TESTER_ADDRESS,
) -> list[int]:
    """Build the list of physical request (TX) arbitration ids to probe.

    For 11-bit modes the range is swept directly as arbitration ids. For 29-bit
    modes the range is interpreted as *target-address bytes* and each is formed
    into a physical diagnostic request id ``0x18DA{target}{tester}`` (normal-fixed
    convention) — so ``--range 00-FF`` sweeps every ECU address.

    The functional id (``0x7DF``, inside the default 11-bit range) is left out: it
    addresses every ECU at once, the safety guard refuses the ``10 01`` probe on it,
    and ``discover --functional`` is the sweep that uses it.
    """
    start, end = addr_range
    if is_extended(mode):
        ids = [0x18DA0000 | (target << 8) | tester for target in range(start, end + 1)]
    else:
        ids = list(range(start, end + 1))
    functional = functional_request_id(mode)
    return [tx_id for tx_id in ids if tx_id != functional]


def _alive_row(tx_id: int, rx_id: int, name: str | None, status: str, detail: str) -> list[Cell]:
    """One responding address: request and reply id, ECU, status, reply or NRC meaning."""
    mark = POSITIVE if status == "positive" else REFUSED
    ecu: Cell = (name, ansi.raw.BOLD) if name else ("NEW", ansi.raw.BOLD + ansi.raw.YELLOW)
    return [
        mark,
        (fmt_id(tx_id), ""),
        (f"RX {fmt_id(rx_id)}", ansi.raw.DIM),
        ecu,
        status_cell(status),
        (detail, ansi.raw.DIM),
    ]


async def mode_discover(
    terminal: Terminal,
    addr_range: tuple[int, int],
    verbose: bool,
    as_json: bool,
    delay: float = 0.2,
    save: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
    register: bool = False,
    dry_run: bool = False,
    identify: bool = False,
    mode: AddressingMode = DEFAULT_MODE,
    tester: int = DEFAULT_TESTER_ADDRESS,
    *,
    profile: Profile,
):
    """Sweep a range of CAN arbitration IDs to find responding ECUs.

    For each address, sets the ELM327 header and sends a default session
    request (10 01). ECUs that respond positively or with a known NRC
    are considered "alive".

    Args:
        addr_range: (start, end) inclusive. For 11-bit ``mode`` these are TX
            arbitration ids (e.g. 0x700, 0x7EF); for a 29-bit ``mode`` they are
            target-address bytes (e.g. 0x00, 0xFF) formed into
            ``0x18DA{target}{tester}`` request ids.
        delay: Seconds to wait between addresses to avoid overwhelming
            the WiCAN WebSocket. Default 0.2s.
        mode: CAN addressing mode (from the profile); selects 11-bit vs the
            29-bit normal-fixed diagnostic convention.
        tester: Tester/source address for 29-bit request ids (default 0xF1).
        register: Register newly-discovered ECUs as files in the profile's ecus/ directory.
        dry_run: With ``register``, preview additions without writing.
        identify: Run ``canair identity`` on each alive ECU after the sweep
            (skips the interactive prompt). When unset, offer interactively.
    """
    tx_ids = discovery_targets(addr_range, mode, tester)
    functional_id = functional_request_id(mode)
    if not tx_ids:
        raise ValueError(f"the range holds only the functional id {fmt_id(functional_id)}")
    total = len(tx_ids)
    start, end = addr_range

    scope = (
        f"target 0x{start:02X}..0x{end:02X} → {fmt_id(tx_ids[0])}..{fmt_id(tx_ids[-1])}"
        if is_extended(mode)
        else f"TX {fmt_id(start)}..{fmt_id(end)}"
    )
    out = sys.stderr if as_json else sys.stdout
    paint = Painter(out)

    def say(*args, **kw) -> None:
        print(*args, file=out, **kw)

    say(paint.heading("ECU discovery", f"{total} addresses"))
    settings = [
        ("range", scope),
        ("mode", mode.value),
        ("probe", "10 01 (default session request)"),
        ("delay", f"{delay}s between addresses"),
    ]
    if not is_extended(mode) and start <= functional_id <= end:
        settings.append(("skips", f"{fmt_id(functional_id)}, the functional id (see --functional)"))
    say("\n".join(paint.fields(settings)))
    say()

    alive = []
    errors = []
    progress = ProgressLine()

    for i, tx_id in enumerate(tx_ids):
        try:
            await terminal.set_header(tx_id)
            response = await terminal.send_uds(DEFAULT_SESSION_REQUEST, timeout=2.0)
        except Exception as e:
            errors.append((tx_id, str(e)))
            if verbose:
                progress.clear()
                say(f"  {paint(*PROBE_FAILED)} {fmt_id(tx_id)}: {e}")
            # On connection errors, wait longer before retrying
            await asyncio.sleep(1.0)
            continue

        if response["ok"]:
            n_bytes = len(response["bytes"])
            alive.append((tx_id, "positive", response["hex"]))
            progress.clear()
            say(
                f"  {paint(*POSITIVE)} {fmt_id(tx_id)}: OK ({n_bytes} bytes) "
                f"{paint(response['hex'], ansi.raw.DIM)}"
            )
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            # NRC means the ECU is alive but rejected the request
            alive.append((tx_id, f"NRC 0x{nrc:02X}", desc))
            progress.clear()
            say(f"  {paint(*REFUSED)} {fmt_id(tx_id)}: NRC 0x{nrc:02X} ({desc}) — ECU alive")
        else:
            # NO DATA / no response — not an ECU at this address
            if verbose:
                error = response.get("error", "no response")
                progress.clear()
                say(f"  {paint(*ABSENT)} {fmt_id(tx_id)}: {error}")

        # Progress indicator (non-verbose)
        if not verbose and not response["ok"] and response.get("nrc") is None:
            done = i + 1
            if done % 16 == 0 or done == total:
                pct = done / total * 100
                progress.update(f"  {ELLIPSIS} {done}/{total} ({pct:.0f}%)")

        # Pace requests to avoid overwhelming the WiCAN
        await asyncio.sleep(delay)

    progress.clear()
    say(paint.heading("Discovery results"))
    counts = [
        ("alive", paint(str(len(alive)), ansi.raw.GREEN if alive else "")),
        ("silent", str(total - len(alive) - len(errors))),
        ("errors", paint(str(len(errors)), ansi.raw.RED if errors else "")),
    ]
    say("\n".join(paint.fields(counts)))

    # Each responder's RX is resolved exactly once, so the printed table, the
    # --json document and the saved capture cannot disagree about it.
    ecus = load_ecus(profile=profile)
    rx_of = {tx_id: rx_for_tx(tx_id, ecus, mode=mode, profile=profile) for tx_id, _, _ in alive}
    names = {tx: name for tx, _, _, name in _classify_alive(alive, profile=profile)[0]}

    if alive:
        n_new = len(alive) - len(names)
        say(paint.heading("Responding addresses"))
        rows = [_alive_row(tx_id, rx_of[tx_id], names.get(tx_id), s, d) for tx_id, s, d in alive]
        widths = column_widths(rows)
        for row in rows:
            say(render_row(row, widths, paint))
        say(f"\n  Cross-reference: {len(names)} known, {n_new} new (not in ECU registry).")

    if errors:
        say(paint.heading("Errors"))
        for tx_id, err in errors:
            say(f"{INDENT}{fmt_id(tx_id)}  {paint(err, ansi.raw.RED)}")

    if as_json:
        doc = {
            "range": f"{fmt_id(tx_ids[0])}-{fmt_id(tx_ids[-1])}",
            "mode": mode.value,
            "alive": [
                {
                    "tx": fmt_id(t),
                    "rx": fmt_id(rx_of[t]),
                    "status": s,
                    "detail": d,
                    "name": names.get(t),
                    "known": t in names,
                }
                for t, s, d in alive
            ],
            "errors": [{"tx": fmt_id(t), "error": e} for t, e in errors],
        }
        print(json.dumps(doc, indent=2))

    # Auto-save to captures
    if save and alive:
        enriched = []
        for tx_id_val, status, detail in alive:
            name = names.get(tx_id_val)
            ecu_label = f"{name} ({status})" if name else status
            enriched.append((tx_id_val, rx_of[tx_id_val], ecu_label, detail))

        silent_count = total - len(alive) - len(errors)
        suggested = suggest_discover_label(addr_range)
        meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
        if meta:
            label, vehicle_states, notes = meta
            session_dict = build_discover_session(
                alive=enriched,
                silent_count=silent_count,
                error_count=len(errors),
                addr_range=addr_range,
                label=label,
                vehicle_states=vehicle_states,
                notes=notes,
                transport=client_transport(terminal),
            )
            save_session_journaled(session_dict, profile=profile)

    # Auto-register newly-discovered ECUs into ecus/
    if register:
        _register_discovered(alive, dry_run=dry_run, out=out, profile=profile)

    # Offer to identify the discovered ECUs (the natural next RE step).
    await _offer_identify(terminal, alive, identify, as_json, profile=profile)

    say()


def _classify_alive(
    alive: list[tuple[int, str, str]], *, profile: Profile
) -> tuple[list[tuple[int, str, str, str]], list[tuple[int, str, str, None]]]:
    """Split alive probes into (known, new) against the profile's ECU registry.

    ``known`` items carry the registered ECU name; ``new`` items are addresses
    with no ``ecus/`` entry yet. TX ids are matched against the registry, which
    is keyed by TX-id int.
    """
    ecus = load_ecus(profile=profile)
    known: list[tuple[int, str, str, str]] = []
    new: list[tuple[int, str, str, None]] = []
    for tx, status, detail in alive:
        entry = ecus.get(tx)
        if entry:
            known.append((tx, status, detail, entry["name"]))
        else:
            new.append((tx, status, detail, None))
    return known, new


async def _offer_identify(
    terminal: Terminal,
    alive: list[tuple[int, str, str]],
    identify: bool,
    as_json: bool,
    *,
    profile: Profile,
) -> None:
    """Offer to run ``canair identity`` on each alive ECU as the next RE step.

    Runs unconditionally when ``identify`` is set (the ``--identify`` flag);
    otherwise asks on a terminal. Skipped for ``--json`` output and non-interactive stdin: the
    offer is optional, so a script without a terminal is not asked (nor refused). Declining
    leaves the finished sweep's exit status alone.
    """
    if not alive or as_json:
        return

    if not identify and not sys.stdin.isatty():
        return
    if not confirm(
        f"Identify {len(alive)} discovered ECU(s) now (read identity DIDs via UDS/KWP2000)?",
        yes=identify,
    ):
        return

    for tx_id, _status, _detail in alive:
        await mode_identity(
            terminal, tx_id, session=False, wake=False, as_json=False, profile=profile
        )


def _register_discovered(
    alive: list[tuple[int, str, str]],
    dry_run: bool = False,
    via: str = "10 01",
    out: TextIO | None = None,
    *,
    profile: Profile,
) -> None:
    """Register alive TX ids that don't yet have an ``ecus/<name>.yaml`` file.

    New entries get a placeholder ``Unknown-<TX>`` name and a provenance note;
    existing entries are left untouched. ``id_protocol`` is intentionally left
    unset — a 10 01 probe cannot distinguish UDS from KWP2000 (that's the job of
    ``canair identity``). ``via`` names the probe in that note.
    """
    from datetime import date

    out = out or sys.stdout
    paint = Painter(out)

    def say(*args) -> None:
        print(*args, file=out)

    say(paint.heading("Register"))
    if not alive:
        say("  No alive ECUs to register.")
        return

    known = set(load_ecus(profile=profile).keys())

    new = [(tx, status) for tx, status, _ in alive if tx not in known]
    if not new:
        say(f"  All {len(alive)} alive ECU(s) already registered.")
        return

    if dry_run:
        say(f"  Would register {len(new)} new ECU(s) ({len(alive) - len(new)} already known):")
        for tx, status in new:
            say(f"{INDENT}{fmt_id(tx)} -> Unknown-{tx:03X}  {paint(f'({status})', ansi.raw.DIM)}")
        say(paint("  (dry-run — nothing written)", ansi.raw.DIM))
        return

    today = date.today().isoformat()
    added = 0
    for tx, status in new:
        try:
            register_ecu(tx, notes=f"Discovered {today} via {via} ({status}).", profile=profile)
            added += 1
            say(
                f"{INDENT}{paint(*POSITIVE)} {fmt_id(tx)} -> Unknown-{tx:03X}  "
                f"{paint(f'({status})', ansi.raw.DIM)}"
            )
        except EcusEditError as e:
            say(f"{INDENT}{paint(*PROBE_FAILED)} {fmt_id(tx)}: {e}")
    say(f"  Registered {added} new ECU(s); {len(alive) - len(new)} already known.")
    say("  Next: name them, then run `canair identity <ecu>` to fill in metadata.")
