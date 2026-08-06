# Profile write targets & contribution-workspace hygiene

Status: **NOT STARTED** — investigated and designed 2026-08-05; every claim below
is backed by evidence gathered from this machine and from the second machine
(`ssh agent`). Phase A is approved for implementation; Phase B is approved as a
design to write up here and implement after a review round.

Direct follow-up to `plans/2026-08-05-contribute-workspace-self-collision.md`
(the capture-overlay + self-collision fixes, landed in `6393566`/`85b4985`). That
plan's fixes work — this one covers everything the follow-up investigation
uncovered *around* them.

## How this came about

After the overlay fix landed, a `canair contribute --diff` on the second machine
still produced a 12-file, 44k-line diff (committed as `debug-contribute.diff`,
`ce19ced`). Investigating that diff produced three findings, only the first of
which was expected:

1. **The fix simply wasn't there.** `ce19ced`'s parent is `b7b6ac4`, and
   `git merge-base --is-ancestor 6393566 ce19ced` → false. The fix had never been
   pushed, so the second machine could not have had it. Once it did,
   **PR #7 went from 12 files / +16,776 −14,549 to 2 files / +2,227 −0** — exactly
   the intended outcome, and the fix is now validated in the real world. PR #7 is
   closed and `origin/main` carries `2026-08-03.json` + `2026-08-05.json`.
2. **The managed workspace is not hermetic** — a second, independent bug that the
   user had to work around by hand (§A1).
3. **The captures being contributed had been recorded into the `uv tool`
   install snapshot**, not into any checkout — the root cause of the whole
   episode, and a data-loss hazard (§A2).

## Evidence

### The workspace retains state between runs (§A1)

The managed clone's reflog on the second machine:

```
b7b6ac4 HEAD@{2026-08-05 10:32:51}: checkout: moving from contribute/… to contribute/…   ← the --diff run
b7b6ac4 HEAD@{2026-08-05 10:46:28}: reset: moving to HEAD                                ← MANUAL cleanup
3c9ad23 HEAD@{2026-08-05 10:46:42}: branch: Reset to origin/main                         ← runs that then worked
c7cb5ec HEAD@{2026-08-05 10:48:03}: commit: profiles: contribute ioniq-2017 …
```

Before that manual `reset`, `git status --porcelain` in the workspace showed
**10 `M` + 2 `A`** entries, and `git diff --numstat` matched `debug-contribute.diff`
line-for-line (`71/71`, `431/431`, …, `1137/0`, `1090/0`).

Cause: `--diff` deliberately never commits, so it always leaves the copied profile
behind as uncommitted modifications (plus `--intent-to-add` entries from
`diff_profile`) in the **persistent** clone. `start_branch`'s
`git checkout -B <branch> <base>` does *not* clean those: git only overwrites paths
that differ between the old HEAD and the new base, and carries local modifications
to the rest. Concretely, `git diff --stat b7b6ac4..3c9ad23 -- profiles/ioniq-2017/captures`
touched only **2 of the 10** polluted files, so **8 would have survived** into the
next run's diff — and, had it not been `--diff`, into the commit and the PR.

So the pollution is silent *and* nondeterministic (it depends on what upstream
happened to touch), and the alternative failure mode is a hard `checkout -B` abort
("local changes would be overwritten") reported as "could not create branch".

### The captures lived only in the install snapshot (§A2)

```
~/.local/share/uv/tools/canair/lib/python3.12/site-packages/profiles/ioniq-2017/captures/
    2026-08-03.json   1 session, 186 captures   "IGPM BCM SKM"  [SLEEP]
    2026-08-05.json   2 sessions, 176 captures  "BCM IGPM AAF" / "BCM IGPM"
```

- `~/projects/canair/profiles/ioniq-2017/captures/` does **not** contain them, and
  that repo's `git status` is clean — so a bare `canair … --save` wrote them into
  the frozen `site-packages` copy.
- `diff -rq snapshot repo` over the whole profile shows *only* those two files, so
  nothing else had diverged.
- The workspace's copies are byte-identical to the snapshot's, which is how the
  later `uv run` `--diff` (reading the *repo* profile, which lacks them) still
  showed them as additions: they were leftovers from an earlier **bare**
  `canair contribute` run.
- `uv tool install --reinstall` / `canair update` replaces `site-packages`, so
  **those captures would have been destroyed** — they survived only because the
  tool was never reinstalled (tool and clone both at 1.14.0). *(Verify the wipe
  behaviour when implementing §A2f.)*

