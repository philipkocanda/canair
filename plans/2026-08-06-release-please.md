# Adopt release-please for releases

Status: **IN PROGRESS** — Phase 0 (the manual v1.15.0 seam) is prepared; Phases
1-5 are open work.

## Motivation

Cutting a release is six manual steps (`RELEASING.md`): bump `pyproject.toml`,
re-run `uv lock`, move `CHANGELOG.md`'s `[Unreleased]` section into a dated one,
refresh the compare links, commit, annotated-tag, `gh release create`. Every step
is a place to drift, and two of them already have: the `uv lock` step exists only
as a warning in the skill file (`contributing-code/SKILL.md:572`) because it was
once forgotten, and prior releases have been tagged without their changelog
section (the skill tells you to backfill).

[release-please](https://github.com/googleapis/release-please) replaces all six
with a **Release PR** that it keeps continuously up to date: it bumps the version,
writes the changelog section, and — on merge — tags `vX.Y.Z` and publishes the
GitHub Release that `canair update` already consumes.

What it does **not** replace is the *writing*. This repo's release notes are
prose, deliberately (`SKILL.md:589-601`: "write release notes for the reader, not
the committer", derived from `git log` and never raw commit subjects). That
tension is the central design decision below.

## Decisions (confirmed with the user)

1. **Hybrid changelog.** release-please generates the section from commit
   subjects; the maintainer rewrites it into prose on the release-PR branch before
   merging. Automates the bookkeeping (version, date, compare link, tag, release)
   while keeping the prose bar. The alternatives — accepting generated subjects
   verbatim, or `skip-changelog: true` with a hand-written file — were rejected as
   too low and too little automation respectively.
2. **Strict conventional types, area as scope.** `feat(monitor):`,
   `fix(captures):`, `refactor(captures):`, `docs(skills):`,
   `chore(profiles):`. The current convention is explicitly *not* an enum
   (`SKILL.md:520`: "the prefix is the *area/kind* touched, not a fixed enum"),
   which release-please cannot act on. The area survives as the conventional
   *scope*, so nothing expressive is lost.
3. **Fine-grained PAT** in `secrets.RELEASE_PLEASE_TOKEN`. Release PRs opened with
   the default `GITHUB_TOKEN` do not trigger workflows, so `ci.yml`'s
   `pull_request` job would never run on them; with a PAT you merge a green PR.
4. **A `commit-msg` pre-commit hook is in scope**, not an optional follow-up. A
   non-conventional subject is *silently* dropped from the changelog, and a bare
   subject (`bitfields`, `plan`, `skills and docs` all exist in recent history)
   parses as nothing at all. A local guard is the only thing that makes the
   convention self-enforcing, and it matches the repo's existing "pre-commit
   mirrors the CI gates" philosophy.

## Why a clean manual seam first (Phase 0)

The 24 commits since `v1.14.1` predate the convention. There is **no `feat:`
among them**, so release-please would compute `1.14.2` for work that includes two
clear features (`d66d91b analysis: carry run-length values forward`,
`9d005e0 tui: payload-ordered signals`). Meanwhile `CHANGELOG.md`'s
`[Unreleased]` section was already written for exactly that work.

So v1.15.0 is cut **by hand, the old way**, and release-please is adopted on top
of it. Everything before the seam is manual history; everything after is
automated. This avoids `bootstrap-sha`/`release-as` fudging and a first bump that
would have been wrong.

## Phase 0 — cut v1.15.0 manually (PREPARED)

Done in the working tree, following the existing `RELEASING.md`:

- All CI gates green: `ruff check`, `ruff format --check`, `ty check`,
  `pytest -q` (4806 passed, 1 skipped), `validate all` on all three bundled
  profiles, `make gen-check`, `mkdocs build --strict`.
- **Boy Scout fix:** `make gen-check` was failing before any of this work —
  `docs/profiles/index.md` and `docs/profiles/ioniq-2017.md` were stale (parameter
  count 359→362, research backlog 61→64) from recent profile commits that did not
  regenerate them. CI on `main` was red. Regenerated with
  `scripts/gen_profiles_index.py`.
- `pyproject.toml` → `1.15.0` (minor: new features, no breaking changes).
- `uv lock` re-run; diff verified to be the single `version` line, no dependency
  churn.
- `CHANGELOG.md`: `## [Unreleased]` → `## [1.15.0] - 2026-08-06`; the
  `[Unreleased]: …compare/v1.14.1...HEAD` link reference replaced with
  `[1.15.0]: …compare/v1.14.1...v1.15.0`.
- `uv sync` re-run; `canair --version` confirms `canair 1.15.0`.

**The `[Unreleased]` section is deliberately not re-added** — Phase 3 removes it
permanently (see below), so re-adding an empty one would be immediate churn.

Remaining: commit, `git tag -a v1.15.0`, push, `gh release create v1.15.0`.

After this, the manifest anchor is `1.15.0` and a published, non-draft GitHub
Release exists — which is what release-please reads to find the previous release,
so no bootstrap configuration is needed.

## Phase 1 — switch the commit convention

Lands **before** the automation, so the first automated window is already clean.

- **`.claude/skills/contributing-code/SKILL.md` §"Commit messages" (`:520-531`).**
  Replace the "not a fixed enum" rule with the fixed set
  `feat|fix|perf|refactor|docs|test|chore|ci|build|revert`, the area as a
  parenthesised scope, and `!` / `BREAKING CHANGE:` for majors. Keep every
  existing rule verbatim (body explains the *why*, reference the plan doc, no
  "Stage N" scaffolding in the subject). Add a migration table for the retired
  prefixes:

  | was | becomes |
  |---|---|
  | `tui:` | `feat(monitor):` / `refactor(tui):` |
  | `analysis:` | `feat(analysis):` |
  | `profiles(ioniq-2017):` | `feat(profiles):` / `chore(profiles):` |
  | `skills:` / `skills(x):` | `docs(skills):` |
  | `captures:` / `lock:` / `bix:` | type + that area as scope |
  | `formatting:` | `fix(formatting):` / `refactor(formatting):` |
  | bare subject (`bitfields`, `plan`) | **forbidden** — parses as nothing, silently dropped |

  State plainly which types move the version: only `feat` (minor), `fix`/anything
  else (patch), `!`/`BREAKING CHANGE` (major).
- **`CONTRIBUTING.md` §"Contribute code".** Add a short "Commit messages" block
  linking Conventional Commits and noting that the subject drives both the version
  bump and the changelog.
- **`.pre-commit-config.yaml`.** Add `commit-msg` to
  `default_install_hook_types` and a `conventional-pre-commit` hook pinned by
  `rev`, with the allowed types listed explicitly so the hook and the skill file
  cannot disagree. Update the install instructions in `CONTRIBUTING.md` and the
  comment at the top of the hook config (both currently name only
  `pre-commit`/`pre-push`).

## Phase 2 — release-please configuration

### `release-please-config.json` (new, repo root)

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": {
    ".": {
      "release-type": "python",
      "package-name": "canair",
      "include-component-in-tag": false,
      "changelog-path": "CHANGELOG.md",
      "extra-files": [
        { "type": "toml", "path": "uv.lock",
          "jsonpath": "$.package[?(@.name=='canair')].version" }
      ],
      "changelog-sections": [
        { "type": "feat",     "section": "Added" },
        { "type": "fix",      "section": "Fixed" },
        { "type": "refactor", "section": "Changed" },
        { "type": "perf",     "section": "Performance" },
        { "type": "docs",     "section": "Documentation" },
        { "type": "deps",     "section": "Dependencies" },
        { "type": "revert",   "section": "Reverts" },
        { "type": "chore", "section": "Chores", "hidden": true },
        { "type": "test",  "section": "Tests",  "hidden": true },
        { "type": "ci",    "section": "CI",     "hidden": true },
        { "type": "build", "section": "Build",  "hidden": true },
        { "type": "style", "section": "Styles", "hidden": true }
      ]
    }
  }
}
```

Every non-default earns its place:

- **`include-component-in-tag: false`** — the `python` strategy requires
  `package-name`, which otherwise becomes the tag component and yields
  `canair-v1.16.0`. Tags must stay `vX.Y.Z`: `canlib/update_check.py`
  (`RELEASES_LATEST_URL`, `_parse_version`) and `canair update`'s
  `git checkout <tag>` both depend on that shape. **Non-negotiable.**
- **`extra-files` → `uv.lock`** — the `python` strategy does not touch `uv.lock`
  (`src/strategies/python.ts`), which pins canair's own version. The `toml`
  updater performs a byte-offset splice via `src/util/toml-edit.ts`
  (`replaceTomlValue`), preserving formatting and comments and re-parsing the
  result for validity — safe on a 178 KB generated lock. The "reformats and strips
  comments" caveat in `generic-toml.ts`'s docstring applies to full re-emission,
  not this replacement path. This automates the trap recorded at `SKILL.md:572`.
- **`changelog-sections`** — sections are renamed to Keep a Changelog's vocabulary
  (`Added`/`Fixed`/`Changed`) instead of release-please's defaults
  (`Features`/`Bug Fixes`) so a generated section reads consistently with the
  131 KB of hand-written history above it. `refactor` and `docs` are **un-hidden**
  because this project's changelog genuinely covers both.

### `.release-please-manifest.json` (new, repo root)

```json
{ ".": "1.15.0" }
```

### `.github/workflows/release.yml` (new)

```yaml
name: Release
on:
  push:
    branches: [main]
