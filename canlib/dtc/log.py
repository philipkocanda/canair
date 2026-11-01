"""Persistent DTC scan history — record scans and diff against the previous one.

Each ``canair dtc`` read appends a decoded snapshot to the profile's DTC log
(unless ``--no-log``) and reports what changed since the last scan *of the same
scope* (all-ECU sweep, or a single ECU) on the same car: which codes **cleared**,
which are **new**, and which **persist**. That's how you tell, next time, whether a
fault went away (e.g. after a fix or a self-heal) rather than eyeballing two dumps.

The file is tool-managed (append-only, newest entry last). An ECU is named by its
response address only (:mod:`canlib.dtc.refs`); its name comes from the profile:

    format: 2
    scans:
    - timestamp: '2026-07-22T15:40:03'
      scope: all                 # 'all', or the one ECU read (its rx, e.g. '0x7EC')
      label: before clearing     # optional, from --label
      ecus:                      # rx -> what it reported; a sweep lists only ECUs with codes
        '0x78B': {protocol: kwp, dtcs: [B2915-00, B2916-00]}
        '0x73B': {protocol: uds, dtcs: [C182C-00]}
      unread: ['0x7AD']          # full sweep only: ECUs that gave no DTC list
      variant: 28kwh             # the car (a variants.yaml ID), when one is selected
      revisions: {VCU: kwp}      # determined revisions of multi-revision ECUs
    clears:
    - timestamp: '2026-07-22T16:02:11'
      rx: '0x7EC'                # the ECU `canair dtc --clear` cleared
      protocol: kwp
      group: '0xFFFF'
      codes: [P1AAA-00]          # what it held before the clear, when that could be read

Only readings and clears are recorded. Whatever follows from them — that an ECU was
clean after a clear, which codes disappeared between two scans — is worked out when
the log is read, never stored. A full sweep lists, under ``unread:``, the ECUs that
gave no DTC list (no response, or an NRC): their absence from ``ecus:`` says nothing,
so they are neither "cleared" against the previous sweep nor "clean" in
:mod:`canlib.dtc.history`. Every entry is tagged with the car it was read from
(``variant``, and ``revisions:`` for the multi-revision ECUs it covers) — see
:mod:`canlib.dtc.scope`.

This is a fact about *your* car, not shared data, and the only record of its fault
history, so it lives in user state (``<config dir>/dtc_log/<profile>.yaml``) rather
than in the profile, which a reinstall or re-clone deletes. A legacy
``<profile>/dtc_log.yaml``, and a log in an earlier format, is converted on first
use (:mod:`canlib.migrations.dtc_log`).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from canlib import ansi
from canlib.dtc.refs import EcuRefs
from canlib.dtc.scope import ALL_SCOPE, Car, current_car, entry_revision

if TYPE_CHECKING:
    from canlib.profile import Profile

FORMAT = 2
SECTIONS = ("scans", "clears")

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
    """``profile``'s DTC log, brought up to date (legacy copies, format) once per process."""
    dest = log_dir() / f"{profile.name}.yaml"
    key = (dest, profile.root, profile.overlays)
    if key not in _migrated:
        _migrated.add(key)
        from canlib.migrations.dtc_log import migrate

        try:
            migrate(profile, dest)
        except OSError as e:
            print(f"warning: could not migrate the DTC log: {e}", file=sys.stderr)
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


def write_log(data: Mapping, path: Path) -> Path:
    """Write a whole log document to ``path`` (creating its directory), with the header."""
    from ruamel.yaml.comments import CommentedMap

    from canlib.yaml_rt import dump

    doc = data if isinstance(data, CommentedMap) else CommentedMap(data)
    if doc.get("format") != FORMAT:
        doc.insert(0, "format", FORMAT)
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
    """Build a scan entry. ``scope`` is ``all`` or the ECU's rx; ``ecus`` maps rx →
    {protocol, dtcs:[codes]}; ``unread`` lists the ECUs a full sweep got no DTC list from."""
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


