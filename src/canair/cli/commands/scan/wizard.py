"""The interactive wizard of ``canair scan range``."""

from __future__ import annotations

from canair.core.prompt import confirm
from canair.live.scan.presets import (
    SERVICE_PRESETS,
    ScanPlan,
    ServiceError,
    is_wide_service,
    plan_scan,
    preset_by_service,
    resolve_service,
)
from canair.render import messages
from canair.render.console import console, escape
from canair.render.glyphs import WARN

from .plan import fmt_ranges


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt from None
    if not raw and default is not None:
        return default
    return raw


def _ask(question: str, plan: ScanPlan | None, field: str) -> bool:
    """A wizard yes/no whose default is no; the smart plan's advice is part of the question."""
    advice = " (recommended for this ECU)" if plan is not None and getattr(plan, field) else ""
    return _prompt(f"{question}{advice} [y/N]").lower().startswith("y")


def _ecu_choices() -> list[tuple[str, int, str]]:
    """Return [(name, tx_id, description)] for ECUs known to the profile."""
    from canair.ecus.pids import load_pids
    from canair.ecus.registry import load_ecus

    try:
        ecus = load_ecus()
    except Exception:
        ecus = {}
    try:
        pids = load_pids()
    except Exception:
        pids = {}

    seen: dict[str, tuple[str, int, str]] = {}
    # ECUs with defined PIDs first (most actionable), then any others from ecus.yaml.
    for name, defn in (pids.get("ecus", {}) or {}).items():
        tx = defn.get("tx_id")
        if tx is None:
            continue
        desc = ecus.get(tx, {}).get("description", "") if ecus else ""
        seen[name.upper()] = (name, int(tx), desc)
    for tx, info in (ecus or {}).items():
        name = info.get("name")
        if not name or name.upper() in seen:
            continue
        seen[name.upper()] = (name, int(tx), info.get("description", ""))
    return sorted(seen.values(), key=lambda t: t[0])


def run_wizard(args) -> bool:
    """Interactively populate ``args`` for a scan. Returns False if cancelled."""
    from rich.table import Table

    out = console()
    out.print("\n[bold]canair scan — interactive setup[/bold]")
    out.print("[dim]Press Ctrl+C to cancel at any time.[/dim]\n")

    try:
        # 1. ECU
        if args.tx is None:
            choices = _ecu_choices()
            if choices:
                table = Table(title="ECUs in the active profile", title_justify="left")
                table.add_column("#", justify="right", style="dim")
                table.add_column("ECU", style="bold cyan")
                table.add_column("TX")
                table.add_column("Description")
                for i, (name, tx, desc) in enumerate(choices, 1):
                    table.add_row(str(i), name, f"0x{tx:03X}", desc or "")
                out.print(table)
                sel = _prompt("Select an ECU (number, name, or hex TX id)")
                args.tx = resolve_ecu_selection(sel, choices)
            else:
                args.tx = _prompt("ECU name or hex TX id")
            if not args.tx:
                messages.note("no ECU selected — cancelled")
                return False

        # Compute the smart plan to seed defaults.
        try:
            plan = plan_scan(args.tx)
        except Exception:
            plan = None

        # 2. Service
        out.print()
        stable = Table(title="Services", title_justify="left")
        stable.add_column("name", style="bold cyan")
        stable.add_column("hex")
        stable.add_column("description")
        for p in SERVICE_PRESETS:
            note = f"  [yellow]{WARN}[/yellow] {escape(p.caution)}" if p.caution else ""
            stable.add_row(p.name, f"0x{p.service:02X}", escape(p.summary) + note)
        out.print(stable)
        default_service = plan.service_name if plan and plan.service_name else "live-data"
        svc_in = _prompt("Service (preset name or hex byte)", default_service)
        try:
            service_int, _preset_name = resolve_service(svc_in)
        except ServiceError as e:
            messages.error(str(e))
            return False
        args.service = f"{service_int:02X}"
        wide = is_wide_service(service_int)

        # 3. Range
        if plan is not None and plan.service == service_int:
            default_range = fmt_ranges(plan.ranges, wide)
        else:
            preset = preset_by_service(service_int)
            default_range = preset.default_range if preset else ("0000-00FF" if wide else "01-FF")
        args.range = _prompt("Range (START-END[,...], hex)", default_range)

        # 4. Session / wake: the plan's advice goes in the question, the answer defaults to no.
        out.print()
        args.session = bool(args.session) or _ask(
            "Enter extended session (10 03)?", plan, "session"
        )
        args.wake = bool(args.wake) or _ask("Wake ECU from deep sleep first (10 01)?", plan, "wake")

        # 5. Confirm — show the equivalent one-liner so the user learns it.
        out.print()
        out.print("[bold]Ready to scan:[/bold]  " + escape(equiv_command(args)))
        if not confirm("Run this scan now?"):
            return False
    except KeyboardInterrupt:
        messages.note("cancelled")
        return False

    return True


def resolve_ecu_selection(sel: str, choices: list[tuple[str, int, str]]) -> str:
    """Map a wizard ECU selection (number/name/hex) to an ECU token."""
    sel = sel.strip()
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    return sel


def equiv_command(args) -> str:
    parts = [
        "canair scan range",
        str(args.tx),
        f"--service {args.service}",
        f"--range {args.range}",
    ]
    if args.append:
        parts.append(f"--append {args.append}")
    if args.session:
        parts.append("--session")
    if args.wake:
        parts.append("--wake")
    return " ".join(parts)
