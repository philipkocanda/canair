"""Editing a saved session: its notes, transport, variant, states, spans and removal."""

from pathlib import Path
from typing import cast

from canair.captures import io as capture_io
from canair.captures.keepmode import KeepMode, PersistedKeepMode, persisted_keep_mode
from canair.captures.types import (
    CaptureFile,
    CaptureSession,
    StateSpans,
    StateSpanSource,
)
from canair.states.spans import StateSpan
from canair.states.tokens import parse_states as _parse_states


def _write_captures_file(fpath: Path, data: CaptureFile) -> None:
    """Serialize a capture-file dict back to disk (JSON)."""
    capture_io.dump_capture_file(fpath, data)


def set_capture_note(fpath: Path, session_idx: int, capture_idx: int, note: str) -> None:
    """Set (or clear) the ``notes`` field on one capture, addressed by index.

    A non-empty ``note`` is stored verbatim; an empty/blank note removes the
    field entirely. Raises IndexError if the indices don't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    cap = data["sessions"][session_idx]["captures"][capture_idx]
    note = note.strip()
    if note:
        cap["notes"] = note
    else:
        cap.pop("notes", None)
    _write_captures_file(fpath, data)


def set_session_field(session: CaptureSession, key: str, value: object) -> None:
    """Set ``key`` on a session (a falsy ``value`` removes it), keeping ``captures`` last.

    A key added to a session that lacked it would otherwise land *after* the
    (potentially huge) ``captures`` array, burying the metadata the on-disk field
    order keeps at the top (see ``TestSessionFieldOrder``).
    """
    # Dynamic-key access: every caller passes a CaptureSession field name.
    fields = cast(dict, session)
    if value:
        fields[key] = value
    else:
        fields.pop(key, None)
    if "captures" in fields:
        fields["captures"] = fields.pop("captures")


def set_session_transport(fpath: Path, session_idx: int, transport: str) -> None:
    """Set (or clear) the ``transport`` provenance on one session, by index.

    For correcting a session recorded with the wrong label — before every save
    path read it off the live client, the one-shot producers re-resolved the
    configuration and so ignored ``--transport``/``--wican`` and fallback. An
    empty ``transport`` removes the field. Raises IndexError if the index doesn't
    resolve.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    set_session_field(session, "transport", transport.strip())
    _write_captures_file(fpath, data)


def set_session_variant(fpath: Path, session_idx: int, variant: str) -> None:
    """Set (or clear) the ``variant`` tag on one session, by index.

    The tag's ``attribution`` becomes ``asserted``: a person said which car it was,
    after the fact. An empty ``variant`` removes the tag and its attribution. Raises
    IndexError if the index doesn't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    variant = variant.strip()
    set_session_field(session, "variant", variant)
    attribution = dict(session.get("attribution") or {})
    if variant:
        attribution["variant"] = "asserted"
    else:
        attribution.pop("variant", None)
    set_session_field(session, "attribution", attribution)
    _write_captures_file(fpath, data)


def set_session_note(fpath: Path, session_idx: int, note: str) -> None:
    """Set (or clear) the ``notes`` field on one session, addressed by index.

    A non-empty ``note`` is stored verbatim; an empty/blank note removes the
    field entirely. Raises IndexError if the index doesn't resolve. Use this
    instead of hand-editing a session's ``notes`` in a capture file.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    set_session_field(session, "notes", note.strip())
    _write_captures_file(fpath, data)


def set_session_keep_mode(
    fpath: Path, session_idx: int, keep_mode: KeepMode | PersistedKeepMode | None
) -> None:
    """Set (or clear) the ``keep_mode`` field on one session, addressed by index.

    Only ``"changes"`` (run-length) and ``"unique"`` (legacy global dedup) are
    meaningful — they tell later analysis how the payloads were deduplicated; any
    other value clears the field. Use this to backfill sessions captured before
    ``keep_mode`` was persisted, instead of hand-editing a capture file.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    set_session_field(session, "keep_mode", persisted_keep_mode(keep_mode))
    _write_captures_file(fpath, data)


def set_session_states(fpath: Path, session_idx: int, vehicle_states) -> None:
    """Set (or clear) the ``vehicle_states`` field on one session, by index.

    ``vehicle_states`` is normalized to a token list (a comma-separated string or
    a list are both accepted); a non-empty list is stored, an empty one removes
    the field. Raises IndexError if the index doesn't resolve. Use this instead
    of hand-editing a session's ``vehicle_states`` in a capture file — e.g. to
    back-fill a session that reconciled with an empty state before the monitor's
    span-aware state back-fill existed.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    set_session_field(session, "vehicle_states", _parse_states(vehicle_states))
    _write_captures_file(fpath, data)


