"""``canair scan`` — the scan command group.

Three kinds of scan live under one command:

  * ``canair scan range <ECU>``      — sweep a PID/DID range on one ECU (the
    general-purpose scan, with friendly ``--service`` presets + a wizard).
  * ``canair scan iocontrol <ECU>``  — SAFE IOControl discovery. Auto-selects
    UDS ``0x2F`` or KWP2000 ``0x30`` from the ECU's ``id_protocol`` and only ever
    sends the side-effect-free returnControlToECU sub-function (never actuates).
  * ``canair scan routines <ECU>``   — SAFE RoutineControl (``0x31``) discovery
    (probes requestRoutineResults only).

For convenience, a bare ``canair scan <ECU>`` (or ``canair scan`` with no args)
is treated as ``canair scan range …`` — see ``_inject_default_scan_kind`` in
``canlib/cli.py``.
"""

from __future__ import annotations

import argparse
import sys

from canlib.commands._live import (
    add_connection_args,
    ecu_completer,
    finalize_live_parser,
    run_live,
)
from canlib.scan_presets import (
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

NAME = "scan"

# Subcommand names under ``canair scan``. Kept in sync with ``cli._SCAN_KINDS``
# (which injects "range" when the token after ``scan`` isn't one of these).
SCAN_KINDS = ("range", "iocontrol", "routines")


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Scan an ECU: range | iocontrol | routines",
        description="Scan an ECU. Choose a kind:\n"
        "  range      sweep a PID/DID range (general purpose)\n"
        "  iocontrol  SAFE IOControl discovery (UDS 0x2F / KWP2000 0x30, auto)\n"
        "  routines   SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)\n\n"
        "A bare `canair scan BMS` (or `canair scan` alone) is shorthand for "
        "`canair scan range …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    kinds = parser.add_subparsers(dest="scan_kind", metavar="<kind>")
    _add_range_parser(kinds)
    _add_iocontrol_parser(kinds)
    _add_routines_parser(kinds)
    parser.set_defaults(func=_group_help, _scan_group_parser=parser)
    return parser


def _group_help(args) -> int:
    """Fallback when ``canair scan`` is invoked with no resolvable kind."""
    parser = getattr(args, "_scan_group_parser", None)
    if parser is not None:
        parser.print_help()
    return 1


# ---------------------------------------------------------------------------
# scan range — the general-purpose PID/DID sweep
# ---------------------------------------------------------------------------


def _add_range_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "range",
        help="Sweep a range of PIDs/DIDs on an ECU",
        description="Scan a range of PIDs/DIDs on an ECU. One scan at a time only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
getting started:
  canair scan range                 # interactive wizard — pick ECU/service/range
  canair scan BMS                   # smart defaults for that ECU (bare = range)
  canair scan range IGPM            # UDS ECU → read-did over its known DID range

"""
        + presets_help()
        + """

examples:
  canair scan range BMS --service live-data --range 01-FF
  canair scan range 7E4 --service read-did --range BC01-BC0B
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
        metavar="START-END",
        default=None,
        help="PID/DID range in hex (default: smart per-ECU)",
    )
    parser.add_argument("--append", metavar="HEX", help="Hex bytes to append after each DID")
    parser.add_argument("--session", action="store_true", help="Enter extended session (10 03)")
    parser.add_argument("--wake", action="store_true", help="Wake ECUs from deep sleep (10 01)")
    parser.add_argument("--save", action="store_true", help="Save results to captures/")
    parser.add_argument("--label", metavar="TEXT", default=None, help="Session label for --save")
    parser.add_argument("--state", metavar="TEXT", default=None, help="Session state for --save")
    parser.add_argument("--notes", metavar="TEXT", default=None, help="Session notes for --save")
    add_connection_args(parser)
    finalize_live_parser(parser, scan=True)
    # Override the shared dispatch with our resolve-then-run wrapper.
    parser.set_defaults(func=run_range)
    return parser


# ---------------------------------------------------------------------------
# scan iocontrol — SAFE IOControl discovery (UDS 0x2F / KWP2000 0x30, auto)
# ---------------------------------------------------------------------------


