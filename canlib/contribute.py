"""Orchestration for ``canair contribute`` — one-command profile pull requests.

Turning a reverse-engineered profile into an upstream contribution normally
means the "fork dance": fork the repo on GitHub, clone it, branch, copy the
profile in, commit, push, open a PR. That is a lot to ask of a non-git-savvy
contributor. This module automates the whole thing via the GitHub CLI (``gh``),
which also handles authentication (a friendly browser/device-flow login).

Crucially, it does **not** matter where the source profile lives — bundled in
the repo, in ``~/.config/canair/profiles/``, or at an arbitrary ``--path``. The
destination is always ``profiles/<name>/`` inside a *fork* of the upstream repo,
so the profile is simply **copied** into a managed fork checkout and git computes
the diff. That single indirection makes the source location irrelevant and
handles both a brand-new profile (an added directory) and edits to an existing
bundled profile (a diff) uniformly.

The heavy lifting (``git``/``gh`` invocations) goes through the module-level
:func:`_run` so tests can drive the flow with a fake runner and no network. The
command layer (``canlib.commands.contribute``) owns the user interaction; this
module stays pure orchestration.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .constants import GITHUB_REPO as UPSTREAM_REPO

UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}.git"
GH_INSTALL_URL = "https://github.com/cli/cli#installation"

# Bundle members copied into a contribution. ``out/`` (generated), ``logs/``,
# and ``references/`` (may hold third-party / licensed material) are excluded;
# captures are opt-in-controlled separately.
_DEFINITION_MEMBERS = (
    "profile.yaml",
    "vehicle_states.yaml",
    "states.yaml",  # legacy name (honoured by Profile.states_file)
    "can_buses.yaml",
    "ecus",
    "signals",
)
_CAPTURE_MEMBER = "captures"
# Never copied even under captures/ (transient write-ahead log for --save).
_CAPTURE_SKIP = ("_",)


@dataclass
class Step:
    """Result of one git/gh invocation, for reporting and JSON output."""

    cmd: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stderr or self.stdout).strip()


@dataclass
class Preflight:
    """Whether the environment can run an automated contribution."""

    gh: str | None
    git: str | None
    authenticated: bool

    @property
    def ready(self) -> bool:
        return bool(self.gh and self.git and self.authenticated)


@dataclass
class ContributionPlan:
    """Everything the command needs to act on, once resolved."""

    profile_name: str
    branch: str
    include_captures: bool
    steps: list[Step] = field(default_factory=list)


def _run(cmd: list[str], cwd: Path | None = None) -> Step:
    """Run a subprocess, capturing output. Never raises on non-zero exit."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    return Step(cmd=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# --- environment ------------------------------------------------------------


def preflight() -> Preflight:
    """Probe for ``gh``/``git`` and whether ``gh`` is authenticated."""
    gh = shutil.which("gh")
    git = shutil.which("git")
    authed = False
    if gh:
        # `gh auth status` exits 0 when at least one host is logged in.
        authed = _run([gh, "auth", "status"]).ok
    return Preflight(gh=gh, git=git, authenticated=authed)


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


# --- workspace (the managed fork clone) -------------------------------------


def workspace_dir() -> Path:
    """Persistent fork-clone location, reused across contributions."""
    from .config import config_dir

    return config_dir() / "contribute" / UPSTREAM_REPO.split("/")[-1]


def ensure_fork_clone(pre: Preflight, workspace: Path) -> list[Step]:
    """Ensure ``workspace`` is a clone of the user's fork, synced to upstream/main.

    First run: ``gh repo fork <upstream> --clone`` into the workspace parent.
    Later runs: reuse the clone, fetch upstream, and hard-reset to upstream/main
    so each contribution starts from a clean, current base. Returns the executed
    steps; a non-``ok`` last step signals failure.
    """
    assert pre.gh and pre.git  # guarded by the caller's readiness check
    steps: list[Step] = []
    workspace.parent.mkdir(parents=True, exist_ok=True)

    if not (workspace / ".git").is_dir():
        # gh forks under the user's account and clones it into ./<repo> in cwd.
        fork = _run(
            [pre.gh, "repo", "fork", UPSTREAM_REPO, "--clone", "--remote"],
            cwd=workspace.parent,
        )
        steps.append(fork)
        if not fork.ok:
            return steps

    steps.append(_ensure_upstream_remote(pre, workspace))
    steps.append(_run([pre.git, "-C", str(workspace), "fetch", "upstream", "--quiet"]))
    if not steps[-1].ok:
        # Fall back to origin if upstream isn't reachable/named as expected.
        steps.append(_run([pre.git, "-C", str(workspace), "fetch", "origin", "--quiet"]))
    return steps


def _ensure_upstream_remote(pre: Preflight, workspace: Path) -> Step:
    """Make sure an ``upstream`` remote points at the canonical repo."""
    assert pre.git
    existing = _run([pre.git, "-C", str(workspace), "remote"])
    remotes = set(existing.stdout.split())
    if "upstream" in remotes:
        return existing
    return _run([pre.git, "-C", str(workspace), "remote", "add", "upstream", UPSTREAM_URL])