permissions:
  contents: write
  pull-requests: write
  issues: write        # PR labelling goes through the issues API
concurrency:
  group: release-please
  cancel-in-progress: false
jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@v4
        id: release
        with:
          token: ${{ secrets.RELEASE_PLEASE_TOKEN }}
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

Version-tag pinning (`@v4`) matches the repo's existing style
(`actions/checkout@v7`, `astral-sh/setup-uv@v9.0.0`).

### `ci.yml` — one added gate

Insert after "Set up Python", before "Sync dependencies":

```yaml
      - name: Lockfile is current
        run: uv lock --check
```

A backstop for the `extra-files` splice: `GenericToml.updateContent` only
`logger.warn`s when its jsonpath matches nothing, so a silent miss would ship a
tag whose `uv sync` re-locks. This turns that into a red release PR.

### Repo settings (manual, one-off)

- Fine-grained PAT scoped to `philipkocanda/canair` with **Contents: write**,
  **Pull requests: write**, **Issues: write**; stored as `RELEASE_PLEASE_TOKEN`.
- With a PAT, "Allow GitHub Actions to create and approve pull requests" should
  not be needed (the PAT acts as the user); enable it if PR creation 403s.

## Phase 3 — prepare `CHANGELOG.md` for the generator

`[Unreleased]` is **removed permanently** (Phase 0 already renamed the heading and
dropped its link reference). Two reasons:

