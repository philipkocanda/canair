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
  canair ecu 0x7EC           # hex RX id resolves too (the ECU's response address)
  canair ecu --states        # add a STATES column (states each ECU is readable in)
  canair ecu --sort states   # group the list by vehicle state
  canair ecu BMS --json      # machine-readable
  canair ecu --json          # all ECUs as JSON
  canair ecu HVAC edit       # open HVAC's ecus/ YAML in $EDITOR (TTY only)
  canair ecu rename Unknown-7D5 EPB   # rename an ECU (rewrites key + file)

Columns & legend:
  BUS    physical CAN bus segment(s) the ECU sits on (profile-specific codes,
         e.g. Hyundai B-CAN/P-CAN/C-CAN/MM-CAN/H-CAN/ALL); some ECUs span two
         (shown `H-CAN/P-CAN`). Blank (`—`) when unknown. The list is sorted by
         BUS by default. Shown as the last (widest, most-variable) column so
         the numeric columns stay aligned.
  PIDS   number of active (non-ignored) PIDs/DIDs defined.
  VERIF  verified/total parameters (green when all verified).
  CAPS   number of saved captures for the ECU.
  cap    in the per-PID detail view, "N cap" = number of saved captures for
         that individual PID.
  STATES the vehicle states the ECU is readable/awake in — its ECU-level
         `vehicle_states`, or the union of its PIDs' when that's unset. Opt-in
         (`--states`); shown after BUS.

  Sort with `--sort {bus,name,tx,proto,pids,verif,caps,states}`: string/hex
  columns (bus, name, tx, proto, states) ascending; numeric columns (pids,
  verif, caps) descending. `name` breaks ties.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from canlib import ansi
from canlib.capture_types import CaptureEntry
from canlib.commands._group import group_help
from canlib.commands._hexarg import HexArgError, parse_hex_arg
from canlib.commands._hints import ecu_completer as _ecu_completer
from canlib.ecus import load_ecus, resolve_tx, rx_addr_str
from canlib.edit_echo import echo_edit
from canlib.pids import load_pids, pid_status
from canlib.profile import require_writable_definitions
from canlib.states import ecu_states

NAME = "ecu"

# ANSI colors (match the sibling audit tools: research, coverage)
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
        from canlib.capture_store import load_all_captures

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
        from canlib.capture_store import load_all_captures

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
    "states": ("states", "asc"),
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
        if key_name == "states":
            # Group by resolved states; stateless ECUs (key "~") sort last.
            return "/".join(r["states"]) if r.get("states") else "~"
        if key_name == "name":
            return str(r["name"]).upper()
        value = r.get(key_name)
        if key_name == "tx_id":
            # Hex TX id: sort ascending by its numeric value (== hex order).
            return value if value is not None else 0
        if reverse:
            # Numeric column: sort descending, but absent values (None — e.g.
            # a registry-only ECU with no PID definitions) sort last.
            # reverse=True flips the list, so rank present > absent here.
            return (0 if value is None else 1, value or 0)
        # String column (id_protocol): missing values sort last.
        return str(value).upper() if value is not None else "~"

    records.sort(key=key, reverse=reverse)


def _list_records(ecus: dict, pids_data: dict, sort: str = "bus") -> list[dict]:
    """Build one record per registry ECU, joined to ecus/ by tx_id.

    Sorted by ``bus`` (default; group by CAN segment, unbussed ECUs last) or any
    other column in ``_SORT_COLUMNS`` — numeric columns descending, string/hex
    ascending, with ``name`` as the tie-breaker.
    """
    cap_counts = _all_captures_by_ecu()
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
            # States the ECU is readable/awake in (ECU-level, else PID union).
            "states": ecu_states(ecu_def) if ecu_def is not None else [],
        }
        if ecu_def is not None:
            rec.update(_pid_stats(ecu_def))
            rec["captures"] = cap_counts.get(name.upper(), 0)
        records.append(rec)
    _sort_records(records, sort)
    return records


