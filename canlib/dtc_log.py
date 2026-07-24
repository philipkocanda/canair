"""Persistent DTC scan history — record scans and diff against the previous one.

Each ``canair dtc … --log`` run appends a decoded snapshot to the profile's
``dtc_log.yaml`` and reports what changed since the last scan *of the same
scope* (all-ECU sweep, or a single ECU): which codes **cleared**, which are
**new**, and which **persist**. That's how you tell, next time, whether a fault
went away (e.g. after a fix or a self-heal) rather than eyeballing two dumps.

The file is tool-managed (append-only, newest entry last):

    scans:
    - timestamp: '2026-07-22T15:40:03'
      scope: all                 # 'all' or an ECU display name like 'BMS (0x7E4)'
      label: before clearing     # optional, from --label
      ecus:                      # only ECUs that had codes (empty on a clean sweep)
        AMP (0x783): {tx: '0x783', protocol: kwp, dtcs: [B2915-00, B2916-00]}
        PLC (0x733): {tx: '0x733', protocol: uds, dtcs: [C182C-00]}
    clears:
    - timestamp: '2026-07-22T16:02:11'
      type: manual               # 'manual' (ran --clear) or 'detected' (gone since last scan)
      scope: BMS (0x7E4)
      ecu: BMS (0x7E4)           # manual only
      protocol: kwp              # manual only
      group: '0xFFFF'            # manual only
      codes: [P1AAA-00]          # manual: codes present before the clear
    - timestamp: '2026-07-22T16:30:00'
      type: detected
      scope: all
      cleared: [['PLC (0x733)', 'C182C-00']]   # (ecu, code) pairs gone vs last scan

This is per-vehicle runtime state, not shared data: it lives in the active
profile's directory (naturally uncommitted for a user profile under
``~/.config/canair/profiles/``) and is gitignored for the repo-bundled profile.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def log_path(path: Path | None = None) -> Path:
    """Path to the active profile's dtc_log.yaml (or the override ``path``)."""
    if path is not None:
        return path
    from .profile import active

    return active().root / "dtc_log.yaml"


def load_log(path: Path | None = None) -> dict:
    """Load the DTC log, or an empty ``{"scans": []}`` when absent."""
    import yaml

    p = log_path(path)
    if not p.exists():
        return {"scans": []}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data.get("scans"), list):
        data["scans"] = []
    return data


