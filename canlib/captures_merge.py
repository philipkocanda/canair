"""Session-set merges of capture files (git merge-driver core + contribute overlay).

A dated capture file (``captures/YYYY-MM-DD.json``) is an **append-only** log:
``{"sessions": [ … ]}`` where each session is a self-contained block that, once
written, is never rewritten (only new sessions are appended, via
:func:`canlib.captures.save_session`). Two machines that each record on the same
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
  not rewrite capture logs it adds nothing to.

Session identity
----------------
Two sessions are "the same session" when their **content is byte-identical**
(same JSON). That is deliberately strict: the append-only writer never mutates a
session, so a real duplicate (the shared history both sides inherited from the
merge base) is identical, while any difference means a genuinely distinct
recording that must be kept. When both sides *modify the same base session
differently* — which should not happen for append-only data — we refuse to guess
and signal a conflict so git falls back to normal markers and a human decides.
"""

from __future__ import annotations

import json
from typing import Any


class MergeConflict(Exception):
    """A genuine conflict the union cannot safely resolve.

    Raised when both sides modified (or one modified, one deleted) the *same*
    base session in incompatible ways. The driver turns this into a non-zero
    exit so git writes the normal 3-way conflict markers instead.
    """


def session_key(session: Any) -> str:
    """Stable content key for a session (order-insensitive JSON).

    A session has no id — its identity *is* its content, deliberately, which is
    what makes both this merge and the layered-profile read (where the same
    session may exist in two layers) safe to deduplicate.
    """
    return json.dumps(session, sort_keys=True, ensure_ascii=False)


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


def merge_sessions(base: Any, ours: Any, theirs: Any) -> list[dict]:
    """Three-way union of the ``sessions`` lists of base/ours/theirs.

    Returns the merged session list, deterministically ordered. Raises
    :class:`MergeConflict` when both sides changed the *same* base session in
    incompatible ways (a case that should not arise for append-only capture
    logs, but which we refuse to auto-resolve rather than silently pick a side).

    Algorithm (keyed by exact session content):

    * A session present in base and still on **both** sides is kept once (this is
      how the shared history collapses to a single copy).
    * A session added on **exactly one** side (absent from base) is included.
    * A session in base that is **removed** on one side while **unchanged** on the
      other is honoured as a deletion — dropped.
    * A base session that is gone from **both** sides (each replaced by a
      *different* session) is a genuine divergent edit → conflict.
    """
    base_s = _sessions(base)
    ours_s = _sessions(ours)
    theirs_s = _sessions(theirs)

    base_keys = {session_key(s) for s in base_s}
    ours_keys = {session_key(s) for s in ours_s}
    theirs_keys = {session_key(s) for s in theirs_s}

    # Conflict guard: a base session that survives on neither side means both
    # sides changed/removed the very same recording. For an append-only log this
    # should never happen; when it does, don't guess — let git show markers.
    for k in base_keys:
        if k not in ours_keys and k not in theirs_keys:
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


def overlay_documents(upstream: Any, source: Any) -> dict | None:
    """``upstream`` with ``source``'s not-yet-present sessions appended, or None.

    The **overlay** counterpart to :func:`merge_documents`, for combining two
    independent recordings where one side is authoritative and a deletion must
    not be inferred — ``canair contribute`` laying a contributor's local captures
    on top of the upstream copy. It **never drops** a session (a local file that
    is merely *behind* upstream must not propose deleting what it lacks) and
    returns ``None`` when ``source`` adds nothing, so the caller can leave the
    file's bytes alone entirely.

    Deliberately **order-preserving and asymmetric**, unlike the merge-driver:
    upstream's session list is kept verbatim and new sessions are appended after
    it. The driver's canonical re-sort would otherwise rewrite an untouched file
    end-to-end — many sessions carry no first-capture ``time``, so
    :func:`_sort_key` falls back to sorting them by *label*, which bears no
    relation to the append order the file was written in. That turned every
    contribution into a whole-history diff (see
    ``plans/2026-08-05-contribute-workspace-self-collision.md``).
    """
    up_raw = upstream.get("sessions") if isinstance(upstream, dict) else None
    up_sessions = list(up_raw) if isinstance(up_raw, list) else []
    seen = {session_key(s) for s in up_sessions}

    added: list[dict] = []
    for session in _sessions(source):
        key = session_key(session)
        if key in seen:
            continue
        seen.add(key)
        added.append(session)
    if not added:
        return None

    base = dict(upstream) if isinstance(upstream, dict) else {}
    return {**base, "sessions": [*up_sessions, *added]}
