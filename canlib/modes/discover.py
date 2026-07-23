"""Discover ECUs by sweeping a range of TX addresses."""

import asyncio
import json
import sys

from ..terminal import WiCANTerminal


async def mode_discover(
    terminal: WiCANTerminal,
    addr_range: tuple[int, int],
    verbose: bool,
    as_json: bool,
    delay: float = 0.2,
    save: bool = False,
    label: str | None = None,
    state: str | None = None,
    notes: str | None = None,
    register: bool = False,
    dry_run: bool = False,
    identify: bool = False,
):
    """Sweep a range of CAN arbitration IDs to find responding ECUs.

    For each address, sets the ELM327 header and sends a default session
    request (10 01). ECUs that respond positively or with a known NRC
    are considered "alive".

    Args:
        addr_range: (start, end) TX IDs inclusive (e.g. 0x700, 0x7EF).
        delay: Seconds to wait between addresses to avoid overwhelming
            the WiCAN WebSocket. Default 0.2s.
        register: Register newly-discovered ECUs as files in the profile's ecus/ directory.
        dry_run: With ``register``, preview additions without writing.
        identify: Run ``canair identity`` on each alive ECU after the sweep
            (skips the interactive prompt). When unset, offer interactively.
    """
    start, end = addr_range
    total = end - start + 1

    print(
        f"\n  ECU discovery: TX 0x{start:03X}..0x{end:03X} ({total} addresses)"
        f"\n  Probe: 10 01 (default session request)"
        f"\n  Delay: {delay}s between addresses"
    )
    print()

    alive = []
    errors = []

    for i, tx_id in enumerate(range(start, end + 1)):
        try:
            await terminal.set_header(tx_id)
            response = await terminal.send_uds("1001", timeout=2.0)
        except Exception as e:
            errors.append((tx_id, str(e)))
            if verbose:
                print(f"  ! 0x{tx_id:03X}: {e}")
            # On connection errors, wait longer before retrying
            await asyncio.sleep(1.0)
            continue

        if response["ok"]:
            n_bytes = len(response["bytes"])
            alive.append((tx_id, "positive", response["hex"]))
            print(f"  + 0x{tx_id:03X}: OK ({n_bytes} bytes) {response['hex']}")
        elif response.get("nrc") is not None:
            nrc = response["nrc"]
            desc = response["nrc_desc"]
            # NRC means the ECU is alive but rejected the request
            alive.append((tx_id, f"NRC 0x{nrc:02X}", desc))
            print(f"  ~ 0x{tx_id:03X}: NRC 0x{nrc:02X} ({desc}) — ECU alive")
        else:
            # NO DATA / no response — not an ECU at this address
            if verbose:
                error = response.get("error", "no response")
                print(f"  - 0x{tx_id:03X}: {error}")

        # Progress indicator (non-verbose)
        if not verbose and not response["ok"] and response.get("nrc") is None:
            done = i + 1
            if done % 16 == 0 or done == total:
                pct = done / total * 100
                print(
                    f"  ... {done}/{total} ({pct:.0f}%)",
                    end="\r",
                    file=sys.stderr,
                )

        # Pace requests to avoid overwhelming the WiCAN
        await asyncio.sleep(delay)

    print("\n  --- Discovery Results ------------------------------------")
    print(f"  Alive:  {len(alive)}")
    print(f"  Silent: {total - len(alive) - len(errors)}")
    print(f"  Errors: {len(errors)}")

    if alive:
        known, new = _classify_alive(alive)
        names = {tx: name for tx, _, _, name in known}
        print("\n  Responding addresses:")
        for tx_id, status, detail in alive:
            rx_id = tx_id + 8
            tag = names.get(tx_id) or "NEW"
            print(f"    0x{tx_id:03X} (RX 0x{rx_id:03X}) [{tag}] — {status}: {detail}")
        print(f"\n  Cross-reference: {len(known)} known, {len(new)} new (not in ECU registry).")

    if errors:
        print("\n  Errors:")
        for tx_id, err in errors:
            print(f"    0x{tx_id:03X}: {err}")

    if as_json:
        names = {tx: name for tx, _, _, name in _classify_alive(alive)[0]}
        out = {
            "range": f"0x{start:03X}-0x{end:03X}",
            "alive": [
                {
                    "tx": f"0x{t:03X}",
                    "rx": f"0x{t + 8:03X}",
                    "status": s,
                    "detail": d,
                    "name": names.get(t),
                    "known": t in names,
                }
                for t, s, d in alive
            ],
            "errors": [{"tx": f"0x{t:03X}", "error": e} for t, e in errors],
        }
        print(json.dumps(out, indent=2))

    # Auto-save to captures
    if save and alive:
        from ..captures import (
            build_discover_session,
            resolve_metadata,
            save_session_journaled,
            suggest_discover_label,
        )

        # Enrich alive list with ECU names from the registry
        from ..ecus import ecu_name as _ecu_name

        enriched = []
        for tx_id_val, status, detail in alive:
            name = _ecu_name(tx_id_val)
            ecu_label = f"{name} ({status})" if name != f"0x{tx_id_val:03X}" else status
            enriched.append((tx_id_val, ecu_label, detail))

        silent_count = total - len(alive) - len(errors)
        suggested = suggest_discover_label(addr_range)
        meta = resolve_metadata(label, state, notes, suggested_label=suggested)
        if meta:
            label, state, notes = meta
            session_dict = build_discover_session(
                alive=enriched,
                silent_count=silent_count,
                error_count=len(errors),
                addr_range=addr_range,
                label=label,
                state=state,
                notes=notes,
            )
            save_session_journaled(session_dict)

    # Auto-register newly-discovered ECUs into ecus/
    if register:
        _register_discovered(alive, dry_run=dry_run)

    # Offer to identify the discovered ECUs (the natural next RE step).
    await _offer_identify(terminal, alive, identify, as_json)

    print()


