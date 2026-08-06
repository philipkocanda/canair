# Releasing canair

Releases are automated with [release-please](https://github.com/googleapis/release-please).
You do not bump versions, write changelog headings, tag, or cut releases by hand —
you **review and merge a release pull request**. The design and its rationale are
recorded in `plans/2026-08-06-release-please.md`.

canair follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** — incompatible CLI/behaviour or profile/capture-schema changes.
- **MINOR** — new commands/features, backwards compatible.
- **PATCH** — backwards-compatible bug fixes.

Which one you get is **derived from commit subjects**, not chosen: `feat:` → minor,
any other type → patch, and `!` (or a `BREAKING CHANGE:` footer) → major. So the
SemVer meanings above are a rule about *how to type your commits* — see the commit
convention in `CONTRIBUTING.md` and `.claude/skills/contributing-code/SKILL.md`.

The version is single-sourced from `pyproject.toml` (`[project].version`) and
surfaced at runtime as `canlib.__version__` (read via `importlib.metadata`).
There is no second copy to keep in sync — release-please updates `pyproject.toml`
and `uv.lock` together in the release PR.

`canair --version` (and `canair status`/`update`, and every recorded capture)
reports the *provenance* version from `canlib/build_info.py`: the same package
version, plus the branch and short commit when canair is running from a git
checkout rather than an installed release (`1.15.0+main.343b244`). Release
comparisons always use the pure `canlib.__version__`, so the suffix never affects
version ordering — and a released install shows no suffix at all.

## Cutting a release

1. **Land work on `main` with conventional commit subjects.** release-please keeps
   a pull request titled `chore(main): release X.Y.Z` open and continuously
   up to date, bumping `pyproject.toml` + `uv.lock` and writing the `CHANGELOG.md`
   section. Nothing to run.

2. **Check the release PR is green.** CI runs on it like any other PR (see
   [Prerequisites](#prerequisites)); the default branch requires the `test` check.

3. **Rewrite the generated changelog section into prose.** This is the one manual
   step, and the important one. release-please emits raw commit subjects; canair's
   changelog is written for the reader — themed, explaining *what changed and why
   it matters*, with no internal scaffolding (no "Stage N", no private branch
   names) and plan docs referenced by name. The rules are in
   `.claude/skills/contributing-code/SKILL.md` → "Cutting a release". Edit
   `CHANGELOG.md` **on the release PR's branch**.

4. **Merge the PR.** release-please then tags `vX.Y.Z` and publishes the GitHub
   Release automatically.

5. **Verify.**

   ```bash
   gh release view vX.Y.Z
   git fetch --tags && git checkout vX.Y.Z
   uv sync && uv run canair --version   # reports X.Y.Z, no +branch.sha suffix
   ```

## Three traps

These are inherent to the tool, not bugs — each one silently degrades a release if
you do not know about it.

- **Curate last.** release-please **force-pushes the release branch** whenever the
  notes change, discarding hand edits to `CHANGELOG.md`. Anything merged to `main`
  after you curate wipes your prose. Curate immediately before merging, and
  re-apply if something lands in between.

- **The GitHub Release body comes from the PR *body*, not `CHANGELOG.md`.**
  release-please re-parses the pull request body to build the release, so
  curating only the file leaves the published Release showing raw commit
  subjects. Either edit the PR body too (carefully — it is parsed), or fix it
  afterwards:

  ```bash
  gh release edit vX.Y.Z --notes-file - <<'EOF'
  <the curated CHANGELOG section>
  EOF
  ```

- **A release PR appearing is not a reason to release.** Any releasable commit
  proposes at least a patch bump, so a docs-only week opens a PR. It is a
  *proposal* — merge on your own schedule. Conversely, if **no** PR appears, every
  commit since the last release was either a hidden type (`chore`, `test`, `ci`,
  `build`, `style`) or touched only excluded paths (`plans/`, `.claude/`).

## Prerequisites

- **`RELEASE_PLEASE_TOKEN`** — a fine-grained PAT scoped to this repository with
  **Contents: write**, **Pull requests: write**, **Issues: write**, stored as a
  repository secret. This is required, not a nicety: resources created with the
  default `GITHUB_TOKEN` do not trigger workflows, so CI would never run on a
  release PR — and since the default branch requires the `test` check, every
  release PR would be unmergeable except by admin bypass. If the secret is
  missing or expired, the Release workflow fails fast with an error naming these
  scopes.

## Configuration

| File | Role |
|---|---|
| `release-please-config.json` | how versions and changelog sections are computed |
| `.release-please-manifest.json` | the last released version (`{".": "X.Y.Z"}`) |
| `.github/workflows/release.yml` | opens/updates the release PR; tags and releases on merge |

Worth knowing before you change any of it:

- **Tags must stay `vX.Y.Z`** (`include-component-in-tag: false`). `canair update`
  and `canlib/update_check.py` both depend on that exact shape — a `canair-v…`
  tag would silently break update checking for every installed copy.
- **`uv.lock` is updated by a targeted TOML splice**, not by re-locking, and that
  updater only *warns* when its jsonpath matches nothing. CI's `uv lock --check`
  gate is what turns such a miss into a red release PR instead of a bad tag. If
  you upgrade release-please and that gate fails, check the jsonpath first.
- **`plans/` and `.claude/` are excluded** so internal scaffolding does not
  propose releases; a commit touching those *and* real code still counts.

## Overrides

- **Force a specific version:** add a `Release-As: X.Y.Z` footer to a commit, or
  set `release-as` in `release-please-config.json` (remove it after the release,
  or it pins every subsequent one).
- **Recover from a bad release PR:** set `last-release-sha` in the config to the
  commit release-please should measure from, then remove it once a good release PR
  has merged.

## Notes

- Tags are `vX.Y.Z` (with the `v` prefix); the `pyproject.toml` version is the
  bare `X.Y.Z`.
- If you build a wheel, `hatchling` reads the version straight from
  `pyproject.toml`, so no extra step is needed.
- Releases are published (not drafts), so the GitHub `releases/latest` endpoint —
  which is what `canair update` polls — resolves to the newest one immediately.
- Everything up to and including **v1.15.0** was released by hand; the checklist
  that described that process is in this file's git history.
