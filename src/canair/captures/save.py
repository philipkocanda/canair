"""Saving a session: the version stamp, the capture tag, the file write and the banner."""

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

from canair.captures import io as capture_io
from canair.captures.io import write_lock
from canair.captures.session_edit import annotate_session_spans, set_session_field
from canair.captures.tags import capture_tag
from canair.captures.types import (
    CaptureSession,
)
from canair.ecus.registry import build_rx_index, ecu_name_from_ref
from canair.install.build_info import full_version
from canair.install.context import snapshot_write_note
from canair.privacy import sensitive
from canair.privacy.sensitive import take_withheld_note
from canair.profile.model import Profile
from canair.variants.tags import TAG_FIELDS, narrow_to_captured

_FILE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _stamp_version(session: CaptureSession) -> CaptureSession:
    """Return a copy of ``session`` with the recording canair ``version`` stamped.

    Called from :func:`save_session` — the single choke point every session flows
    through before landing on disk — so every recorded session carries the tool
    version that wrote it (provenance for debugging capture issues traced to a
    specific release). Recorded from a git checkout, the stamp carries that
    checkout's branch and commit too (``1.15.0+main.343b244``, see
    :mod:`canair.install.build_info`), so a reading can be traced to the exact code that
    produced it — not just the release it was near. An existing ``version`` (e.g.
    on a recovered/re-saved session) is left untouched. Inserted right after
    ``label`` so the on-disk field order stays readable (date, label, version, …).
    """
    if session.get("version"):
        return session

    version = full_version()

    rebuilt: dict = {}
    for key, value in session.items():
        rebuilt[key] = value
        if key == "label":
            rebuilt["version"] = version
    if "version" not in rebuilt:
        # No ``label`` key (shouldn't happen for a valid session) — append it.
        rebuilt["version"] = version
    # Sound: every key/value is copied verbatim from an already-typed
    # CaptureSession, plus the one `version: str` this function adds. The cast is
    # unavoidable because the whole point is to rebuild the dict in a *different
    # key order*, which no TypedDict literal can express.
    return cast(CaptureSession, rebuilt)


def current_capture_tag(captures_dir: Path | None = None, *, profile: Profile) -> dict | None:
    """The variant tag a recording into ``captures_dir`` gets right now, or None.

    ``profile``'s explicitly selected variant (``--variant`` / ``CANAIR_VARIANT``), and only
    when ``captures_dir`` is that profile's own store (or None): a write aimed at another
    directory is no evidence about the car the profile selected.
    """
    if captures_dir is not None:
        try:
            if Path(captures_dir).resolve() != profile.captures_dir.resolve():
                return None
        except OSError:  # pragma: no cover - unreadable path
            return None
    return capture_tag(profile)


def _captured_ecus(session: CaptureSession, *, profile: Profile) -> set[str]:
    """Upper-cased names of the ECUs whose replies ``session`` holds."""

    try:
        index = build_rx_index(profile=profile)
    except Exception:
        return set()
    names = set()
    for cap in session.get("captures") or []:
        rx = capture_io.capture_rx(cap)
        if rx:
            names.add(str(ecu_name_from_ref(rx, index, profile=profile)).upper())
    return names


def apply_capture_tag(
    session: CaptureSession, tag: dict | None, *, profile: Profile
) -> CaptureSession:
    """``session`` with ``tag`` set, unless it already carries a tag of its own.

    Revisions are kept only for the ECUs the session captured.
    """
    if not tag or session.get("variant") or session.get("revisions"):
        return session

    if tag.get("revisions"):
        tag = narrow_to_captured(tag, _captured_ecus(session, profile=profile))
    tagged = cast(CaptureSession, dict(session))
    for key in TAG_FIELDS:
        if key in tag:
            set_session_field(tagged, key, tag[key])
    return tagged


def saved_banner(capture_file: Path, n_captures: int) -> str:
    """The one-line "where the captures landed" banner.

    Always the **full path**, never a bare filename: a save is worthless if the
    user has to guess which profile's captures/ directory it went into. Shared
    by :func:`save_session` (every save path funnels through it) and by the
    monitor's post-TUI replay of banners Textual swallowed.

    When the path is inside an install snapshot the banner grows a warning and a
    remedy, because that write "succeeds" and is then destroyed by the next
    reinstall — the loudest place to say so is next to the path itself.
    """
    banner = f"  \u2192 Saved {n_captures} capture(s) to {capture_file}"

    notes = [n for n in (take_withheld_note(), snapshot_write_note(capture_file)) if n]
    return "\n".join([banner, *notes])


def save_session(
    session: CaptureSession,
    captures_dir: Path | None = None,
    *,
    stamp_variant: bool = True,
    profile: Profile,
) -> Path:
    """Append a session dict to captures/YYYY-MM-DD.json. Returns the file path.

    The file is named after the session's own ``date`` (falling back to today),
    so a session captured on — or recovered from — a different day lands in the
    correct per-day file rather than today's. When ``captures_dir`` is None, the
    active vehicle profile's captures/ directory is used.

    Every session is stamped with the recording canair ``version`` here (the one
    place all save paths funnel through), unless it already carries one, and is
    annotated with a ``state_spans`` timeline when its payloads show the vehicle
    changing state mid-recording — see :func:`annotate_session_spans`.

    It is also tagged with the selected variant (:func:`current_capture_tag`) unless
    ``stamp_variant`` is False — the journal's reconcile, whose sessions carry the tag
    recorded when the journal was opened, so a crash recovered later by a run with a
    different selection still names the car that was actually recorded.
    """
    session = _stamp_version(session)
    if stamp_variant:
        session = apply_capture_tag(
            session, current_capture_tag(captures_dir, profile=profile), profile=profile
        )
    if captures_dir is None:
        captures_dir = profile.captures_dir
    # The last gate before disk: a reply to a sensitive PID is never written,
    # whichever producer built the session (the journal applies the same rule).
    session = cast(
        CaptureSession,
        sensitive.withhold_session(
            dict(session), sensitive.set_for_captures(captures_dir, profile=profile)
        ),
    )
    # Use the session's own date (leading YYYY-MM-DD, tolerating a same-day
    # suffix like "2026-04-17-b") so the file matches when the payloads were
    # actually acquired; fall back to "now" when the session has no usable date,
    # so a malformed one can never name a path outside ``captures_dir``.
    raw_date = str(session.get("date") or "").strip()[:10]
    file_date = raw_date if _FILE_DATE.fullmatch(raw_date) else datetime.now().strftime("%Y-%m-%d")
    capture_file = captures_dir / f"{file_date}{capture_io.CAPTURE_SUFFIX}"

    # Read, append, write and annotate as one step: two sessions saving into the same day's
    # file at once would otherwise each write over the other's session.
    with write_lock(captures_dir):
        if capture_file.exists():
            data = capture_io.load_capture_file(capture_file)
            if "sessions" not in data:
                data = {"sessions": []}
            data["sessions"].append(session)
        else:
            data = {"sessions": [session]}

        capture_io.dump_capture_file(capture_file, data)
        annotate_session_spans(capture_file, profile=profile)

    print(saved_banner(capture_file, len(session.get("captures", []))), file=sys.stderr)
    return capture_file
