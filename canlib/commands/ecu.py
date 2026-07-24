"""``canair ecu`` — list ECUs, or show one ECU's details and PID stats.

With no argument this prints a plain, pipeable list of every ECU in the active
profile's ``ecus/`` files (one name per line). Given an ECU name, alias,
or hex TX id it prints that ECU's identity fields plus reverse-engineering stats
(PIDs, parameters, verified count, captures, research backlog, IO-control,
routines) and a per-PID breakdown.

Examples:
  canair ecu                 # plain list of all ECUs (one per line)
  canair ecu BMS             # details + stats for the BMS
  canair ecu MDPS            # aliases resolve too (MDPS -> EPS)
  canair ecu 0x7E4           # hex TX id also works
  canair ecu BMS --json      # machine-readable
  canair ecu --json          # all ECUs as JSON

Columns & legend:
  IDENT  identity confidence — how sure we are the ECU is correctly
         identified (name/part/role), NOT how complete its decoding is:
           confirmed (conf)   — verified by part number / firmware / behaviour
           probable (prob)    — strong circumstantial evidence
           tentative (tent)   — plausible but unverified
           speculative (spec) — a guess, e.g. borrowed from another vehicle
         A leading `~` means the level was DERIVED from the available evidence;
         without it, the level was set explicitly in the ECU registry.
  PIDS   number of active (non-ignored) PIDs/DIDs defined.
  PARM   number of decoded parameters defined across those PIDs.
  VERIF  verified/total parameters (green when all verified).
  CAPS   number of saved captures for the ECU.
  cap    in the per-PID detail view, "N cap" = number of saved captures for
         that individual PID.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.ecus import ecu_identity_confidence, load_ecus, resolve_tx, rx_addr_str
from canlib.pids import load_pids, pid_status

NAME = "ecu"

# ANSI colors (match the sibling audit tools: research, coverage)
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

# identity_confidence → (compact code, color) for the list/detail views.
_CONF_STYLE = {
    "confirmed": ("conf", _GREEN),
    "probable": ("prob", _CYAN),
    "tentative": ("tent", _YELLOW),
    "speculative": ("spec", _RED),
}


def _conf_cell(rec: dict) -> str:
    """Colored, width-6 confidence cell for the list view (``~`` = derived)."""
    conf = rec.get("identity_confidence") or ""
    explicit = rec.get("identity_confidence_explicit", False)
    code, color = _CONF_STYLE.get(conf, (conf[:4], _DIM))
    text = ("" if explicit else "~") + code
    return f"{color}{text:<6}{_RESET}"


# Identity fields to surface in the detail view, in display order.
# (name/alias/description/id_protocol are handled separately in the header.)
_IDENTITY_FIELDS = [
    ("part_number", "Part number"),
    ("supplier", "Supplier"),
    ("mfg_date", "Mfg date"),
    ("hw_version", "HW version"),
    ("sw_version", "SW version"),
    ("hw_sw", "HW/SW"),
    ("boot_sw", "Boot SW"),
    ("app_sw", "App SW"),
    ("fw_version", "FW version"),
    ("firmware", "Firmware"),
    ("calibration", "Calibration"),
    ("ecu_id", "ECU id"),
    ("sw_id", "SW id"),
    ("serial", "Serial"),
    ("diag_address", "Diag addr"),
    ("vin", "VIN"),
]


def _pids_def_for_tx(pids_data: dict, tx_id: int) -> tuple[str | None, dict | None]:
    """Find the ecus/ ECU entry whose ``tx_id`` matches, returning (name, def)."""
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
            return ecu_name, ecu_def
    return None, None


def _pid_stats(ecu_def: dict) -> dict:
    """Compute PID/parameter/research/etc. counts for one ecus/ ECU entry."""
    pids = ecu_def.get("pids", {}) or {}
    active_pids = {
        k: v for k, v in pids.items() if isinstance(v, dict) and pid_status(v) != "ignored"
    }
    params = [
        pr
        for p in active_pids.values()
        for pr in (p.get("parameters") or {}).values()
        if isinstance(pr, dict)
    ]
    research = ecu_def.get("research", []) or []
    return {
        "pids": len(active_pids),
        "ignored": len(pids) - len(active_pids),
        "params": len(params),
        "verified": sum(1 for pr in params if pr.get("verified")),
        "research_open": sum(
            1 for r in research if isinstance(r, dict) and r.get("status") != "done"
        ),
        "research_total": len(research),
        "iocontrol": len(ecu_def.get("iocontrol", {}) or {}),
        "iocontrol_discoveries": len(ecu_def.get("iocontrol_discoveries", {}) or {}),
        "routines": len(ecu_def.get("routines", {}) or {}),
    }


def _captures_by_pid(ecu_name: str) -> tuple[Counter, int]:
    """Return (per-PID capture counts, total captures) for an ECU name."""
    try:
        from canlib.commands.captures import load_all_captures

        caps = load_all_captures()
    except Exception:
        return Counter(), 0
    per_pid: Counter = Counter()
    total = 0
    for c in caps:
        if str(c.get("ecu", "")).upper() == ecu_name.upper():
            total += 1
            per_pid[str(c.get("pid", "")).upper()] += 1
    return per_pid, total


# ── list mode ─────────────────────────────────────────────────────────────


def _all_captures_by_ecu() -> Counter:
    """Total capture counts keyed by canonical ECU short name (upper-cased)."""
    try:
        from canlib.commands.captures import load_all_captures

        caps = load_all_captures()
    except Exception:
        return Counter()
    return Counter(str(c.get("ecu", "")).upper() for c in caps)


def _list_records(ecus: dict, pids_data: dict, with_captures: bool = False) -> list[dict]:
    """Build one record per registry ECU, joined to ecus/ by tx_id, name-sorted."""
    cap_counts = _all_captures_by_ecu() if with_captures else Counter()
    records = []
    for tx_id, info in ecus.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name") or f"0x{tx_id:03X}"
        _pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)
        conf, conf_explicit = ecu_identity_confidence(info)
        rec = {
            "name": name,
            "alias": info.get("alias"),
            "tx_id": tx_id,
            "tx": f"0x{tx_id:03X}",
            "rx": rx_addr_str(tx_id),
            "description": info.get("description", ""),
            "id_protocol": info.get("id_protocol"),
            "identity_confidence": conf,
            "identity_confidence_explicit": conf_explicit,
            "has_pids": ecu_def is not None,
        }
        if ecu_def is not None:
            rec.update(_pid_stats(ecu_def))
            if with_captures:
                rec["captures"] = cap_counts.get(name.upper(), 0)
        records.append(rec)
    # name is always a str at runtime; the record dict's inferred value union
    # includes int (tx_id) so narrow explicitly for the sort key.
    records.sort(key=lambda r: str(r["name"]).upper())
    return records


def cmd_list(records: list[dict], as_json: bool) -> int:
    if as_json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
        return 0

    n_pids = sum(1 for r in records if r["has_pids"])
    print(f"\n  {_BOLD}ECUs{_RESET} — {len(records)} in registry, {n_pids} with PID definitions\n")

    # Column header.
    print(
        f"  {_DIM}{'NAME':<12} {'TX':<6} {'PROTO':<8} {'IDENT':<6} "
        f"{'PIDS':>4} {'PARM':>5} {'VERIF':>7} {'CAPS':>5}{_RESET}"
    )

    for r in records:
        name = r["name"]
        alias = f" {_DIM}({r['alias']}){_RESET}" if r.get("alias") else ""
        proto = r.get("id_protocol") or "?"
        conf = _conf_cell(r)
        if not r["has_pids"]:
            # Registry-only module: no PID data to summarise.
            print(
                f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} {conf} "
                f"{_DIM}{'—':>4} {'—':>5} {'—':>7} {'—':>5}{_RESET}{alias}"
            )
            continue
        params = r["params"]
        verified = r["verified"]
        vcolor = _GREEN if params and verified == params else (_YELLOW if verified else _DIM)
        vstr = f"{verified}/{params}"
        caps = r.get("captures", 0)
        cstr = f"{caps:>5}" if caps else f"{_YELLOW}{'0':>5}{_RESET}"
        print(
            f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} {conf} "
            f"{r['pids']:>4} {params:>5} {vcolor}{vstr:>7}{_RESET} "
            f"{cstr}{alias}"
        )
    print()
    print(
        f"  {_DIM}IDENT = identity confidence: "
        f"{_GREEN}conf{_DIM}irmed · {_CYAN}prob{_DIM}able · {_YELLOW}tent{_DIM}ative · "
        f"{_RED}spec{_DIM}ulative   (~ = derived from evidence, else set in registry){_RESET}"
    )
    return 0


# ── detail mode ─────────────────────────────────────────────────────────────


def _detail_record(info: dict, tx_id: int, pids_name: str | None, ecu_def: dict | None) -> dict:
    name = info.get("name") or f"0x{tx_id:03X}"
    conf, conf_explicit = ecu_identity_confidence(info)
    rec = {
        "name": name,
        "alias": info.get("alias"),
        "description": info.get("description", ""),
        "id_protocol": info.get("id_protocol"),
        "identity_confidence": conf,
        "identity_confidence_explicit": conf_explicit,
        "tx": f"0x{tx_id:03X}",
        "rx": rx_addr_str(tx_id),
        "notes": info.get("notes"),
        "identity": {k: info[k] for k, _ in _IDENTITY_FIELDS if info.get(k) is not None},
    }
    if ecu_def is not None:
        rec["stats"] = _pid_stats(ecu_def)
        rec["vehicle_states"] = ecu_def.get("vehicle_states")
        per_pid, total = _captures_by_pid(name)
        rec["captures"] = total
        rec["pid_list"] = _pid_details(ecu_def, per_pid)
    else:
        rec["stats"] = None
        rec["captures"] = 0
        rec["pid_list"] = []
    return rec


def _pid_details(ecu_def: dict, per_pid: Counter) -> list[dict]:
    out = []
    for pid_code, pid_def in (ecu_def.get("pids", {}) or {}).items():
        if not isinstance(pid_def, dict):
            continue
        params = pid_def.get("parameters", {}) or {}
        code = str(pid_code).upper()
        status = pid_status(pid_def)
        out.append(
            {
                "pid": code,
                "params": len(params),
                "verified": sum(
                    1 for pr in params.values() if isinstance(pr, dict) and pr.get("verified")
                ),
                "status": status,
                "ignored": status == "ignored",
                "captures": per_pid.get(code, 0),
            }
        )
    out.sort(key=lambda p: p["pid"])
    return out


def cmd_detail(rec: dict, as_json: bool) -> int:
    if as_json:
        json.dump(rec, sys.stdout, indent=2, default=str)
        print()
        return 0

    # Header
    title = f"{_BOLD}{_CYAN}{rec['name']}{_RESET}"
    if rec.get("alias"):
        title += f" {_DIM}(alias: {rec['alias']}){_RESET}"
    print(f"\n  {title}")
    if rec.get("description"):
        print(f"  {rec['description']}")

    # Addresses / protocol
    proto = rec.get("id_protocol") or "?"
    print(
        f"\n  {_DIM}TX{_RESET} {rec['tx']}    {_DIM}RX{_RESET} {rec['rx']}    "
        f"{_DIM}protocol{_RESET} {proto}"
    )

    # Identity confidence
    conf = rec.get("identity_confidence") or ""
    _code, ccolor = _CONF_STYLE.get(conf, ("", _DIM))
    origin = (
        "set in registry" if rec.get("identity_confidence_explicit") else "derived from evidence"
    )
    print(f"  {_DIM}identity confidence{_RESET} {ccolor}{conf}{_RESET} {_DIM}({origin}){_RESET}")

    # Identity fields
    if rec["identity"]:
        print(f"\n  {_BOLD}Identity{_RESET}")
        for key, label in _IDENTITY_FIELDS:
            if key in rec["identity"]:
                print(f"    {label:<12} {rec['identity'][key]}")

    # Stats
    stats = rec.get("stats")
    if stats is None:
        print(
            f"\n  {_YELLOW}No PID definitions{_RESET} "
            f"{_DIM}(no pids: — identity-only module){_RESET}"
        )
    else:
        print(f"\n  {_BOLD}Stats{_RESET}")
        verified = stats["verified"]
        params = stats["params"]
        vcolor = _GREEN if params and verified == params else (_YELLOW if verified else _DIM)
        print(
            f"    {'PIDs':<14} {stats['pids']}"
            + (f"  {_DIM}(+{stats['ignored']} ignored){_RESET}" if stats["ignored"] else "")
        )
        print(f"    {'Parameters':<14} {params}")
        print(f"    {'Verified':<14} {vcolor}{verified}/{params}{_RESET}")
        print(f"    {'Captures':<14} {rec['captures']}")
        if stats["research_total"]:
            print(
                f"    {'Research':<14} {stats['research_open']} open "
                f"{_DIM}/ {stats['research_total']} total{_RESET}"
            )
        if stats["iocontrol"] or stats["iocontrol_discoveries"]:
            extra = (
                f"  {_DIM}(+{stats['iocontrol_discoveries']} discoveries){_RESET}"
                if stats["iocontrol_discoveries"]
                else ""
            )
            print(f"    {'IO-control':<14} {stats['iocontrol']}{extra}")
        if stats["routines"]:
            print(f"    {'Routines':<14} {stats['routines']}")
        if rec.get("vehicle_states"):
            avail = ", ".join(str(a) for a in rec["vehicle_states"])
            print(f"    {'States':<14} {avail}")

    # Per-PID breakdown
    if rec["pid_list"]:
        print(f"\n  {_BOLD}PIDs{_RESET}")
        for p in rec["pid_list"]:
            flags = []
            status = p.get("status", "active")
            if status != "active":
                flags.append(f"{_DIM}{status}{_RESET}")
            if not p["captures"]:
                flags.append(f"{_YELLOW}no capture{_RESET}")
            flag_str = ("  " + " ".join(flags)) if flags else ""
            vcolor = _GREEN if p["params"] and p["verified"] == p["params"] else _DIM
            print(
                f"    {_CYAN}{p['pid']:<8}{_RESET} "
                f"{p['params']:>2}p  {vcolor}{p['verified']:>2} verified{_RESET}  "
                f"{_DIM}{p['captures']} cap{_RESET}{flag_str}"
            )

    # Notes last (can be long/multiline)
    if rec.get("notes"):
        notes = " ".join(str(rec["notes"]).split())
        print(f"\n  {_BOLD}Notes{_RESET}\n    {notes}")
    print()
    return 0


def _unknown_ecu(value: str, records: list[dict]) -> int:
    print(f"{_RED}Unknown ECU {value!r}.{_RESET}", file=sys.stderr)
    names = [r["name"] for r in records]
    print("\nAvailable ECUs:", file=sys.stderr)
    print("  " + ", ".join(names), file=sys.stderr)
    return 1


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect ECUs (list/detail) or add one: show | add",
        description="Inspect or edit the profile's ECU registry.\n"
        "  show   list ECUs, or show one ECU's details and PID stats (default)\n"
        "  add    register a new ECU in the active profile's ecus/ (offline)\n\n"
        "A bare `canair ecu` or `canair ecu BMS` is shorthand for `canair ecu show …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    kinds = parser.add_subparsers(dest="ecu_kind", metavar="<kind>")
    _add_show_parser(kinds)
    _add_add_parser(kinds)
    parser.set_defaults(func=_group_help, _ecu_group_parser=parser)
    return parser


def _group_help(args) -> int:
    parser = getattr(args, "_ecu_group_parser", None)
    if parser is not None:
        parser.print_help()
    return 1


def _add_show_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "show",
        help="List ECUs, or show one ECU's details and PID stats",
        description="List ECUs, or show one ECU's details and PID stats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "ecu", nargs="?", help="ECU name, alias, or hex TX id (omit to list all)"
    ).completer = _ecu_completer
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(func=run)
    return parser


def _add_add_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "add",
        help="Register a new ECU in the active profile (offline; no device)",
        description="Register a new ECU as ecus/<name>.yaml in the active profile.\n\n"
        "Offline counterpart to `canair discover --register` (which needs a live "
        "bus): use this to seed a known ECU into a blank profile — e.g. one shared "
        "with another model-year — ready for contributions. The write is validated "
        "and comment-preserving (never hand-edit ecus/).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair ecu add 7C6 --name CLU --description 'Cluster (instrument panel)'\n"
        "  canair ecu add 0x7E4 --name BMS --id-protocol KWP2000\n"
        "  canair ecu add 770 --name IGPM --notes 'Seeded offline; no PIDs yet'\n",
    )
    parser.add_argument("tx", metavar="TX", help="ECU TX id (hex, e.g. 7C6 or 0x7C6)")
    parser.add_argument("--name", help="ECU short name (default: Unknown-<TX>)")
    parser.add_argument("--description", help="Human description")
    parser.add_argument(
        "--id-protocol", dest="id_protocol", help="Identity protocol (UDS | KWP2000)"
    )
    parser.add_argument("--notes", help="Free-text notes")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing identity fields"
    )
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.set_defaults(func=cmd_add)
    return parser


def cmd_add(args) -> int:
    from canlib.ecus_edit import EcusEditError, register_ecu, tx_key

    try:
        tx_id = int(str(args.tx), 16)
    except ValueError:
        print(
            f"{_RED}Invalid TX id {args.tx!r} — expected hex (e.g. 7C6).{_RESET}", file=sys.stderr
        )
        return 1

    fields = {
        k: v
        for k, v in (
            ("description", args.description),
            ("id_protocol", args.id_protocol),
            ("notes", args.notes),
        )
        if v is not None
    }
    try:
        wrote = register_ecu(
            tx_id,
            name=args.name,
            overwrite=args.overwrite,
            ecus_dir=args.dir,
            **fields,
        )
    except EcusEditError as e:
        print(f"{_RED}{e}{_RESET}", file=sys.stderr)
        return 1

    disp = tx_key(tx_id)
    label = args.name or f"Unknown-{tx_id:03X}"
    if wrote:
        print(f"{_GREEN}  ✓ registered {label} ({disp}){_RESET}")
    else:
        print(f"{_DIM}  {label} ({disp}) already registered; nothing to change.{_RESET}")
    return 0


def run(args) -> int:
    ecus = load_ecus()
    pids_data = load_pids()

    if not args.ecu:
        records = _list_records(ecus, pids_data, with_captures=True)
        if not records:
            print("No ECUs found in the active profile (see `canair profile show`).")
            return 1
        return cmd_list(records, args.json)

    tx_id = resolve_tx(args.ecu)
    info = ecus.get(tx_id) if tx_id is not None else None
    if info is None:
        return _unknown_ecu(args.ecu, _list_records(ecus, pids_data))

    # info is only non-None when tx_id resolved (see the guarded .get above).
    assert tx_id is not None
    pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)
    rec = _detail_record(info, tx_id, pids_name, ecu_def)
    return cmd_detail(rec, args.json)
