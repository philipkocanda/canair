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

    start, end = pid_range
    total = end - start + 1

    wide_did = service in (0x22, 0x2F, 0x31)
    did_fmt = "04X" if wide_did else "02X"
    did_label = "DID" if wide_did else "PID"

    suffix_label = f" + suffix {append_bytes}" if append_bytes else ""

    ecu = ecu_name(tx_id)
    console.print(
        f"\n  [bold]Scanning {ecu_display(tx_id)}[/bold] — {service_label(service)}, "
        f"{did_label}s 0x{start:{did_fmt}}..0x{end:{did_fmt}} "
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
            for pid_val in range(start, end + 1):
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
                            from ..uds_services import service_name

                            named = service_name(nrc_svc)
                            svc_note = (
                                f" [dim](rejecting {named or 'service'} "
                                f"0x{nrc_svc:02X})[/dim]"
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
        from ..ecus import rx_addr_str

        suggested = suggest_scan_label(ecu, service, pid_range, append_bytes)
        n_pos = len(positive)
        n_neg = len(negative)
        console.print(f"\n  Save: {n_pos} positive, {n_neg} negative responses.")
        meta = resolve_metadata(label, state, notes, suggested_label=suggested)
        if meta:
            label, state, notes = meta
            session_dict = build_scan_session(
                ecu_ref=rx_addr_str(tx_id),
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