def set_session_state_spans(
    fpath: Path,
    session_idx: int,
    spans,
    *,
    source: str = "backfill",
    version: str | None = None,
) -> None:
    """Set (or clear) the ``state_spans`` block on one session, by index.

    An empty ``spans`` removes the block, so a re-run that can no longer
    reconstruct a timeline leaves no stale one behind. ``source`` records who
    produced it (``live`` while recording, ``backfill`` after the fact), which is
    what lets a back-fill refuse to overwrite live-recorded truth. Raises
    IndexError if the index doesn't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    session = data["sessions"][session_idx]
    block: StateSpans | None = None
    if spans:
        block = {"source": cast("StateSpanSource", source), "spans": list(spans)}
        if version:
            block["version"] = version
    set_session_field(session, "state_spans", block)
    _write_captures_file(fpath, data)


def annotate_session_spans(
    fpath: Path,
    session_idx: int = -1,
    *,
    cycle_tol: float | None = None,
) -> list[StateSpan] | None:
    """Derive and store the ``state_spans`` timeline for a just-saved session.

    Called right after a session lands on disk, so a recording is queryable at
    per-capture state resolution without a manual back-fill. It reconstructs the
    timeline from the *saved payloads* (:func:`canair.captures.state_inference.session_state_spans`)
    rather than from what the monitor happened to suggest per cycle — the same
    single model ``--backfill-state-spans`` uses, on strictly better evidence
    (every decoded signal, not just the ones the live suggester had fresh).

    Returns the spans written, or ``None`` when there was nothing to record (a
    single-state session, no decodable evidence, or a profile with no ``when:``
    predicates). Never raises: a failure here must not lose the captures that were
    just saved, so the session simply keeps its state union.
    """
    try:
        from canair.captures.state_inference import DEFAULT_CYCLE_TOL_S, session_state_spans
        from canair.captures.store import flatten_session
        from canair.ecus.pids import build_ecu_index, load_pids
        from canair.states.vocabulary import load_states

        data = capture_io.load_capture_file(fpath)
        sessions = data.get("sessions") or []
        if not sessions:
            return None
        idx = session_idx if session_idx >= 0 else len(sessions) - 1
        session = sessions[idx]
        recorded = _parse_states(session.get("vehicle_states") or [])
        if len(recorded) <= 1:
            return None  # nothing temporal to disambiguate

        rules = load_states()
        if not any(r.predicate is not None for r in rules):
            return None
        entries = flatten_session(fpath, idx, session)
        result = session_state_spans(
            recorded,
            [e for e in entries if e.get("payload")],
            rules,
            build_ecu_index(load_pids()),
            cycle_tol=cycle_tol if cycle_tol is not None else DEFAULT_CYCLE_TOL_S,
        )
        if not result.is_timeline:
            return None
        from canair.install.build_info import full_version

        set_session_state_spans(fpath, idx, result.spans, source="record", version=full_version())
        return list(result.spans)
    except Exception:
        return None


def delete_capture(fpath: Path, session_idx: int, capture_idx: int) -> bool:
    """Delete one capture, addressed by index. Returns True if its (now empty)
    session was removed too. Raises IndexError if the indices don't resolve.
    """
    data = capture_io.load_capture_file(fpath)
    captures = data["sessions"][session_idx]["captures"]
    del captures[capture_idx]
    removed_session = not captures
    if removed_session:
        del data["sessions"][session_idx]
    _write_captures_file(fpath, data)
    return removed_session
