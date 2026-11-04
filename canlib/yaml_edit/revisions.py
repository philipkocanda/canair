"""Editing ECU revisions, ECU presence and revision gates in ``ecus/``.

``canair ecu revisions`` / ``canair ecu set-variants`` / ``canair pids set-revisions`` /
``canair variants set-revisions`` write through here.
Each ECU-file edit is a ruamel round-trip through :func:`canlib.yaml_edit.ecu.edit_ecu`,
which re-parses, validates (including :func:`canlib.variants.revisions.check_ecu`) and
reverts on failure, so comments and field order survive and a broken edit never lands.

A revision ID is written into the history too — PID and signal gates, variants'
``revisions:`` lists, and capture sessions' ``revisions`` tags — so :func:`rename_revision`
rewrites every reference, all or nothing, and :func:`remove_revision` refuses while one
remains. Like ``variants rename``, only the profile's own files are rewritten: a layered
profile's base is never touched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from canlib.captures import io as capture_io
from canlib.variants.revisions import (
    FINGERPRINT_KINDS,
    declared,
    normalize_revision_id,
    revision_id_problem,
)

from .commit import restore, write_atomic
from .ecu import EcusEditError, edit_ecu
from .ecufile import clear_cache, find_ecu_file
from .ruamel_ops import find_key, flow_map, flow_seq, place_after, remove_key, rename_key

if TYPE_CHECKING:
    from canlib.profile import Profile


def _profile(profile: Profile | None) -> Profile:
    from canlib.profile import active

    return profile or active()


def _ecu_file(ecu: str, profile: Profile) -> tuple[Path, str]:
    """The ECU's file and its canonical (top-level key) name."""
    from canlib.core import yaml_io

    fpath = find_ecu_file(ecu, profile.ecus_dir, error=EcusEditError)
    keys = list(yaml_io.safe_load(fpath.read_text()) or {})
    name = next((k for k in keys if str(k).upper() == ecu.strip().upper()), None)
    if name is None:  # matched by alias: the file holds one ECU
        name = keys[0]
    return fpath, name


def _checked_id(raw: str) -> str:
    rid = normalize_revision_id(raw)
    if problem := revision_id_problem(rid):
        raise EcusEditError(problem)
    return rid


def _edit_ecu(ecu: str, profile: Profile | None, change) -> Path:
    prof = _profile(profile)
    fpath, name = _ecu_file(ecu, prof)
    with edit_ecu(fpath, name) as doc:
        change(name, doc.section)
    return fpath


# ── declaring revisions ─────────────────────────────────────────────────────

# Where an ECU's revisions: block sits: after the fields that say what the ECU is.
_REVISIONS_AFTER = ("tx_id", "rx_id", "addressing", "identity", "can_bus")


def _block(ecu_def: CommentedMap) -> CommentedMap:
    block = ecu_def.get("revisions")
    if not isinstance(block, dict):
        raise EcusEditError("this ECU declares no revisions")
    return block


def _find(block: Mapping, rid: str) -> Any:
    return find_key(block, rid, normalize_revision_id)


def add_revision(
    ecu: str, revision: str, *, protocol: str | None = None, profile: Profile | None = None
) -> Path:
    """Declare a revision of ``ecu`` (appended: the first declared is the reference)."""
    rid = _checked_id(revision)

    def change(name: str, ecu_def: CommentedMap) -> None:
        block = ecu_def.get("revisions")
        if not isinstance(block, dict):
            block = CommentedMap()
            place_after(ecu_def, "revisions", block, _REVISIONS_AFTER)
        if _find(block, rid) is not None:
            raise EcusEditError(f"{name} already declares revision {rid!r}")
        entry = CommentedMap()
        if protocol:
            entry["identity"] = flow_map({"id_protocol": protocol})
        block[rid] = entry if entry else CommentedMap()

    return _edit_ecu(ecu, profile, change)


