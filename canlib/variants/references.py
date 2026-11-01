"""Renaming or removing a variant, with everything that refers to it.

A variant ID is written into the history — capture sessions' ``variant`` tags, the
raw-CAN log index and DTC-log entries (in user state, :mod:`canlib.dtc.log`) — and
into the ECU files that are fitted only to some variants (``variants:``). Renaming one
in ``variants.yaml`` alone would leave all of that naming a car the profile no longer
declares, so a rename rewrites every reference with it, all or nothing; a removal is
refused while anything still refers to the variant. Like ``states rename``, it touches
only the profile's own files and its DTC log — a layered profile's base is never
rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from canlib import yaml_io
from canlib.captures import io as capture_io
from canlib.variants import normalize_variant_id

if TYPE_CHECKING:
    from canlib.profile import Profile


@dataclass(frozen=True)
class References:
    """Where a variant ID is used, file by file (path → number of references)."""

    captures: dict[Path, int]
    can_index: int
    dtc_log: int
    # ECUs whose `variants:` (presence) list names it.
    ecus: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(self.captures.values()) + self.can_index + self.dtc_log + len(self.ecus)

    def describe(self) -> str:
        parts = []
        if self.ecus:
            parts.append(f"the variants: list of ECU(s) {', '.join(self.ecus)}")
        if self.captures:
            parts.append(
                f"{sum(self.captures.values())} capture session(s) in {len(self.captures)} file(s)"
            )
        if self.can_index:
            parts.append(f"{self.can_index} raw-CAN log(s)")
        if self.dtc_log:
            parts.append(f"{self.dtc_log} DTC-log entr(ies)")
        return ", ".join(parts)


def _entries(path: Path, section: str) -> list:
    if not path.exists():
        return []
    data = yaml_io.safe_load(path.read_text()) or {}
    entries = data.get(section) if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def find_references(profile: Profile, variant_id: str) -> References:
    """Every use of ``variant_id`` in ``profile``'s own recorded history."""
    from canlib.dtc.log import log_file as dtc_log_file

    vid = normalize_variant_id(variant_id)
    captures: dict[Path, int] = {}
    for layer in profile.capture_layers:
        if not layer.is_dir():
            continue
        for path in capture_io.iter_capture_files(layer):
            sessions = (capture_io.load_capture_file(path) or {}).get("sessions") or []
            n = sum(1 for s in sessions if s.get("variant") == vid)
            if n:
                captures[path] = n
    can_index = sum(1 for e in _entries(profile.can_index_file, "logs") if e.get("variant") == vid)
    dtc_log = sum(
        1
        for section in ("scans", "clears")
        for e in _entries(dtc_log_file(profile), section)
        if isinstance(e, dict) and e.get("variant") == vid
    )
    return References(captures, can_index, dtc_log, _fitted_ecus(profile, vid))


def _fitted_ecus(profile: Profile, vid: str) -> tuple[str, ...]:
    """ECUs whose ``variants:`` names ``vid``."""
    from canlib.pids import load_raw_pids
    from canlib.variants.revisions import presence

    ecus = load_raw_pids(profile).get("ecus") or {}
    return tuple(name for name, d in ecus.items() if vid in (presence(d) or ()))


def _retag_yaml(path: Path, sections: tuple[str, ...], old: str, new: str) -> str | None:
    """``path``'s text with every ``variant: old`` entry retagged, or None if unchanged."""
    from io import StringIO

    from canlib.yaml_rt import dump, round_trip_yaml

    if not path.exists():
        return None
    data = round_trip_yaml().load(path.read_text())
    changed = False
    for section in sections:
        for entry in (data or {}).get(section) or []:
            if isinstance(entry, dict) and entry.get("variant") == old:
                entry["variant"] = new
                changed = True
    if not changed:
        return None
    buf = StringIO()
    dump(data, buf)
    return buf.getvalue()


def retag_history(profile: Profile, old: str, new: str) -> list[Path]:
    """Rewrite every ``old`` reference in ``profile``'s own history to ``new``.

    All or nothing: every new file body is computed first, then written, and the
    originals are restored if any write fails. Returns the files written.
    """
    from canlib.dtc.log import log_file as dtc_log_file

    old, new = normalize_variant_id(old), normalize_variant_id(new)
    planned: dict[Path, str] = {}
    cdir = profile.captures_dir
    if cdir.is_dir():
        for path in capture_io.iter_capture_files(cdir):
            data = capture_io.load_capture_file(path)
            sessions = (data or {}).get("sessions") or []
            hits = [s for s in sessions if s.get("variant") == old]
            if hits:
                for s in hits:
                    s["variant"] = new
                planned[path] = capture_io.capture_text(data)
    for path, sections in (
        (profile.can_index_file, ("logs",)),
        (dtc_log_file(profile), ("scans", "clears")),
    ):
        text = _retag_yaml(path, sections, old, new)
        if text is not None:
            planned[path] = text

    originals = {p: p.read_text() for p in planned}
    written: list[Path] = []
    try:
        for path, text in planned.items():
            path.write_text(text)
            written.append(path)
    except OSError:
        for path in written:
            path.write_text(originals[path])
        raise
    return written


def rename_everywhere(old: str, new: str, profile: Profile) -> tuple[Path, list[Path]]:
    """Rename a variant in ``variants.yaml`` and in all of the profile's own history.

    Returns ``variants.yaml`` and the history files rewritten. If rewriting the
    history fails, ``variants.yaml`` is restored, so the two never disagree.
    """
    from canlib.ecus.revisions_edit import set_ecu_variants
    from canlib.pids import load_raw_pids
    from canlib.variants.edit import rename_variant
    from canlib.variants.revisions import presence

    old_id, new_id = normalize_variant_id(old), normalize_variant_id(new)
    fitted = {
        name: presence(d)
        for name, d in (load_raw_pids(profile).get("ecus") or {}).items()
        if old_id in (presence(d) or ())
    }
    original = profile.variants_file.read_text()
    path = rename_variant(old, new, profile=profile)
    ecu_files: dict[Path, str] = {}
    try:
        for name, ids in fitted.items():
            from canlib.ecus.files import find_by_name

            fpath = find_by_name(name, profile.ecus_dir)
            if fpath is not None:
                ecu_files.setdefault(fpath, fpath.read_text())
            renamed = [new_id if v == old_id else v for v in ids or ()]
            set_ecu_variants(name, renamed, profile=profile)
        written = [*ecu_files, *retag_history(profile, old, new)]
    except Exception:
        profile.variants_file.write_text(original)
        for fpath, text in ecu_files.items():
            fpath.write_text(text)
        from canlib.pids import clear_cache

        clear_cache()
        raise
    return path, written


def remove_unreferenced(variant_id: str, profile: Profile) -> Path:
    """Remove a variant from ``variants.yaml``, refusing while anything refers to it."""
    from canlib.variants.edit import VariantsEditError, remove_variant

    refs = find_references(profile, variant_id)
    if refs.total:
        raise VariantsEditError(
            f"variant {normalize_variant_id(variant_id)!r} is still used by {refs.describe()}. "
            "Retag them first (`canair captures uds --set-variant` for sessions, `canair ecu "
            "set-variants` for ECUs), or rename the variant instead of removing it."
        )
    return remove_variant(variant_id, profile=profile)
