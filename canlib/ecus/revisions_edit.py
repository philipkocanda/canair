"""Editing ECU revisions, ECU presence and revision gates in ``ecus/``.

``canair ecu revisions`` / ``canair ecu set-variants`` / ``canair pids set-revisions`` /
``canair variants set-revisions`` write through here, so a revision is never hand-edited.
Each ECU-file edit is a ruamel round-trip through :func:`canlib.ecus.edit._safe_write`,
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
from canlib.ecus.edit import EcusEditError, _load_doc, _safe_write
from canlib.variants.revisions import (
    FINGERPRINT_KINDS,
    declared,
    normalize_revision_id,
    revision_id_problem,
)

if TYPE_CHECKING:
    from canlib.profile import Profile


def _profile(profile: Profile | None) -> Profile:
    from canlib.profile import active

    return profile or active()


def _ecu_file(ecu: str, profile: Profile) -> tuple[Path, str]:
    """The ECU's file and its canonical (top-level key) name."""
    from canlib.ecus.files import find_by_name

    fpath = find_by_name(ecu, profile.ecus_dir)
    if fpath is None:
        raise EcusEditError(f"ECU {ecu!r} not found in any ecus/*.yaml")
    data = _load_doc(fpath)
    name = next((k for k in data if str(k).upper() == ecu.strip().upper()), None)
    if name is None:  # matched by alias: the file holds one ECU
        name = next(iter(data))
    return fpath, name


def _checked_id(raw: str) -> str:
    rid = normalize_revision_id(raw)
    if problem := revision_id_problem(rid):
        raise EcusEditError(problem)
    return rid


def _flow_seq(items: Sequence[Any]) -> CommentedSeq:
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def _flow_map(pairs: Mapping[str, Any]) -> CommentedMap:
    m = CommentedMap(pairs)
    m.fa.set_flow_style()
    return m


def _insert_after(mapping: CommentedMap, key: str, value: Any, after: Sequence[str]) -> None:
    """Set ``key``; a new key goes straight after the last of ``after`` present, else last."""
    if key in mapping:
        mapping[key] = value
        return
    keys = list(mapping)
    pos = max((keys.index(a) + 1 for a in after if a in mapping), default=len(keys))
    mapping.insert(pos, key, value)


def _edit_ecu(ecu: str, profile: Profile | None, change) -> Path:
    prof = _profile(profile)
    fpath, name = _ecu_file(ecu, prof)
    original = fpath.read_text()
    data = _load_doc(fpath)
    ecu_def = data[name]
    if not isinstance(ecu_def, dict):
        raise EcusEditError(f"{fpath.name}/{name} is not a mapping")
    change(name, ecu_def)
    _safe_write(fpath, original, data)
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
    return next((k for k in block if normalize_revision_id(k) == rid), None)


