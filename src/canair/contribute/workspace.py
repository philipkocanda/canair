"""The workspace: a fork clone or a direct upstream clone, its reset and its leftovers."""

from __future__ import annotations

import shutil
from pathlib import Path

from canair.contribute.git import (
    _NO_LFS_ENV,
    BASE_BRANCH,
    MODE_DIRECT,
    MODE_FORK,
    UPSTREAM_URL,
    Preflight,
    Step,
    _run,
    _upstream_ref,
    status_entries,
)
from canair.contribute.github import can_push_directly
from canair.core.paths import GITHUB_REPO as UPSTREAM_REPO


def workspace_dir() -> Path:
    """Persistent clone location, reused across contributions."""
    from canair.core.paths import config_dir

    return config_dir() / "contribute" / UPSTREAM_REPO.split("/")[-1]


def is_managed_workspace(path: Path) -> bool:
    """Whether ``path`` is the throwaway clone canair manages (vs the user's own).

    Load-bearing for :func:`reset_workspace`: the managed clone holds nothing a
    contributor authored, so discarding its local state is always safe. A
    ``--repo-dir`` checkout is the opposite — it may hold hours of unrelated work,
    so it is only ever *warned* about.
    """
    try:
        return path.resolve() == workspace_dir().resolve()
    except OSError:
        return path == workspace_dir()


def ensure_workspace(pre: Preflight, workspace: Path) -> tuple[list[Step], str, bool]:
    """Ensure ``workspace`` is a usable checkout; return ``(steps, mode, ok)``.

    Chooses the mode by the user's upstream access:

    * ``MODE_DIRECT`` — has push access (or owns the repo, which can't be
      forked): clone the upstream repo directly (``origin`` = upstream).
    * ``MODE_FORK`` — no push access: fork under the user's account and clone the
      fork (``origin`` = fork, ``upstream`` remote = the source repo).

    On a reused clone it just fetches. ``ok`` reflects only the **critical**
    clone/fork step — a failed *fetch* (offline, or a local ``--repo-dir`` with no
    matching remote) is tolerated, since an existing checkout can still be
    branched from its local base.
    """
    assert pre.gh and pre.git
    workspace.parent.mkdir(parents=True, exist_ok=True)
    fresh = not (workspace / ".git").is_dir()
    mode = _detect_mode(pre, workspace, fresh)

    steps: list[Step] = []
    if fresh:
        if mode == MODE_DIRECT:
            steps.append(
                _run([pre.gh, "repo", "clone", UPSTREAM_REPO, str(workspace)], env=_NO_LFS_ENV)
            )
        else:
            steps.append(
                _run(
                    [pre.gh, "repo", "fork", UPSTREAM_REPO, "--clone", "--remote"],
                    cwd=workspace.parent,
                    env=_NO_LFS_ENV,
                )
            )
        if not steps[-1].ok:
            return steps, mode, False

    # Fetches are best-effort (don't fail the run when offline / no remote).
    if mode == MODE_FORK:
        steps.append(_ensure_upstream_remote(pre, workspace))
        fetched = _fetch_base(pre, workspace, "upstream")
        steps.append(fetched)
        if not fetched.ok:
            steps.append(_fetch_base(pre, workspace, "origin"))
    else:
        steps.append(_fetch_base(pre, workspace, "origin"))
    return steps, mode, True


def _fetch_base(pre: Preflight, workspace: Path, remote: str) -> Step:
    """Fetch only ``remote``'s base branch, the one ref :func:`_upstream_ref` reads.

    A plain ``git fetch`` also downloads every other branch on the remote (each
    open contribution's branch among them), which nothing here reads. Not
    refreshing the contribution branch's tracking ref also keeps the
    ``--force-with-lease`` in :func:`push_branch` meaningful: a commit someone
    else pushed to that branch makes the push fail instead of being overwritten.
    """
    assert pre.git
    return _run([pre.git, "-C", str(workspace), "fetch", remote, BASE_BRANCH, "--quiet"])


def _detect_mode(pre: Preflight, workspace: Path, fresh: bool) -> str:
    """Fork vs direct. Reuse an existing clone's remotes; else probe access."""
    if not fresh:
        # An existing clone tells us how it was set up: a fork has an `upstream`
        # remote distinct from `origin`; a direct clone has origin == upstream.
        assert pre.git
        has_upstream = "upstream" in _run([pre.git, "-C", str(workspace), "remote"]).stdout.split()
        return MODE_FORK if has_upstream else MODE_DIRECT
    return MODE_DIRECT if can_push_directly(pre) else MODE_FORK