def _classify_alive(
    alive: list[tuple[int, str, str]],
) -> tuple[list[tuple[int, str, str, str]], list[tuple[int, str, str, None]]]:
    """Split alive probes into (known, new) against the profile's ECU registry.

    ``known`` items carry the registered ECU name; ``new`` items are addresses
    with no ``ecus/`` entry yet. TX ids are matched against the registry, which
    is keyed by TX-id int.
    """
    from ..ecus import load_ecus

    ecus = load_ecus()
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
    terminal: WiCANTerminal,
    alive: list[tuple[int, str, str]],
    identify: bool,
    as_json: bool,
) -> None:
    """Offer to run ``canair identity`` on each alive ECU as the next RE step.

    Runs unconditionally when ``identify`` is set (the ``--identify`` flag);
    otherwise prompts interactively on a TTY. Skipped for ``--json`` output and
    non-interactive stdin so scripted/piped runs stay non-blocking.
    """
    if not alive or as_json:
        return

    run = identify
    if not run:
        if not sys.stdin.isatty():
            return
        print(
            f"\n  Identify {len(alive)} discovered ECU(s) now "
            "(read identity DIDs via UDS/KWP2000)? [y/N] ",
            end="",
            flush=True,
        )
        answer = sys.stdin.readline().strip().lower()
        run = answer in ("y", "yes")
    if not run:
        return

    # Local import avoids a module-load cycle (identity imports from ecus/decode).
    from .identity import mode_identity

    for tx_id, _status, _detail in alive:
        await mode_identity(terminal, tx_id, session=False, wake=False, as_json=False)


def _register_discovered(alive: list[tuple[int, str, str]], dry_run: bool = False) -> None:
    """Register alive TX ids that don't yet have an ``ecus/<name>.yaml`` file.

    New entries get a placeholder ``Unknown-<TX>`` name and a provenance note;
    existing entries are left untouched. ``id_protocol`` is intentionally left
    unset — a 10 01 probe cannot distinguish UDS from KWP2000 (that's the job of
    ``canair identity``).
    """
    from datetime import date

    from ..ecus import load_ecus

    print("\n  --- Register ---------------------------------------------")
    if not alive:
        print("  No alive ECUs to register.")
        return

    known = set(load_ecus().keys())

    new = [(tx, status) for tx, status, _ in alive if tx not in known]
    if not new:
        print(f"  All {len(alive)} alive ECU(s) already registered.")
        return

    if dry_run:
        print(f"  Would register {len(new)} new ECU(s) ({len(alive) - len(new)} already known):")
        for tx, status in new:
            print(f"    0x{tx:03X} -> Unknown-{tx:03X}  ({status})")
        print("  (dry-run — nothing written)")
        return

    from ..ecus_edit import EcusEditError, register_ecu

    today = date.today().isoformat()
    added = 0
    for tx, status in new:
        try:
            register_ecu(tx, notes=f"Discovered {today} via 10 01 ({status}).")
            added += 1
            print(f"    + 0x{tx:03X} -> Unknown-{tx:03X}  ({status})")
        except EcusEditError as e:
            print(f"    ! 0x{tx:03X}: {e}")
    print(f"  Registered {added} new ECU(s); {len(alive) - len(new)} already known.")
    print("  Next: name them, then run `canair identity <ecu>` to fill in metadata.")