def cmd_list(records: list[dict], as_json: bool, show_states: bool = False) -> int:
    if as_json:
        json.dump(records, sys.stdout, indent=2, default=str)
        print()
        return 0

    n_pids = sum(1 for r in records if r["has_pids"])
    print(
        f"\n  {ansi.BOLD}ECUs{ansi.RESET} — {len(records)} in registry, {n_pids} with PID definitions\n"
    )

    # Column header. BUS (and optional STATES) are last: they're the widest,
    # most-variable columns, so trailing them keeps the numeric columns aligned.
    bus_hdr = f"{'BUS':<12}" if show_states else "BUS"
    states_hdr = "  STATES" if show_states else ""
    print(
        f"  {ansi.DIM}{'NAME':<12} {'TX':<6} {'PROTO':<8} "
        f"{'PIDS':>4} {'VERIF':>7} {'CAPS':>5}  {bus_hdr}{states_hdr}{ansi.RESET}"
    )

    for r in records:
        name = r["name"]
        proto = r.get("id_protocol") or "?"
        bus = "/".join(r["can_bus"]) if r.get("can_bus") else "—"
        # Pad BUS only when a STATES column follows it (else it's trailing).
        bus_disp = f"{bus:<12}" if show_states else bus
        states = "/".join(r["states"]) if r.get("states") else "—"
        states_seg = f"  {states}" if show_states else ""
        if not r["has_pids"]:
            # Registry-only module: no PID data to summarise.
            print(
                f"  {ansi.CYAN}{name:<12}{ansi.RESET} {r['tx']:<6} {proto:<8} "
                f"{ansi.DIM}{'—':>4} {'—':>7} {'—':>5}{ansi.RESET}  {ansi.CYAN}{bus_disp}{ansi.RESET}{states_seg}"
            )
            continue
        params = r["params"]
        verified = r["verified"]
        vcolor = (
            ansi.GREEN if params and verified == params else (ansi.YELLOW if verified else ansi.DIM)
        )
        vstr = f"{verified}/{params}"
        caps = r.get("captures")
        if caps:
            cstr = f"{caps:>5}"
        else:
            cstr = f"{ansi.YELLOW}{'0':>5}{ansi.RESET}"
        print(
            f"  {ansi.CYAN}{name:<12}{ansi.RESET} {r['tx']:<6} {proto:<8} "
            f"{r['pids']:>4} {vcolor}{vstr:>7}{ansi.RESET} "
            f"{cstr}  {ansi.CYAN}{bus_disp}{ansi.RESET}{states_seg}"
        )
    print()
    return 0


# ── detail mode ─────────────────────────────────────────────────────────────