def set_fingerprint(
    ecu: str,
    revision: str,
    *,
    responds: str | None = None,
    identity: Mapping[str, str] | None = None,
    clear: bool = False,
    profile: Profile | None = None,
) -> Path:
    """Add a fingerprint to a revision (``--clear``: drop all of them first).

    ``responds`` is a safe read only this revision answers; ``identity`` maps identity
    fields to glob patterns. Recorded and checked; matching a car is a later stage.
    """
    rid = _checked_id(revision)
    if not clear and responds is None and not identity:
        raise EcusEditError(f"give a fingerprint ({' or '.join(FINGERPRINT_KINDS)}), or --clear")
    if responds is not None and identity:
        raise EcusEditError("one fingerprint at a time: --responds or --identity")

    def change(name: str, ecu_def: CommentedMap) -> None:
        block = _block(ecu_def)
        key = _find(block, rid)
        if key is None:
            raise EcusEditError(f"{name} declares no revision {rid!r}")
        entry = block[key]
        if not isinstance(entry, dict):
            entry = CommentedMap()
            block[key] = entry
        if clear:
            entry.pop("fingerprints", None)
        if responds is None and not identity:
            return
        fps = entry.get("fingerprints")
        if not isinstance(fps, list):
            fps = CommentedSeq()
            entry["fingerprints"] = fps
        if responds is not None:
            fp = flow_map({"responds": DoubleQuotedScalarString(responds.strip().upper())})
        else:
            fields = {k: DoubleQuotedScalarString(v) for k, v in (identity or {}).items()}
            fp = flow_map({"identity": flow_map(fields)})
        fps.append(fp)

    return _edit_ecu(ecu, profile, change)


def set_ecu_variants(
    ecu: str, variants: Sequence[str] | None, *, profile: Profile | None = None
) -> Path:
    """Say which variants ``ecu`` is fitted to (None: every variant — the key is removed)."""
    from canlib.variants import load_variants, normalize_variant_id

    prof = _profile(profile)
    ids = [normalize_variant_id(v) for v in (variants or []) if str(v).strip()]
    if ids:
        known = load_variants(prof)
        unknown = [v for v in ids if v not in known]
        if unknown:
            raise EcusEditError(
                f"variant(s) {', '.join(unknown)} not declared "
                f"(declared: {', '.join(known) or 'none'})"
            )

    def change(name: str, ecu_def: CommentedMap) -> None:
        if not ids:
            if "variants" not in ecu_def:
                raise EcusEditError(f"{name} is already fitted to every variant")
            del ecu_def["variants"]
            return
        place_after(
            ecu_def,
            "variants",
            flow_seq(list(dict.fromkeys(ids))),
            (*_REVISIONS_AFTER, "revisions"),
        )

    return _edit_ecu(ecu, prof, change)


# ── gating PIDs and signals ─────────────────────────────────────────────────


def set_gate(
    ecu: str,
    pid: str,
    signal: str | None,
    revisions: Sequence[str] | None,
    *,
    profile: Profile | None = None,
) -> Path:
    """Gate a PID (or one of its signals) to ``revisions`` (None: every revision)."""
    revs = [_checked_id(r) for r in (revisions or []) if str(r).strip()]

    def change(name: str, ecu_def: CommentedMap) -> None:
        if revs:
            have = declared(ecu_def)
            unknown = [r for r in revs if r not in have]
            if unknown:
                raise EcusEditError(
                    f"{name} declares no revision {', '.join(unknown)} "
                    f"(declared: {', '.join(have) or 'none'})"
                )
        pids = ecu_def.get("pids") or {}
        pkey = next((k for k in pids if str(k).upper() == str(pid).strip().upper()), None)
        if pkey is None:
            raise EcusEditError(f"PID {pid!r} not found under {name}")
        node = pids[pkey]
        after: tuple[str, ...] = ("status", "period")
        if signal is not None:
            params = node.get("parameters") or {}
            skey = next((k for k in params if k == signal), None)
            if skey is None:
                raise EcusEditError(f"signal {signal!r} not found under {name} {pkey}")
            node = params[skey]
            after = tuple(node)  # a signal's gate goes last
        if not revs:
            if "revisions" not in node:
                raise EcusEditError("it already applies to every revision")
            del node["revisions"]
            return
        place_after(node, "revisions", flow_seq(list(dict.fromkeys(revs))), after)

    return _edit_ecu(ecu, profile, change)


