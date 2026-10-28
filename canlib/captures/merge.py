"""Session-set merges of capture files (git merge-driver core + contribute overlay).

A dated capture file (``captures/YYYY-MM-DD.json``) is an **append-only** log:
``{"sessions": [ … ]}`` where each session is a self-contained block. New
sessions are only ever appended (via :func:`canlib.captures.save_session`); the
metadata editors and repairs rewrite a session in place but never its anchor
(see Session identity). Two machines that each record on the same
day therefore append *different* sessions to the tail of the same file — a
change git's line-based merge cannot reconcile, because every session ends with
identical boilerplate (``}``/``]``/``}``) so the diff misaligns and splits the
conflict *inside* individual capture records.

The data model, however, is trivially mergeable: disjoint additions to a list.
This module implements that as pure functions so they can be driven by the git
merge-driver command, by ``canair contribute``, and by tests. Two flavours, with
deliberately different ordering contracts:

* :func:`merge_documents` — the **3-way merge driver**. Honours deletions and
  re-sorts canonically so ``merge(A, B) == merge(B, A)`` (order-independent, and
  therefore re-mergeable).
* :func:`overlay_documents` — the **contribute overlay**. Never deletes, keeps
  the destination's existing order and appends, so contributing a profile does
  not rewrite capture logs it adds nothing to. A source session that is an
  edited copy of an upstream one *replaces* it in place, and every replacement is
  reported (:func:`overlay`) so the caller can show it before anything is shared.

Session identity
----------------
A session has no id. Two notions of "the same session" are used together:

* **Content** (:func:`session_key`) — byte-identical JSON. A real duplicate (the
  shared history both sides inherited) is identical, so content is what collapses
  it to one copy.
* **Anchor** (:func:`session_anchor`) — the fields no editor ever rewrites: the
  session's ``date``, ``label`` and recording ``version``, plus its first
  capture's ``time``, ``rx`` and ``pid``. The metadata editors (``--set-notes``,
  ``--set-transport``, ``--set-state``, the state back-fills) and the repairs
  (``migrate-discover``, ``migrate-rx``, ``split-batched``, which keeps the first
  row's DID and time) all change a session's content but not its anchor. So a
  session that differs in content but shares an anchor is the **same recording,
  edited** — a replacement, not an addition. Deleting a session's first capture
  does change the anchor, so the trimmed session reads as a different recording.

An anchor is only trusted when it is **unique within each document** compared
(:func:`unique_anchors`); legacy untimed sessions can share one, and those fall
back to content identity rather than being paired by guesswork.

When both sides of a merge edit the same base session *differently*, or one
edits it while the other deletes it, we refuse to guess and signal a conflict so
git falls back to normal markers and a human decides. Both sides making the
*identical* edit (a deterministic repair run on two clones) is not a conflict.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from canlib.captures.io import capture_rx


class MergeConflict(Exception):
    """A genuine conflict the union cannot safely resolve.

    Raised when both sides modified (or one modified, one deleted) the *same*
    base session in incompatible ways. The driver turns this into a non-zero
    exit so git writes the normal 3-way conflict markers instead.
    """


def session_key(session: Any) -> str:
    """Stable content key for a session (order-insensitive JSON).

    Content identity: what collapses a session present in two places (the shared
    history of a merge, both layers of a layered profile) to one copy. An *edited*
    copy differs here but shares its :func:`session_anchor`.
    """
    return json.dumps(session, sort_keys=True, ensure_ascii=False)


def session_anchor(session: Any) -> str | None:
    """The recording a session *is*, independent of any later edit — or None.

    Built from the fields no editor or repair rewrites (see the module docstring),
    so an edited copy keeps the anchor of the original. ``None`` for a session
    with no first capture to anchor on. Compare anchors only through
    :func:`unique_anchors`: one alone does not prove two sessions are the same.
    """
    if not isinstance(session, dict):
        return None
    caps = session.get("captures")
    if not (isinstance(caps, list) and caps and isinstance(caps[0], dict)):
        return None
    first = caps[0]
    return json.dumps(
        [
            str(session.get("date") or ""),
            str(session.get("label") or ""),
            str(session.get("version") or ""),
            str(first.get("time") or ""),
            capture_rx(first),
            str(first.get("pid") or ""),
        ],
        ensure_ascii=False,
    )


def unique_anchors(sessions: list[Any]) -> dict[str, int]:
    """``anchor -> index`` for every anchor held by exactly one of ``sessions``.

    An anchor two sessions share (legacy untimed sessions with the same label, or
    an edited copy recorded *next to* its original) cannot say which one a third
    session corresponds to, so it is left out and those sessions fall back to
    content identity.
    """
    anchors = [session_anchor(s) for s in sessions]
    counts = Counter(a for a in anchors if a is not None)
    return {a: i for i, a in enumerate(anchors) if a is not None and counts[a] == 1}


def changed_fields(old: Any, new: Any) -> list[str]:
    """Top-level session fields whose values differ between two copies, sorted."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    return sorted(k for k in {*old, *new} if old.get(k) != new.get(k))