def _detail_record(
    info: Mapping[str, Any],
    tx_id: int,
    pids_name: str | None,
    ecu_def: dict | None,
    bus_labels: dict | None = None,
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
    per_pid, total = _captures_by_pid(name)
    if ecu_def is not None:
        rec["stats"] = _pid_stats(ecu_def)
        rec["vehicle_states"] = ecu_def.get("vehicle_states")
        rec["captures"] = total
        rec["pid_list"] = _pid_details(ecu_def, per_pid)
    else:
        rec["stats"] = None
        rec["captures"] = total
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
    out.sort(key=lambda p: str(p["pid"]))
    return out


def cmd_detail(rec: dict, as_json: bool) -> int:
    if as_json:
        json.dump(rec, sys.stdout, indent=2, default=str)
        print()
        return 0

    # Header
    title = f"{ansi.BOLD}{ansi.CYAN}{rec['name']}{ansi.RESET}"
    if rec.get("alias"):
        title += f" {ansi.DIM}(alias: {rec['alias']}){ansi.RESET}"
    print(f"\n  {title}")
    if rec.get("description"):
        print(f"  {rec['description']}")

    # Addresses / protocol
    proto = rec.get("id_protocol") or "?"
    print(
        f"\n  {ansi.DIM}TX{ansi.RESET} {rec['tx']}    {ansi.DIM}RX{ansi.RESET} {rec['rx']}    "
        f"{ansi.DIM}protocol{ansi.RESET} {proto}"
    )
    if rec.get("can_bus"):
        labels = rec.get("can_bus_labels") or rec["can_bus"]
        # Render "CODE (Name)" when a human label differs from the bare code.
        parts = [
            f"{code} ({label})" if label and label != code else code
            for code, label in zip(rec["can_bus"], labels, strict=False)
        ]
        print(f"  {ansi.DIM}CAN bus{ansi.RESET} {', '.join(parts)}")

    # Identity fields
    if rec["identity"]:
        print(f"\n  {ansi.BOLD}Identity{ansi.RESET}")
        for key, label in _IDENTITY_FIELDS:
            if key in rec["identity"]:
                print(f"    {label:<12} {rec['identity'][key]}")

    # Stats
    stats = rec.get("stats")
    if stats is None:
        print(
            f"\n  {ansi.YELLOW}No PID definitions{ansi.RESET} "
            f"{ansi.DIM}(no pids: — identity-only module){ansi.RESET}"
        )
    else:
        print(f"\n  {ansi.BOLD}Stats{ansi.RESET}")
        verified = stats["verified"]
        params = stats["params"]
        vcolor = (
            ansi.GREEN if params and verified == params else (ansi.YELLOW if verified else ansi.DIM)
        )
        print(
            f"    {'PIDs':<14} {stats['pids']}"
            + (f"  {ansi.DIM}(+{stats['ignored']} ignored){ansi.RESET}" if stats["ignored"] else "")
        )
        print(f"    {'Parameters':<14} {params}")
        print(f"    {'Verified':<14} {vcolor}{verified}/{params}{ansi.RESET}")
        print(f"    {'Captures':<14} {rec['captures']}")
        if stats["research_total"]:
            print(
                f"    {'Research':<14} {stats['research_open']} open "
                f"{ansi.DIM}/ {stats['research_total']} total{ansi.RESET}"
            )
        if stats["iocontrol"] or stats["iocontrol_discoveries"]:
            extra = (
                f"  {ansi.DIM}(+{stats['iocontrol_discoveries']} discoveries){ansi.RESET}"
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
        print(f"\n  {ansi.BOLD}PIDs{ansi.RESET}")
        for p in rec["pid_list"]:
            flags = []
            status = p.get("status", "active")
            if status != "active":
                flags.append(f"{ansi.DIM}{status}{ansi.RESET}")
            caps = p["captures"]
            if not caps:
                flags.append(f"{ansi.YELLOW}no capture{ansi.RESET}")
            flag_str = ("  " + " ".join(flags)) if flags else ""
            vcolor = ansi.GREEN if p["params"] and p["verified"] == p["params"] else ansi.DIM
            cap_seg = f"  {ansi.DIM}{caps} cap{ansi.RESET}"
            print(
                f"    {ansi.CYAN}{p['pid']:<8}{ansi.RESET} "
                f"{p['params']:>2}p  {vcolor}{p['verified']:>2} verified{ansi.RESET}"
                f"{cap_seg}{flag_str}"
            )
        print(
            f"\n  {ansi.DIM}Tip: `canair ecu {rec['name']} pids` shows each PID's "
            f"latest decoded state.{ansi.RESET}"
        )

    # Notes last (can be long/multiline)
    if rec.get("notes"):
        notes = " ".join(str(rec["notes"]).split())
        print(f"\n  {ansi.BOLD}Notes{ansi.RESET}\n    {notes}")
    print()
    return 0


def _latest_capture_by_pid(ecu_name: str) -> dict[str, CaptureEntry]:
    """Map each PID (upper-cased) to this ECU's most recent payload capture.

    Capture files are chronological, so the last-seen payload entry per PID wins.
    Returns an empty dict when captures can't be loaded.
    """
    try:
        from canlib.capture_store import load_all_captures

        caps = load_all_captures()
    except Exception:
        return {}
    latest: dict[str, CaptureEntry] = {}
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
    from canlib.capture_store import decoded_preview

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
            rec["values"] = decoded_preview(cap) or {}
            rec["date"] = cap.get("date")
            rec["time"] = cap.get("time")
            rec["vehicle_states"] = list(cap.get("vehicle_states") or [])
        out.append(rec)
    out.sort(key=lambda r: str(r["pid"]))
    return out


def _value_grid(values: Mapping[str, Any], width: int, indent: str, ncols: int = 2) -> list[str]:
    """Render ``name value`` pairs as an aligned multi-column grid.

    Names are left-aligned and values right-aligned within per-column widths so
    the pairs line up in tidy columns (rather than a flat wrapped blob). Each
    cell is ``name … value``; the number of columns is reduced automatically
    when a single row wouldn't fit ``width``.
    """
    items = [(str(k), str(v)) for k, v in values.items()]
    if not items:
        return []

    gap = "   "  # between columns
    # Shrink ncols until a row fits the target width (or we're down to 1 column).
    while ncols > 1:
        rows = [items[i : i + ncols] for i in range(0, len(items), ncols)]
        name_w = [max((len(r[c][0]) for r in rows if c < len(r)), default=0) for c in range(ncols)]
        val_w = [max((len(r[c][1]) for r in rows if c < len(r)), default=0) for c in range(ncols)]
        row_w = len(indent) + sum(name_w) + sum(val_w) + 2 * ncols + len(gap) * (ncols - 1)
        if row_w <= width:
            break
        ncols -= 1

    rows = [items[i : i + ncols] for i in range(0, len(items), ncols)]
    name_w = [
        max((len(rows[r][c][0]) for r in range(len(rows)) if c < len(rows[r])), default=0)
        for c in range(ncols)
    ]
    val_w = [
        max((len(rows[r][c][1]) for r in range(len(rows)) if c < len(rows[r])), default=0)
        for c in range(ncols)
    ]

    lines: list[str] = []
    for row in rows:
        cells = [
            f"{ansi.DIM}{n:<{name_w[c]}}{ansi.RESET} {ansi.BOLD}{v:>{val_w[c]}}{ansi.RESET}"
            for c, (n, v) in enumerate(row)
        ]
        lines.append(indent + gap.join(cells))
    return lines


def cmd_pids(info: Mapping[str, Any], tx_id: int, ecu_def: dict | None, as_json: bool) -> int:
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

    title = f"{ansi.BOLD}{ansi.CYAN}{name}{ansi.RESET}"
    if info.get("alias"):
        title += f" {ansi.DIM}(alias: {info['alias']}){ansi.RESET}"
    print(f"\n  {title} {ansi.DIM}(0x{tx_id:03X}){ansi.RESET} — latest decoded state")

    if ecu_def is None:
        print(
            f"\n  {ansi.YELLOW}No PID definitions{ansi.RESET} {ansi.DIM}(identity-only module){ansi.RESET}\n"
        )
        return 0
    if not records:
        print(f"\n  {ansi.YELLOW}No PIDs defined for {name}.{ansi.RESET}\n")
        return 0

    n_with = sum(1 for r in records if r["values"])
    print(
        f"  {ansi.DIM}{len(records)} PIDs · {n_with} with a recent capture "
        f"(each PID's newest value + the vehicle state it was read in){ansi.RESET}\n"
    )

    from canlib.states import join_states

    width = 96
    for r in records:
        flags = []
        if r["status"] != "active":
            flags.append(f"{ansi.YELLOW}{r['status']}{ansi.RESET}")
        # Context (state/date) for the capture the values came from.
        ctx = ""
        if r["values"]:
            st = join_states(r["vehicle_states"])
            when = " ".join(x for x in [r.get("date") or "", r.get("time") or ""] if x).strip()
            bits = []
            if st:
                bits.append(
                    f"{ansi.DIM}vehicle_state{ansi.RESET} {ansi.BOLD}{ansi.GREEN}{st}{ansi.RESET}"
                )
            if when:
                bits.append(f"{ansi.CYAN}{when}{ansi.RESET}")
            ctx = (
                f"  {ansi.DIM}·{ansi.RESET} " + f" {ansi.DIM}·{ansi.RESET} ".join(bits)
                if bits
                else ""
            )
        flag_str = ("  " + " ".join(flags)) if flags else ""
        print(f"  {ansi.BOLD}{ansi.CYAN}{r['pid']}{ansi.RESET}{ctx}{flag_str}")

        if r["values"]:
            for line in _value_grid(r["values"], width, "      "):
                print(line)
        elif r["n_params"] == 0:
            print(f"      {ansi.DIM}(no parameters defined){ansi.RESET}")
        else:
            print(
                f"      {ansi.YELLOW}no capture{ansi.RESET} {ansi.DIM}({r['n_params']} params defined){ansi.RESET}"
            )

    print(
        f"\n  {ansi.DIM}Latest values only. Full history/diff: "
        f"`canair captures {name} <PID>` · stats: `canair decode {name} <PID> --stats`{ansi.RESET}\n"
    )
    return 0


def cmd_edit(info: Mapping[str, Any], tx_id: int) -> int:
    """Open the ECU's ``ecus/<name>.yaml`` file in ``$EDITOR`` (TTY only).

    A human escape hatch for bulk/awkward edits the surgical `canair pids`
    subcommands don't reach. It refuses to run when stdin/stdout isn't a
    terminal so agents can't drive it — they must use the validated `canair
    pids` editors instead. After the editor exits, the file is re-validated and
    any errors are surfaced (the edit is *not* auto-reverted — the user owns it).
    """
    import os
    import shutil
    import subprocess

    from canlib.ecus_edit import find_ecu_file_by_tx

    name = info.get("name") or f"0x{tx_id:03X}"

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            f"{ansi.RED}`canair ecu {name} edit` requires an interactive terminal.{ansi.RESET}\n"
            f"{ansi.DIM}It opens $EDITOR by design, so it can't be scripted or driven by an "
            f"agent.\nUse the surgical, validated editors instead — e.g. "
            f"`canair pids upsert-param`, `canair pids set-can-bus`, "
            f"`canair ecu add`.{ansi.RESET}",
            file=sys.stderr,
        )
        return 1

    path = find_ecu_file_by_tx(tx_id)
    if path is None or not path.exists():
        print(
            f"{ansi.RED}No ecus/ file found for {name} (0x{tx_id:03X}).{ansi.RESET}\n"
            f"{ansi.DIM}Register it first with `canair ecu add`.{ansi.RESET}",
            file=sys.stderr,
        )
        return 1

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = next((e for e in ("nano", "vim", "vi") if shutil.which(e)), None)
    if not editor:
        print("No editor found. Set $EDITOR or edit directly:", file=sys.stderr)
        print(f"  {path}", file=sys.stderr)
        return 1

    rc = subprocess.call([*editor.split(), str(path)])
    if rc != 0:
        print(f"{ansi.YELLOW}Editor exited with status {rc}; skipping validation.{ansi.RESET}")
        return rc

    # Re-validate the edited file (not auto-reverted — the user owns the edit).
    from canlib.commands.validate.pids import _run_pids

    print(f"\n{ansi.DIM}Validating {path.name} …{ansi.RESET}")
    vrc = _run_pids([str(path)], stats=False)
    if vrc:
        print(
            f"{ansi.YELLOW}Validation failed — re-run `canair ecu {name} edit` to fix, "
            f"or `canair validate pids`.{ansi.RESET}",
            file=sys.stderr,
        )
    return vrc


def _unknown_ecu(value: str, records: list[dict]) -> int:
    print(f"{ansi.RED}Unknown ECU {value!r}.{ansi.RESET}", file=sys.stderr)
    names = [r["name"] for r in records]
    print("\nAvailable ECUs:", file=sys.stderr)
    print("  " + ", ".join(names), file=sys.stderr)
    return 1


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect ECUs (list/detail), add one, or rename one: show | add | rename",
        description="Inspect or edit the profile's ECU registry.\n"
        "  show     list ECUs, or show one ECU's details and PID stats (default)\n"
        "  add      register a new ECU in the active profile's ecus/ (offline)\n"
        "  rename   rename an ECU (rewrites its key and ecus/ file)\n\n"
        "A bare `canair ecu` or `canair ecu BMS` is shorthand for `canair ecu show …`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:")[1] if "Examples:" in __doc__ else "",
    )
    kinds = parser.add_subparsers(dest="ecu_kind", metavar="<kind>")
    _add_show_parser(kinds)
    _add_add_parser(kinds)
    _add_rename_parser(kinds)
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
        choices=["pids", "edit"],
        help="'pids': compact per-PID view with each PID's latest decoded state "
        "(e.g. `canair ecu BMS pids`); "
        "'edit': open the ECU's ecus/ YAML file in $EDITOR (TTY only — agents "
        "must use `canair pids` instead; e.g. `canair ecu HVAC edit`)",
    )
    parser.add_argument(
        "--sort",
        choices=list(_SORT_COLUMNS),
        default="bus",
        help="List ordering: 'bus' (default; group by CAN segment) or by column: "
        "name/tx/proto/states (ascending), pids/verif/caps (descending)",
    )
    parser.add_argument(
        "--states",
        action="store_true",
        help="Add a STATES column: the vehicle states each ECU is readable/awake in "
        "(ECU-level vehicle_states, else the union of its PIDs')",
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
        "  canair ecu add 0x704 --name BMS --rx-id 0x784   # non-standard response addr\n"
        "  canair ecu add 0x18DB33F1 --name EVC --mode normal_29bit --rx-id 0x18DAF1DB --fc-id 0x18DADBF1\n"
        "  canair ecu add 770 --name IGPM --notes 'Seeded offline; no PIDs yet'\n",
    )
    parser.add_argument("tx", metavar="TX", help="ECU TX id (hex, e.g. 7C6 or 0x7C6)")
    parser.add_argument("--name", help="ECU short name (default: Unknown-<TX>)")
    parser.add_argument("--description", help="Human description")
    parser.add_argument(
        "--id-protocol", dest="id_protocol", help="Identity protocol (UDS | KWP2000)"
    )
    parser.add_argument(
        "--rx-id",
        dest="rx_id",
        help="CAN response address override (hex, e.g. 0x784) — for an ECU whose "
        "response addr isn't tx_id + the profile's addressing.rx_offset",
    )
    parser.add_argument(
        "--mode",
        help="Addressing mode (normal_11bit | normal_29bit | normal_fixed_29bit | "
        "normal_extended_11bit | extended_29bit) — required to seed a 29-bit ECU",
    )
    parser.add_argument(
        "--target-address",
        dest="target_address",
        help="ISO-TP target extension byte (hex) — extended-11-bit/29-bit modes",
    )
    parser.add_argument(
        "--source-address",
        dest="source_address",
        help="ISO-TP tester (source) byte (hex, default 0xF1) — extended-11-bit modes",
    )
    parser.add_argument(
        "--fc-id",
        dest="fc_id",
        help="Flow-control arbitration override (hex) — functional-TX / physical-RX ECUs",
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
    from canlib.ecus_edit import EcusEditError, find_ecu_file_by_tx, register_ecu, tx_key

    try:
        tx_id = int(str(args.tx), 16)
    except ValueError:
        print(
            f"{ansi.RED}Invalid TX id {args.tx!r} — expected hex (e.g. 7C6).{ansi.RESET}",
            file=sys.stderr,
        )
        return 1

    try:
        rx_id = parse_hex_arg(getattr(args, "rx_id", None), "rx-id")
        target_address = parse_hex_arg(getattr(args, "target_address", None), "target-address")
        source_address = parse_hex_arg(getattr(args, "source_address", None), "source-address")
        fc_id = parse_hex_arg(getattr(args, "fc_id", None), "fc-id")
    except HexArgError as e:
        print(str(e), file=sys.stderr)
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
    if args.dir is None:
        require_writable_definitions()
    try:
        wrote = register_ecu(
            tx_id,
            name=args.name,
            overwrite=args.overwrite,
            ecus_dir=args.dir,
            rx_id=rx_id,
            mode=getattr(args, "mode", None),
            target_address=target_address,
            source_address=source_address,
            fc_id=fc_id,
            **fields,
        )
    except EcusEditError as e:
        print(f"{ansi.RED}{e}{ansi.RESET}", file=sys.stderr)
        return 1

    disp = tx_key(tx_id)
    label = args.name or f"Unknown-{tx_id:03X}"
    if wrote:
        fpath = find_ecu_file_by_tx(tx_id, args.dir)
        if fpath is None:  # pragma: no cover - a write that left no file is a bug
            print(f"{ansi.GREEN}  ✓ registered {label} ({disp}){ansi.RESET}")
        else:
            echo_edit(f"registered {label} ({disp})", fpath)
    else:
        print(f"{ansi.DIM}  {label} ({disp}) already registered; nothing to change.{ansi.RESET}")
    return 0


def _add_rename_parser(kinds) -> argparse.ArgumentParser:
    parser = kinds.add_parser(
        "rename",
        help="Rename an ECU (rewrites its key and ecus/ file)",
        description="Rename an ECU in the active profile.\n\n"
        "An ECU's name is its top-level YAML key plus its ecus/<name>.yaml "
        "filename, so this rewrites the key and moves the file. The write is "
        "validated and comment-preserving (never hand-edit ecus/). Use it to "
        "promote a placeholder (e.g. Unknown-7D5) to a real name once identified.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  canair ecu rename Unknown-7D5 EPB\n"
        "  canair ecu rename 0x7D5 EPB          # resolve the old ECU by hex id\n",
    )
    parser.add_argument(
        "ecu", metavar="ECU", help="ECU to rename: current name, alias, or hex TX/RX id"
    ).completer = _ecu_completer
    parser.add_argument("new_name", metavar="NEW_NAME", help="New ECU short name")
    parser.add_argument(
        "--dir", type=Path, default=None, help="ecus/ directory (default: active profile)"
    )
    parser.set_defaults(func=cmd_rename)
    return parser


def cmd_rename(args) -> int:
    from canlib.ecus_edit import EcusEditError, rename_ecu, tx_key

    tx_id = resolve_tx(args.ecu)
    if tx_id is None:
        return _unknown_ecu(args.ecu, _list_records(load_ecus(), load_pids()))

    if args.dir is None:
        require_writable_definitions()
    try:
        new_path = rename_ecu(tx_id, args.new_name, ecus_dir=args.dir)
    except EcusEditError as e:
        print(f"{ansi.RED}{e}{ansi.RESET}", file=sys.stderr)
        return 1

    echo_edit(f"renamed {args.ecu} → {args.new_name} ({tx_key(tx_id)})", new_path)
    return 0


def run(args) -> int:
    from canlib.can_buses import bus_names

    ecus = load_ecus()
    pids_data = load_pids()
    labels = bus_names()

    if not args.ecu:
        records = _list_records(ecus, pids_data, sort=getattr(args, "sort", "bus"))
        if not records:
            print("No ECUs found in the active profile (see `canair profile show`).")
            return 1
        # Sorting by states implies showing the column (else the order is invisible).
        show_states = getattr(args, "states", False) or getattr(args, "sort", "bus") == "states"
        return cmd_list(records, args.json, show_states=show_states)

    tx_id = resolve_tx(args.ecu)
    info = ecus.get(tx_id) if tx_id is not None else None
    if info is None:
        return _unknown_ecu(args.ecu, _list_records(ecus, pids_data))

    # info is only non-None when tx_id resolved (see the guarded .get above).
    assert tx_id is not None
    pids_name, ecu_def = _pids_def_for_tx(pids_data, tx_id)

    if getattr(args, "view", None) == "pids":
        return cmd_pids(info, tx_id, ecu_def, args.json)

    if getattr(args, "view", None) == "edit":
        return cmd_edit(info, tx_id)

    rec = _detail_record(info, tx_id, pids_name, ecu_def, bus_labels=labels)
    return cmd_detail(rec, args.json)