def _upstream_ref(workspace: Path, git: str) -> str:
    """The base ref to branch from — prefer upstream/main, else origin/main."""
    for ref in ("upstream/main", "origin/main"):
        if _run([git, "-C", str(workspace), "rev-parse", "--verify", "--quiet", ref]).ok:
            return ref
    return "main"


def start_branch(pre: Preflight, workspace: Path, branch: str) -> Step:
    """Create/reset ``branch`` off the current upstream base."""
    assert pre.git
    base = _upstream_ref(workspace, pre.git)
    return _run([pre.git, "-C", str(workspace), "checkout", "-B", branch, base])


# --- copying the profile ----------------------------------------------------


def copy_profile(profile, workspace: Path, *, include_captures: bool) -> Path:
    """Copy the resolved profile bundle into ``workspace/profiles/<name>/``.

    Each *managed* member (ecus/, signals/, profile.yaml, states, buses, and —
    when ``include_captures`` — captures/) is replaced with the user's copy so
    the git diff reflects the current state. Members the tool does **not** manage
    (``out/`` generated JSON, ``references/`` possibly-third-party material,
    ``logs/``, and captures/ when excluded) are left untouched, so contributing
    into an existing upstream profile never *deletes* its captures/references
    just because this contribution didn't include them. Append-only capture files
    still merge cleanly upstream via the capture merge driver. Returns the
    destination directory.
    """
    dest = workspace / "profiles" / profile.name
    dest.mkdir(parents=True, exist_ok=True)  # overlay — do NOT wipe the whole dir

    members = list(_DEFINITION_MEMBERS)
    if include_captures:
        members.append(_CAPTURE_MEMBER)

    for member in members:
        src = profile.root / member
        if not src.exists():
            continue
        target = dest / member
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target)  # replace this member subtree wholesale
            shutil.copytree(
                src,
                target,
                ignore=shutil.ignore_patterns(*_CAPTURE_SKIP, ".journal", "*.tmp"),
            )
        else:
            shutil.copy2(src, target)
    return dest


def dir_size(path: Path) -> int:
    """Total size in bytes of everything under ``path`` (0 if absent)."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# --- commit / push / PR -----------------------------------------------------


def commit_profile(pre: Preflight, workspace: Path, profile_name: str, message: str) -> Step:
    """Stage the profile dir and commit. A no-op commit is reported as a Step."""
    assert pre.git
    rel = f"profiles/{profile_name}"
    add = _run([pre.git, "-C", str(workspace), "add", "--", rel])
    if not add.ok:
        return add
    return _run([pre.git, "-C", str(workspace), "commit", "-o", rel, "-m", message])


def has_changes(pre: Preflight, workspace: Path, profile_name: str) -> bool:
    """True when the profile dir has any uncommitted change (worth a PR).

    Uses ``git status --porcelain`` so freshly-copied **untracked** files count
    (``git diff`` alone ignores untracked paths).
    """
    assert pre.git
    rel = f"profiles/{profile_name}"
    status = _run([pre.git, "-C", str(workspace), "status", "--porcelain", "--", rel])
    return bool(status.stdout.strip())


def push_branch(pre: Preflight, workspace: Path, branch: str) -> Step:
    assert pre.git
    return _run(
        [pre.git, "-C", str(workspace), "push", "--force-with-lease", "-u", "origin", branch]
    )


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
            "main",
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=workspace,
    )


def fork_head(pre: Preflight, workspace: Path, branch: str) -> str:
    """``owner:branch`` head spec for the PR, resolving the fork owner via gh.

    Falls back to a bare branch name if the owner can't be determined (gh can
    still infer the head from the pushed branch on the fork remote).
    """
    assert pre.gh
    who = _run([pre.gh, "api", "user", "--jq", ".login"])
    owner = who.stdout.strip() if who.ok else ""
    return f"{owner}:{branch}" if owner else branch


def default_message(profile, *, include_captures: bool) -> str:
    """Commit-message subject/body for a profile contribution."""
    model = str(profile.meta.get("car_model") or profile.name)
    scope = "profile + captures" if include_captures else "profile"
    return f"profiles: contribute {profile.name} ({model})\n\nAdd/update {scope} for {model}."


def default_pr_body(profile, *, include_captures: bool) -> str:
    model = str(profile.meta.get("car_model") or profile.name)
    captures_line = (
        "- Includes `captures/` (raw evidence).\n"
        if include_captures
        else "- Definitions only (no captures).\n"
    )
    return (
        f"Contributed via `canair contribute`.\n\n"
        f"**Vehicle:** {model}\n"
        f"**Profile:** `profiles/{profile.name}/`\n\n"
        f"{captures_line}\n"
        "- [ ] `canair validate all` passes\n"
        "- [ ] Reviewed for PII / VIN / location data\n"
    )


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