1. The open release PR *is* the always-current unreleased view, so the section is
   duplicate bookkeeping.
2. release-please's insertion anchor is
   `versionHeaderRegex = /\n###? v?[0-9[]/` (`src/updaters/changelog.ts`), whose
   `[0-9[]` character class **matches `## [Unreleased]`** — new releases would be
   inserted *above* it, burying it between releases. That regex is **not exposed**
   in `release-please-config.json` (checked against the published schema), so it
   cannot be retargeted. *(If a staging area is ever wanted back: a bracket-free
   `## Unreleased` stops matching, so entries land below it — at the cost of
   hand-moving content every release.)*

Also: release-please writes **inline** compare links
(`## [1.16.0](…/compare/v1.15.0...v1.16.0) (2026-08-10)`), not reference links.
Freeze the existing bottom `[X.Y.Z]: …` block as history and add a short HTML
comment at the seam noting that entries from v1.16.0 on are generated and then
hand-edited in the release PR, so the format change is explained in-file.

## Phase 4 — rewrite `RELEASING.md`

Keep the SemVer definitions at the top (they describe *this* project's
major/minor/patch, including profile/capture-schema breakage). Replace the
checklist with:

1. Land work on `main` with conventional subjects. release-please keeps a
   `chore(main): release X.Y.Z` PR open and current.
2. When ready: confirm the PR is green, then **rewrite the generated
   `CHANGELOG.md` section into prose** on the release branch — themed, no internal
   scaffolding, plan docs referenced by name (the `SKILL.md:589-601` rules,
   unchanged).