def add_revision(
    ecu: str, revision: str, *, protocol: str | None = None, profile: Profile | None = None
) -> Path:
    """Declare a revision of ``ecu`` (appended: the first declared is the reference)."""
    rid = _checked_id(revision)

    def change(name: str, ecu_def: CommentedMap) -> None:
        block = ecu_def.get("revisions")
        if not isinstance(block, dict):
            block = CommentedMap()
            _insert_after(ecu_def, "revisions", block, _REVISIONS_AFTER)
        if _find(block, rid) is not None:
            raise EcusEditError(f"{name} already declares revision {rid!r}")
        entry = CommentedMap()
        if protocol:
            entry["identity"] = _flow_map({"id_protocol": protocol})
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
            fp = _flow_map({"responds": DoubleQuotedScalarString(responds.strip().upper())})
        else:
            fields = {k: DoubleQuotedScalarString(v) for k, v in (identity or {}).items()}
            fp = _flow_map({"identity": _flow_map(fields)})
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
        _insert_after(
            ecu_def,
            "variants",
            _flow_seq(list(dict.fromkeys(ids))),
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
        _insert_after(node, "revisions", _flow_seq(list(dict.fromkeys(revs))), after)

    return _edit_ecu(ecu, profile, change)


# ── renaming and removing a revision, with everything that refers to it ─────


@dataclass
class RevisionReferences:
    """Where an ECU revision ID is used."""

    gates: int = 0
    variants: list[str] = field(default_factory=list)
    captures: dict[Path, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.gates + len(self.variants) + sum(self.captures.values())

    def describe(self) -> str:
        parts = []
        if self.gates:
            parts.append(f"{self.gates} PID/signal gate(s)")
        if self.variants:
            parts.append(f"variant(s) {', '.join(self.variants)}")
        if self.captures:
            n = sum(self.captures.values())
            parts.append(f"{n} capture session(s) in {len(self.captures)} file(s)")
        return ", ".join(parts)


def _gate_nodes(ecu_def: Mapping) -> list[Any]:
    out = []
    for pid_def in (ecu_def.get("pids") or {}).values():
        if not isinstance(pid_def, Mapping):
            continue
        out.append(pid_def)
        out += [p for p in (pid_def.get("parameters") or {}).values() if isinstance(p, Mapping)]
    return [n for n in out if isinstance(n.get("revisions"), list)]


def _session_revision(session: Mapping, ecu: str) -> str | None:
    tags = session.get("revisions")
    if not isinstance(tags, Mapping):
        return None
    return next((str(v) for k, v in tags.items() if str(k).upper() == ecu.upper()), None)


def find_revision_references(ecu: str, revision: str, profile: Profile) -> RevisionReferences:
    from canlib.pids import load_raw_pids
    from canlib.variants import load_variants

    rid = normalize_revision_id(revision)
    refs = RevisionReferences()
    ecus = load_raw_pids(profile).get("ecus") or {}
    name = next((k for k in ecus if str(k).upper() == ecu.upper()), ecu)
    ecu_def = ecus.get(name) or {}
    refs.gates = sum(
        1 for n in _gate_nodes(ecu_def) if rid in [normalize_revision_id(r) for r in n["revisions"]]
    )
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
    return refs


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
            path.write_text(text)
            written.append(path)
    except OSError:
        for path in written:
            path.write_text(originals[path])
        raise
    return written


def rename_revision(
    ecu: str, old: str, new: str, *, profile: Profile | None = None
) -> tuple[Path, list[Path]]:
    """Rename a revision of ``ecu`` and every reference to it, all or nothing.

    Rewrites the declaration (keeping its position), every PID/signal gate naming it,
    each variant's ``revisions:`` list for the ECU, and the ``revisions`` tags of the
    profile's own capture sessions. Returns the ECU file and the other files rewritten.
    """
    from canlib.variants.edit import set_variant_revisions

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
        rebuilt = CommentedMap()
        for k, v in block.items():
            rebuilt[new_id if k == key else k] = v
        ecu_def["revisions"] = rebuilt
        for node in _gate_nodes(ecu_def):
            node["revisions"] = _flow_seq(
                [new_id if normalize_revision_id(r) == old_id else r for r in node["revisions"]]
            )

    def retag(session: dict) -> bool:
        tags = session.get("revisions")
        if not isinstance(tags, dict):
            return False
        key = next((k for k in tags if str(k).upper() == name.upper()), None)
        if key is None or tags[key] != old_id:
            return False
        tags[key] = new_id
        return True

    planned = _retag_captures(prof, retag)
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
        fpath.write_text(ecu_original)
        if variants_original is not None:
            prof.variants_file.write_text(variants_original)
        from canlib.pids import clear_cache

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
        del block[key]
        if not block:
            del ecu_def["revisions"]

    return _edit_ecu(ecu, prof, change)


# ── following an ECU or variant rename ──────────────────────────────────────


def retag_ecu_rename(old: str, new: str, profile: Profile) -> list[Path]:
    """After an ECU rename: rewrite variants' ``revisions:`` keys and capture tags."""
    from canlib.variants.edit import rename_variant_revisions_ecu

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
    return written + _write_all(_retag_captures(profile, retag))