# ── renaming and removing a revision, with everything that refers to it ─────


@dataclass
class RevisionReferences:
    """Where an ECU revision ID is used."""

    gates: int = 0
    variants: list[str] = field(default_factory=list)
    captures: dict[Path, int] = field(default_factory=dict)
    dtc_log: int = 0

    @property
    def total(self) -> int:
        return self.gates + len(self.variants) + sum(self.captures.values()) + self.dtc_log

    def describe(self) -> str:
        parts = []
        if self.gates:
            parts.append(f"{self.gates} PID/signal gate(s) or override(s)")
        if self.variants:
            parts.append(f"variant(s) {', '.join(self.variants)}")
        if self.captures:
            n = sum(self.captures.values())
            parts.append(f"{n} capture session(s) in {len(self.captures)} file(s)")
        if self.dtc_log:
            parts.append(f"{self.dtc_log} DTC-log entr(ies)")
        return ", ".join(parts)


def _nodes(ecu_def: Mapping) -> list[Any]:
    """Every PID and signal mapping of an ECU."""
    out = []
    for pid_def in (ecu_def.get("pids") or {}).values():
        if not isinstance(pid_def, Mapping):
            continue
        out.append(pid_def)
        out += [p for p in (pid_def.get("parameters") or {}).values() if isinstance(p, Mapping)]
    return out


def _gate_nodes(ecu_def: Mapping) -> list[Any]:
    return [n for n in _nodes(ecu_def) if isinstance(n.get("revisions"), list)]


def _override_nodes(ecu_def: Mapping, rid: str) -> list[Any]:
    """PIDs and signals with a ``per_revision`` entry for ``rid``."""
    return [
        n
        for n in _nodes(ecu_def)
        if isinstance(n.get("per_revision"), Mapping)
        and any(normalize_revision_id(k) == rid for k in n["per_revision"])
    ]


def _session_revision(session: Mapping, ecu: str) -> str | None:
    tags = session.get("revisions")
    if not isinstance(tags, Mapping):
        return None
    return next((str(v) for k, v in tags.items() if str(k).upper() == ecu.upper()), None)


def find_revision_references(ecu: str, revision: str, profile: Profile) -> RevisionReferences:
    from canlib.ecus.pids import load_raw_pids
    from canlib.variants import load_variants

    rid = normalize_revision_id(revision)
    refs = RevisionReferences()
    ecus = load_raw_pids(profile).get("ecus") or {}
    name = next((k for k in ecus if str(k).upper() == ecu.upper()), ecu)
    ecu_def = ecus.get(name) or {}
    refs.gates = sum(
        1 for n in _gate_nodes(ecu_def) if rid in [normalize_revision_id(r) for r in n["revisions"]]
    ) + len(_override_nodes(ecu_def, rid))
    refs.variants = [
        vid for vid, v in load_variants(profile).items() if rid in (v.revisions.get(name) or ())
    ]
    for layer in profile.capture_layers:
        if not layer.is_dir():
            continue
        for path in capture_io.iter_capture_files(layer):
            sessions = (capture_io.load_capture_file(path) or {}).get("sessions") or []
            n = sum(1 for s in sessions if _session_revision(s, name) == rid)
            if n:
                refs.captures[path] = n
    refs.dtc_log = sum(1 for e in _dtc_log_entries(profile) if _session_revision(e, name) == rid)
    return refs


def _dtc_log_entries(profile: Profile) -> list:
    from canlib.dtc.log import load_log, log_file

    data = load_log(log_file(profile))
    return [
        e for section in ("scans", "clears") for e in data.get(section) or [] if isinstance(e, dict)
    ]


