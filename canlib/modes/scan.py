"""Scan one or more ranges of PIDs/DIDs."""

import asyncio
import json
import sys
from collections.abc import Sequence

from ..id_ranges import IdRange, as_id_ranges, count_ids, format_id_ranges, iter_ids
from ..transport.protocol import Terminal


async def mode_scan(
    terminal: Terminal,
    tx_id: int,
    service: int,
    pid_ranges: IdRange | Sequence[IdRange],
    verbose: bool,
    as_json: bool,
    append_bytes: str = "",
    session: bool = False,
    wake: bool = False,
    save: bool = False,
    label: str | None = None,
    vehicle_states=None,
    notes: str | None = None,
):
    """Scan PID/DID ranges and show which ids respond positively.

    Args:
        pid_ranges: One ``(start, end)`` pair or several; an id covered twice is sent once.
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
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table

    from ..ecus import ecu_display, ecu_name
    from ..scan_presets import service_label

    console = Console()

    ranges = as_id_ranges(pid_ranges)
    total = count_ids(ranges)

    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"
    did_label = "DID" if wide_did else "PID"
    ranges_text = format_id_ranges(ranges, 4 if wide_did else 2)

    suffix_label = f" + suffix {append_bytes}" if append_bytes else ""

    ecu = ecu_name(tx_id)
    console.print(
        f"\n  [bold]Scanning {ecu_display(tx_id)}[/bold] — {service_label(service)}, "
        f"{did_label}s {ranges_text} "
        f"([cyan]{total}[/cyan] {did_label}s){suffix_label}"
    )

    await terminal.set_header(tx_id)

    tester_task = None
    if session:
        _, tester_task = await terminal.enter_extended_session(wake=wake)

    positive = []
    negative = []
    errors = []

    is_tty = sys.stdout.isatty()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[green]{task.fields[hits]} hit(s)[/green]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not is_tty,
    )

    try:
        with progress:
            task = progress.add_task(f"Scanning {ecu}", total=total, hits=0)
            for pid_val in iter_ids(ranges):
                req = f"{service:02X}{pid_val:{did_fmt}}{append_bytes}"

                response = await terminal.send_uds(req, timeout=2.0)

                if response["ok"]:
                    n_bytes = len(response["bytes"])
                    positive.append((pid_val, response))
                    line = (
                        f"  [green]+[/green] 0x{pid_val:{did_fmt}}: "
                        f"OK ([cyan]{n_bytes}[/cyan] bytes)"
                    )
                    if verbose:
                        line += f"  [dim]{response['hex']}[/dim]"
                    progress.console.print(line)
                    progress.update(task, hits=len(positive))
                elif response.get("nrc") is not None:
                    nrc = response["nrc"]
                    desc = response["nrc_desc"]
                    negative.append((pid_val, nrc, desc))
                    if verbose:
                        svc_note = ""
                        nrc_svc = response.get("nrc_service")
                        if nrc_svc is not None and nrc_svc != service:
                            from canlib.uds.services import service_name

                            named = service_name(nrc_svc)
                            svc_note = (
                                f" [dim](rejecting {named or 'service'} 0x{nrc_svc:02X})[/dim]"
                            )
                        progress.console.print(
                            f"  [dim]- 0x{pid_val:{did_fmt}}: NRC 0x{nrc:02X} ({desc})[/dim]"
                            + svc_note
                        )
                else:
                    error = response.get("error", "unknown")
                    errors.append((pid_val, error))
                    if verbose:
                        progress.console.print(
                            f"  [yellow]! 0x{pid_val:{did_fmt}}: {error}[/yellow]"
                        )

                progress.advance(task)
    finally:
        if tester_task:
            tester_task.cancel()
            try:
                await tester_task
            except asyncio.CancelledError:
                pass
            if verbose:
                console.print("  [dim][tester] Background keepalive stopped.[/dim]")

    # --- Results summary ---
    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("ECU", ecu_display(tx_id))
    summary.add_row("Positive", f"[green]{len(positive)}[/green]")
    summary.add_row("Negative", str(len(negative)))
    summary.add_row("Errors", f"[yellow]{len(errors)}[/yellow]" if errors else "0")
    console.print("\n  [bold]Scan results[/bold]")
    console.print(summary)

    if positive:
        hits = Table(title=f"Responding {did_label}s", title_justify="left")
        hits.add_column(did_label, style="bold green")
        hits.add_column("Bytes", justify="right", style="cyan")
        hits.add_column("Payload", style="dim", overflow="fold")
        for pid_val, resp in positive:
            n = len(resp["bytes"])
            hexstr = resp["hex"]
            shown = f"{hexstr[:60]}…" if len(hexstr) > 60 else hexstr
            hits.add_row(f"0x{pid_val:{did_fmt}}", str(n), shown)
        console.print(hits)

    if as_json:
        out = {
            "ecu": ecu,
            "tx_id": f"0x{tx_id:03X}",
            "service": f"0x{service:02X}",
            "range": ",".join(f"0x{a:{did_fmt}}-0x{b:{did_fmt}}" for a, b in ranges),
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
        from canlib.transport.stats import client_transport

        from ..captures import (
            build_scan_session,
            resolve_metadata,
            save_session_journaled,
            suggest_scan_label,
        )
        from ..ecus import rx_addr_str

        suggested = suggest_scan_label(ecu, service, ranges, append_bytes)
        n_pos = len(positive)
        n_neg = len(negative)
        console.print(f"\n  Save: {n_pos} positive, {n_neg} negative responses.")
        meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
        if meta:
            label, vehicle_states, notes = meta
            session_dict = build_scan_session(
                ecu_ref=rx_addr_str(tx_id),
                tx_id=tx_id,
                service=service,
                pid_range=ranges,
                positive=positive,
                negative=negative,
                errors=errors,
                label=label,
                vehicle_states=vehicle_states,
                notes=notes,
                append_bytes=append_bytes,
                session_flag=session,
                transport=client_transport(terminal),
            )
            save_session_journaled(session_dict)

    _print_next_steps(console, ecu, service, positive, negative, errors, saved=save)


def _print_next_steps(console, ecu, service, positive, negative, errors, saved):
    """Suggest sensible follow-up commands based on what the scan found."""
    hints: list[str] = []
    if positive:
        first = (
            f"0x{positive[0][0]:04X}"
            if service in (0x22, 0x2F, 0x31)
            else f"0x{positive[0][0]:02X}"
        )
        if not saved:
            hints.append(
                f'Record these hits:   [cyan]canair scan {ecu} … --save --label "…"[/cyan]'
            )
        hints.append(
            f"Inspect a payload:   [cyan]canair captures {ecu} {first.removeprefix('0x')}[/cyan]"
        )
        hints.append(
            f"Define a parameter:  [cyan]canair pids upsert-param {ecu} {first.removeprefix('0x')} NAME EXPR[/cyan]"
        )
    elif errors and not negative:
        hints.append("All requests errored — the ECU may be asleep or need --session/--wake.")
        hints.append("Try a smaller --range, or check the car state (ACC/ignition on).")
    elif not positive:
        hints.append("No positive responses. Try a different --service or --range,")
        hints.append("or run [cyan]canair discover[/cyan] to confirm the ECU is alive.")

    if hints:
        console.print("\n  [bold]Next steps[/bold]")
        for h in hints:
            console.print(f"    • {h}")
    console.print()
