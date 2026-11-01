"""Persistent DTC scan history — record scans and diff against the previous one.

Each ``canair dtc`` read appends a decoded snapshot to the profile's DTC log
(unless ``--no-log``) and reports what changed since the last scan *of the same
scope* (all-ECU sweep, or a single ECU) on the same car: which codes **cleared**,
which are **new**, and which **persist**. That's how you tell, next time, whether a
fault went away (e.g. after a fix or a self-heal) rather than eyeballing two dumps.

The file is tool-managed (append-only, newest entry last):

    scans:
    - timestamp: '2026-07-22T15:40:03'
      scope: all                 # 'all' or an ECU display name like 'BMS (0x7E4)'
      label: before clearing     # optional, from --label
      ecus:                      # only ECUs that had codes (empty on a clean sweep)
        AMP (0x783): {tx: '0x783', protocol: kwp, dtcs: [B2915-00, B2916-00]}
        PLC (0x733): {tx: '0x733', protocol: uds, dtcs: [C182C-00]}
      unread: [SKM (0x7A5)]      # full sweep only: ECUs that gave no DTC list
      variant: 28kwh             # the car (a variants.yaml ID), when one is selected
      revisions: {VCU: kwp}      # determined revisions of multi-revision ECUs
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

A full sweep also lists, under ``unread:``, the ECUs that gave no DTC list (no
response, or an NRC): their absence from ``ecus:`` says nothing, so they are
neither "cleared" against the previous sweep nor "clean" in :mod:`canlib.dtc.history`.
Every entry is tagged with the car it was read from (``variant``, and ``revisions:``
for the multi-revision ECUs it covers) — see :mod:`canlib.dtc.scope`.

This is a fact about *your* car, not shared data, and the only record of its fault
history, so it lives in user state (``<config dir>/dtc_log/<profile>.yaml``) rather
than in the profile, which a reinstall or re-clone deletes. A legacy
``<profile>/dtc_log.yaml`` is moved there on first use
(:mod:`canlib.migrations.dtc_log`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import ansi
from canlib.dtc.scope import (
    ALL_SCOPE,
    Car,
    current_car,
    display_name,
    display_tx,
    entry_revision,
    scope_key,
)

if TYPE_CHECKING:
    from canlib.profile import Profile

_HEADER = (
    "DTC scan history recorded by `canair dtc` for one profile's car. This is the only\n"
    "record of it: do not delete. View it with `canair dtc log`."
)

_migrated: set[tuple[Path, Path, tuple[Path, ...]]] = set()


def log_dir() -> Path:
    """The directory holding every profile's DTC log (user state, not a profile)."""
    from canlib.config import config_dir

    return config_dir() / "dtc_log"


def log_file(profile: Profile) -> Path:
    """``profile``'s DTC log, after folding in any legacy in-profile copy (once)."""
    dest = log_dir() / f"{profile.name}.yaml"
    key = (dest, profile.root, profile.overlays)
    if key not in _migrated:
        _migrated.add(key)
        from canlib.migrations.dtc_log import migrate

        try:
            migrate(profile, dest)
        except OSError as e:
            print(f"warning: could not migrate the legacy DTC log: {e}", file=sys.stderr)
    return dest


def log_path(path: Path | None = None) -> Path:
    """Path to the active profile's DTC log (or the override ``path``)."""
    if path is not None:
        return path
    from canlib.profile import active

    return log_file(active())


def load_log(path: Path | None = None) -> dict:
    """Load the DTC log, or an empty ``{"scans": []}`` when absent."""
    from canlib import yaml_io

    p = log_path(path)
    if not p.exists():
        return {"scans": []}
    with open(p) as f:
        data = yaml_io.safe_load(f) or {}
    if not isinstance(data.get("scans"), list):
        data["scans"] = []
    return data


