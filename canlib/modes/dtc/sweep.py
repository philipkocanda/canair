"""``canair dtc --all``: read every ECU in the profile, retry the silent ones, log the sweep."""

from __future__ import annotations

import json

from canlib import ansi

from ...ecus import ecu_display
from ...transport.protocol import Terminal
from .reading import _read_one
from .scan_log import _log_scan, _print_log_result


def _scan_status(res: dict) -> str:
    """One-line result summary for a single ECU in an all-ECU scan."""
    if "dtcs" in res:
        n = res["count"]
        base = f"{n} DTC(s)" if n else "clean"
        return f"{base} (via {res['command']})" if res.get("note") else base
    if "nrc" in res:
        from canlib.uds.parse import nrc_abbrev

        return f"NRC {res['nrc']} {nrc_abbrev(int(res['nrc'], 16))}"
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


async def _wake_read(terminal, tx_id, mask, protocol, verbose, timeout):
    """Wake the ECU (1001) then re-read DTCs with a longer timeout."""
    await terminal.set_header(tx_id)
    try:
        await terminal.send_uds("1001", timeout=timeout)
    except Exception:
        pass
    return await _read_one(terminal, tx_id, mask, protocol, verbose, timeout=timeout)


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
):
    """Sweep every ECU in the profile registry and read its stored DTCs.

    Each ECU's service is auto-selected by its ``id_protocol`` (UDS 0x19 vs
    KWP2000 0x18). Reads run in the default session (no wake) — the same way the
    single-ECU reads succeed — and are strictly sequential (one connection).

    Non-responding ECUs (asleep/slow) are, by default, retried once with a wake
    (1001) and a longer timeout so a genuinely-present ECU isn't silently
    skipped; ``retry=False`` disables that.
    """
    from ...ecus import load_ecus

    registry = load_ecus()
    tx_ids = sorted(registry)
    name_w = max((len(ecu_display(t)) for t in tx_ids), default=12) if not as_json else 0

    if not as_json:
        print(
            f"\n  Scanning {len(tx_ids)} ECUs for DTCs "
            f"(protocol={protocol}, mask=0x{mask & 0xFF:02X})...\n"
        )

    results = []
    for tx_id in tx_ids:
        res = await _read_one(terminal, tx_id, mask, protocol, verbose, timeout=timeout)
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
                f"with wake + {retry_timeout:.0f}s timeout...{ansi.RESET}\n"
            )
        for i in retry_idx:
            res2 = await _wake_read(terminal, tx_ids[i], mask, protocol, verbose, retry_timeout)
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

    faulty = [r for r in results if _classify(r) == "faulty"]
    clean = [r for r in results if _classify(r) == "clean"]
    nrc = [r for r in results if _classify(r) == "nrc"]
    no_resp = [r for r in results if _classify(r) == "no_response"]
    total_codes = sum(r["count"] for r in faulty)

    # Record to the DTC history log and compute the diff vs the last full sweep.
    log_path = log_prev = log_diff = None
    if log:
        log_path, log_prev, log_diff = _log_scan("all", results, label, vehicle_states)

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
        print(f"  ⚠ {len(faulty)} ECU(s) with DTCs — {total_codes} code(s) total:\n")
        for r in faulty:
            print(f"  {r['ecu']} ({r['protocol']}):")
            for d in r["dtcs"]:
                flags = ", ".join(d["status_bits"]) or "raw status"
                interp = d.get("interpretation") or {}
                meaning = interp.get("meaning") or ""
                print(f"      {d['dtc']}  {d['status']}  {flags}")
                if meaning:
                    print(f"        → {meaning}")
        print()
    else:
        print(f"  ✓ No DTCs on any of the {len(results) - len(no_resp)} responding ECUs.\n")

    if log:
        _print_log_result(log_path, log_prev, log_diff)