3. Merge. release-please tags `vX.Y.Z` and publishes the GitHub Release.
4. Verify: `gh release view vX.Y.Z`, then `uv sync && uv run canair --version` on
   the tag.

Document the three traps explicitly, because each one silently degrades the
result:

- **Edit last.** release-please force-pushes the release branch whenever the notes
  change, discarding hand edits. Any commit merged after you curate wipes them —
  curate immediately before merging, or re-apply.
- **The GitHub Release body comes from the PR *body*, not from `CHANGELOG.md`**
  (`src/manifest.ts:1162-1172` re-parses the PR body to build the release).
  Curating only the file leaves the Release showing raw subjects. Either also edit
  the PR body (carefully — it is parsed) or fix it after merge with
  `gh release edit vX.Y.Z --notes-file -`.
- **Any releasable commit proposes at least a patch bump**
  (`versioning-strategies/default.ts`); only a window of *entirely hidden* types is
  skipped (`strategies/base.ts:331`). Since `docs`/`refactor` are un-hidden, a
  docs-only week will open a release PR. That is a *proposal* — merge on your own
  schedule.

Then update `SKILL.md` §"Cutting a release" (`:561-603`) to point at the new
`RELEASING.md`, keep the notes-writing rules, and **delete the now-obsolete
hand-`uv lock` paragraph** (`:572-577`) — release-please does it.

## Phase 5 — verification

1. **Local dry run before pushing the workflow** — proves the config, the tag
   pattern and the `uv.lock` splice with no side effects:

   ```bash
   npx release-please release-pr --repo-url=philipkocanda/canair \
     --token=$GH_TOKEN --dry-run --config-file=release-please-config.json \
     --manifest-file=.release-please-manifest.json
   ```

   Confirm: tag has no `canair-` prefix; `pyproject.toml`, `uv.lock` and
   `CHANGELOG.md` all appear in the update list; previous release resolves to
   `v1.15.0`.
2. **First real cycle** — land one `fix(...)` and one `feat(...)`; confirm the PR
   proposes a **minor**, CI runs on it, the `uv.lock` diff is the single `version`
   line, and the new section lands directly above `## [1.15.0]`.
3. **Post-merge** — `canair update --check` and `canair status` still resolve the
   new release (they read `/releases/latest` plus `vX.Y.Z` tags; nothing should
   change, but it is the one user-visible integration).

## Optional follow-ups (deliberately not bundled)

- **`scripts/sync_release_notes.py`** plus a workflow step gated on
  `steps.release.outputs.release_created`: extract the merged `CHANGELOG.md`
  section for `${tag_name}` and `gh release edit --notes-file -`. Removes the
  "curate in two places" divergence entirely (~30 lines). Worth doing once the
  manual loop has run a couple of times.
- **PR-title linting** (`amannn/action-semantic-pull-request`) — only useful if
  the repo moves from merge commits (`c0d3d52`, `060789b`) to squash merges, where
  the PR title becomes the commit subject.

## Out of scope

PyPI publishing (canair installs from a git clone by design — `canair update`
checks out tags), multi-package/monorepo layout, per-profile versioning,
prerelease/beta channels, and rewriting historical changelog entries into the
generated format.

## Files touched

| File | Change | Phase |
|---|---|---|
| `pyproject.toml`, `uv.lock` | 1.15.0 — the last manual bump | 0 |
| `CHANGELOG.md` | cut 1.15.0; drop `[Unreleased]` + its link ref | 0 |
| `docs/profiles/index.md`, `docs/profiles/ioniq-2017.md` | regenerated stats (CI was red) | 0 |
| `.claude/skills/contributing-code/SKILL.md` | §commit messages rewritten; §cutting a release retargeted; stale `uv lock` para removed | 1, 4 |
| `CONTRIBUTING.md` | commit-message convention + hook install | 1 |
| `.pre-commit-config.yaml` | `commit-msg` conventional hook | 1 |
| `release-please-config.json` | new | 2 |
| `.release-please-manifest.json` | new | 2 |
| `.github/workflows/release.yml` | new | 2 |
| `.github/workflows/ci.yml` | add `uv lock --check` | 2 |
| `CHANGELOG.md` | seam comment for the format change | 3 |
| `RELEASING.md` | rewritten around the Release PR | 4 |
