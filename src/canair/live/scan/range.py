"""Scan one or more ranges of PIDs/DIDs.

A range scan sends whatever service it is given, so it can sweep a service that changes the
car's state: UDS IOControl or RoutineControl, KWP2000 IOControl, an OBD-II DTC clear. Before it
sends one, :func:`confirm_actuating_scan` asks, judged request by request by the
read/release/actuate classifier (:func:`canair.safety.actuation.classify`). A request the
blocklist refuses anyway is not asked about.

RoutineControl (``0x31``) is refused outright on a KWP2000 ECU (:func:`check_range_scan`): there
it is StartRoutineByLocalIdentifier, so ``31 F0 00`` to ``31 F0 FF`` would start routine
``0xF0`` 256 times. ``canair scan routines`` reads a KWP2000 routine's results with ``0x33``.
"""

import json
from collections.abc import Sequence

from canair.captures.metadata import resolve_metadata
from canair.core.id_ranges import IdRange, as_id_ranges, count_ids, format_id_ranges, iter_ids
from canair.core.prompt import confirm
from canair.live.scan.presets import preset_by_service, service_label
from canair.privacy.sensitive import MASK, profile_set
from canair.profile.model import Profile
from canair.render import messages
from canair.render.console import console as make_console
from canair.render.console import escape
from canair.render.glyphs import ELLIPSIS
from canair.safety.actuation import ACTUATE, classify
from canair.safety.blocklist import SafetyBlocked, check_command_safety
from canair.transport.stats import client_transport
from canair.uds.echo import request_echo
from canair.uds.protocol import KWP, DiagProtocol
from canair.uds.services import ROUTINE_CONTROL, identifier_width

from ...captures.build import build_scan_session, suggest_scan_label
from ...captures.oneshot import save_session_journaled
from ...ecus.names import ecu_display, ecu_name
from ...ecus.registry import rx_addr_str
from ...transport.protocol import Terminal
from ..hold import held_session


def scan_request(service: int, pid_val: int, append_bytes: str = "") -> str:
    """The request a range scan sends for one id: ``{service}{id}{append}``, as hex text."""
    width = 4 if identifier_width(service) == 2 else 2
    return f"{service:02X}{pid_val:0{width}X}{append_bytes}"


def check_range_scan(service: int, protocol: DiagProtocol, ecu: str) -> None:
    """Raise :class:`SafetyBlocked` for RoutineControl to a KWP2000 ECU, where it starts one."""
    if service == ROUTINE_CONTROL and protocol == KWP:
        raise SafetyBlocked(
            f"{ecu} speaks KWP2000, where 0x31 is StartRoutineByLocalIdentifier: every request "
            "of this scan would start a routine\n"
            f"Read a KWP2000 routine's results with `canair scan routines {ecu}` (service 0x33)"
        )


def actuating_requests(
    terminal: Terminal, tx_id: int, service: int, ranges: Sequence[IdRange], append_bytes: str
) -> list[str]:
    """The requests of a scan that would change the car's state and that the blocklist allows."""
    policy = terminal.session_policy
    protocol = policy.protocol(tx_id)
    out = []
    for pid_val in iter_ids(ranges):
        req = scan_request(service, pid_val, append_bytes)
        if classify(req, protocol) == ACTUATE and check_command_safety(req, policy, tx_id) is None:
            out.append(req)
    return out


