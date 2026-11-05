"""``canair scan range``: the general-purpose PID/DID sweep, its smart plan and its wizard."""

from __future__ import annotations

import argparse
import sys

from canair.cli.live import add_connection_args, ecu_completer, run_live
from canair.cli.live.handlers import scans
from canair.core.id_ranges import format_id_ranges, parse_id_ranges
from canair.core.prompt import confirm
from canair.live.scan_presets import (
    FAMILY_SIZE,
    SERVICE_PRESETS,
    ScanPlan,
    ServiceError,
    is_wide_service,
    plan_scan,
    preset_by_service,
    presets_help,
    resolve_service,
    service_label,
)
from canair.render import messages
from canair.render.console import console, escape
from canair.render.glyphs import WARN

# ---------------------------------------------------------------------------
# scan range — the general-purpose PID/DID sweep
# ---------------------------------------------------------------------------


def add_range_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "range",
        help="Sweep a range of PIDs/DIDs on an ECU",
        description="Scan one or more ranges of PIDs/DIDs on an ECU. One scan at a time only. "
        "Without --range, a UDS ECU's DID families are swept: every high byte the profile "
        "shows it using (its 22/2F PIDs and its IOControl DIDs), the whole family by default "
        "(--slots narrows it).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
getting started:
  canair scan range                 # interactive wizard — pick ECU/service/range
  canair scan BMS                   # smart defaults for that ECU (bare = range)
  canair scan range IGPM            # UDS ECU → read-did over its known DID families
  canair scan range PSM --slots 32  # first 32 DIDs of each family (xx00-xx1F)

"""
        + presets_help()
        + """

examples:
  canair scan range BMS --service live-data --range 01-FF
  canair scan range 7E4 --service read-did --range BC01-BC0B
  canair scan range SKM --service read-did --range B000-B01F,B100-B11F
  canair scan range IGPM --service iocontrol --range E000-E0FF --append 03 --session

tips:
  * Run ONE scan at a time — parallel scans lock up the WiCAN.
  * Start with a small --range to gauge ECU response time, then widen.
  * Add --save --label "..." to record results to captures/.
  * For SAFE actuator/routine discovery use `canair scan iocontrol`/`routines`.
""",
    )
    ecu_arg = parser.add_argument(
        "tx",
        metavar="ECU",
        nargs="?",
        default=None,
        help="ECU name or TX ID (e.g. BMS or 7E4). Omit for the interactive wizard.",
    )
    try:
        ecu_arg.completer = ecu_completer  # type: ignore[attr-defined]
    except Exception:
        pass
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Force the interactive wizard even when an ECU is given",
    )
    parser.add_argument(
        "--service",
        metavar="SVC",
        default=None,
        help="UDS service: a preset name (live-data, read-did, iocontrol, routine) "
        "or a hex byte (default: smart per-ECU)",
    )
    parser.add_argument(
        "--range",
        metavar="START-END[,...]",
        default=None,
        help="PID/DID range in hex, or a comma list of ranges (default: smart per-ECU)",
    )
    parser.add_argument(
        "--slots",
        metavar="N",
        type=_slots,
        default=None,
        help="With the smart default: sweep only the first N DIDs of each DID family "
        "(1-256, default 256 = the whole family). Can't be combined with --range.",
    )
    parser.add_argument("--append", metavar="HEX", help="Hex bytes to append after each DID")
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument(
        "--label",
        metavar="TEXT",
        default=None,
        help="Session label for --save (asked for when omitted; with no terminal the "
        "session is saved under a generated label)",
    )
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    # The wizard and plan resolution run before the shared live runtime.
    parser.set_defaults(func=run_range)
    return parser


def _slots(value: str) -> int:
    try:
        n = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--slots must be a number, got {value!r}") from None
    if not 1 <= n <= FAMILY_SIZE:
        raise argparse.ArgumentTypeError(f"--slots must be 1-{FAMILY_SIZE}, got {n}")
    return n


def _fmt_ranges(ranges, wide: bool) -> str:
    return format_id_ranges(ranges, 4 if wide else 2)


def _resolve_plan(args) -> None:
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
            plan = plan_scan(args.tx, slots=slots or FAMILY_SIZE)
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
            args.range = _fmt_ranges(plan.ranges, wide)
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

    _print_plan_summary(args, service_int, preset_name, plan if auto else None)


def _print_plan_summary(args, service_int, preset_name, plan) -> None:
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


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


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
    from canair.ecus import load_ecus
    from canair.ecus.pids import load_pids

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


def _run_wizard(args) -> bool:
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
                args.tx = _resolve_ecu_selection(sel, choices)
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
            default_range = _fmt_ranges(plan.ranges, wide)
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
        out.print("[bold]Ready to scan:[/bold]  " + escape(_equiv_command(args)))
        if not confirm("Run this scan now?"):
            return False
    except KeyboardInterrupt:
        messages.note("cancelled")
        return False

    return True


def _resolve_ecu_selection(sel: str, choices: list[tuple[str, int, str]]) -> str:
    """Map a wizard ECU selection (number/name/hex) to an ECU token."""
    sel = sel.strip()
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(choices):
            return choices[idx][0]
    return sel


def _equiv_command(args) -> str:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_range(args) -> int:
    """Resolve friendly/absent args, optionally run the wizard, then scan."""
    want_wizard = args.interactive or args.tx is None
    if want_wizard:
        if not sys.stdin.isatty():
            if args.tx is None:
                messages.error(
                    "no ECU given. Specify one (e.g. `canair scan BMS`), run "
                    "`canair scan range` in a terminal for the wizard, or `canair discover` "
                    "to find live ECUs."
                )
                return 2
            # -i without a TTY: fall through to non-interactive resolution.
        else:
            if not _run_wizard(args):
                return 1

    _resolve_plan(args)
    return run_live(args, scans.RANGE)
