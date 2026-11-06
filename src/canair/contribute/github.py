"""The GitHub side: the viewer's permission, the pull request and its text."""

from __future__ import annotations

import platform
from pathlib import Path

from canair.contribute.git import BASE_BRANCH, MODE_DIRECT, Preflight, Step, _run
from canair.core.paths import GITHUB_REPO as UPSTREAM_REPO
from canair.variants import effective_meta

GH_INSTALL_URL = "https://github.com/cli/cli#installation"


# GitHub permission levels that allow pushing a branch to the repo directly
# (so no fork is needed). Anything below WRITE (TRIAGE/READ/NONE) must fork.
_PUSH_PERMISSIONS = {"ADMIN", "MAINTAIN", "WRITE"}


def gh_install_hint() -> str:
    """Platform-appropriate instructions for installing the GitHub CLI."""
    system = platform.system()
    if system == "Darwin":
        primary = "brew install gh"
    elif system == "Linux":
        primary = f"see the install guide for your distro (apt/dnf/pacman):\n    {GH_INSTALL_URL}"
    elif system == "Windows":
        primary = "winget install --id GitHub.cli   (or: scoop install gh)"
    else:
        primary = f"see {GH_INSTALL_URL}"
    return (
        "The GitHub CLI (`gh`) is required to open a pull request automatically.\n"
        "  Install it:\n"
        f"    {primary}\n"
        "  Then authenticate (opens your browser):\n"
        "    gh auth login\n"
        f"  Install guide: {GH_INSTALL_URL}"
    )


def viewer_permission(pre: Preflight) -> str:
    """The authenticated user's permission on the upstream repo (GitHub term).

    One of ``ADMIN``/``MAINTAIN``/``WRITE``/``TRIAGE``/``READ``/``NONE`` (or
    ``""`` when it can't be determined). Drives fork-vs-direct: WRITE+ can push
    to the repo directly, so no fork is created.
    """
    assert pre.gh
    res = _run(
        [
            pre.gh,
            "repo",
            "view",
            UPSTREAM_REPO,
            "--json",
            "viewerPermission",
            "--jq",
            ".viewerPermission",
        ]
    )
    return res.stdout.strip() if res.ok else ""


def can_push_directly(pre: Preflight) -> bool:
    """Whether the user can push a branch to upstream (so forking is unnecessary).

    True for a collaborator/maintainer/owner. Notably the **repo owner cannot
    fork their own repo** ("a single user account cannot own both a parent and a
    fork"), so detecting push access is what lets an owner use this command.
    """
    return viewer_permission(pre) in _PUSH_PERMISSIONS


def find_open_pr(pre: Preflight, branch: str) -> tuple[int, str] | None:
    """The open upstream PR for ``branch``, as ``(number, url)``, or None.

    A re-run is the normal case, not an error: the default branch name is
    date-based, so contributing twice in a day pushes to a branch that already has
    a pull request — and ``gh pr create`` then fails outright. Pushing the branch
    has *already* updated that PR, so the run succeeded; this lookup is what lets
    the command say so instead of reporting a failure.

    ``--head`` takes the plain branch name here (unlike :func:`pr_head`'s
    ``owner:branch``): ``gh pr list`` filters on the head *ref*, and qualifying it
    with an owner matches nothing.
    """
    assert pre.gh
    res = _run(
        [
            pre.gh,
            "pr",
            "list",
            "--repo",
            UPSTREAM_REPO,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ]
    )
    if not res.ok:
        return None
    import json

    try:
        prs = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for pr in prs if isinstance(prs, list) else []:
        number, url = pr.get("number"), pr.get("url")
        if isinstance(number, int) and url:
            return number, str(url)
    return None


def create_pr(
    pre: Preflight,
    workspace: Path,
    *,
    title: str,
    body: str,
    head: str,
) -> Step:
    """Open the PR against the upstream repo's default branch. Returns the step
    (``stdout`` is the PR URL on success). ``head`` is ``owner:branch``."""
    assert pre.gh
    return _run(
        [
            pre.gh,
            "pr",
            "create",
            "--repo",
            UPSTREAM_REPO,
            "--base",
            BASE_BRANCH,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=workspace,
    )


def pr_head(pre: Preflight, branch: str, mode: str) -> str:
    """The ``--head`` spec for the PR.

    Direct (same-repo) PRs use a bare ``branch``; cross-fork PRs need
    ``owner:branch`` so GitHub finds the branch on the user's fork.
    """
    assert pre.gh
    if mode == MODE_DIRECT:
        return branch
    who = _run([pre.gh, "api", "user", "--jq", ".login"])
    owner = who.stdout.strip() if who.ok else ""
    return f"{owner}:{branch}" if owner else branch


def default_message(profile, *, include_captures: bool) -> str:
    """Commit-message subject/body for a profile contribution."""
    model = str(effective_meta(profile).get("car_model") or profile.name)
    scope = "profile + captures" if include_captures else "profile"
    return f"profiles: contribute {profile.name} ({model})\n\nAdd/update {scope} for {model}."


def default_pr_body(
    profile,
    *,
    include_captures: bool,
    summary: str = "",
    logs: str = "",
) -> str:
    """The generated PR body. ``summary``/``logs`` are pre-rendered Markdown sections."""
    model = str(effective_meta(profile).get("car_model") or profile.name)
    if summary:
        detail = f"{summary}\n"
    elif include_captures:
        detail = "- Includes `captures/` (raw evidence).\n"
    else:
        detail = "- Definitions only (no captures).\n"
    variant = profile.variant
    tag = f"**Variant:** `{variant}`\n" if variant not in (None, "all", "untagged") else ""
    body = (
        f"Contributed via `canair contribute`.\n\n"
        f"**Vehicle:** {model}\n"
        f"{tag}"
        f"**Profile:** `profiles/{profile.name}/`\n\n"
        f"{detail}\n"
        "- [ ] `canair validate all` passes\n"
        "- [ ] Reviewed for PII / VIN / location data\n"
    )
    if logs:
        body += f"\n{logs}\n"
    return body


def update_pr_body(pre: Preflight, number: int, body: str) -> Step:
    """Replace an open upstream PR's body (a re-run's new summary)."""
    assert pre.gh
    return _run([pre.gh, "pr", "edit", str(number), "--repo", UPSTREAM_REPO, "--body", body])


def manual_instructions(profile_name: str, branch: str) -> str:
    """Fallback steps when the automated path can't run (no gh, etc.)."""
    return (
        "  To contribute manually:\n"
        f"    1. Fork https://github.com/{UPSTREAM_REPO} on GitHub and clone your fork.\n"
        f"    2. Copy your profile into the clone at profiles/{profile_name}/.\n"
        f"    3. git checkout -b {branch}\n"
        f"       git add profiles/{profile_name} && git commit -m 'contribute {profile_name}'\n"
        "       git push -u origin HEAD\n"
        "    4. Open a pull request against the repo's main branch.\n"
    )
