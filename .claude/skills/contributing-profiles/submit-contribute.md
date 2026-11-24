# Submitting with canair contribute

The `canair contribute` PR flow: what it does for you, and what stays your job.

## Submit — `canair contribute` (alias `canair share`)

`canair contribute` opens the pull request for you. It works wherever the profile is stored.
Flags are in `canair contribute --help` and `docs/reference/cli/contribute.md`. The gates, the
managed checkout, the captures overlay and re-runs are in `docs/development/contribute-pipeline.md`.
The command:

- Runs `canair validate all` and refuses a broken profile.
- Runs a PII pre-flight and needs your confirmation. It is a heuristic net: you are the
  backstop (see `pii-location.md`).
- Runs staleness and rollback guards and needs your confirmation past each.
- Pushes to your fork and opens the PR through the GitHub CLI (`gh`). A re-run updates the open PR.

`--diff` previews the diff that would be submitted, then stops. `--dry-run` prepares the branch and
commit locally, with no push and no PR.

**Prefer doing it by hand?** A normal PR putting the bundle under `profiles/<your-car>/` works just
as well. You still owe the same validation and PII/security scrub.

> **When submitting a *specific* profile, always pass `--profile NAME`** (and on any authoring
> command that writes into a profile). Without it, writes and PRs target whatever
> `default_profile` resolves to, which may not be the car you mean.
