"""The per-ECU summary behind ``canair ecu``: stats, list rows and latest decoded values."""

from __future__ import annotations

from collections import Counter

from canair.captures.types import CaptureEntry
from canair.ecus.pid_status import pid_status
from canair.ecus.registry import rx_addr_str
from canair.profile.model import Profile
from canair.states.selectors import ecu_states


def pids_def_for_tx(pids_data: dict, tx_id: int) -> tuple[str | None, dict | None]:
    """Find the ecus/ ECU entry whose ``tx_id`` matches, returning (name, def)."""
    for ecu_name, ecu_def in pids_data.get("ecus", {}).items():
        if isinstance(ecu_def, dict) and ecu_def.get("tx_id") == tx_id:
            return ecu_name, ecu_def
    return None, None


def pid_stats(ecu_def: dict) -> dict:
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


def captures_by_pid(ecu_name: str, *, profile: Profile) -> tuple[Counter, int]:
    """Return (per-PID capture counts, total captures) for an ECU name."""
    try:
        from canair.captures.store import load_all_captures

        caps = load_all_captures(profile=profile)
    except Exception:
        return Counter(), 0
    per_pid: Counter = Counter()
    total = 0
    for c in caps:
        if str(c.get("ecu", "")).upper() == ecu_name.upper():
            total += 1
            per_pid[str(c.get("pid", "")).upper()] += 1
    return per_pid, total


def all_captures_by_ecu(profile: Profile) -> Counter:
    """Total capture counts keyed by canonical ECU short name (upper-cased)."""
    try:
        from canair.captures.store import load_all_captures

        caps = load_all_captures(profile=profile)
    except Exception:
        return Counter()
    return Counter(str(c.get("ecu", "")).upper() for c in caps)


# Sort columns → (record key, direction). Numeric columns sort descending
# (most-populated first); string/hex columns sort ascending. `bus` is the
# default (group by CAN segment). Order here also drives the --sort choices.
SORT_COLUMNS = {
    "bus": ("can_bus", "asc"),
    "name": ("name", "asc"),
    "tx": ("tx_id", "asc"),
    "proto": ("id_protocol", "asc"),
    "pids": ("pids", "desc"),
    "verif": ("verified", "desc"),
    "caps": ("captures", "desc"),
    "states": ("states", "asc"),
}


def sort_records(records: list[dict], sort: str) -> None:
    """Sort ``records`` in place by the named column (see ``SORT_COLUMNS``).

    Numeric columns sort descending, string/hex columns ascending; ``name`` is
    always the tie-breaker (ascending). Missing/unbussed values sort last.
    """
    key_name, direction = SORT_COLUMNS.get(sort, SORT_COLUMNS["bus"])
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


def list_records(ecus: dict, pids_data: dict, sort: str = "bus", *, profile: Profile) -> list[dict]:
    """Build one record per registry ECU, joined to ecus/ by tx_id.

    Sorted by ``bus`` (default; group by CAN segment, unbussed ECUs last) or any
    other column in ``SORT_COLUMNS`` — numeric columns descending, string/hex
    ascending, with ``name`` as the tie-breaker.
    """
    cap_counts = all_captures_by_ecu(profile=profile)
    records = []
    for tx_id, info in ecus.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name") or f"0x{tx_id:03X}"
        _pids_name, ecu_def = pids_def_for_tx(pids_data, tx_id)
        rec = {
            "name": name,
            "alias": info.get("alias"),
            "tx_id": tx_id,
            "tx": f"0x{tx_id:03X}",
            "rx": rx_addr_str(tx_id, profile=profile),
            "description": info.get("description", ""),
            "id_protocol": info.get("id_protocol"),
            "can_bus": info.get("can_bus"),
            "has_pids": ecu_def is not None,
            # States the ECU is readable/awake in (ECU-level, else PID union).
            "states": ecu_states(ecu_def, profile=profile) if ecu_def is not None else [],
        }
        if ecu_def is not None:
            rec.update(pid_stats(ecu_def))
            rec["captures"] = cap_counts.get(name.upper(), 0)
        records.append(rec)
    sort_records(records, sort)
    return records


def pid_details(ecu_def: dict, per_pid: Counter) -> list[dict]:
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


def latest_capture_by_pid(ecu_name: str, *, profile: Profile) -> dict[str, CaptureEntry]:
    """Map each PID (upper-cased) to this ECU's most recent payload capture.

    Capture files are chronological, so the last-seen payload entry per PID wins.
    Returns an empty dict when captures can't be loaded.
    """
    try:
        from canair.captures.store import load_all_captures

        caps = load_all_captures(profile=profile)
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


def pids_latest_records(ecu_def: dict | None, ecu_name: str, *, profile: Profile) -> list[dict]:
    """One record per defined PID with its latest decoded parameter values.

    Values are the *decoded* parameters (name -> formatted value string) from the
    most recent capture of that PID — never raw hex. PIDs with no capture, or no
    parameters defined, are still listed (so the view shows *all* available PIDs).
    """
    from canair.captures.store import decoded_preview

    latest = latest_capture_by_pid(ecu_name, profile=profile)
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
            rec["values"] = decoded_preview(cap, profile=profile) or {}
            rec["date"] = cap.get("date")
            rec["time"] = cap.get("time")
            rec["vehicle_states"] = list(cap.get("vehicle_states") or [])
        out.append(rec)
    out.sort(key=lambda r: str(r["pid"]))
    return out
