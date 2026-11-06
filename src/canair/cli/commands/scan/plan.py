"""Turning the flags of ``canair scan range`` into a scan plan."""

from __future__ import annotations

import argparse

from canair.core.id_ranges import format_id_ranges, parse_id_ranges
from canair.live.scan.presets import (
    FAMILY_SIZE,
    ScanPlan,
    ServiceError,
    is_wide_service,
    plan_scan,
    preset_by_service,
    resolve_service,
    service_label,
)
from canair.profile import active
from canair.render import messages
from canair.render.console import console, escape


def slots(value: str) -> int:
    try:
        n = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--slots must be a number, got {value!r}") from None
    if not 1 <= n <= FAMILY_SIZE:
        raise argparse.ArgumentTypeError(f"--slots must be 1-{FAMILY_SIZE}, got {n}")
    return n


def fmt_ranges(ranges, wide: bool) -> str:
    return format_id_ranges(ranges, 4 if wide else 2)


def resolve_plan(args) -> None:
    """Populate ``args.service`` (hex str) and ``args.range`` ('START-END').

    Applies smart per-ECU defaults when the user left ``--service``/``--range``
    unset, and resolves friendly service preset names to hex. Prints a short
    summary of the resolved plan so the user learns the underlying command.
    """
    auto = args.service is None and args.range is None
    slots = getattr(args, "slots", None)
    if slots is not None and args.range is not None:
        messages.fail("--slots shapes the smart default; drop it or drop --range.", code=2)
    plan: ScanPlan | None = None
    if args.service is None or args.range is None:
        try:
            plan = plan_scan(args.tx, slots=slots or FAMILY_SIZE, profile=active())
        except Exception:
            plan = None

    # --- service ---
    if args.service is None:
        if plan is not None:
            service_int = plan.service
            preset_name = plan.service_name
        else:
            service_int, preset_name = 0x21, "live-data"
    else:
        try:
            service_int, preset_name = resolve_service(args.service)
        except ServiceError as e:
            messages.fail(str(e), code=2)
    args.service = f"{service_int:02X}"
    wide = is_wide_service(service_int)

    # --- range ---
    if args.range is None:
        if plan is not None and plan.service == service_int:
            args.range = fmt_ranges(plan.ranges, wide)
        else:
            preset = preset_by_service(service_int)
            args.range = preset.default_range if preset else ("0000-00FF" if wide else "01-FF")

    # --- session/wake: only auto-apply in pure smart mode ---
    if auto and plan is not None:
        if plan.session and not args.session:
            args.session = True
        if plan.wake and not args.wake:
            args.wake = True

    try:
        parse_id_ranges(args.range)
    except ValueError as e:
        messages.fail(f"--range {args.range}: {e}", code=2)

    print_plan_summary(args, service_int, preset_name, plan if auto else None)


def print_plan_summary(args, service_int, preset_name, plan) -> None:
    out = console(stderr=True)
    svc = service_label(service_int, preset_name)
    parts = [f"[bold cyan]{escape(str(args.tx))}[/bold cyan]", f"service {escape(svc)}"]
    parts.append(f"range {escape(args.range)}")
    if args.append:
        parts.append(f"append {escape(args.append)}")
    if args.session:
        parts.append("session")
    if args.wake:
        parts.append("wake")
    out.print("  [dim]Scan plan:[/dim] " + "  |  ".join(parts))
    if plan is not None and plan.reason:
        out.print(f"  [dim]         → {escape(plan.reason)}[/dim]")