def _sessions(doc: Any) -> list[dict]:
    """Extract the session list from a parsed capture doc, tolerating shapes."""
    if isinstance(doc, dict):
        sessions = doc.get("sessions")
        if isinstance(sessions, list):
            return [s for s in sessions if isinstance(s, dict)]
    return []


def _first_time(session: dict) -> str:
    """First capture timestamp of a session, for chronological ordering."""
    caps = session.get("captures")
    if isinstance(caps, list) and caps and isinstance(caps[0], dict):
        return str(caps[0].get("time") or "")
    return ""


def _sort_key(session: dict) -> tuple[str, str, str]:
    """Deterministic order: date, then first-capture time, then label.

    Keeps the merged file chronological (matching the append order) and, crucially,
    makes the merge **order-independent** — ``merge(A, B)`` and ``merge(B, A)``
    produce byte-identical output, so the result is stable and re-mergeable.
    """
    return (str(session.get("date") or ""), _first_time(session), str(session.get("label") or ""))


def _replacement(
    session: dict, base_anchors: dict[str, int], base_keys: set[str], side: list[dict]
) -> dict | None:
    """The session that replaces base ``session`` on ``side`` — its edited copy — or None.

    Paired by anchor, unique on both sides of the comparison, and only with a
    session that is not itself part of the base (an untouched base session is not
    anyone's edit).
    """
    anchor = session_anchor(session)
    if anchor is None or anchor not in base_anchors:
        return None
    idx = unique_anchors(side).get(anchor)
    if idx is None:
        return None
    candidate = side[idx]
    return None if session_key(candidate) in base_keys else candidate


def merge_sessions(base: Any, ours: Any, theirs: Any) -> list[dict]:
    """Three-way union of the ``sessions`` lists of base/ours/theirs.

    Returns the merged session list, deterministically ordered. Raises
    :class:`MergeConflict` when both sides changed the *same* base session in
    incompatible ways, rather than silently picking a side.

    Algorithm (keyed by exact session content, with anchors to tell an edit from
    a deletion):

    * A session present in base and still on **both** sides is kept once (this is
      how the shared history collapses to a single copy).
    * A session added on **exactly one** side (absent from base) is included —
      including an edited copy of a base session the other side left alone, so
      the edit wins.
    * A session in base that is **removed** on one side while **unchanged** on the
      other is honoured as a deletion — dropped.
    * A base session that is gone from **both** sides is a conflict, unless both
      sides replaced it with the **identical** edited copy (the same deterministic
      repair run on two clones), which is kept once.
    """
    base_s = _sessions(base)
    ours_s = _sessions(ours)
    theirs_s = _sessions(theirs)

    base_keys = {session_key(s) for s in base_s}
    ours_keys = {session_key(s) for s in ours_s}
    theirs_keys = {session_key(s) for s in theirs_s}
    base_anchors = unique_anchors(base_s)

    # Conflict guard: a base session that survives on neither side means both
    # sides changed/removed the very same recording. Only the same edit on both
    # sides is safe to accept; anything else — two different edits, an edit
    # against a deletion, an unpaired change — is left to a human.
    for b in base_s:
        k = session_key(b)
        if k in ours_keys or k in theirs_keys:
            continue
        ours_x = _replacement(b, base_anchors, base_keys, ours_s)
        theirs_x = _replacement(b, base_anchors, base_keys, theirs_s)
        if ours_x is not None and theirs_x is not None:
            if session_key(ours_x) == session_key(theirs_x):
                continue
        raise MergeConflict(
            "a session present in the merge base was modified or removed on "
            "both sides; refusing to auto-resolve"
        )

    # Standard 3-way union, keyed by exact session content:
    #   • in base and still on both sides            → keep (unchanged)
    #   • in base, gone from exactly one side        → deletion → drop
    #   • NOT in base, present on a side             → addition → keep
    # Deletions are honoured (a base session absent from one side, unchanged on
    # the other, is dropped) while genuine same-day appends are added.
    merged: dict[str, dict] = {}
    for s in ours_s:
        k = session_key(s)
        if k in base_keys and k not in theirs_keys:
            continue  # deleted on theirs, unchanged on ours → honour deletion
        merged.setdefault(k, s)
    for s in theirs_s:
        k = session_key(s)
        if k in base_keys and k not in ours_keys:
            continue  # deleted on ours, unchanged on theirs → honour deletion
        merged.setdefault(k, s)

    return sorted(merged.values(), key=_sort_key)


