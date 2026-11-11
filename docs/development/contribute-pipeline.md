# The contribute pipeline

How `canair contribute` (alias `share`) turns the active profile into an upstream pull request
(PR) with no manual fork, clone, branch or push. Read this page before you change the pipeline, a
gate or the managed workspace. Before you *use* the command, read the `contributing-profiles`
skill. It holds the rules for personally identifiable information (PII) and security keys.

## What it does

The command copies the profile into a **managed fork checkout**
(`~/.config/canair/contribute/canair/`), commits there, pushes, and runs `gh pr create`. It does
not depend on where the source profile is stored. It works when the source profile is bundled in
the repo, in `~/.config/canair/profiles/`, or in a `--path` bundle. The command copies the profile
and lets git compute the diff.

| Layer | Where | Job |
|---|---|---|
| git and `gh` orchestration | `contribute/` (`git.py`, `workspace.py`, `copy.py`, `checks.py`, `github.py`) | Runs git and `gh`, with an injectable runner so tests need no network. |
| Source clean-up | `contribute/source.py` | Decides which uncommitted source changes the `HEAD` of the workspace already holds. |
| Data scrub | `contribute/pii.py`, `contribute/logs.py` | The personal-data scan and the log scrub. |
| CLI and pipeline | `cli/commands/contribute/` (`parser.py`, `pipeline.py`, `stage.py`, `submit.py`, `tidy.py`) | The sequence the contributor sees. |
| Pre-flight policy | `cli/commands/contribute/gates.py` | One function per question, in the order the pipeline asks them. |
| Reporting | `cli/commands/contribute/report.py` | The human output and the `--json` output. |

## The sequence

`cli/commands/contribute/pipeline.py::contribute` runs these steps in order. Each step advances
the run or ends it with `Stop`.

1. **Resolve the profile and the branch.** canair contributes a profile that was adopted before a
   rename under its new upstream name.
2. **Installed-snapshot guard.** This guard catches an active profile that came from a frozen
   `site-packages`, `uv tool` or `pipx` copy. See [layered profiles](layered-profiles.md).
3. **`validate all`.**
4. **Environment** (git and `gh` are present and authenticated) and **capture size**.
5. **Workspace.** Make the managed checkout ready. This step has three parts:

    - The **self-collision guard** refuses when the active profile *is* the copy inside the
      workspace (`contribute/checks.py::workspace_collision`). `--yes` cannot override it.
    - The leftovers gate.
    - The hermetic reset.

6. **Stage.** Copy the profile in. Here the **rollback guard** finds out whether the contribution
   would remove committed upstream definition lines. Such a removal signals a stale source
   (`checks.py::definition_rollback`). `--diff` stops after this step.
7. **Privacy gate** (`gates.privacy`, scanning with `contribute/pii.py`). The test for the shape
   of a vehicle identification number (VIN) is shared with `canair search` (one definition, in
   `core/textscan.py`).
8. **Captures gate** (`gates.captures`).
9. **Submit.** Commit, push, `gh pr create`, then the source clean-up (`tidy.py`).

Every gate has one contract. It lets the run continue, or it reports the condition and raises
`Stop`. A consent gate goes through `Reporter.gate`, so `--json` refuses without `--yes` instead of
waiting on a prompt that no machine can answer.

## Rules a change must keep

- **canair makes the managed workspace hermetic before the copy.** `workspace.reset_workspace`
  runs `reset --hard` to the upstream base and `clean -fd -- profiles/`. `checkout -B` alone keeps
  an uncommitted file that an earlier `--diff` left behind, and `commit_profile` stages the whole
  profile directory. That is how eight stale capture files once ended up in a PR.
- **canair clears leftovers before branching.** An uncommitted copy from an earlier run makes
  `checkout -B` refuse when the old branch and the base disagree on a path. `gates.leftovers` asks
  (`--yes` passes) and copies the files to `workspace.backup_dir()` (outside the clone).
  `discard_local_changes` then resets to `HEAD`, which keeps an earlier `--dry-run` commit.
- **Every run discards its own uncommitted copy on the way out** (`stage.discard_staged`, in a
  `finally`). A `--diff`, a declined gate, or `--json` without `--yes` then never leaves a copy for
  the next run. The leftovers gate remains for crashes.
- **canair never resets a `--repo-dir` that the user supplies.** It warns about that directory
  (`_own_checkout_is_clean`), and the reset is gated on `is_managed_workspace`.
- **A re-run updates the open PR.** The branch name is date-based, so `github.find_open_pr` looks
  it up after the push and reports `updated PR #N` as success. `gh pr create` would error.
- **After the PR opens (or when the profile already matches upstream), canair offers to clean up
  the source checkout.** The offer applies when the profile sits at `profiles/<name>` of a git
  checkout.

    - canair backs up the uncommitted changes that the `HEAD` of the workspace holds exactly. It
      restores those files to `HEAD` on request. The request comes from `--discard-local` or
      `--keep-local`, or canair asks. Under `--yes` and `--json`, canair keeps the changes.
    - Pulling the merged PR then does not conflict.
    - "Holds exactly" is defined in `contribute/source.py`: blob identity through the filters of
      the clone, or a capture log that the overlay would not change.
    - canair leaves anything else alone.

## Captures are overlaid, not replaced

`captures/merge.py::overlay` is append-only. A source that is behind upstream never proposes to
delete upstream sessions. A capture log that the source adds nothing to stays byte-identical.

A source session that shares the **anchor** of an upstream session but not its content *replaces*
it in place (see [layered profiles](layered-profiles.md)). `--diff` lists every replacement, and
`gates.captures` gates it, because a stale source looks identical. That gate also warns about
unsplit multi-DID (data identifier) replies that the contribution adds.

Flags:

- `--diff` previews.
- `--dry-run` stops before pushing.
- `--yes` skips the prompts.
- `--no-captures` ships definitions only.