def build_clear(
    rx: str,
    *,
    protocol: str | None = None,
    group: str | None = None,
    codes: list[str] | None = None,
    label: str | None = None,
    timestamp: str | None = None,
) -> dict:
    """Build the record of a ``--clear`` on the ECU ``rx``; ``codes`` are what it held."""
    entry: dict = {"timestamp": timestamp or datetime.now().isoformat(timespec="seconds")}
    fields = {"rx": rx, "protocol": protocol, "group": group, "codes": codes, "label": label}
    entry.update({k: v for k, v in fields.items() if v is not None})
    return entry


def append_clear(entry: dict, path: Path | None = None) -> Path:
    """Append a clear to the log's ``clears:`` list, returning the path."""
    return _append("clears", entry, path)


def entry_ecus(entry: Mapping[str, Any], refs: EcuRefs) -> list[str] | None:
    """The ECU names an entry covers for the revision check (None: every ECU)."""
    ref = entry.get("rx") if "rx" in entry else entry.get("scope")
    if ref == ALL_SCOPE:
        return None
    name = refs.name(ref)
    return [name] if name else []


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
    ecus = entry_ecus(entry, EcuRefs.current())
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


def _same_scope(entry: Mapping, scope: str, car: Car, refs: EcuRefs) -> bool:
    return entry.get("scope") == scope and car.admits(entry, entry_ecus(entry, refs))


def latest_matching(scope: str, path: Path | None = None, car: Car | None = None) -> dict | None:
    """The most recent logged scan of ``scope`` on ``car`` (default: the active one)."""
    car, refs = car or current_car(), EcuRefs.current()
    for entry in reversed(load_log(path).get("scans", [])):
        if _same_scope(entry, scope, car, refs):
            return entry
    return None


def prior_matching(
    scope: str, before: str, path: Path | None = None, car: Car | None = None
) -> dict | None:
    """The most recent same-scope scan logged strictly *before* the ``before``
    timestamp — the entry a stored scan was diffed against when it was recorded."""
    car, refs = car or current_car(), EcuRefs.current()
    match = None
    for entry in load_log(path).get("scans", []):
        if _same_scope(entry, scope, car, refs) and entry.get("timestamp", "") < before:
            match = entry
    return match


def clears_between(
    after: str, before: str, path: Path | None = None, car: Car | None = None
) -> list[dict]:
    """The clears logged after ``after`` and up to ``before`` (timestamps) on ``car``."""
    car, refs = car or current_car(), EcuRefs.current()
    return [
        e
        for e in load_log(path).get("clears") or []
        if isinstance(e, Mapping)
        and after < str(e.get("timestamp", "")) <= before
        and car.admits(e, entry_ecus(e, refs))
    ]


def _pairs(entry: Mapping) -> set[tuple[str, str]]:
    """The set of (rx, code) pairs recorded in a scan entry."""
    out: set[tuple[str, str]] = set()
    for rx, info in (entry.get("ecus") or {}).items():
        for code in (info or {}).get("dtcs") or []:
            out.add((str(rx), str(code)))
    return out


def _unread(entry: Mapping) -> set[str]:
    return {str(u) for u in entry.get("unread") or []}


def _comparable(rx: str, previous: Mapping, current: Mapping, refs: EcuRefs) -> bool:
    """Whether both scans read ``rx``, from the same revision of it."""
    if rx in _unread(previous) or rx in _unread(current):
        return False
    name = refs.name(rx)
    if name is None:
        return True
    before, now = entry_revision(previous, name), entry_revision(current, name)
    return before is None or now is None or before == now


