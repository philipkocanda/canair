"""``canair ecu`` — list ECUs, or show one ECU's details and PID stats.

With no argument this prints a plain, pipeable list of every ECU in the active
profile's ``ecus.yaml`` registry (one name per line). Given an ECU name, alias,
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
"""

import argparse
import json
import sys
from collections import Counter

from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.ecus import load_ecus, resolve_tx, rx_addr_str
from canlib.pids import load_pids

NAME = "ecu"

# ANSI colors (match the sibling audit tools: research, coverage)
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_RESET = "\033[0m"

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
    """Find the pids/ ECU entry whose ``tx_id`` matches, returning (name, def)."""
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
            return ecu_name, ecu_def
    return None, None


def _pid_stats(ecu_def: dict) -> dict:
    """Compute PID/parameter/research/etc. counts for one pids/ ECU entry."""
    pids = ecu_def.get("pids", {}) or {}
    active_pids = {k: v for k, v in pids.items() if isinstance(v, dict) and not v.get("ignored")}
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
        "research_open": sum(1 for r in research if isinstance(r, dict) and r.get("status") != "done"),
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
    """Build one record per registry ECU, joined to pids/ by tx_id, name-sorted."""
    cap_counts = _all_captures_by_ecu() if with_captures else Counter()
    records = []
    for tx_id, info in ecus.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name") or f"0x{tx_id:03X}"
        pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)
        rec = {
            "name": name,
            "alias": info.get("alias"),
            "tx_id": tx_id,
            "tx": f"0x{tx_id:03X}",
            "rx": rx_addr_str(tx_id),
            "description": info.get("description", ""),
            "id_protocol": info.get("id_protocol"),
            "has_pids": ecu_def is not None,
        }
        if ecu_def is not None:
            rec.update(_pid_stats(ecu_def))
            if with_captures:
                rec["captures"] = cap_counts.get(name.upper(), 0)
        records.append(rec)
    records.sort(key=lambda r: r["name"].upper())
    return records


def cmd_list(records: list[dict], as_json: bool) -> int:
    if as_json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
        return 0

    n_pids = sum(1 for r in records if r["has_pids"])
    print(f"\n  {_BOLD}ECUs{_RESET} — {len(records)} in registry, "
          f"{n_pids} with PID definitions\n")

    # Column header.
    print(f"  {_DIM}{'NAME':<12} {'TX':<6} {'PROTO':<8} "
          f"{'PIDS':>4} {'PARM':>5} {'VERIF':>7} {'CAPS':>5}{_RESET}")

    for r in records:
        name = r["name"]
        alias = f" {_DIM}({r['alias']}){_RESET}" if r.get("alias") else ""
        proto = (r.get("id_protocol") or "?")
        if not r["has_pids"]:
            # Registry-only module: no PID data to summarise.
            print(f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} "
                  f"{_DIM}{'—':>4} {'—':>5} {'—':>7} {'—':>5}{_RESET}{alias}")
            continue
        params = r["params"]
        verified = r["verified"]
        vcolor = _GREEN if params and verified == params else (_YELLOW if verified else _DIM)
        vstr = f"{verified}/{params}"
        caps = r.get("captures", 0)
        cstr = f"{caps:>5}" if caps else f"{_YELLOW}{'0':>5}{_RESET}"
        print(f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} "
              f"{r['pids']:>4} {params:>5} {vcolor}{vstr:>7}{_RESET} "
              f"{cstr}{alias}")
    print()
    return 0


# ── detail mode ─────────────────────────────────────────────────────────────


def _detail_record(info: dict, tx_id: int, pids_name: str | None, ecu_def: dict | None) -> dict:
    name = info.get("name") or f"0x{tx_id:03X}"
    rec = {
        "name": name,
        "alias": info.get("alias"),
        "description": info.get("description", ""),
        "id_protocol": info.get("id_protocol"),
        "tx": f"0x{tx_id:03X}",
        "rx": rx_addr_str(tx_id),
        "notes": info.get("notes"),
        "identity": {k: info[k] for k, _ in _IDENTITY_FIELDS if info.get(k) is not None},
    }
    if ecu_def is not None:
        rec["stats"] = _pid_stats(ecu_def)
        rec["availability"] = ecu_def.get("availability")
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
        out.append({
            "pid": code,
            "params": len(params),
            "verified": sum(1 for pr in params.values() if isinstance(pr, dict) and pr.get("verified")),
            "ignored": bool(pid_def.get("ignored")),
            "enabled": pid_def.get("enabled", True),
            "captures": per_pid.get(code, 0),
        })
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
    print(f"\n  {_DIM}TX{_RESET} {rec['tx']}    {_DIM}RX{_RESET} {rec['rx']}    "
          f"{_DIM}protocol{_RESET} {proto}")

    # Identity fields
    if rec["identity"]:
        print(f"\n  {_BOLD}Identity{_RESET}")
        for key, label in _IDENTITY_FIELDS:
            if key in rec["identity"]:
                print(f"    {label:<12} {rec['identity'][key]}")

    # Stats
    stats = rec.get("stats")
    if stats is None:
        print(f"\n  {_YELLOW}No PID definitions{_RESET} "
              f"{_DIM}(not present in pids/ — registry-only module){_RESET}")
    else:
        print(f"\n  {_BOLD}Stats{_RESET}")
        verified = stats["verified"]
        params = stats["params"]
        vcolor = _GREEN if params and verified == params else (_YELLOW if verified else _DIM)
        print(f"    {'PIDs':<14} {stats['pids']}"
              + (f"  {_DIM}(+{stats['ignored']} ignored){_RESET}" if stats["ignored"] else ""))
        print(f"    {'Parameters':<14} {params}")
        print(f"    {'Verified':<14} {vcolor}{verified}/{params}{_RESET}")
        print(f"    {'Captures':<14} {rec['captures']}")
        if stats["research_total"]:
            print(f"    {'Research':<14} {stats['research_open']} open "
                  f"{_DIM}/ {stats['research_total']} total{_RESET}")
        if stats["iocontrol"] or stats["iocontrol_discoveries"]:
            extra = f"  {_DIM}(+{stats['iocontrol_discoveries']} discoveries){_RESET}" \
                if stats["iocontrol_discoveries"] else ""
            print(f"    {'IO-control':<14} {stats['iocontrol']}{extra}")
        if stats["routines"]:
            print(f"    {'Routines':<14} {stats['routines']}")
        if rec.get("availability"):
            avail = ", ".join(str(a) for a in rec["availability"])
            print(f"    {'Availability':<14} {avail}")

    # Per-PID breakdown
    if rec["pid_list"]:
        print(f"\n  {_BOLD}PIDs{_RESET}")
        for p in rec["pid_list"]:
            flags = []
            if p["ignored"]:
                flags.append(f"{_DIM}ignored{_RESET}")
            elif not p["enabled"]:
                flags.append(f"{_DIM}disabled{_RESET}")
            if not p["captures"]:
                flags.append(f"{_YELLOW}no capture{_RESET}")
            flag_str = ("  " + " ".join(flags)) if flags else ""
            vcolor = _GREEN if p["params"] and p["verified"] == p["params"] else _DIM
            print(f"    {_CYAN}{p['pid']:<8}{_RESET} "
                  f"{p['params']:>2}p  {vcolor}{p['verified']:>2} verified{_RESET}  "
                  f"{_DIM}{p['captures']} cap{_RESET}{flag_str}")

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

    pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)
    rec = _detail_record(info, tx_id, pids_name, ecu_def)
    return cmd_detail(rec, args.json)
