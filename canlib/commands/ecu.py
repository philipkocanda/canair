"""``canair ecu`` — list ECUs, or show one ECU's details and PID stats.

With no argument this prints a plain, pipeable list of every ECU in the active
profile's ``ecus/`` files (one name per line). Given an ECU name, alias,
or hex TX/RX id it prints that ECU's identity fields plus reverse-engineering stats
(PIDs, parameters, verified count, captures, research backlog, IO-control,
routines) and a per-PID breakdown.

Examples:
  canair ecu                 # plain list of all ECUs (one per line)
  canair ecu BMS             # details + stats for the BMS
  canair ecu MDPS            # aliases resolve too (MDPS -> EPS)
  canair ecu 0x7E4           # hex TX id also works
  canair ecu 0x7EC           # hex RX id resolves too (RX = TX + 8)
  canair ecu --captures      # include capture-count columns (parses captures — slower)
  canair ecu BMS --captures  # per-PID capture counts for the BMS
  canair ecu BMS --json      # machine-readable
  canair ecu --json          # all ECUs as JSON

Columns & legend:
  BUS    physical CAN bus segment(s) the ECU sits on (profile-specific codes,
         e.g. Hyundai B-CAN/P-CAN/C-CAN/M-CAN/H-CAN/All); some ECUs span two
         (shown `H-CAN/P-CAN`). Blank (`—`) when unknown. The list is sorted by
         BUS by default.
  PIDS   number of active (non-ignored) PIDs/DIDs defined.
  VERIF  verified/total parameters (green when all verified).
  CAPS   number of saved captures for the ECU. Only computed with `--captures`
         (parsing every capture is slow); shown as `—` otherwise.
  cap    in the per-PID detail view, "N cap" = number of saved captures for
         that individual PID (only shown with `--captures`).

  Sort with `--sort {bus,name,tx,proto,pids,verif,caps}`: string/hex columns
  (bus, name, tx, proto) ascending; numeric columns (pids, verif, caps)
  descending. `name` breaks ties.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from canlib.commands._group import group_help
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.ecus import load_ecus, resolve_tx, rx_addr_str
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


# Sort columns → (record key, direction). Numeric columns sort descending
# (most-populated first); string/hex columns sort ascending. `bus` is the
# default (group by CAN segment). Order here also drives the --sort choices.
_SORT_COLUMNS = {
    "bus": ("can_bus", "asc"),
    "name": ("name", "asc"),
    "tx": ("tx_id", "asc"),
    "proto": ("id_protocol", "asc"),
    "pids": ("pids", "desc"),
    "verif": ("verified", "desc"),
    "caps": ("captures", "desc"),
}


def _sort_records(records: list[dict], sort: str) -> None:
    """Sort ``records`` in place by the named column (see ``_SORT_COLUMNS``).

    Numeric columns sort descending, string/hex columns ascending; ``name`` is
    always the tie-breaker (ascending). Missing/unbussed values sort last.
    """
    key_name, direction = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["bus"])
    reverse = direction == "desc"

    # Stable pre-sort by name so that, within an equal primary key, ties always
    # resolve alphabetically — even under reverse=True (where an inline name
    # tie-breaker would itself be flipped).
    records.sort(key=lambda r: str(r["name"]).upper())

    def key(r: dict):
        if key_name == "can_bus":
            # Group by CAN segment(s); unbussed ECUs (key "~") sort last.
            return "/".join(r["can_bus"]) if r.get("can_bus") else "~"
        if key_name == "name":
            return str(r["name"]).upper()
        value = r.get(key_name)
        if key_name == "tx_id":
            # Hex TX id: sort ascending by its numeric value (== hex order).
            return value if value is not None else 0
        if reverse:
            # Numeric column: sort descending, but absent values (None — e.g.
            # a registry-only ECU, or captures without --captures) sort last.
            # reverse=True flips the list, so rank present > absent here.
            return (0 if value is None else 1, value or 0)
        # String column (id_protocol): missing values sort last.
        return str(value).upper() if value is not None else "~"

    records.sort(key=key, reverse=reverse)


def _list_records(
    ecus: dict, pids_data: dict, with_captures: bool = False, sort: str = "bus"
) -> list[dict]:
    """Build one record per registry ECU, joined to ecus/ by tx_id.

    Sorted by ``bus`` (default; group by CAN segment, unbussed ECUs last) or any
    other column in ``_SORT_COLUMNS`` — numeric columns descending, string/hex
    ascending, with ``name`` as the tie-breaker.
    """
    cap_counts = _all_captures_by_ecu() if with_captures else Counter()
    records = []
    for tx_id, info in ecus.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name") or f"0x{tx_id:03X}"
        _pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)
        rec = {
            "name": name,
            "alias": info.get("alias"),
            "tx_id": tx_id,
            "tx": f"0x{tx_id:03X}",
            "rx": rx_addr_str(tx_id),
            "description": info.get("description", ""),
            "id_protocol": info.get("id_protocol"),
            "can_bus": info.get("can_bus"),
            "has_pids": ecu_def is not None,
        }
        if ecu_def is not None:
            rec.update(_pid_stats(ecu_def))
            # None = capture counts not requested (--captures off); a display "—"
            # vs. an integer 0 ("counted, none found"). Keeps the key present so
            # --json is shape-stable.
            rec["captures"] = cap_counts.get(name.upper(), 0) if with_captures else None
        records.append(rec)
    _sort_records(records, sort)
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
        f"  {_DIM}{'NAME':<12} {'TX':<6} {'PROTO':<8} {'BUS':<8} "
        f"{'PIDS':>4} {'VERIF':>7} {'CAPS':>5}{_RESET}"
    )

    for r in records:
        name = r["name"]
        proto = r.get("id_protocol") or "?"
        bus = "/".join(r["can_bus"]) if r.get("can_bus") else "—"
        if not r["has_pids"]:
            # Registry-only module: no PID data to summarise.
            print(
                f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} {_CYAN}{bus:<8}{_RESET} "
                f"{_DIM}{'—':>4} {'—':>7} {'—':>5}{_RESET}"
            )
            continue
        params = r["params"]
        verified = r["verified"]
        vcolor = _GREEN if params and verified == params else (_YELLOW if verified else _DIM)
        vstr = f"{verified}/{params}"
        caps = r.get("captures")
        if caps is None:
            cstr = f"{_DIM}{'—':>5}{_RESET}"  # counts not requested (--captures)
        elif caps:
            cstr = f"{caps:>5}"
        else:
            cstr = f"{_YELLOW}{'0':>5}{_RESET}"
        print(
            f"  {_CYAN}{name:<12}{_RESET} {r['tx']:<6} {proto:<8} {_CYAN}{bus:<8}{_RESET} "
            f"{r['pids']:>4} {vcolor}{vstr:>7}{_RESET} "
            f"{cstr}"
        )
    print()
    return 0


# ── detail mode ─────────────────────────────────────────────────────────────


def _detail_record(
    info: dict,
    tx_id: int,
    pids_name: str | None,
    ecu_def: dict | None,
    bus_labels: dict | None = None,
    with_captures: bool = False,
) -> dict:
    name = info.get("name") or f"0x{tx_id:03X}"
    bus_labels = bus_labels or {}
    can_bus = info.get("can_bus")
    rec = {
        "name": name,
        "alias": info.get("alias"),
        "description": info.get("description", ""),
        "id_protocol": info.get("id_protocol"),
        "can_bus": can_bus,
        "can_bus_labels": [bus_labels.get(c, c) for c in (can_bus or [])],
        "tx": f"0x{tx_id:03X}",
        "rx": rx_addr_str(tx_id),
        "notes": info.get("notes"),
        "identity": {k: info[k] for k, _ in _IDENTITY_FIELDS if info.get(k) is not None},
    }
    # Capture counts require parsing every capture file, so they're opt-in
    # (--captures); when off, `captures` is None (== "not computed", shown as
    # "—") rather than 0, and the per-PID rows carry no count.
    if ecu_def is not None:
        rec["stats"] = _pid_stats(ecu_def)
        rec["vehicle_states"] = ecu_def.get("vehicle_states")
        per_pid = None
        if with_captures:
            per_pid, total = _captures_by_pid(name)
            rec["captures"] = total
        else:
            rec["captures"] = None
        rec["pid_list"] = _pid_details(ecu_def, per_pid)
    else:
        rec["stats"] = None
        rec["captures"] = _captures_by_pid(name)[1] if with_captures else None
        rec["pid_list"] = []
    return rec


def _pid_details(ecu_def: dict, per_pid: Counter | None) -> list[dict]:
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
                # None when capture counts weren't requested (--captures off).
                "captures": per_pid.get(code, 0) if per_pid is not None else None,
            }
        )
    out.sort(key=lambda p: str(p["pid"]))
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
    if rec.get("can_bus"):
        labels = rec.get("can_bus_labels") or rec["can_bus"]
        # Render "CODE (Name)" when a human label differs from the bare code.
        parts = [
            f"{code} ({label})" if label and label != code else code
            for code, label in zip(rec["can_bus"], labels, strict=False)
        ]
        print(f"  {_DIM}CAN bus{_RESET} {', '.join(parts)}")

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
        if rec["captures"] is not None:
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
            caps = p["captures"]
            if caps is not None and not caps:
                flags.append(f"{_YELLOW}no capture{_RESET}")
            flag_str = ("  " + " ".join(flags)) if flags else ""
            vcolor = _GREEN if p["params"] and p["verified"] == p["params"] else _DIM
            # "N cap" only when counts were computed (--captures); otherwise omit.
            cap_seg = f"  {_DIM}{caps} cap{_RESET}" if caps is not None else ""
            print(
                f"    {_CYAN}{p['pid']:<8}{_RESET} "
                f"{p['params']:>2}p  {vcolor}{p['verified']:>2} verified{_RESET}"
                f"{cap_seg}{flag_str}"
            )

    # Notes last (can be long/multiline)
    if rec.get("notes"):
        notes = " ".join(str(rec["notes"]).split())
        print(f"\n  {_BOLD}Notes{_RESET}\n    {notes}")
    print()
    return 0


def _latest_capture_by_pid(ecu_name: str) -> dict[str, dict]:
    """Map each PID (upper-cased) to this ECU's most recent payload capture.

    Capture files are chronological, so the last-seen payload entry per PID wins.
    Returns an empty dict when captures can't be loaded.
    """
    try:
        from canlib.commands.captures import load_all_captures

        caps = load_all_captures()
    except Exception:
        return {}
    latest: dict[str, dict] = {}
    for c in caps:
        if str(c.get("ecu", "")).upper() != ecu_name.upper():
            continue
        if not c.get("payload"):
            continue
        latest[str(c.get("pid", "")).upper()] = c
    return latest


def _pids_latest_records(ecu_def: dict | None, ecu_name: str) -> list[dict]:
    """One record per defined PID with its latest decoded parameter values.

    Values are the *decoded* parameters (name -> formatted value string) from the
    most recent capture of that PID — never raw hex. PIDs with no capture, or no
    parameters defined, are still listed (so the view shows *all* available PIDs).
    """
    from canlib.commands._captures_query import _decoded_preview

    latest = _latest_capture_by_pid(ecu_name)
    out: list[dict] = []
    for pid_code, pid_def in (ecu_def or {}).get("pids", {}).items():
        if not isinstance(pid_def, dict):
            continue
        code = str(pid_code).upper()
        status = pid_status(pid_def)
        n_params = len(pid_def.get("parameters", {}) or {})
        cap = latest.get(code)
        rec: dict = {
            "pid": code,
            "status": status,
            "n_params": n_params,
            "values": None,
            "date": None,
            "time": None,
            "vehicle_states": None,
        }
        if cap is not None:
            rec["values"] = _decoded_preview(cap) or {}
            rec["date"] = cap.get("date")
            rec["time"] = cap.get("time")
            rec["vehicle_states"] = list(cap.get("vehicle_states") or [])
        out.append(rec)
    out.sort(key=lambda r: str(r["pid"]))
    return out


def _wrap_pairs(pairs: list[str], width: int, indent: str) -> list[str]:
    """Wrap ``NAME=value`` pairs into lines no wider than ``width`` (2-space gap)."""
    lines: list[str] = []
    cur = indent
    for p in pairs:
        add = p if cur == indent else "  " + p
        if len(cur) + len(add) > width and cur != indent:
            lines.append(cur)
            cur = indent + p
        else:
            cur += add
    if cur != indent:
        lines.append(cur)
    return lines


def cmd_pids(info: dict, tx_id: int, ecu_def: dict | None, as_json: bool) -> int:
    """Compact per-PID view: every defined PID + its latest decoded state.

    Shows the *decoded* parameter values (not raw hex) from the most recent
    capture of each PID — a quick "what does this ECU currently report?" glance.
    Points at `canair captures`/`canair decode` for full history and statistics.
    """
    name = info.get("name") or f"0x{tx_id:03X}"
    records = _pids_latest_records(ecu_def, name)

    if as_json:
        json.dump(
            {"ecu": name, "tx": f"0x{tx_id:03X}", "pids": records},
            sys.stdout,
            indent=2,
            default=str,
        )
        print()
        return 0

    title = f"{_BOLD}{_CYAN}{name}{_RESET}"
    if info.get("alias"):
        title += f" {_DIM}(alias: {info['alias']}){_RESET}"
    print(f"\n  {title} {_DIM}(0x{tx_id:03X}){_RESET} — latest decoded state")

    if ecu_def is None:
        print(f"\n  {_YELLOW}No PID definitions{_RESET} {_DIM}(identity-only module){_RESET}\n")
        return 0
    if not records:
        print(f"\n  {_YELLOW}No PIDs defined for {name}.{_RESET}\n")
        return 0

    n_with = sum(1 for r in records if r["values"])
    print(f"  {_DIM}{len(records)} PIDs · {n_with} with a recent capture{_RESET}\n")

    from canlib.states import join_states

    width = 96
    for r in records:
        flags = []
        if r["status"] != "active":
            flags.append(f"{_DIM}{r['status']}{_RESET}")
        # Context (state/date) for the capture the values came from.
        ctx = ""
        if r["values"]:
            st = join_states(r["vehicle_states"])
            when = " ".join(x for x in [r.get("date") or "", r.get("time") or ""] if x).strip()
            bits = [b for b in [f"[{st}]" if st else "", when] if b]
            ctx = f"  {_DIM}{' · '.join(bits)}{_RESET}" if bits else ""
        flag_str = ("  " + " ".join(flags)) if flags else ""
        print(f"  {_CYAN}{r['pid']:<8}{_RESET}{ctx}{flag_str}")

        if r["values"]:
            pairs = [f"{k}={v}" for k, v in r["values"].items()]
            for line in _wrap_pairs(pairs, width, "      "):
                print(line)
        elif r["n_params"] == 0:
            print(f"      {_DIM}(no parameters defined){_RESET}")
        else:
            print(
                f"      {_YELLOW}no capture{_RESET} {_DIM}({r['n_params']} params defined){_RESET}"
            )

    print(
        f"\n  {_DIM}Latest values only. Full history/diff: "
        f"`canair captures {name} <PID>` · stats: `canair decode {name} <PID> --stats`{_RESET}\n"
    )
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
    parser.set_defaults(func=group_help("_ecu_group_parser"), _ecu_group_parser=parser)
    return parser


def _add_show_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "show",
        help="List ECUs, or show one ECU's details and PID stats",
        description="List ECUs, or show one ECU's details and PID stats.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    parser.add_argument(
        "ecu",
        nargs="?",
        help="ECU name, alias, or hex TX/RX id (omit to list all)",
    ).completer = _ecu_completer
    parser.add_argument(
        "view",
        nargs="?",
        choices=["pids"],
        help="'pids': compact per-PID view with each PID's latest decoded state "
        "(e.g. `canair ecu BMS pids`)",
    )
    parser.add_argument(
        "--sort",
        choices=list(_SORT_COLUMNS),
        default="bus",
        help="List ordering: 'bus' (default; group by CAN segment) or by column: "
        "name/tx/proto (ascending), pids/verif/caps (descending)",
    )
    parser.add_argument(
        "-c",
        "--captures",
        action="store_true",
        help="Include per-ECU/PID capture counts (parses all captures — slower)",
    )
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
    from canlib.can_buses import bus_names

    ecus = load_ecus()
    pids_data = load_pids()
    labels = bus_names()
    with_captures = getattr(args, "captures", False)

    if not args.ecu:
        records = _list_records(
            ecus, pids_data, with_captures=with_captures, sort=getattr(args, "sort", "bus")
        )
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

    if getattr(args, "view", None) == "pids":
        return cmd_pids(info, tx_id, ecu_def, args.json)

    rec = _detail_record(
        info, tx_id, pids_name, ecu_def, bus_labels=labels, with_captures=with_captures
    )
    return cmd_detail(rec, args.json)