def diff_scans(
    previous: Mapping, current: Mapping, clears: Iterable[Mapping] = (), refs: EcuRefs | None = None
) -> dict:
    """Return ``{cleared, new, persisting, cleared_manually}`` lists of (rx, code) pairs.

    ``clears`` are the ``--clear`` runs between the two scans: the codes they wiped are
    ``cleared_manually``, not "cleared", and one that is back is "new" again. An ECU one
    of the scans could not read, or read as another revision, is left out: its codes
    neither cleared nor appeared, they were not comparable."""
    refs = refs or EcuRefs.current()
    wiped = {str(c.get("rx")) for c in clears}
    prev = {p for p in _pairs(previous) if _comparable(p[0], previous, current, refs)}
    curr = {p for p in _pairs(current) if _comparable(p[0], previous, current, refs)}
    manual = {p for p in prev if p[0] in wiped}
    prev -= manual
    return {
        "cleared": sorted(prev - curr),
        "new": sorted(curr - prev),
        "persisting": sorted(prev & curr),
        "cleared_manually": sorted(manual),
    }


def format_diff(diff: dict, previous: Mapping, refs: EcuRefs | None = None) -> list[str]:
    """Human-readable lines comparing the current scan to ``previous``."""
    resolved = refs or EcuRefs.current()

    def pairs(items) -> str:
        return ", ".join(f"{resolved.label(rx)} {code}" for rx, code in items)

    lines = [f"  Compared to last scan ({previous.get('timestamp', '?')}):"]
    cleared, new = diff["cleared"], diff["new"]
    manual = diff.get("cleared_manually") or []
    if manual:
        lines.append(
            f"    {ansi.DIM}--clear since then ({len(manual)}): {pairs(manual)}{ansi.RESET}"
        )
    if cleared:
        lines.append(f"    {ansi.GREEN}✓ cleared ({len(cleared)}){ansi.RESET}: {pairs(cleared)}")
    if new:
        lines.append(f"    {ansi.RED}+ new ({len(new)}){ansi.RESET}: {pairs(new)}")
    lines.append(f"    = still present: {len(diff['persisting'])}")
    if not cleared and not new:
        lines.append(f"    {ansi.DIM}(no change since last scan){ansi.RESET}")
    return lines


def describe_car(entry: Mapping) -> str | None:
    """``variant 28kwh · VCU kwp`` for a tagged entry, None for an untagged one."""
    parts = [f"variant {entry['variant']}"] if entry.get("variant") else []
    revisions = entry.get("revisions")
    if isinstance(revisions, Mapping):
        parts += [f"{ecu} {rid}" for ecu, rid in revisions.items()]
    return " · ".join(parts) or None


def scope_label(scope: str, refs: EcuRefs | None = None) -> str:
    """``all``, or the scanned ECU as people name it."""
    return scope if scope == ALL_SCOPE else (refs or EcuRefs.current()).label(scope)


def render_scan(
    entry: Mapping, previous: Mapping | None = None, clears: Iterable[Mapping] = ()
) -> list[str]:
    """Human-readable, decoded view of a logged scan entry (offline; no device).

    Shows the scan's timestamp/scope/label/state, every ECU's codes with their
    decoded meaning, and — when a ``previous`` same-scope scan is given — the
    change since it (``clears`` are the ``--clear`` runs in between). This is what
    ``canair dtc log`` prints."""
    from canlib.dtc.describe import describe_dtc

    refs = EcuRefs.current()
    lines = [f"\n  DTC log: {scope_label(str(entry.get('scope', '?')), refs)}"]
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
        for rx in sorted(ecus, key=refs.label):
            codes = (ecus[rx] or {}).get("dtcs") or []
            if not codes:
                continue
            lines.append(f"  {refs.label(rx)} — {len(codes)} DTC(s):")
            code_w = max(len(c) for c in codes)
            name = refs.name(rx)
            for code in codes:
                meaning = describe_dtc(code, ecu=name).get("meaning") or ""
                lines.append(f"    {code:<{code_w}}  {meaning}".rstrip())
            lines.append("")
    if entry.get("unread"):
        unread = ", ".join(refs.label(u) for u in entry["unread"])
        lines.append(f"  {ansi.DIM}Not read: {unread}{ansi.RESET}")
        lines.append("")

    if previous is not None:
        lines.extend(format_diff(diff_scans(previous, entry, clears, refs), previous, refs))
    else:
        lines.append(f"  {ansi.DIM}(no earlier scan of this scope to compare against){ansi.RESET}")
    lines.append("")
    return lines
