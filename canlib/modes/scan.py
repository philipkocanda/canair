"""Scan a range of PIDs/DIDs."""

import asyncio
import json
import sys

from ..terminal import WiCANTerminal


async def mode_scan(
    terminal: WiCANTerminal,
    tx_id: int,
    service: int,
    pid_range: tuple[int, int],
    verbose: bool,
    as_json: bool,
    append_bytes: str = "",
    session: bool = False,
    wake: bool = False,
    save: bool = False,
    label: str | None = None,
    state: str | None = None,
    notes: str | None = None,
):
    """Scan a range of PIDs and show which respond positively.

    Args:
        append_bytes: Hex string to append after each PID (e.g. "03" for
            IOControl ShortTermAdjustment).
        session: If True, enter extended diagnostic session (10 03) before
            scanning and send periodic TesterPresent (3E 00) in the background.
        save: If True, prompt for metadata and save results to captures/.

    IMPORTANT -- scan gently and patiently:
        - Only ONE scan at a time.
        - ECUs need time to recover between requests.
        - Use a modest --range first and check results before continuing.
    """
    start, end = pid_range
    total = end - start + 1

    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"
    did_label = "DID" if wide_did else "PID"

    suffix_label = f" + suffix {append_bytes}" if append_bytes else ""
    print(
        f"\n  Scanning TX 0x{tx_id:03X}, service 0x{service:02X}, "
        f"{did_label}s 0x{start:{did_fmt}}..0x{end:{did_fmt}} ({total} {did_label}s){suffix_label}"
    )

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    print()

    positive = []
    negative = []
    errors = []

    try:
        for pid_val in range(start, end + 1):
            req = f"{service:02X}{pid_val:{did_fmt}}{append_bytes}"

            response = await terminal.send_uds(req, timeout=2.0)

            if response["ok"]:
                n_bytes = len(response["bytes"])
                positive.append((pid_val, response))
                status = f"  + 0x{pid_val:{did_fmt}}: OK ({n_bytes} bytes)"
                if verbose:
                    status += f"  {response['hex']}"
                print(status)
            elif response.get("nrc") is not None:
                nrc = response["nrc"]
                desc = response["nrc_desc"]
                negative.append((pid_val, nrc, desc))
                if verbose:
                    print(f"  - 0x{pid_val:{did_fmt}}: NRC 0x{nrc:02X} ({desc})")
            else:
                error = response.get("error", "unknown")
                errors.append((pid_val, error))
                if verbose:
                    print(f"  ! 0x{pid_val:{did_fmt}}: {error}")

            if not verbose and not response["ok"]:
                if (pid_val - start + 1) % 16 == 0:
                    pct = (pid_val - start + 1) / total * 100
                    print(
                        f"  ... {pid_val - start + 1}/{total} ({pct:.0f}%)",
                        end="\r",
                        file=sys.stderr,
                    )
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
            if verbose:
                print("  [tester] Background keepalive stopped.", file=sys.stderr)

    print("\n  --- Scan Results ----------------------------------------")
    print(f"  Positive: {len(positive)}")
    print(f"  Negative: {len(negative)}")
    print(f"  Errors:   {len(errors)}")

    if positive:
        print(f"\n  Responding {did_label}s:")
        for pid_val, resp in positive:
            n = len(resp["bytes"])
            print(
                f"    0x{pid_val:{did_fmt}} -- {n} bytes: {resp['hex'][:60]}{'...' if len(resp['hex']) > 60 else ''}"
            )

    if as_json:
        out = {
            "tx_id": f"0x{tx_id:03X}",
            "service": f"0x{service:02X}",
            "range": f"0x{start:{did_fmt}}-0x{end:{did_fmt}}",
            "append": append_bytes if append_bytes else None,
            "session": session,
            "positive": [{"did": f"0x{p:{did_fmt}}", "bytes": r["hex"]} for p, r in positive],
            "negative": [
                {"did": f"0x{p:{did_fmt}}", "nrc": f"0x{n:02X}", "desc": d} for p, n, d in negative
            ],
            "errors": [{"did": f"0x{p:{did_fmt}}", "error": e} for p, e in errors],
        }
        print(json.dumps(out, indent=2))

    # Auto-save to captures
    if save:
        from ..captures import (
            build_scan_session,
            resolve_metadata,
            save_session,
            suggest_scan_label,
        )
        from ..pids import ecu_name

        ecu = ecu_name(tx_id)
        suggested = suggest_scan_label(ecu, service, pid_range, append_bytes)
        n_pos = len(positive)
        n_neg = len(negative)
        print(f"\n  Save: {n_pos} positive, {n_neg} negative responses.")
        meta = resolve_metadata(label, state, notes, suggested_label=suggested)
        if meta:
            label, state, notes = meta
            session_dict = build_scan_session(
                ecu_name=ecu,
                tx_id=tx_id,
                service=service,
                pid_range=pid_range,
                positive=positive,
                negative=negative,
                errors=errors,
                label=label,
                state=state,
                notes=notes,
                append_bytes=append_bytes,
                session_flag=session,
            )
            save_session(session_dict)

    print()