def merge_documents(base: Any, ours: Any, theirs: Any) -> dict:
    """Merge three parsed capture documents into one ``{"sessions": [...]}`` dict."""
    return {"sessions": merge_sessions(base, ours, theirs)}


@dataclass(frozen=True)
class SessionReplacement:
    """An upstream session the overlay replaced with the source's edited copy.

    ``index`` is its position in the upstream (and result) session list; ``fields``
    names the top-level session fields that differ, e.g. ``["notes"]``.
    """

    index: int
    date: str
    label: str
    time: str
    fields: list[str]


@dataclass
class Overlay:
    """What :func:`overlay` did: the resulting document (None if unchanged) and why."""

    doc: dict | None
    added: int = 0
    replaced: list[SessionReplacement] = field(default_factory=list)


def overlay(upstream: Any, source: Any) -> Overlay:
    """Lay ``source``'s sessions onto ``upstream``: append the new, replace the edited.

    The **overlay** counterpart to :func:`merge_documents`, for combining two
    copies where no merge base is available — ``canair contribute`` laying a
    contributor's local captures on top of the upstream copy. It **never drops** a
    session (a local file that is merely *behind* upstream must not propose
    deleting what it lacks) and its ``doc`` is ``None`` when ``source`` changes
    nothing, so the caller can leave the file's bytes alone entirely.

    A source session sharing an anchor with an upstream session but differing in
    content is the same recording edited on one side (``--set-notes``,
    ``migrate-discover``, ``split-batched`` …). With no base to say *which* side
    edited it, the **source wins** — contributing your edit is the point — and the
    upstream copy is replaced in place rather than duplicated next to it. Every
    replacement is returned in ``replaced``, because the same shape is also what a
    *stale* source looks like (upstream edited it later); the caller must surface
    them so that revert can't happen unnoticed.

    Deliberately **order-preserving and asymmetric**, unlike the merge-driver:
    upstream's session list is kept verbatim (replacements in place) and new
    sessions are appended after it. The driver's canonical re-sort would otherwise
    rewrite an untouched file end-to-end — many sessions carry no first-capture
    ``time``, so :func:`_sort_key` falls back to sorting them by *label*, which
    bears no relation to the append order the file was written in. That turned
    every contribution into a whole-history diff (see
    ``plans/2026-08-05-contribute-workspace-self-collision.md``).
    """
    up_raw = upstream.get("sessions") if isinstance(upstream, dict) else None
    up_sessions = list(up_raw) if isinstance(up_raw, list) else []
    seen = {session_key(s) for s in up_sessions}
    up_anchors = unique_anchors(up_sessions)
    src_sessions = _sessions(source)
    src_anchors = unique_anchors(src_sessions)

    result = Overlay(doc=None)
    added: list[dict] = []
    for i, session in enumerate(src_sessions):
        key = session_key(session)
        if key in seen:
            continue
        seen.add(key)
        anchor = session_anchor(session)
        at = up_anchors.get(anchor) if anchor is not None and src_anchors.get(anchor) == i else None
        if at is None:
            added.append(session)
            continue
        old = up_sessions[at]
        up_sessions[at] = session
        result.replaced.append(
            SessionReplacement(
                index=at,
                date=str(session.get("date") or ""),
                label=str(session.get("label") or ""),
                time=_first_time(session),
                fields=changed_fields(old, session),
            )
        )
    if not added and not result.replaced:
        return result

    base = dict(upstream) if isinstance(upstream, dict) else {}
    result.doc = {**base, "sessions": [*up_sessions, *added]}
    result.added = len(added)
    return result


def overlay_documents(upstream: Any, source: Any) -> dict | None:
    """The document :func:`overlay` produces, or None when ``source`` changes nothing."""
    return overlay(upstream, source).doc