def _add_iocontrol_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "iocontrol",
        help="SAFE IOControl discovery (UDS 0x2F / KWP2000 0x30, auto by id_protocol)",
        description="Probe returnControlToECU across an id range on one or more ECUs. "
        "The service is auto-selected per ECU from its id_protocol: UDS ECUs use "
        "InputOutputControlByIdentifier (0x2F, 16-bit DID); KWP2000 ECUs (BMS, VCU, "
        "MCU, LDC, AAF) use InputOutputControlByLocalIdentifier (0x30, 8-bit LID). "
        "Only the side-effect-free sub-function is ever sent — the scanner never "
        "actuates. Hits are written to pids/<ecu>.yaml under an iocontrol_discoveries: "
        "section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan iocontrol IGPM              # UDS 0x2F DID scan\n"
        "  canair scan iocontrol BMS               # KWP2000 0x30 LID scan (auto)\n"
        "  canair scan iocontrol BMS --did-range 00-FF\n"
        "  canair scan iocontrol IGPM BCM --did-range B000-BFFF\n",
    )
    parser.add_argument(
        "iocontrol_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--did-range",
        metavar="START-END",
        default=None,
        help="Id range: DID for UDS (per-ECU defaults), LID 00-FF for KWP2000 (default 00-FF)",
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser


# ---------------------------------------------------------------------------
# scan routines — SAFE RoutineControl (0x31) discovery
# ---------------------------------------------------------------------------


def _add_routines_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "routines",
        help="SAFE RoutineControl discovery (UDS 0x31 SF03 / KWP2000 0x33, auto)",
        description="Probe routine results across a range on one or more ECUs. The service "
        "is auto-selected per ECU from its id_protocol: UDS ECUs use RoutineControl "
        "(0x31, requestRoutineResults SF 0x03); KWP2000 ECUs (BMS, VCU, MCU, LDC, AAF) use "
        "RequestRoutineResultsByLocalIdentifier (0x33). 0x31 (StartRoutine on KWP2000) is "
        "NEVER sent to a KWP2000 ECU — only the read-only results service. Hits are written "
        "to pids/<ecu>.yaml under a routines: section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair scan routines IGPM               # UDS 0x31 SF03\n"
        "  canair scan routines BMS                # KWP2000 0x33 LID scan (auto)\n"
        "  canair scan routines IGPM BCM --rid-range F000-F0FF\n",
    )
    parser.add_argument(
        "routines_scan", nargs="+", metavar="ECU", help="ECUs to scan (at least one required)"
    ).completer = ecu_completer
    parser.add_argument(
        "--rid-range",
        metavar="START-END",
        default="F000-F0FF",
        help="RID range for UDS (default F000-F0FF); KWP2000 ECUs use LID 00-FF",
    )
    parser.add_argument(
        "--throttle-ms", type=int, default=150, help="Delay in ms between probes (default 150)"
    )
    add_connection_args(parser)
    finalize_live_parser(parser)
    return parser


# ---------------------------------------------------------------------------
# Resolution: turn friendly/absent --service/--range into concrete values
# ---------------------------------------------------------------------------


def _fmt_range(rng: tuple[int, int], wide: bool) -> str:
    fmt = "04X" if wide else "02X"
    return f"{rng[0]:{fmt}}-{rng[1]:{fmt}}"


def _resolve_plan(args) -> None:
    """Populate ``args.service`` (hex str) and ``args.range`` ('START-END').

    Applies smart per-ECU defaults when the user left ``--service``/``--range``
    unset, and resolves friendly service preset names to hex. Prints a short
    summary of the resolved plan so the user learns the underlying command.
    """
    auto = args.service is None and args.range is None
    plan: ScanPlan | None = None
    if args.service is None or args.range is None:
        try:
            plan = plan_scan(args.tx)
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
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
    args.service = f"{service_int:02X}"
    wide = is_wide_service(service_int)

    # --- range ---
    if args.range is None:
        if plan is not None and plan.service == service_int:
            args.range = _fmt_range(plan.pid_range, wide)
        else:
            preset = preset_by_service(service_int)
            args.range = preset.default_range if preset else ("0000-00FF" if wide else "01-FF")

    # --- session/wake: only auto-apply in pure smart mode ---
    if auto and plan is not None:
        if plan.session and not args.session:
            args.session = True
        if plan.wake and not args.wake:
            args.wake = True

    _print_plan_summary(args, service_int, preset_name, plan if auto else None)