def write_log(data: dict, path: Path) -> Path:
    """Write a whole log document to ``path`` (creating its directory), with the header."""
    from ruamel.yaml.comments import CommentedMap

    from canlib.yaml_rt import dump

    doc = data if isinstance(data, CommentedMap) else CommentedMap(data)
    if not doc.ca.comment:
        doc.yaml_set_start_comment(_HEADER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        dump(doc, f)
    return path


def build_scan(
    scope: str,
    ecus: dict,
    *,
    label: str | None = None,
    vehicle_states: list | None = None,
    notes: str | None = None,
    unread: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Build a scan entry. ``ecus`` maps display-name → {tx, protocol, dtcs:[codes]};
    ``unread`` lists the ECUs a full sweep asked but got no DTC list from."""
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
    if unread:
        entry["unread"] = list(unread)
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


def _scope_ecus(scope: str) -> list[str] | None:
    """The ECU names a scope covers for the revision check (None: every ECU)."""
    return None if scope_key(scope) == ALL_SCOPE else [display_name(scope)]


def _with_tag(entry: dict) -> dict:
    """``entry`` tagged with the car it was read from: the selected variant and the
    revisions of the multi-revision ECUs it covers. A code means something only for
    the car and ECU revision that reported it."""
    if "variant" in entry or "revisions" in entry:
        return entry
    from canlib.profile import ProfileError, active
    from canlib.variants.tags import capture_tag, narrow_to_captured

    try:
        tag = capture_tag(active())
    except ProfileError:
        return entry
    if not tag:
        return entry
    ecus = _scope_ecus(str(entry.get("scope", "")))
    if ecus is not None:
        tag = narrow_to_captured(tag, {e.upper() for e in ecus})
    out = dict(entry)
    for key in ("variant", "revisions"):
        if tag.get(key):
            out[key] = tag[key]
    return out


def _append(section: str, entry: dict, path: Path | None = None) -> Path:
    """Append ``entry`` to the named top-level list, preserving comments."""
    entry = _with_tag(entry)
    from ruamel.yaml.comments import CommentedMap

    from canlib.yaml_rt import round_trip_yaml as _yaml

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
    return write_log(data, p)


def _same_scope(entry: dict, scope: str, car: Car) -> bool:
    return scope_key(str(entry.get("scope", ""))) == scope_key(scope) and car.admits(
        entry, _scope_ecus(scope)
    )


def latest_matching(scope: str, path: Path | None = None, car: Car | None = None) -> dict | None:
    """The most recent logged scan of ``scope`` on ``car`` (default: the active one)."""
    car = car or current_car()
    for entry in reversed(load_log(path).get("scans", [])):
        if _same_scope(entry, scope, car):
            return entry
    return None


def prior_matching(
    scope: str, before: str, path: Path | None = None, car: Car | None = None
) -> dict | None:
    """The most recent same-scope scan logged strictly *before* the ``before``
    timestamp — the entry a stored scan was diffed against when it was recorded."""
    car = car or current_car()
    match = None
    for entry in load_log(path).get("scans", []):
        if _same_scope(entry, scope, car) and entry.get("timestamp", "") < before:
            match = entry
    return match


def _pairs(entry: dict) -> set[tuple[str, str]]:
    """The set of (ecu, code) pairs recorded in a scan entry."""
    out: set[tuple[str, str]] = set()
    for ecu, info in (entry.get("ecus") or {}).items():
        for code in (info or {}).get("dtcs") or []:
            out.add((ecu, code))
    return out


def _unread(entry: dict) -> set[str]:
    return {str(u) for u in entry.get("unread") or []}


def _comparable(ecu: str, previous: dict, current: dict) -> bool:
    """Whether both scans read ``ecu``, from the same revision of it."""
    if ecu in _unread(previous) or ecu in _unread(current):
        return False
    name = display_name(ecu)
    before, now = entry_revision(previous, name), entry_revision(current, name)
    return before is None or now is None or before == now


def diff_scans(previous: dict, current: dict) -> dict:
    """Return ``{cleared, new, persisting}`` lists of (ecu, code) pairs.

    An ECU one of the scans could not read, or read as another revision, is left out:
    its codes neither cleared nor appeared, they were not comparable."""
    prev = {p for p in _pairs(previous) if _comparable(p[0], previous, current)}
    curr = {p for p in _pairs(current) if _comparable(p[0], previous, current)}
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
            f"    {ansi.GREEN}✓ cleared ({len(cleared)}){ansi.RESET}: "
            + ", ".join(f"{e} {c}" for e, c in cleared)
        )
    if new:
        lines.append(
            f"    {ansi.RED}+ new ({len(new)}){ansi.RESET}: "
            + ", ".join(f"{e} {c}" for e, c in new)
        )
    lines.append(f"    = still present: {len(diff['persisting'])}")
    if not cleared and not new:
        lines.append(f"    {ansi.DIM}(no change since last scan){ansi.RESET}")
    return lines


def ecu_name_for(display: str, info: Mapping) -> str:
    """The registry name of the ECU a logged ``display`` label (and its ``tx``) names.

    Resolved by TX id, so a code logged before an ECU rename is still described with
    that ECU's meanings; falls back to the name in the label."""
    from canlib.ecus import load_ecus

    tx = display_tx(str(info.get("tx") or "")) or display_tx(display)
    try:
        entry = load_ecus().get(tx) if tx is not None else None
    except Exception:
        entry = None
    return str(entry["name"]) if entry else display_name(display)


def describe_car(entry: dict) -> str | None:
    """``variant 28kwh · VCU kwp`` for a tagged entry, None for an untagged one."""
    parts = [f"variant {entry['variant']}"] if entry.get("variant") else []
    revisions = entry.get("revisions")
    if isinstance(revisions, dict):
        parts += [f"{ecu} {rid}" for ecu, rid in revisions.items()]
    return " · ".join(parts) or None


def render_scan(entry: dict, previous: dict | None = None) -> list[str]:
    """Human-readable, decoded view of a logged scan entry (offline; no device).

    Shows the scan's timestamp/scope/label/state, every ECU's codes with their
    decoded meaning, and — when a ``previous`` same-scope scan is given — the
    change since it. This is what ``canair dtc log`` prints."""
    from canlib.dtc.describe import describe_dtc

    lines = [f"\n  DTC log: {entry.get('scope', '?')}"]
    lines.append(f"  Scanned:     {entry.get('timestamp', '?')}")
    car = describe_car(entry)
    if car:
        lines.append(f"  Car:         {car}")
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
        lines.append(f"  {ansi.GREEN}No DTCs stored in this scan.{ansi.RESET}")
    else:
        for ecu in sorted(ecus):
            codes = (ecus[ecu] or {}).get("dtcs") or []
            if not codes:
                continue
            lines.append(f"  {ecu} — {len(codes)} DTC(s):")
            code_w = max(len(c) for c in codes)
            name = ecu_name_for(ecu, ecus[ecu] or {})
            for code in codes:
                meaning = describe_dtc(code, ecu=name).get("meaning") or ""
                lines.append(f"    {code:<{code_w}}  {meaning}".rstrip())
            lines.append("")
    if entry.get("unread"):
        lines.append(f"  {ansi.DIM}Not read: {', '.join(entry['unread'])}{ansi.RESET}")
        lines.append("")

    if previous is not None:
        lines.extend(format_diff(diff_scans(previous, entry), previous))
    else:
        lines.append(f"  {ansi.DIM}(no earlier scan of this scope to compare against){ansi.RESET}")
    lines.append("")
    return lines
