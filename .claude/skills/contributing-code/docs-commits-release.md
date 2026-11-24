# Docs, commits and releases

Keeping docs/README current, commit messages, concurrent-agent commits, cutting a release.

## Docs, changelog and screenshots

The docs-and-README rule (user-facing change means a docs change, the README/`docs/` split,
generated `docs/reference/cli/`, link checks) is in `AGENTS.md` → Keep docs & README current. Three
things it does not cover:

- **`docs/development/`** (not `contributing/`) is where an *engineering* workflow change belongs.
- **`CHANGELOG.md` — do not edit it.** It's release-please output. The changelog equivalent of
  keeping docs current is **writing a good commit subject**; the only manual editing happens on the
  release PR.
- **Screenshots are generated, not hand-made** from `docs/screenshots/shots.yaml` by
  `scripts/gen/gen_screenshots.py`, against the read-only bundled profile (device-free, PII-free).
  If your change alters a screenshotted command's output, re-render (`make screenshots`) and
  commit the asset; adding a shot means a `shots.yaml` entry. CI and the pre-push hook run
  `--check` (asset presence + command validity), so a renamed command/flag fails until you
  regenerate. Never screenshot views that surface free-text capture notes/labels.

## Commit messages

**canair uses [Conventional Commits](https://www.conventionalcommits.org/)** — `release-please`
parses the subject to derive the version bump and changelog section, so the subject's shape is fixed
machine-readable input (read `git log --oneline` for the house style of the *body*).

- **`type(scope): lower-case summary`.** The type enum and the scope vocabulary are shared with the
  `commit-msg` pre-commit hook and `release-please-config.json`'s `changelog-sections` — read those
  (and `git log` for existing scopes) rather than coining a synonym, and change all three together.
  Scope is optional; omit it for repo-wide changes.
- **Only three things move the version:** `feat` → minor, any other allowed type → patch, `!` (or a
  `BREAKING CHANGE:` footer) → major. Use `!` for an incompatible CLI/behaviour change or a
  profile/capture-schema break, matching `RELEASING.md`.
- **A malformed subject fails silently** — an unrecognized type is dropped from the changelog
  without complaint and a bare subject parses as nothing; the hook rejects both. Never copy the
  old free-form prefixes (`tui:`, `analysis:`, bare `plan`) from `git log`: use a type plus the
  area as the scope.
- **Fixing something that never shipped keeps the *original* commit's type** (`ci:` corrects `ci:`),
  so the entry stays out of the release notes. Reserve `fix:` for something a released version did
  wrong.
- **The body explains the *why* and the shape of the change** — wrapped prose and bullets, not a
  file-by-file list (the diff already has that). It isn't parsed, so it's prose for humans.
  **Reference the plan doc** when the change implements one, and keep internal scaffolding (Stage N,
  phase numbers) out of the subject.
- **Write the subject for the changelog reader** — it's the first draft of a release note; a vague
  one turns the maintainer's rewrite into a re-investigation.
- **Commit only when asked, and only intended files.** Follow the git rules in `AGENTS.md`: inspect
  status/diff/log first, stage deliberately (a pre-existing partial index is a smell — reconcile
  it), never commit secrets/PII, don't push/tag/amend unless asked.

### Committing safely when other agents may be working concurrently

The index and the stash are **global shared state**, so one agent's staging races another's and a
broad `git add` can commit files another agent is mid-edit on.

- **Never `git add -A`, `git add .`, or `git commit -a`** — always scope to explicit pathspecs you
  touched.
- **Prefer `git commit -o -- <paths>`** (`--only`): it builds the commit from HEAD's tree plus
  exactly those paths via a temporary index, ignoring whatever else is staged. (It still reads those
  paths from the working tree, so scoping to files you own matters too.)
- **`git stash` is not a fix** — it's more global shared state; scope your commit instead.

## Cutting a release

Releases are automated by release-please and `RELEASING.md` is authoritative: follow it. Never bump
the version, tag or create a release by hand. A human rewrites the changelog prose on the release
PR's branch immediately before merging, as `RELEASING.md` describes.
