"""The changes a contribution leaves behind in the contributor's own checkout.

A profile recorded inside a canair checkout (``profiles/<name>/`` of a clone) keeps
its new captures and edits uncommitted there. Once a contribution has put them on
the pull request's branch they are duplicates, and when the PR merges, pulling
upstream conflicts with them. This module finds which of those changes the
contribution ref already holds, so that only those can be dropped (after a
backup).

A path counts as held only when dropping it loses nothing:

* a file is byte-identical to the ref's copy, judged through the clone's own git
  filters, so a Git LFS capture log compares by content rather than by pointer;
* a dated capture log adds no session to the ref's copy and edits none of them
  (the same overlay rule the contribution itself used);
* a deleted file is absent from the ref too.

Everything else is reported but left alone, and a profile outside a canair
checkout is skipped entirely: pulling upstream cannot bring its changes back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import contribute as C
from .profile import members_by_role

_CAPTURE_MEMBER = members_by_role("evidence")[0].name


@dataclass(frozen=True)
class SourceChanges:
    """The uncommitted changes under a profile in the contributor's checkout."""

    checkout: Path
    held: list[str] = field(default_factory=list)  # repo-relative; on the ref
    not_held: list[str] = field(default_factory=list)  # repo-relative; left alone
    untracked: frozenset[str] = frozenset()  # the subset of ``held`` git does not track


def checkout_of(pre: C.Preflight, profile_root: Path, profile_name: str) -> Path | None:
    """The canair checkout ``profile_root`` sits in at ``profiles/<profile_name>``, or None."""
    assert pre.git
    if not profile_root.is_dir():
        return None
    top = C._run([pre.git, "-C", str(profile_root), "rev-parse", "--show-toplevel"])
    if not top.ok or not top.stdout.strip():
        return None
    checkout = Path(top.stdout.strip())
    try:
        rel = profile_root.resolve().relative_to(checkout.resolve())
    except ValueError:
        return None
    return checkout if rel == Path("profiles") / profile_name else None


def find_changes(
    pre: C.Preflight, profile_root: Path, profile_name: str, workspace: Path, ref: str = "HEAD"
) -> SourceChanges | None:
    """Sort the checkout's changes under the profile by whether ``workspace``'s ``ref`` holds them.

    None when the profile is not in a canair checkout (or is in ``workspace``
    itself) or has no uncommitted change.
    """
    assert pre.git
    checkout = checkout_of(pre, profile_root, profile_name)
    if checkout is None or _same(checkout, workspace):
        return None
    entries = C.status_entries(pre.git, checkout, f"profiles/{profile_name}")
    if not entries:
        return None
    held: list[str] = []
    not_held: list[str] = []
    untracked: set[str] = set()
    for code, path in entries:
        # A staged rename or copy also touches its source path; leave it to the user.
        if code[0] not in "RC" and _held(pre.git, checkout, workspace, ref, path):
            held.append(path)
            if code == "??":
                untracked.add(path)
        else:
            not_held.append(path)
    return SourceChanges(checkout, held, not_held, frozenset(untracked))


def discard(pre: C.Preflight, changes: SourceChanges, backup: Path) -> list[C.Step]:
    """Back up ``changes.held`` to ``backup``, then restore those paths to ``HEAD``.

    Returns the git steps that failed (empty on success). Untracked files are
    deleted; tracked ones are restored in both the index and the working tree.
    """
    assert pre.git
    if not changes.held:
        return []
    C.backup_leftovers(
        changes.checkout,
        changes.held,
        backup,
        heading=f"Contributed changes discarded from {changes.checkout}",
    )
    tracked = [p for p in changes.held if p not in changes.untracked]
    failed: list[C.Step] = []
    if tracked:
        step = C._run(
            [
                pre.git,
                "-C",
                str(changes.checkout),
                "restore",
                "--source=HEAD",
                "--staged",
                "--worktree",
                "--",
                *tracked,
            ]
        )
        if not step.ok:
            failed.append(step)
    for rel in changes.untracked:
        (changes.checkout / rel).unlink(missing_ok=True)
    return failed


def _held(git: str, checkout: Path, workspace: Path, ref: str, path: str) -> bool:
    """Whether ``workspace``'s ``ref`` holds the checkout's version of ``path``."""
    src = checkout / path
    blob = C._run([git, "-C", str(workspace), "rev-parse", "--verify", "--quiet", f"{ref}:{path}"])
    if not src.exists():
        return not blob.ok
    if not blob.ok or not src.is_file():
        return False
    ours = C._run([git, "-C", str(workspace), "hash-object", f"--path={path}", str(src)])
    if ours.ok and ours.stdout.strip() == blob.stdout.strip():
        return True
    return _is_capture_log(path) and _capture_log_held(git, workspace, ref, path, src)


def _is_capture_log(path: str) -> bool:
    parts = Path(path).parts
    return len(parts) == 4 and parts[2] == _CAPTURE_MEMBER and parts[3].endswith(".json")


def _capture_log_held(git: str, workspace: Path, ref: str, path: str, src: Path) -> bool:
    """Whether the ref's capture log already has every session of ``src`` as written."""
    from .captures.io import load_capture_file, parse_capture_text
    from .captures.merge import overlay

    theirs = C._run([git, "-C", str(workspace), "show", f"{ref}:{path}"])
    if not theirs.ok:
        return False
    try:
        result = overlay(parse_capture_text(theirs.stdout), load_capture_file(src))
    except (OSError, ValueError):
        return False
    return result.doc is None and not result.replaced


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False