def _ensure_upstream_remote(pre: Preflight, workspace: Path) -> Step:
    """Make sure an ``upstream`` remote points at the canonical repo."""
    assert pre.git
    existing = _run([pre.git, "-C", str(workspace), "remote"])
    remotes = set(existing.stdout.split())
    if "upstream" in remotes:
        return existing
    return _run([pre.git, "-C", str(workspace), "remote", "add", "upstream", UPSTREAM_URL])


def reset_workspace(pre: Preflight, workspace: Path) -> list[Step]:
    """Discard every local change in ``workspace``, making the staging area hermetic.

    ``git checkout -B`` alone is **not** enough: it only overwrites paths whose
    content differs between the old HEAD and the new base, so an uncommitted file
    left by an earlier run (``--diff`` stages a copy and never commits) survives
    into the next contribution and is swept into its commit by
    ``git add -- profiles/<name>``. That is how eight stale capture files once
    rode along in a PR.

    So the staged tree is rebuilt from the base ref every run: ``reset --hard``
    for tracked modifications, ``clean -fd -- profiles/`` for untracked leftovers
    (scoped to ``profiles/`` — nothing else is ever written there, and a full
    clean would drop the LFS/config state of the clone).

    Only ever called for :func:`is_managed_workspace`; a user's own ``--repo-dir``
    checkout is warned about instead. Returns the steps for reporting.
    """
    assert pre.git
    git = pre.git
    base = _upstream_ref(workspace, git)
    return [
        _run([git, "-C", str(workspace), "reset", "--hard", "--quiet", base]),
        _run([git, "-C", str(workspace), "clean", "-fd", "--quiet", "--", "profiles/"]),
    ]


def leftover_changes(pre: Preflight, workspace: Path) -> list[str]:
    """Repo-relative paths an earlier run left uncommitted in the managed workspace.

    Every tracked change, plus untracked files under ``profiles/`` (the only place
    a run writes). These block :func:`start_branch` whenever the old HEAD — an
    earlier contribution branch — and the new base disagree on a path, which is
    why they are cleared (after a backup) *before* branching.
    """
    assert pre.git
    return [
        path
        for code, path in status_entries(pre.git, workspace)
        if code != "??" or path.startswith("profiles/")
    ]


def backup_dir() -> Path:
    """Where discarded workspace leftovers are saved.

    Outside the clone itself, so deleting the workspace (the remedy for a broken
    one) keeps them.
    """
    return workspace_dir().parent / "backups"


def backup_leftovers(
    workspace: Path, paths: list[str], dest: Path, *, heading: str | None = None
) -> Path:
    """Copy each leftover in ``paths`` from ``workspace`` into ``dest``.

    Files are copied verbatim (no git filters, so LFS-tracked capture logs keep
    their content), preserving their repo-relative paths. A path that no longer
    exists (a deletion) is only listed in the ``MANIFEST.txt`` written alongside,
    under ``heading``.
    """
    dest.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel in paths:
        src = workspace / rel
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            lines.append(f"saved    {rel}")
        else:
            lines.append(f"deleted  {rel}")
    heading = heading or f"Uncommitted leftovers discarded from {workspace}"
    (dest / "MANIFEST.txt").write_text(f"{heading}\n\n" + "\n".join(lines) + "\n")
    return dest


def discard_local_changes(pre: Preflight, workspace: Path) -> list[Step]:
    """Drop uncommitted changes, keeping the current branch's commits.

    Unlike :func:`reset_workspace` this resets to ``HEAD``, not the base, so a
    ``--dry-run`` commit on an earlier branch survives; it only has to clear the
    way for :func:`start_branch`.
    """
    assert pre.git
    git = pre.git
    return [
        _run([git, "-C", str(workspace), "reset", "--hard", "--quiet", "HEAD"]),
        _run([git, "-C", str(workspace), "clean", "-fd", "--quiet", "--", "profiles/"]),
    ]


def local_changes(pre: Preflight, workspace: Path, profile_name: str) -> list[str]:
    """``git status --porcelain`` entries under ``profiles/<name>`` in ``workspace``.

    Used to warn before a **user-supplied** ``--repo-dir`` contributes work canair
    did not put there: ``commit_profile`` stages the whole profile directory, so
    an unrelated uncommitted edit in it would silently join the PR.
    """
    assert pre.git
    rel = f"profiles/{profile_name}"
    res = _run([pre.git, "-C", str(workspace), "status", "--porcelain", "--", rel])
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]