def _retag_dtc_log(profile: Profile, rewrite_entry) -> dict[Path, str]:
    """New text for ``profile``'s DTC log when a rewrite changes any of its entries.

    Its entries carry the same ``revisions:`` tag a capture session does."""
    from io import StringIO

    from canlib.core.yaml_rt import dump, round_trip_yaml
    from canlib.dtc.log import log_file

    path = log_file(profile)
    if not path.exists():
        return {}
    data = round_trip_yaml().load(path.read_text())
    entries = [e for s in ("scans", "clears") for e in (data or {}).get(s) or []]
    changed = [rewrite_entry(e) for e in entries if isinstance(e, dict)]
    if not any(changed):
        return {}
    buf = StringIO()
    dump(data, buf)
    return {path: buf.getvalue()}


def _retag_captures(profile: Profile, rewrite_session) -> dict[Path, str]:
    """New text for every capture file of ``profile``'s own store a rewrite changes."""
    planned: dict[Path, str] = {}
    cdir = profile.captures_dir
    if not cdir.is_dir():
        return planned
    for path in capture_io.iter_capture_files(cdir):
        data = capture_io.load_capture_file(path)
        sessions = (data or {}).get("sessions") or []
        # Every session is visited (no short-circuit): each may need rewriting.
        changed = [rewrite_session(s) for s in sessions]
        if any(changed):
            planned[path] = capture_io.capture_text(data)
    return planned


def _write_all(planned: Mapping[Path, str]) -> list[Path]:
    originals = {p: p.read_text() for p in planned}
    written: list[Path] = []
    try:
        for path, text in planned.items():
            write_atomic(path, text)
            written.append(path)
    except OSError:
        for path in written:
            restore(path, originals[path])
        raise
    return written


def rename_revision(
    ecu: str, old: str, new: str, *, profile: Profile | None = None
) -> tuple[Path, list[Path]]:
    """Rename a revision of ``ecu`` and every reference to it, all or nothing.

    Rewrites the declaration (keeping its position), every PID/signal gate naming it,
    each variant's ``revisions:`` list for the ECU, and the ``revisions`` tags of the
    profile's own capture sessions and of its DTC log. Returns the ECU file and the
    other files rewritten.
    """
    from canlib.yaml_edit.variants import set_variant_revisions

    prof = _profile(profile)
    old_id, new_id = _checked_id(old), _checked_id(new)
    fpath, name = _ecu_file(ecu, prof)

    def change(_name: str, ecu_def: CommentedMap) -> None:
        block = _block(ecu_def)
        key = _find(block, old_id)
        if key is None:
            raise EcusEditError(f"{name} declares no revision {old_id!r}")
        if old_id != new_id and _find(block, new_id) is not None:
            raise EcusEditError(f"{name} already declares revision {new_id!r}")
        rename_key(block, key, new_id)
        for node in _gate_nodes(ecu_def):
            node["revisions"] = flow_seq(
                [new_id if normalize_revision_id(r) == old_id else r for r in node["revisions"]]
            )
        for node in _override_nodes(ecu_def, old_id):
            over = node["per_revision"]
            rename_key(over, find_key(over, old_id, normalize_revision_id), new_id)

    def retag(session: dict) -> bool:
        tags = session.get("revisions")
        if not isinstance(tags, dict):
            return False
        key = next((k for k in tags if str(k).upper() == name.upper()), None)
        if key is None or tags[key] != old_id:
            return False
        tags[key] = new_id
        return True

    planned = {**_retag_captures(prof, retag), **_retag_dtc_log(prof, retag)}
    variants_original = prof.variants_file.read_text() if prof.variants_file.exists() else None
    ecu_original = fpath.read_text()
    _edit_ecu(name, prof, change)
    written: list[Path] = []
    try:
        from canlib.variants import load_variants

        for vid, variant in load_variants(prof).items():
            revs = variant.revisions.get(name)
            if revs and old_id in revs:
                set_variant_revisions(
                    vid, name, [new_id if r == old_id else r for r in revs], profile=prof
                )
                if prof.variants_file not in written:
                    written.append(prof.variants_file)
        written += _write_all(planned)
    except Exception:
        restore(fpath, ecu_original)
        if variants_original is not None:
            restore(prof.variants_file, variants_original)
        clear_cache()
        raise
    return fpath, written


