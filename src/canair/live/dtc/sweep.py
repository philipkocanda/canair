"""``canair dtc --all``: read every ECU in the profile, retry the silent ones, log the sweep."""

from __future__ import annotations

import json

from canair.profile.model import Profile
from canair.render import ansi
from canair.uds.services import DEFAULT_SESSION_REQUEST

from ...dtc.snapshot_report import snapshot_lines, undefined_hints
from ...ecus.names import ecu_display
from ...transport.protocol import Terminal
from .reading import _read_one
from .scan_log import _log_scan, _print_log_result
from .snapshot_capture import report_saved
from .snapshots import SnapshotRun, ecu_pids, fetch_snapshots, wants_snapshots


def _scan_status(res: dict) -> str:
    """One-line result summary for a single ECU in an all-ECU scan."""
    if "dtcs" in res:
        n = res["count"]
        base = f"{n} DTC(s)" if n else "clean"
        return f"{base} (via {res['command']})" if res.get("note") else base
    if "nrc" in res:
        from canair.uds.nrc import nrc_abbrev

        return f"NRC {res['nrc']} {nrc_abbrev(int(res['nrc'], 16), res.get('protocol'))}"
    err = res.get("error", "error")
    return "no response" if ("NO DATA" in err or "No response" in err) else err


def _classify(res: dict) -> str:
    """Bucket a scan result: faulty / clean / nrc / no_response."""
    if res.get("count"):
        return "faulty"
    if "dtcs" in res:
        return "clean"
    if res.get("nrc") is not None:
        return "nrc"
    return "no_response"


def _scan_line(res: dict, name_w: int) -> str:
    """Colored 'ECU proto status' cell — clean is dim, no-response/faulty stand out."""
    color = {
        "faulty": ansi.YELLOW,
        "clean": ansi.DIM,
        "nrc": ansi.DIM,
        "no_response": ansi.YELLOW,
    }[_classify(res)]
    return f"{res['ecu']:<{name_w}}  {res['protocol']:<4}  {color}{_scan_status(res)}{ansi.RESET}"


async def _wake_read(terminal, tx_id, mask, protocol, verbose, timeout, *, profile: Profile):
    """Wake the ECU (1001) then re-read DTCs with a longer timeout."""
    await terminal.set_header(tx_id)
    try:
        await terminal.send_uds(DEFAULT_SESSION_REQUEST, timeout=timeout)
    except Exception:
        pass
    return await _read_one(
        terminal, tx_id, mask, protocol, verbose, timeout=timeout, profile=profile
    )