def confirm_actuating_scan(
    terminal: Terminal,
    tx_id: int,
    service: int,
    pid_ranges: IdRange | Sequence[IdRange],
    append_bytes: str = "",
    *,
    yes: bool = False,
    flag: str = "--yes",
    profile: Profile,
) -> bool:
    """Refuse or ask before a scan that can change the car's state; True when it may run.

    Raises :class:`SafetyBlocked` (:func:`check_range_scan`), and
    :class:`~canair.core.prompt.PromptUnavailable` when a question has no terminal and no
    ``yes``; ``flag`` is what its message tells the user to pass. A scan that only reads asks
    nothing.
    """
    ecu = ecu_name(tx_id, profile=profile)
    check_range_scan(service, terminal.session_policy.protocol(tx_id), ecu)
    ranges = as_id_ranges(pid_ranges)
    risky = actuating_requests(terminal, tx_id, service, ranges, append_bytes)
    if not risky:
        return True
    lines = [
        f"{len(risky)} of the {count_ids(ranges)} requests of this {service_label(service)} scan "
        f"can change the state of {ecu} (the first is {risky[0]})",
        "`canair scan iocontrol` and `canair scan routines` send only read-only probes",
    ]
    preset = preset_by_service(service)
    if preset is not None and preset.caution:
        lines.append(preset.caution)
    messages.warning("\n".join(lines))
    return confirm("Send them?", yes=yes, flag=flag, danger=True)


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
    *,
    profile: Profile,
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
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table

    # The human report is the result, so it goes to stdout; with --json it goes to stderr.
    console = make_console(stderr=as_json)

    ecu_label = ecu_name(tx_id, profile=profile)
    protocol = terminal.session_policy.protocol(tx_id)
    check_range_scan(service, protocol, ecu_label)
    ranges = as_id_ranges(pid_ranges)
    total = count_ids(ranges)

    wide_did = identifier_width(service) == 2
    did_fmt = "04X" if wide_did else "02X"
    did_label = "DID" if wide_did else "PID"
    ranges_text = format_id_ranges(ranges, 4 if wide_did else 2)

    suffix_label = f" + suffix {append_bytes}" if append_bytes else ""

    ecu = ecu_name(tx_id, profile=profile)
    console.print(
        f"\n  [bold cyan]{escape(ecu_display(tx_id, profile=profile))}[/bold cyan] — "
        f"scanning {escape(service_label(service))}, {did_label}s {escape(ranges_text)} "
        f"({total} {did_label}s){escape(suffix_label)}"
    )

    await terminal.set_header(tx_id)

    positive = []
    negative = []
    errors = []
    sensitive = profile_set(profile=profile)

    is_tty = console.is_terminal

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

    async with held_session(terminal, tx_id, profile=profile, enabled=session, wake=wake):
        with progress:
            task = progress.add_task(f"Scanning {ecu}", total=total, hits=0)
            for pid_val in iter_ids(ranges):
                req = scan_request(service, pid_val, append_bytes)

                # Checked by service and echo, so a late reply to the previous id is not
                # counted as a hit (or a refusal) of this one.
                expected = request_echo(req, protocol)
                response = await terminal.send_uds(
                    req,
                    timeout=2.0,
                    expected_sid=service,
                    expected_echo=expected[1] if expected is not None else None,
                )

                if response["ok"]:
                    n_bytes = len(response["bytes"])
                    if sensitive.has_request(tx_id, req):
                        # The hit is reported and recorded, its bytes never are.
                        response["hex"] = MASK
                    positive.append((pid_val, response))
                    line = f"  [green]+[/green] 0x{pid_val:{did_fmt}}: OK ({n_bytes} bytes)"
                    if verbose:
                        line += f"  [dim]{escape(response['hex'])}[/dim]"
                    progress.console.print(line)
                    progress.update(task, hits=len(positive))
                elif response.get("nrc") is not None:
                    nrc = response["nrc"]
                    desc = response["nrc_desc"]
                    negative.append((pid_val, nrc, desc))
                    if verbose:
                        progress.console.print(
                            f"  [dim]- 0x{pid_val:{did_fmt}}: NRC 0x{nrc:02X} ({escape(desc)})[/dim]"
                        )
                else:
                    error = response.get("error", "unknown")
                    errors.append((pid_val, error))
                    if verbose:
                        progress.console.print(
                            f"  [red]! 0x{pid_val:{did_fmt}}: {escape(str(error))}[/red]"
                        )

                progress.advance(task)
            if verbose:
                console.print("  [dim][tester] Background keepalive stopped.[/dim]")

    # --- Results summary ---
    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("ECU", escape(ecu_display(tx_id, profile=profile)))
    summary.add_row("Positive", f"[green]{len(positive)}[/green]")
    summary.add_row("Negative", str(len(negative)))
    summary.add_row("Errors", f"[red]{len(errors)}[/red]" if errors else "0")
    console.print("\n  [bold]Scan results[/bold]")
    console.print(summary)

    if positive:
        hits = Table(title=f"Responding {did_label}s", title_justify="left")
        hits.add_column(did_label, style="cyan")
        hits.add_column("Bytes", justify="right")
        hits.add_column("Payload", style="dim", overflow="fold")
        for pid_val, resp in positive:
            n = len(resp["bytes"])
            hexstr = resp["hex"]
            shown = f"{hexstr[:60]}{ELLIPSIS}" if len(hexstr) > 60 else hexstr
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
        suggested = suggest_scan_label(ecu, service, ranges, append_bytes)
        n_pos = len(positive)
        n_neg = len(negative)
        make_console(stderr=True).print(f"\n  Save: {n_pos} positive, {n_neg} negative responses.")
        meta = resolve_metadata(label, vehicle_states, notes, suggested_label=suggested)
        if meta:
            label, vehicle_states, notes = meta
            session_dict = build_scan_session(
                ecu_ref=rx_addr_str(tx_id, profile=profile),
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
            save_session_journaled(session_dict, profile=profile)

    _print_next_steps(
        make_console(stderr=True), ecu, service, positive, negative, errors, saved=save
    )


def _print_next_steps(console, ecu, service, positive, negative, errors, saved):
    """Suggest sensible follow-up commands based on what the scan found."""
    hints: list[str] = []
    if positive:
        first = f"0x{positive[0][0]:0{identifier_width(service) * 2 or 2}X}"
        if not saved:
            hints.append(
                f'Record these hits:   [cyan]canair scan {ecu} … --save --label "…"[/cyan]'
            )
        hints.append(
            f"Inspect a payload:   [cyan]canair captures {ecu} {first.removeprefix('0x')}[/cyan]"
        )
        hints.append(
            f"Define a signal:    [cyan]canair pids upsert-param {ecu} "
            f"{first.removeprefix('0x')} NAME EXPR[/cyan]"
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
