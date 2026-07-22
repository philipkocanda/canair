"""Parametrized discovery-scan engine shared by the safe UDS/KWP2000 scanners.

The IOControl (UDS ``0x2F``), RoutineControl (UDS ``0x31``) and KWP2000 IOControl
(``0x30``) discovery scanners all share the same loop: probe an id range with a
*side-effect-free* sub-function, classify each response, record hits, upgrade to an
extended session on the first ``NRC 0x7F``, persist progress for resume, and write
hits back to ``ecus/<ecu>.yaml``. Only the request layout, id width, NRC
classification and writeback section differ.

Those deltas live in a :class:`DiscoveryProbe` config; this module owns the one
loop. Each concrete scanner (``modes/iocontrol_scan.py`` etc.) builds a
``DiscoveryProbe`` and delegates its ``mode_*`` entrypoint to
:func:`mode_discovery_scan`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..scan_state import ScanStateWriter
from ..terminal import WiCANTerminal


@dataclass(frozen=True)
class DiscoveryProbe:
    """Everything that distinguishes one discovery scanner from another."""

    name: str  # human title for headers/summary, e.g. "IOControl (0x2F)"
    scan_type: str  # ScanStateWriter type / aborted-scan key, e.g. "iocontrol"
    id_label: str  # "DID" / "RID" / "LID"
    id_width: int  # id size in bytes: 1 (KWP LID) or 2 (UDS DID/RID)
    service: int  # request SID (for the "service not supported" abort message)
    # Send a single probe for ``id_`` (safe sub-function baked in) and return the
    # parsed response dict. Owns request layout + echo validation.
    probe: Callable[[WiCANTerminal, int], Awaitable[dict]]
    # Map a response to (category, nrc). Categories consumed by the loop:
    # "positive" | "exists" | "absent" | "service-absent" | "wrong-session" | "error".
    classify: Callable[[dict], tuple[str, int | None]]
    # Build the scanner-specific hit NamedTuple.
    make_hit: Callable[..., object]
    # Human-readable request preview for the progress line, e.g. "2F 00A0 00".
    request_display: Callable[[int], str]
    # Persist a single hit incrementally (survives disconnects). None = don't write.
    write_hit: Callable[[str, object], None] | None = None

    def id_fmt(self) -> str:
        """Zero-padded hex format spec matching the id width (e.g. ``04X``)."""
        return f"0{self.id_width * 2}X"


async def scan_ecu(
    terminal: WiCANTerminal,
    probe: DiscoveryProbe,
    ecu_name: str,
    tx_id: int,
    id_range: tuple[int, int],
    throttle_ms: int = 150,
    verbose: bool = False,
    session: bool = False,
    wake: bool = False,
    session_mode: str = "03",
) -> list:
    """Scan one ECU over ``id_range`` (inclusive) using ``probe``.

    If ``session`` or ``wake`` is set, a diagnostic session (``session_mode``,
    default ``03`` = UDS extended; ``81`` = KWP2000 standard) is opened up front and
    kept alive; otherwise the session is established lazily on the first ``NRC 0x7F``.
    Persists progress via :class:`ScanStateWriter` so an interrupted scan can be
    resumed. Returns the list of hits (scanner-specific NamedTuples).
    """
    start, end = id_range
    total = end - start + 1
    fmt = probe.id_fmt()

    print(
        f"\n  [{ecu_name} @ 0x{tx_id:03X}] {probe.name}: scanning "
        f"{probe.id_label}s 0x{start:{fmt}}..0x{end:{fmt}} ({total})"
    )
    await terminal.set_header(tx_id)

    hits: list = []
    absent = 0
    errors = 0
    tester_task: asyncio.Task | None = None
    in_extended = False

    # Open the requested session up front (e.g. KWP2000 10 81 on the BMS, which
    # rejects the lazy 10 03 escalation) and keep it alive for the whole sweep.
    if session or wake:
        _, tester_task = await terminal.enter_extended_session(wake=wake, mode=session_mode)
        in_extended = True

    state = ScanStateWriter(probe.scan_type, ecu_name, tx_id, start, end)
    state.open()
    try:
        for id_ in range(start, end + 1):
            idx = id_ - start + 1
            pct = idx / total * 100
            print(
                f"    [{idx}/{total} {pct:.0f}%] probing 0x{id_:{fmt}}  "
                f"({probe.request_display(id_)})" + " " * 4,
                end="\r",
                file=sys.stderr,
            )

            response = await probe.probe(terminal, id_)
            category, nrc = probe.classify(response)

            if category == "service-absent":
                print(
                    f"  [{ecu_name}] NRC 0x{nrc:02X} — ECU doesn't implement "
                    f"service 0x{probe.service:02X}, aborting scan"
                )
                break

            if category == "wrong-session" and not in_extended:
                if verbose:
                    print(f"    0x{id_:{fmt}}: NRC 7F — entering session (10 {session_mode})")
                _, tester_task = await terminal.enter_extended_session(
                    wake=False, mode=session_mode
                )
                in_extended = True
                response = await probe.probe(terminal, id_)
                category, nrc = probe.classify(response)

            session_label = "extended" if in_extended else "default"

            if category == "positive":
                hit = probe.make_hit(id_, session_label, response.get("hex", ""), None, None)
                hits.append(hit)
                if probe.write_hit:
                    probe.write_hit(ecu_name, hit)
                resp_hex = response.get("hex", "")
                nbytes = len(response.get("bytes", []))
                print(
                    f"    + 0x{id_:{fmt}}: positive ({nbytes} bytes)"
                    + (f"  [{resp_hex}]" if resp_hex else "")
                )
            elif category == "exists":
                desc = response.get("nrc_desc", "")
                hit = probe.make_hit(id_, session_label, "", nrc, desc)
                hits.append(hit)
                if probe.write_hit:
                    probe.write_hit(ecu_name, hit)
                print(f"    ~ 0x{id_:{fmt}}: exists (NRC 0x{nrc:02X} {desc})")
            elif category == "absent":
                absent += 1
                if verbose:
                    print(f"    - 0x{id_:{fmt}}: NRC 0x{nrc:02X}")
            else:
                errors += 1
                if verbose:
                    print(f"    ! 0x{id_:{fmt}}: {response.get('error', 'unknown')}")

            state.update(id_, hits=len(hits))

            if throttle_ms > 0:
                await asyncio.sleep(throttle_ms / 1000.0)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass

    print(" " * 60, end="\r", file=sys.stderr)
    print(f"  [{ecu_name}] done — {len(hits)} hits, {absent} absent, {errors} errors")
    state.close()
    return hits


async def mode_discovery_scan(
    terminal: WiCANTerminal,
    probe: DiscoveryProbe,
    pids_data: dict,
    ecus: list[str],
    id_range: tuple[int, int] | None = None,
    default_ranges: dict[str, list[tuple[int, int]]] | None = None,
    default_range: tuple[int, int] = (0x00, 0xFF),
    throttle_ms: int = 150,
    verbose: bool = False,
    write_yaml: bool = True,
    session: bool = False,
    wake: bool = False,
    session_mode: str = "03",
) -> dict[str, list]:
    """Run ``probe`` across one or more ECUs.

    Args:
        ecus: ECU names (case-insensitive, must exist in ``pids_data``).
        id_range: explicit (start, end) inclusive; overrides all defaults.
        default_ranges: per-ECU default ranges when ``id_range`` is None.
        default_range: fallback range for an ECU with no per-ECU default.
        write_yaml: if False, the probe's ``write_hit`` is disabled.
        session/wake/session_mode: open a diagnostic session before scanning
            (``session_mode`` default ``03`` = UDS extended; ``81`` = KWP2000
            standard, for ECUs like the BMS that reject ``10 03``).

    Returns mapping of ECU name (upper-case) → list of hits.
    """
    if not write_yaml:
        probe = dataclasses.replace(probe, write_hit=None)

    ecu_defs: dict[str, dict] = {}
    for fname, fdata in pids_data.get("ecus", {}).items():
        if isinstance(fdata, dict):
            ecu_defs[fname.upper()] = fdata

    results: dict[str, list] = {}
    for ecu in ecus:
        key = ecu.upper()
        if key not in ecu_defs:
            print(f"  WARNING: ECU {ecu!r} not in pids_data, skipping", file=sys.stderr)
            continue
        tx_id = ecu_defs[key].get("tx_id")
        if tx_id is None:
            print(f"  WARNING: ECU {ecu!r} has no tx_id, skipping", file=sys.stderr)
            continue

        if id_range is not None:
            ranges = [id_range]
        else:
            ranges = (default_ranges or {}).get(key, [default_range])

        ecu_hits: list = []
        for rng in ranges:
            ecu_hits.extend(
                await scan_ecu(
                    terminal,
                    probe,
                    ecu_name=key,
                    tx_id=tx_id,
                    id_range=rng,
                    throttle_ms=throttle_ms,
                    verbose=verbose,
                    session=session,
                    wake=wake,
                    session_mode=session_mode,
                )
            )
        results[key] = ecu_hits
        if write_yaml and ecu_hits:
            print(f"  [{key}] {len(ecu_hits)} {probe.id_label} discoveries saved to YAML")

    print(f"\n  --- {probe.name} Discovery Scan Summary ---")
    for ecu_key, hit_list in results.items():
        positive = sum(1 for h in hit_list if getattr(h, "nrc", None) is None)
        nrc_hits = len(hit_list) - positive
        print(f"    {ecu_key}: {len(hit_list)} hits ({positive} positive, {nrc_hits} NRC)")

    return results
