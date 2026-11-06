# The contribute pipeline

How `canair contribute` (alias `share`) turns the active profile into an upstream pull request
with no manual fork, clone, branch or push. Read this page before you change the pipeline, a
gate or the managed workspace. Read the `contributing-profiles` skill before you *use* the
command: it holds the PII and security-key rules.

## What it does

The command copies the profile into a **managed fork checkout**
(`~/.config/canair/contribute/canair/`), commits there, pushes, and runs `gh pr create`. It is
**storage-location-agnostic**: it works whether the source profile is repo-bundled, in
`~/.config/canair/profiles/`, or a `--path` bundle, because it copies the profile and lets git
compute the diff.

| Layer | Where | Job |
|---|---|---|
| git and `gh` orchestration | `contribute/` (`git.py`, `workspace.py`, `copy.py`, `checks.py`, `github.py`) | Runs git and `gh`, with an injectable runner so tests need no network. |
| Source clean-up | `contribute/source.py` | Decides which uncommitted source changes the workspace's `HEAD` already holds. |
| Data scrub | `contribute/pii.py`, `contribute/logs.py` | The personal-data scan and the log scrub. |
| CLI and pipeline | `cli/commands/contribute/` (`parser.py`, `pipeline.py`, `stage.py`, `submit.py`, `tidy.py`) | The sequence the contributor sees. |
| Pre-flight policy | `cli/commands/contribute/gates.py` | One function per question, in the order the pipeline asks them. |
| Reporting | `cli/commands/contribute/report.py` | The human and `--json` duality. |

## The sequence

`cli/commands/contribute/pipeline.py::contribute` runs these steps in order. Each one advances
the run or ends it with `Stop`.

1. **Resolve the profile and the branch.** A profile that was adopted before a rename is
   contributed under its new upstream name.
2. **Installed-snapshot guard.** The active profile came from a frozen `site-packages`, `uv tool`
   or `pipx` copy. See [layered profiles](layered-profiles.md).
3. **`validate all`.**
4. **Environment** (git and `gh` are present and authenticated) and **capture size**.
5. **Workspace.** Make the managed checkout ready: the **self-collision guard** (it refuses, and
   `--yes` cannot override it, when the active profile *is* the workspace's own copy,
   `contribute/checks.py::workspace_collision`), the leftovers gate and the hermetic reset.
6. **Stage.** Copy the profile in. Here the **rollback guard** finds out whether the
   contribution would remove committed upstream definition lines, which signals a stale source
   (`checks.py::definition_rollback`). `--diff` stops after this step.
7. **Privacy gate** (`gates.privacy`, scanning with `contribute/pii.py`). The VIN-shape test is
   shared with `canair search` (one definition, in `core/textscan.py`).
8. **Captures gate** (`gates.captures`).
9. **Submit.** Commit, push, `gh pr create`, then the source clean-up (`tidy.py`).

Every gate has one contract: it lets the run continue, or it reports the condition and raises
`Stop`. A consent gate goes through `Reporter.gate`, so `--json` refuses without `--yes` instead
of it waiting on a prompt that no machine can answer.

## Rules a change must keep

- **The managed workspace is made hermetic before the copy.** `workspace.reset_workspace` runs
  `reset --hard` to the upstream base and `clean -fd -- profiles/`. `checkout -B` alone keeps an
  uncommitted file that an earlier `--diff` left behind, and `commit_profile` stages the whole
  profile directory. That is how eight stale capture files once rode along in a PR.
- **Leftovers are cleared before branching.** An earlier run's uncommitted copy makes
  `checkout -B` refuse when the old branch and the base disagree on a path. `gates.leftovers`
  asks (`--yes` passes), copies the files to `workspace.backup_dir()` (outside the clone), and
  `discard_local_changes` resets to `HEAD`, which keeps an earlier `--dry-run` commit.
- **Every run discards its own uncommitted copy on the way out** (`stage.discard_staged`, in a
  `finally`). A `--diff`, a declined gate, or `--json` without `--yes` then never leaves a copy
  for the next run. The leftovers gate remains for crashes.
- **A user's own `--repo-dir` is never reset.** It is warned about (`_own_checkout_is_clean`),
  and the reset is gated on `is_managed_workspace`.
- **A re-run updates the open PR.** The branch name is date-based, so `github.find_open_pr`
  looks it up after the push and reports `updated PR #N` as success. `gh pr create` would error.
- **After the PR opens (or when the profile already matches upstream), the source checkout is
  offered a clean-up.** When the profile sits at `profiles/<name>` of a git checkout, its
  uncommitted changes that the workspace's `HEAD` holds exactly are backed up and restored to
  `HEAD` on request (`--discard-local`, `--keep-local`, else asked; kept under `--yes` and
  `--json`). Pulling the merged PR then does not conflict. "Holds exactly" is
  `contribute/source.py`: blob identity through the clone's filters, or a capture log that the
  overlay would not change. Anything else is left alone.

## Captures are overlaid, not replaced

`captures/merge.py::overlay` is append-only. A source that is behind upstream never proposes to
delete upstream sessions, and a capture log that the source adds nothing to stays byte-identical.
A source session that shares an upstream session's **anchor** but not its content *replaces* it
in place (see [layered profiles](layered-profiles.md)). `--diff` lists every replacement, and
`gates.captures` gates it, because a stale source looks identical. That gate also warns about
unsplit multi-DID replies that the contribution adds.

Flags: `--diff` previews, `--dry-run` stops before pushing, `--yes` skips the prompts,
`--no-captures` ships definitions only.
