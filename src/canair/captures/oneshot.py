"""Saving a finished session through the write-ahead journal, for the one-shot producers.

Kept apart from :mod:`canair.captures.save` because the journal imports that module to write
the file, so a function of ``save`` that opens a journal would close a cycle.
"""

from pathlib import Path

from canair.captures.journal import CaptureJournal
from canair.captures.types import CaptureSession
from canair.profile.model import Profile


def save_session_journaled(
    session: CaptureSession, captures_dir: Path | None = None, *, profile: Profile
) -> Path | None:
    """Save a pre-built session via a write-ahead journal (crash-safe path).

    Used by the one-shot producers (scan/raw/discover) so every save path shares
    the same recover-on-crash behaviour as streaming query/monitor. The session
    is written to a journal, then reconciled into ``captures/YYYY-MM-DD.json`` and
    the journal removed. Returns the capture file path (or None if empty).

    The session's own ``transport`` (set by its builder from the live client) is
    carried through the journal meta so recovered one-shot captures keep their
    provenance. A session without one is saved without one: re-resolving the
    configuration here would ignore ``--transport``/``--wican`` and any
    connect-time fallback, and record a transport the session never used.
    """
    if captures_dir is None:
        captures_dir = profile.captures_dir

    journal = CaptureJournal.open(
        captures_dir,
        profile=profile,
        label=session.get("label", ""),
        vehicle_states=session.get("vehicle_states"),
        notes=session.get("notes"),
        source="oneshot",
        transport=session.get("transport"),
    )
    journal.append_session(session)
    return journal.reconcile()