canair already *detects* this situation — `contribute.installed_snapshot_kind`, used
by the contribute gate — but says nothing at the moment that matters, the **write**.

### Why an installed user ends up there

- canair **isn't on PyPI** (`docs/getting-started/install.md`), so every installed
  user installed from a clone (`git clone` + `uv tool install .`) — the clone is
  always present and canair can already locate it (uv receipt / `canair update`'s
  clone finder).
- The **first-run chooser** (`canlib/first_run.py`) lists discovered profiles —
  including the snapshot's bundled ones — and records the pick as
  `default_profile`, with no hint that the target is frozen when the running copy
  is the snapshot. It walks the user straight into the trap.
- The remedy that already exists is buried: `docs/concepts/profiles.md` (§"During
  development") mentions `canair config set profiles_dir <clone>/profiles` in its
  last paragraph. The second machine's config even had a commented-out
  `# profiles_dir: ~/vehicles`.

### Three homes, one of them a trap

| Where | Writable | Survives reinstall | Contributable | Good for |
|---|---|---|---|---|
| `~/.config/canair/profiles/<name>/` | yes | yes | yes (`contribute` is storage-location-agnostic) | your own car |
| clone's `profiles/<name>/` via `config set profiles_dir` | yes | yes | yes (git-tracked) | working on a **bundled** profile |
| `site-packages/profiles/<name>/` (bare `canair`'s default) | writes "succeed" | **no — wiped on reinstall** | only from the snapshot | nothing |

### Adjacent defects found while tracing

- **`canair pids`/`signals`/`states`/`groups` print only the bare filename** on
  success (`✓ BMS 2101 SOC  (bms.yaml)`), violating the policy
  `captures.saved_banner` documents for saves ("always the full path… a save is
  worthless if the user has to guess which profile's captures/ it went into"). Same
  failure class as this whole episode, and as the historical `WHL_SPD11`-landed-in-
  `ioniq-5-2022` mistake. ~20 print sites, no shared reporter.
- **Capture entries carry positional locators.** `load_all_captures`
  (`commands/captures/query.py`) sets `"file": fpath.name` plus
  `_session_idx`/`_capture_idx`, and the mutation paths rebuild the target as
  `cdir / e["file"]` (`commands/captures/delete.py`, `.../backfill.py`,
  `.../set_state.py`). Fine for one directory; ambiguous and index-invalidating the
  moment two layers are merged (§B).
- **`canair contribute` fails when the PR already exists.** The 10:48 run pushed
  successfully, then `gh pr create` could not create a second PR for
  `contribute/ioniq-2017-20260805`, so the run exited `_FAILED` ("you can open the
  PR manually"). The branch name is date-based, so *every* same-day re-run hits it.
- **Four parallel `_safe_write` implementations** (`ecus_edit`, `signals_edit`,
  `states_edit`, `groups_edit`) each do write → re-parse → validate → revert. Not
  in scope here; noted as a consolidation candidate.

## Decisions taken (2026-08-05)

| Question | Decision |
|---|---|
| Scope | All of §A1–§A4 |
| Snapshot warning reach | **Every profile write**, not just `--save` |
| `canair profile adopt` | **Yes**, add it |
| `canair update` reinstall guard | **Warn + confirm** (not a hard refusal) |
| Definition-edit output | **Switch to full paths** (accepting the visible output change) |
| Sequencing | **Phase A now**; Phase B written up here, implemented after review |
| Overlay identity (§B) | **Same name + `extends:`** — layer instead of shadow |
| Base-session edits (§B) | **Refuse**, pointing at `canair profile adopt` |

---

# Phase A — implement now

## A1. Make the contribution workspace hermetic

Each run must start from a pristine base, so a previous `--diff`/aborted/crashed
run cannot leak content into the next diff or commit.

1. `canlib/contribute.py`: add `is_managed_workspace(path) -> bool` (resolved-path
   comparison against `workspace_dir()`), and a discard-local mode on the branch
   step — after `git checkout -B <branch> <base>`, run `git reset --hard <base>`
   then `git clean -fd -- profiles/`. Shape it either as
   `start_branch(..., *, discard_local: bool)` or a separate `reset_workspace()`
   called alongside; the command layer decides via `is_managed_workspace`.
   Safe *because* it is canair's own throwaway clone, and it runs **before**
   `copy_profile`, so nothing of the contribution exists yet.
2. **Never** reset a user-supplied `--repo-dir`. Instead add a gate:
   `_contribute_gates.workspace(...)` takes `pre` (already resolved by the
   environment gate, which runs first) and warns + confirms when
   `git status --porcelain -- profiles/<name>` is non-empty in a non-managed
   workspace — today those uncommitted edits are silently swept into the
   contribution by `commit_profile`'s `git add -- profiles/<name>`.
3. Tests:
   - a dirty managed workspace holding a stale re-sorted capture file → after the
     run the file matches upstream and the diff is clean (regression for exactly
     what `debug-contribute.diff` showed);
   - **pin the motivating behaviour**: `checkout -B` alone does *not* discard
     modifications to paths unchanged between the old HEAD and the new base;
   - a dirty `--repo-dir` warns/confirms and its working tree is **not** reset.
4. Docs: `CHANGELOG.md` (Fixed), the `canair contribute` paragraph in `AGENTS.md`
   (each run starts from a pristine base for the managed clone; a dirty
   `--repo-dir` warns).

## A2. Steer every profile write away from the install snapshot

### A2a. Shared detection (`canlib/install_context.py`)

Move `installed_snapshot_kind` out of `canlib/contribute.py` — it is install
knowledge, not contribute orchestration, and `install_context` already owns
`running_origin`/`bundled_profiles_are_snapshot`. Update the one caller
(`commands/_contribute_gates.py`) and the `AGENTS.md` reference to it.

Add a **pure** `snapshot_write_note(path) -> str | None` returning the warning
text when `path` resolves inside an install snapshot. It must name the remedy it
can *verify*:

- a locatable clone → the exact `canair config set profiles_dir <clone>/profiles`;
- otherwise → `canair profile create …` / `canair profile adopt <name>`.

Wording: not a scolding — state where the data landed, that it is outside any
checkout and is lost on reinstall, then the one command that fixes it.

### A2b. `canair profile adopt NAME`

Copy a bundled profile into `~/.config/canair/profiles/<name>` so it shadows the
bundled one and becomes writable. Flags `--set-default`, `--force`. Prints the
destination and the caveat that an adopted copy stops tracking upstream (so a
later contribution can trip the rollback guard — prefer `profiles_dir` when the
intent is to contribute). This is also the remedy A2a/A2e point at and the escape
hatch for §B's refused base-session edits.

### A2c. Capture writes

`captures.saved_banner` already exists precisely to say *where* a save landed and
every save path funnels through it — append the note there. One change covers
`--save`, monitor saves (including the deferred post-TUI replay), journal
reconcile and `canair import uds`.

### A2d. Definition writes + the locator prerequisite

- Introduce the **missing shared edit-confirmation helper** used by
  `commands/pids.py`, `signals.py`, `states.py`, `groups.py` (~20 sites today):
  prints the **full path** (aligning with the capture-save policy) plus the
  snapshot note.
- Fold in the **capture-entry locator fix**: `load_all_captures` keeps `file` as
  the display name but also carries the owning file's full path, and the three
  mutation sites (`commands/captures/delete.py`, `.../backfill.py`,
  `.../set_state.py`) use it instead of `cdir / e["file"]`. A latent bug today and
  a hard prerequisite for §B.
- This changes user-visible output → regenerate `docs/reference/cli/` and any
  affected screenshots.

### A2e. First-run chooser

`canlib/first_run.py`: when the running copy is the snapshot **and** the chosen
profile is a bundled one, say so and offer the writable options (adopt / set
`profiles_dir`) instead of silently recording `default_profile` as a doomed
location.

### A2f. Reinstall guard in `canair update`

Before `uv tool install … --reinstall`, detect profile data that exists only in
the snapshot (capture files absent from the clone, or definitions differing) —
list it, warn, and confirm (`--yes` bypasses). This is the actual moment of data
loss.

### A2 docs

`docs/concepts/profiles.md` — promote the `profiles_dir` pathway from a closing
footnote to *the* answer for installed users, with the three-homes table;
`docs/getting-started/install.md`; `docs/bring-your-own-car/01-create-profile.md`;
`AGENTS.md`; `CHANGELOG.md`.

## A3. `contribute`: handle an already-open PR

Add `find_open_pr(pre, branch)` (`gh pr list --repo … --head <branch> --state open
--json number,url`; use the plain branch name, not `pr_head`'s `owner:branch`).
After a successful push, if a PR already exists, report
`updated PR #N <url>` as **success** instead of failing on `gh pr create`. Test
with the fake runner (asserting no `pr create` call and a success exit).

## A4. Housekeeping

- Delete `debug-contribute.diff` (1.6 MB, tracked at the repo root — it has served
  its purpose and is fully summarised above).
- On the second machine: `git pull` (origin/main already carries the two capture
  files, so the snapshot is no longer their only home) — **after** which a
  reinstall/`canair update` is safe.

## Phase A sequencing & gates

Commits: (A1 + A3) contribute fixes → (A2) snapshot pathway → (A4) housekeeping.
Each must pass `uv run pytest -q`, `ruff check`/`format --check`, `ty check`,
`canair validate all`, `scripts/gen_cli_reference.py --check`,
`scripts/gen_screenshots.py --check`, with `CHANGELOG.md` updated for every
user-facing change.

---

# Phase B — layered profile: bundled base + user captures overlay

The hybrid: **keep the embedded profile as a read-only base and diff against it,
but write captures into the user's folder.** Standard app-bundle-vs-app-data split
— `canair update` keeps refreshing the car definitions while recordings live in
`~/.config/canair/`, and nobody needs to know what a git checkout is.

## Why the seams already fit

- **One reader.** `load_all_captures(captures_dir)`
  (`commands/captures/query.py`) is the single loader every analysis command
  funnels through (`decode`, `correlate`, `align`, `ecu`, `captures`, `keepmode`,
  `capture_dates`, `step_model`). One function becomes layer-aware.
- **One write target.** `save_session` / `save_session_journaled` default to
  `active().captures_dir`.
- **`Profile` is a frozen dataclass of `name` + `root`** with members as derived
  properties, so an optional `overlay` field is backward-compatible.
- **`contribute` is nearly free.** `_overlay_captures` + `overlay_documents`
  already union per-date capture logs onto the upstream copy; since the bundled
  base *is* upstream, overlaying just the user layer produces exactly the right
  PR — the machinery landed in `6393566`.

## Design

1. **Locators first** *(delivered by §A2d)* — capture entries and the edit paths
   carry the owning file's full path. Without this, merging two layers makes
   `file` ambiguous and `_session_idx` (positional within the *merged* list)
   silently wrong.
2. **`Profile.overlay: Path | None`** — `captures_dir` becomes the **write** layer
   (overlay when layered), plus a new `capture_layers` → `[base, overlay]` for
   reads.
3. **Layer-aware `load_all_captures`** — iterate the layers and merge per date with
   the existing `overlay_documents` union (base order first, user additions
   appended; content-key dedup handles a file present in both layers).
4. **Base sessions are read-only.** `captures uds --delete`, capture/session notes,
   `--backfill-states` and `--set-state` on a base-layer session are **refused**
   with a message naming the file and pointing at `canair profile adopt <name>`.
   Rejected alternatives: tombstones (complex, and the reader must apply them) and
   copy-on-write (needs synthetic session ids — today session identity *is* the
   content, deliberately, which is what makes both the git merge driver and the
   overlay correct; see `canlib/captures_merge.py`'s docstring).
5. **Identity: same name + `extends:`.** The overlay lives at
   `~/.config/canair/profiles/<name>/` with `profile.yaml: {extends: <name>}`, and
   discovery **layers** instead of shadowing when a same-named user profile
   declares `extends` to the profile it shadows. So `--profile ioniq-2017`
   transparently includes your captures. `extends:` is deliberately the key
   `plans/2026-07-30-profile-variant-inheritance.md` reserves — that plan notes
   `validate_meta` tolerates unknown top-level keys, so it already parses
   harmlessly. New command surface: `canair profile overlay NAME` scaffolds the
   layer (marker + empty `captures/`).
6. **Per-call-site decisions** for the readers that don't go through
   `load_all_captures`: `commands/validate/captures.py` (validate both layers),
   `pii.py` (scan the overlay — that's what gets contributed), `blind.py`,
   `commands/coverage.py`, `capture_migrate.py` and `capture_field_migrate.py`
   (migrate only the writable layer), `commands/captures/merge_driver.py`
   (unchanged — it operates on git-supplied paths).
7. **`contribute`** points its capture overlay at the user layer.

## Explicitly out of scope

**Definitions do not overlay in this slice.** Merging `ecus/` is per-PID /
per-param merge — precisely the granularity and *write-target* questions that
`plans/2026-07-30-profile-variant-inheritance.md` states are "blocked on a
decision before any code". A definition edit against a layered profile is refused
with a pointer to `profile adopt` / `profiles_dir`. When definitions do get
layered, it must be through that plan's `extends:` mechanism — not a second one
invented here. Cross-link both plans when this lands.

## Relationship to Phase A

Phase A is not throwaway work: the A2 warnings remain the correct fallback for
anyone who has *not* configured an overlay, `profile adopt` is Phase B's escape
hatch for refused base edits, and A2d's locator fix is Phase B's prerequisite.