def build_scan(
    scope: str,
    ecus: dict,
    *,
    label: str | None = None,
    vehicle_states: list | None = None,
    notes: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Build a scan entry. ``ecus`` maps display-name → {tx, protocol, dtcs:[codes]}."""
    entry: dict = {
        "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        "scope": scope,
    }
    if label:
        entry["label"] = label
    if vehicle_states:
        entry["vehicle_states"] = list(vehicle_states)
    if notes:
        entry["notes"] = notes
    entry["ecus"] = ecus
    return entry


def append_scan(entry: dict, path: Path | None = None) -> Path:
    """Append a scan entry to the log (comment-preserving), returning the path."""
    return _append("scans", entry, path)


def build_clear(kind: str, scope: str, *, timestamp: str | None = None, **fields) -> dict:
    """Build a clear event. ``kind`` is 'manual' (ran the clear command) or
    'detected' (codes gone since the last scan). Extra fields (ecu, protocol,
    group, codes, cleared, label) are included when not None."""
    entry: dict = {
        "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        "type": kind,
        "scope": scope,
    }
    for key, val in fields.items():
        if val is not None:
            entry[key] = val
    return entry


def append_clear(entry: dict, path: Path | None = None) -> Path:
    """Append a clear event to the log's ``clears:`` list, returning the path."""
    return _append("clears", entry, path)


def _append(section: str, entry: dict, path: Path | None = None) -> Path:
    """Append ``entry`` to the named top-level list, preserving comments."""
    from ruamel.yaml.comments import CommentedMap

    from .yaml_rt import dump as _dump
    from .yaml_rt import round_trip_yaml as _yaml

    p = log_path(path)
    data = None
    if p.exists():
        with open(p) as f:
            data = _yaml().load(f)
    if not isinstance(data, dict):
        data = CommentedMap()
    if data.get(section) is None:
        data[section] = []
    data[section].append(entry)
    with open(p, "w") as f:
        _dump(data, f)
    return p


def latest_matching(scope: str, path: Path | None = None) -> dict | None:
    """The most recent logged scan whose scope matches (like-for-like compare)."""
    for entry in reversed(load_log(path).get("scans", [])):
        if entry.get("scope") == scope:
            return entry
    return None


def _pairs(entry: dict) -> set[tuple[str, str]]:
    """The set of (ecu, code) pairs recorded in a scan entry."""
    out: set[tuple[str, str]] = set()
    for ecu, info in (entry.get("ecus") or {}).items():
        for code in (info or {}).get("dtcs") or []:
            out.add((ecu, code))
    return out


def diff_scans(previous: dict, current: dict) -> dict:
    """Return ``{cleared, new, persisting}`` lists of (ecu, code) pairs."""
    prev, curr = _pairs(previous), _pairs(current)
    return {
        "cleared": sorted(prev - curr),
        "new": sorted(curr - prev),
        "persisting": sorted(prev & curr),
    }


def format_diff(diff: dict, previous: dict) -> list[str]:
    """Human-readable lines comparing the current scan to ``previous``."""
    lines = [f"  Compared to last scan ({previous.get('timestamp', '?')}):"]
    cleared, new = diff["cleared"], diff["new"]
    if cleared:
        lines.append(
            f"    \033[92m✓ cleared ({len(cleared)})\033[0m: "
            + ", ".join(f"{e} {c}" for e, c in cleared)
        )
    if new:
        lines.append(
            f"    \033[91m+ new ({len(new)})\033[0m: " + ", ".join(f"{e} {c}" for e, c in new)
        )
    lines.append(f"    = still present: {len(diff['persisting'])}")
    if not cleared and not new:
        lines.append("    \033[2m(no change since last scan)\033[0m")
    return lines


def prior_matching(scope: str, before: str, path: Path | None = None) -> dict | None:
    """The most recent same-scope scan logged strictly *before* the ``before``
    timestamp — the entry a stored scan was diffed against when it was recorded."""
    match = None
    for entry in load_log(path).get("scans", []):
        if entry.get("scope") == scope and entry.get("timestamp", "") < before:
            match = entry
    return match


def render_scan(entry: dict, previous: dict | None = None) -> list[str]:
    """Human-readable, decoded view of a logged scan entry (offline; no device).

    Shows the scan's timestamp/scope/label/state, every ECU's codes with their
    decoded meaning, and — when a ``previous`` same-scope scan is given — the
    change since it. This is what ``canair dtc --history`` prints."""
    from .dtc_describe import describe_dtc

    lines = [f"\n  DTC history: {entry.get('scope', '?')}"]
    lines.append(f"  Scanned:     {entry.get('timestamp', '?')}")
    if entry.get("label"):
        lines.append(f"  Label:       {entry['label']}")
    if entry.get("vehicle_states"):
        lines.append(f"  State:       {', '.join(entry['vehicle_states'])}")
    if entry.get("notes"):
        lines.append(f"  Notes:       {entry['notes']}")
    lines.append("")

    ecus = entry.get("ecus") or {}
    total = sum(len((info or {}).get("dtcs") or []) for info in ecus.values())
    if not total:
        lines.append("  \033[92mNo DTCs stored in this scan.\033[0m")
    else:
        for ecu in sorted(ecus):
            codes = (ecus[ecu] or {}).get("dtcs") or []
            if not codes:
                continue
            lines.append(f"  {ecu} — {len(codes)} DTC(s):")
            code_w = max(len(c) for c in codes)
            for code in codes:
                meaning = describe_dtc(code).get("meaning") or ""
                lines.append(f"    {code:<{code_w}}  {meaning}".rstrip())
            lines.append("")

    if previous is not None:
        lines.extend(format_diff(diff_scans(previous, entry), previous))
    else:
        lines.append("  \033[2m(no earlier scan of this scope to compare against)\033[0m")
    lines.append("")
    return lines