def remove_revision(ecu: str, revision: str, *, profile: Profile | None = None) -> Path:
    """Remove a revision of ``ecu``, refusing while anything still refers to it."""
    prof = _profile(profile)
    rid = _checked_id(revision)
    refs = find_revision_references(ecu, rid, prof)
    if refs.total:
        raise EcusEditError(
            f"revision {rid!r} of {ecu} is still used by {refs.describe()}. Regate or retag "
            "them first, or rename the revision instead of removing it."
        )

    def change(name: str, ecu_def: CommentedMap) -> None:
        block = _block(ecu_def)
        key = _find(block, rid)
        if key is None:
            raise EcusEditError(f"{name} declares no revision {rid!r}")
        remove_key(block, key, parent=ecu_def, parent_key="revisions")
        if not block:
            del ecu_def["revisions"]

    return _edit_ecu(ecu, prof, change)


# ── following an ECU or variant rename ──────────────────────────────────────


def retag_ecu_rename(old: str, new: str, profile: Profile) -> list[Path]:
    """After an ECU rename: rewrite variants' ``revisions:`` keys, and the revision tags
    of captures and of the DTC log."""
    from canlib.yaml_edit.variants import rename_variant_revisions_ecu

    def retag(session: dict) -> bool:
        changed = False
        for holder in (session, session.get("attribution") or {}):
            tags = holder.get("revisions")
            if isinstance(tags, dict) and old in tags:
                rebuilt = {new if k == old else k: v for k, v in tags.items()}
                tags.clear()
                tags.update(rebuilt)
                changed = True
        return changed

    written: list[Path] = []
    if profile.variants_file.exists() and rename_variant_revisions_ecu(old, new, profile=profile):
        written.append(profile.variants_file)
    planned = {**_retag_captures(profile, retag), **_retag_dtc_log(profile, retag)}
    return written + _write_all(planned)


# ── per-revision overrides ──────────────────────────────────────────────────


def set_override(
    ecu: str,
    pid: str,
    signal: str | None,
    revision: str,
    fields: Mapping[str, Any],
    *,
    clear: Sequence[str] = (),
    profile: Profile | None = None,
) -> Path:
    """Set ``fields`` (and remove ``clear``) in a PID's or signal's ``per_revision.<rev>``.

    An entry left empty is removed, and so is an empty ``per_revision:``. Which fields
    may differ, and for which revisions, is the validator's call (it reverts the edit).
    """
    rid = _checked_id(revision)

    def change(name: str, ecu_def: CommentedMap) -> None:
        if rid not in declared(ecu_def):
            raise EcusEditError(f"{name} declares no revision {rid!r}")
        pids = ecu_def.get("pids") or {}
        pkey = next((k for k in pids if str(k).upper() == str(pid).strip().upper()), None)
        if pkey is None:
            raise EcusEditError(f"PID {pid!r} not found under {name}")
        node = pids[pkey]
        if signal is not None:
            params = node.get("parameters") or {}
            if signal not in params:
                raise EcusEditError(f"signal {signal!r} not found under {name} {pkey}")
            node = params[signal]
        block = node.get("per_revision")
        if not isinstance(block, dict):
            block = CommentedMap()
            if signal is None and "parameters" in node:
                node.insert(list(node).index("parameters"), "per_revision", block)
            else:
                node["per_revision"] = block
        entry = block.get(rid)
        if not isinstance(entry, dict):
            entry = CommentedMap()
            block[rid] = entry
        for key in clear:
            entry.pop(key, None)
        for key, value in fields.items():
            entry[key] = value
        if not entry:
            del block[rid]
        if not block:
            del node["per_revision"]

    return _edit_ecu(ecu, profile, change)