def _print_plan_summary(args, service_int, preset_name, plan) -> None:
    from rich.console import Console

    console = Console(stderr=True)
    svc = service_label(service_int, preset_name)
    parts = [f"[bold]{args.tx}[/bold]", f"service {svc}", f"range {args.range}"]
    if args.append:
        parts.append(f"append {args.append}")
    if args.session:
        parts.append("session")
    if args.wake:
        parts.append("wake")
    console.print("  [dim]Scan plan:[/dim] " + "  |  ".join(parts))
    if plan is not None and plan.reason:
        console.print(f"  [dim]         → {plan.reason}[/dim]")


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


def _ecu_choices() -> list[tuple[str, int, str]]:
    """Return [(name, tx_id, description)] for ECUs known to the profile."""
    from canlib.ecus import load_ecus
    from canlib.pids import load_pids

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
    from rich.console import Console
    from rich.table import Table

    console = Console()
    console.print("\n[bold cyan]canair scan — interactive setup[/bold cyan]")
    console.print("[dim]Press Ctrl+C to cancel at any time.[/dim]\n")

    try:
        # 1. ECU
        if args.tx is None:
            choices = _ecu_choices()
            if choices:
                table = Table(title="ECUs in the active profile", title_justify="left")
                table.add_column("#", justify="right", style="cyan")
                table.add_column("ECU", style="bold")
                table.add_column("TX", style="magenta")
                table.add_column("Description")
                for i, (name, tx, desc) in enumerate(choices, 1):
                    table.add_row(str(i), name, f"0x{tx:03X}", desc or "")
                console.print(table)
                sel = _prompt("Select an ECU (number, name, or hex TX id)")
                args.tx = _resolve_ecu_selection(sel, choices)
            else:
                args.tx = _prompt("ECU name or hex TX id")
            if not args.tx:
                console.print("[yellow]No ECU selected — cancelled.[/yellow]")
                return False

        # Compute the smart plan to seed defaults.
        try:
            plan = plan_scan(args.tx)
        except Exception:
            plan = None

        # 2. Service
        console.print()
        stable = Table(title="Services", title_justify="left")
        stable.add_column("name", style="bold cyan")
        stable.add_column("hex", style="magenta")
        stable.add_column("description")
        for p in SERVICE_PRESETS:
            note = f"  ⚠ {p.caution}" if p.caution else ""
            stable.add_row(p.name, f"0x{p.service:02X}", p.summary + note)
        console.print(stable)
        default_service = plan.service_name if plan and plan.service_name else "live-data"
        svc_in = _prompt("Service (preset name or hex byte)", default_service)
        try:
            service_int, _preset_name = resolve_service(svc_in)
        except ServiceError as e:
            console.print(f"[red]{e}[/red]")
            return False
        args.service = f"{service_int:02X}"
        wide = is_wide_service(service_int)

        # 3. Range
        if plan is not None and plan.service == service_int:
            default_range = _fmt_range(plan.pid_range, wide)
        else:
            preset = preset_by_service(service_int)
            default_range = preset.default_range if preset else ("0000-00FF" if wide else "01-FF")
        args.range = _prompt("Range (START-END, hex)", default_range)

        # 4. Session / wake
        console.print()
        default_session = "y" if (args.session or (plan and plan.session)) else "n"
        args.session = (
            _prompt("Enter extended session (10 03)? (y/n)", default_session)
            .lower()
            .startswith("y")
        )
        default_wake = "y" if (args.wake or (plan and plan.wake)) else "n"
        args.wake = (
            _prompt("Wake ECU from deep sleep first (10 01)? (y/n)", default_wake)
            .lower()
            .startswith("y")
        )

        # 5. Confirm — show the equivalent one-liner so the user learns it.
        console.print()
        console.print("[bold]Ready to scan:[/bold]  " + _equiv_command(args))
        go = _prompt("Run this scan now? (y/n)", "y")
        if not go.lower().startswith("y"):
            console.print("[yellow]Cancelled.[/yellow]")
            return False
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
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
    parts = ["canair scan range", str(args.tx), f"--service {args.service}", f"--range {args.range}"]
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
                print(
                    "Error: no ECU given. Specify one (e.g. `canair scan BMS`), run "
                    "`canair scan range` in a terminal for the wizard, or `canair discover` "
                    "to find live ECUs.",
                    file=sys.stderr,
                )
                return 2
            # -i without a TTY: fall through to non-interactive resolution.
        else:
            if not _run_wizard(args):
                return 0

    _resolve_plan(args)
    return run_live(args)