async def mode_dtc_scan_all(
    terminal: Terminal,
    mask: int = 0xFF,
    protocol: str = "auto",
    as_json: bool = False,
    verbose: bool = False,
    timeout: float = 2.0,
    retry: bool = True,
    log: bool = False,
    label: str | None = None,
    vehicle_states=None,
    snapshots: bool = True,
    pids_data: dict | None = None,
    save: bool = False,
    notes: str | None = None,
    *,
    profile: Profile,
):
    """Sweep every ECU in the profile registry and read its stored DTCs.

    Each ECU's service is auto-selected by its ``id_protocol`` (UDS 0x19 vs
    KWP2000 0x18). Reads run in the default session (no wake) — the same way the
    single-ECU reads succeed — and are strictly sequential (one connection).

    Non-responding ECUs (asleep/slow) are, by default, retried once with a wake
    (1001) and a longer timeout so a genuinely-present ECU isn't silently
    skipped; ``retry=False`` disables that.

    With ``snapshots`` (the default), the snapshots of every code a UDS ECU reported are
    read last, once every ECU's codes are in, so a slow snapshot read never delays them.
    On a TTY a keypress skips the rest of that step (:func:`_snapshot_step`).
    """
    from ...ecus.registry import load_ecus

    registry = load_ecus(profile=profile)
    tx_ids = sorted(registry)
    name_w = (
        max((len(ecu_display(t, profile=profile)) for t in tx_ids), default=12)
        if not as_json
        else 0
    )

    if not as_json:
        print(
            f"\n  Scanning {len(tx_ids)} ECUs for DTCs "
            f"(protocol={protocol}, mask=0x{mask & 0xFF:02X})…\n"
        )

    results = []
    for tx_id in tx_ids:
        res = await _read_one(
            terminal, tx_id, mask, protocol, verbose, timeout=timeout, profile=profile
        )
        results.append(res)
        if not as_json:
            print(f"  {_scan_line(res, name_w)}")

    # Retry unresponsive ECUs once (wake + longer timeout). NRC/clean/faulty are
    # real answers and left alone; only genuine no-responders are retried.
    retry_idx = [i for i, r in enumerate(results) if _classify(r) == "no_response"]
    if retry and retry_idx:
        retry_timeout = max(timeout * 3, 5.0)
        if not as_json:
            print(
                f"\n  {ansi.DIM}Retrying {len(retry_idx)} unresponsive ECU(s) "
                f"with wake + {retry_timeout:.0f}s timeout…{ansi.RESET}\n"
            )
        for i in retry_idx:
            res2 = await _wake_read(
                terminal, tx_ids[i], mask, protocol, verbose, retry_timeout, profile=profile
            )
            recovered = _classify(res2) != "no_response"
            if recovered:
                results[i] = res2
            if not as_json:
                mark = (
                    f"{ansi.GREEN}↻ recovered{ansi.RESET}"
                    if recovered
                    else f"{ansi.DIM}↻ still silent{ansi.RESET}"
                )
                print(f"  {_scan_line(results[i], name_w)}  {mark}")

    snapshot_run = None
    if snapshots and any(wants_snapshots(r) for r in results):
        snapshot_run = await _snapshot_step(terminal, results, pids_data, as_json, profile=profile)
    if save:
        report_saved(terminal, results, label, vehicle_states, notes, as_json, profile=profile)

    faulty = [r for r in results if _classify(r) == "faulty"]
    clean = [r for r in results if _classify(r) == "clean"]
    nrc = [r for r in results if _classify(r) == "nrc"]
    no_resp = [r for r in results if _classify(r) == "no_response"]
    total_codes = sum(r["count"] for r in faulty)

    # Record to the DTC history log and compute the diff vs the last full sweep.
    log_path = log_prev = log_diff = None
    if log:
        log_path, log_prev, log_diff = _log_scan(
            "all", results, label, vehicle_states, profile=profile
        )

    if as_json:
        out = {
            "scanned": len(results),
            "with_dtcs": len(faulty),
            "clean": len(clean),
            "nrc": [r["ecu"] for r in nrc],
            "no_response": [r["ecu"] for r in no_resp],
            "total_codes": total_codes,
            "results": results,
        }
        if snapshot_run is not None:
            out["snapshots"] = {
                "records_read": snapshot_run.records_read,
                "skipped": snapshot_run.skipped,
                "unsupported": {
                    f"0x{tx:03X}": reason for tx, reason in snapshot_run.refused.items()
                },
            }
        if log:
            # log_path is set by _log_scan() in the identical `if log:` block above.
            assert log_path is not None
            out["log"] = {"file": str(log_path), "diff": log_diff}
        print(json.dumps(out, indent=2))
        return

    print()
    summary = f"  Scanned {len(results)}: {len(faulty)} with DTCs, {len(clean)} clean"
    if nrc:
        summary += f", {len(nrc)} not supported"
    if no_resp:
        summary += f", {ansi.YELLOW}{len(no_resp)} no response{ansi.RESET}"
    print(summary)
    if no_resp:
        print(
            f"  {ansi.YELLOW}⚠ no response{ansi.RESET} {ansi.DIM}(skipped — asleep/unpowered?):{ansi.RESET} "
            + ", ".join(r["ecu"] for r in no_resp)
        )
    print()

    if faulty:
        print(
            f"  {ansi.YELLOW}⚠{ansi.RESET} {len(faulty)} ECU(s) with DTCs — "
            f"{total_codes} code(s) total:\n"
        )
        for r in faulty:
            print(f"  {r['ecu']} ({r['protocol']}):")
            for d in r["dtcs"]:
                flags = ", ".join(d["status_bits"]) or "raw status"
                interp = d.get("interpretation") or {}
                meaning = interp.get("meaning") or ""
                print(f"      {d['dtc']}  {d['status']}  {flags}")
                if meaning:
                    print(f"        → {meaning}")
                for line in snapshot_lines(d, "        "):
                    print(line)
        for hint in undefined_hints(faulty, profile=profile):
            print(hint)
        print()
    else:
        print(f"  ✓ No DTCs on any of the {len(results) - len(no_resp)} responding ECUs.\n")

    if log:
        _print_log_result(log_path, log_prev, log_diff, profile=profile)


async def _snapshot_step(
    terminal: Terminal,
    results: list[dict],
    pids_data: dict | None,
    as_json: bool,
    *,
    profile: Profile,
) -> SnapshotRun:
    """Read the snapshots of every code the sweep found, skippable with a keypress on a TTY."""
    from canair.core.skip_key import skip_listener

    pending = [r for r in results if wants_snapshots(r)]
    codes = sum(len(r["dtcs"]) for r in pending)
    with skip_listener(enabled=not as_json) as keys:
        run = SnapshotRun(skip=lambda: keys.pressed)
        if not as_json:
            hint = f"  {ansi.DIM}(press s or esc to skip){ansi.RESET}" if keys.active else ""
            print(f"\n  Reading snapshots for {codes} code(s) on {len(pending)} ECU(s)…{hint}\n")
        for result in pending:
            tx_id = int(result["tx"], 16)
            ecu, pids = ecu_pids(pids_data, tx_id, profile=profile)
            before = run.records_read
            await fetch_snapshots(terminal, tx_id, result, pids, ecu, run, profile=profile)
            if as_json:
                continue
            if run.skipped:
                break
            status = result.get("snapshot_status")
            if status:
                print(f"  {result['ecu']}  {ansi.DIM}no snapshots ({status['detail']}){ansi.RESET}")
            else:
                print(f"  {result['ecu']}  {run.records_read - before} snapshot record(s)")
    if run.skipped and not as_json:
        print(f"  {ansi.YELLOW}Snapshot step skipped{ansi.RESET} — the codes are unaffected.")
    return run
