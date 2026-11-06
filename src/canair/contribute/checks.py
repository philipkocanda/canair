"""Checks on a contribution before it is made: rollback of upstream definitions and a
workspace that is the source profile itself.
"""

from __future__ import annotations

from pathlib import Path

from canair.contribute.copy import _DEFINITION_MEMBERS
from canair.contribute.git import Preflight, _run

# Curated-definition members that normally only move *forward* — the same set
# that is always contributed (append-only captures/ is excluded, since a
# "deletion" there is prevented at copy time; see copy_profile). A contribution
# that *removes* committed upstream lines from these is a strong signal the
# source profile is stale.
_ROLLBACK_MEMBERS = _DEFINITION_MEMBERS


def definition_rollback(
    pre: Preflight, workspace: Path, profile_name: str
) -> list[tuple[str, int]]:
    """Definition files this contribution would delete committed upstream lines from.

    Curated definitions (``ecus/``, ``profile.yaml``, buses, states, groups,
    signals) normally only grow, so a diff that *removes* lines already merged
    upstream
    means the source is most likely **stale** and the contribution would revert
    that work. Returns ``(relpath, removed_lines)`` for each such file, sorted.

    This is a heuristic — a legitimate cleanup (pruning a dead param) also
    removes lines and will appear here — so the caller *warns and confirms*
    rather than blocking. Brand-new files (a fresh profile) are pure additions
    and never appear. Captures are excluded by construction.
    """
    assert pre.git
    rel = f"profiles/{profile_name}"
    # Make freshly-copied untracked files visible to `git diff` (as in diff_profile).
    _run([pre.git, "-C", str(workspace), "add", "--intent-to-add", "--", rel])
    paths = [f"{rel}/{member}" for member in _ROLLBACK_MEMBERS]
    res = _run([pre.git, "-C", str(workspace), "diff", "--numstat", "--", *paths])
    rolled: list[tuple[str, int]] = []
    for line in res.stdout.splitlines():
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        added, removed, path = cols
        if added == "-" or removed == "-":  # binary file — no line counts
            continue
        if int(removed) > 0:
            rolled.append((path, int(removed)))
    return sorted(rolled)


def workspace_collision(profile_root: Path, workspace: Path, profile_name: str) -> str | None:
    """How the source profile collides with the staging workspace, if at all.

    The workspace is a full canair checkout, so it **bundles profiles of its
    own** — and its bundled copy of the profile being contributed is exactly the
    destination :func:`copy_profile` writes to. Running canair with a cwd inside
    that checkout (``uv run`` builds the package from the local
    ``pyproject.toml``, and ``BUNDLED_PROFILES_DIR`` is relative to the *running*
    package) therefore resolves the "active profile" to the copy destination
    itself. Returns:

    * ``"self"`` — the profile **is** the workspace's own copy: source ==
      destination, so the copy is meaningless (and ``shutil`` would either raise
      ``SameFileError`` or ``rmtree`` the source).
    * ``"inside"`` — the profile merely lives somewhere under the workspace. That
      can work, but :func:`start_branch` resets the workspace's tracked files to
      ``upstream/main`` *before* the copy, so the source is mutated mid-run.
    * ``None`` — no overlap; the normal case.
    """
    try:
        src = profile_root.resolve()
        ws = workspace.resolve()
    except OSError:
        return None
    if src == ws / "profiles" / profile_name:
        return "self"
    if src.is_relative_to(ws):
        return "inside"
    return None
