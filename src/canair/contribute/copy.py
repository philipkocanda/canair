"""Copying the profile into the workspace: the bundle members, the capture overlay."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from canair.captures.merge import SessionReplacement
from canair.privacy.sensitive import SensitiveSet
from canair.profile import member_names, members_by_role

# Bundle members copied into a contribution, from the single declaration in
# canair.profile. Curated definitions always ship; the captures/ evidence is
# opt-in-controlled separately. External material (references/, uncertain
# licence) and generated output (out/) are excluded by their role.
_DEFINITION_MEMBERS = member_names(members_by_role("definition"))


_CAPTURE_MEMBER = members_by_role("evidence")[0].name


# Transient/helper members never contributed, matched as globs against each path
# component: the --save write-ahead log, underscore-prefixed scratch files (the
# same convention capture_io's readers skip) and half-written temp files.
_SKIP_PATTERNS = ("_*", ".journal", "*.tmp")


def copy_profile(
    profile,
    workspace: Path,
    *,
    include_captures: bool,
    warn: Callable[[str], None] | None = None,
    replaced: Callable[[CaptureReplacement], None] | None = None,
) -> Path:
    """Copy the resolved profile bundle into ``workspace/profiles/<name>/``.

    Each *managed* definition member (ecus/, signals/, profile.yaml, states,
    buses) is replaced with the user's copy so the git diff reflects the current
    state. Members the tool does **not** manage (``out/`` generated JSON,
    ``references/`` possibly-third-party material, ``logs/``) are left untouched,
    so contributing into an existing upstream profile never *deletes* its
    references just because this contribution didn't include them.

    ``captures/`` (when ``include_captures``) is handled specially: rather than
    replacing the directory wholesale (which would propose *deleting* any
    upstream capture session a merely-behind source lacks), each dated capture
    log gets the source's **new sessions appended** to the upstream copy already
    in the workspace — captures are append-only evidence, so the contribution
    only ever *adds* sessions, or *replaces* one with the source's edited copy of
    the same recording. See :func:`_overlay_captures`. The source is the
    profile's own capture store (:attr:`Profile.captures_dir` — the user's layer
    of a layered profile, not the read-only base it extends).

    ``warn`` receives a human-readable message for anything skipped that the user
    should know about, and ``replaced`` each upstream session the contribution
    replaces (the caller must surface those — see
    :func:`canair.captures.merge.overlay`). Returns the destination directory.
    """
    dest = workspace / "profiles" / profile.name
    dest.mkdir(parents=True, exist_ok=True)  # overlay — do NOT wipe the whole dir

    for member in _DEFINITION_MEMBERS:
        src = profile.root / member
        if not src.exists():
            continue
        target = dest / member
        # Source == destination happens when canair is run from inside the
        # staging workspace itself (see workspace_collision); copying would raise
        # SameFileError, or rmtree the source for a directory member.
        if _same_path(src, target):
            continue
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target)  # replace this member subtree wholesale
            shutil.copytree(src, target, ignore=shutil.ignore_patterns(*_SKIP_PATTERNS))
        else:
            shutil.copy2(src, target)

    if include_captures:
        from canair.captures.sensitive_purge import purge_dir
        from canair.privacy.sensitive import profile_set

        sset = profile_set(profile)
        _overlay_captures(
            profile.captures_dir, dest / _CAPTURE_MEMBER, warn=warn, replaced=replaced, sset=sset
        )
        # Whatever either side held, a reply to a sensitive PID never ships.
        for r in purge_dir(dest / _CAPTURE_MEMBER, sset):
            if warn:
                warn(
                    f"{_CAPTURE_MEMBER}/{r.path.name}: left out {r.removed} capture(s) of "
                    "sensitive PIDs"
                )
    return dest


def _same_path(a: Path, b: Path) -> bool:
    """Whether two paths denote the same location (unresolvable → not same)."""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _is_transient(rel: Path) -> bool:
    """Whether any component of ``rel`` matches a :data:`_SKIP_PATTERNS` glob."""
    return any(fnmatch(part, pattern) for part in rel.parts for pattern in _SKIP_PATTERNS)


def _overlay_captures(
    src: Path,
    dst: Path,
    *,
    warn: Callable[[str], None] | None = None,
    replaced: Callable[[CaptureReplacement], None] | None = None,
    sset: SensitiveSet | None = None,
) -> None:
    """Overlay ``src`` captures onto ``dst`` without ever deleting upstream data.

    Dated capture logs (top-level ``*.json``) that exist on both sides get the
    source's new sessions **appended** (and its edited copies of upstream
    sessions put in their place, each reported to ``replaced``) so a source that
    is behind upstream never proposes dropping sessions, and a log the source
    changes nothing in is left byte-identical. Everything else under captures/
    (e.g. ``can/`` raw-frame logs and their index) is copied as an overlay: files
    present in the source replace their counterpart, files only upstream are left
    in place. Transient members (:data:`_SKIP_PATTERNS`) are skipped.
    """
    if not src.exists() or _same_path(src, dst):
        return
    dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if _is_transient(rel):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # A dated capture log present on both sides → append the new sessions.
        if rel.parent == Path(".") and path.suffix == ".json" and target.exists():
            problem = _overlay_capture_file(
                upstream=target, source=path, replaced=replaced, sset=sset
            )
            if problem and warn:
                warn(problem)
        else:
            shutil.copy2(path, target)


@dataclass(frozen=True)
class CaptureReplacement:
    """An upstream capture session the contribution replaces with the source's copy."""

    file: str  # e.g. "captures/2026-07-28.json"
    session: SessionReplacement


def _overlay_capture_file(
    *,
    upstream: Path,
    source: Path,
    replaced: Callable[[CaptureReplacement], None] | None = None,
    sset: SensitiveSet | None = None,
) -> str | None:
    """Overlay ``source``'s sessions onto ``upstream``. Returns a warning, or None.

    The source's sensitive rows are stripped first (``sset``), so a session that
    held one does not read as an edited copy replacing its upstream original.

    Leaves the file **untouched** when the source changes no session, so a
    contribution never rewrites — and never reorders — a capture log it doesn't
    actually add to.

    An unreadable file on either side is skipped rather than overwritten: the
    source side can't reach here (``canair validate all`` gates the run), and on
    the upstream side a wholesale overwrite would drop exactly the sessions this
    overlay exists to preserve.
    """
    from canair.captures.io import dump_capture_file, load_capture_file
    from canair.captures.merge import overlay

    try:
        up = load_capture_file(upstream)
        ours = load_capture_file(source)
    except (OSError, ValueError) as e:  # ValueError: bad JSON or a bad repeat encoding
        return f"{_CAPTURE_MEMBER}/{source.name}: not contributed (unreadable capture file: {e})"
    if sset:
        from canair.captures.sensitive_purge import strip_doc

        ours = strip_doc(ours, sset)[0]
    result = overlay(up, ours)
    if result.doc is not None:
        dump_capture_file(upstream, result.doc)
    if replaced is not None:
        for r in result.replaced:
            replaced(CaptureReplacement(f"{_CAPTURE_MEMBER}/{upstream.name}", r))
    return None


def dir_size(path: Path) -> int:
    """Total size in bytes of everything under ``path`` (0 if absent)."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
