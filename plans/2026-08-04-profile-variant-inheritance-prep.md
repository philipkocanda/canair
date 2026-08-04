# Profile-path consolidation — groundwork for variant inheritance

Status: **DONE** (2026-08-04) — all nine commits landed (`d70592e`..`c959682`).
Scope was **Tier 1 + Tier 2**, with three decisions taken up front:

- the `canair contribute` `groups.yaml` omission was fixed **standalone, first**
  (commit 1), ahead of any refactor;
- **`Profile.logs_dir` was deleted** (commit 2) — it had zero consumers;
- prep stops short of any inheritance mechanism (see **Out of scope**).

**What actually shipped**, beyond the plan as written:

- The `groups.yaml` omission was in **two** member lists, not one — the
  stale-source rollback guard (`_ROLLBACK_MEMBERS`) was missing it too, so a
  contribution that *deleted* upstream groups raised no warning. Both fixed;
  after commit 3 they are the same derived tuple and cannot disagree again.
- Commit 3 found a **leak in the blind-rediscovery strip**: it rebuilt member
  paths as literals, so a profile using the legacy `states.yaml` name had its
  `when:` predicates copied into the sandbox unstripped. Now resolved through
  `Profile.states_file`.
- Commit 4 uncovered a **dangerous test setup**: two promote tests redirected
  their writes by monkeypatching a private path resolver, so moving that seam
  sent a real guarded write into the *bundled* profile — the run appended
  `AAF_SPEED` to `profiles/ioniq-2017/ecus/aaf.yaml` (reverted). They now
  activate a throwaway profile, so the write target cannot escape to `profiles/`
  even if the seam moves again.
- Commit 6's staleness was **confirmed reproducible before fixing** (the plan
  flagged it as unproven): a capture from profile B decoded under profile A's
  parameter names, and the logger mapped a tx_id to the other vehicle's ECU name.
  Both are now regression-tested.
- `canair profile show` gained a `dtc_log:` line (a registry member it had never
  listed) and `set_active` now drops derived caches when the profile changes.

Two pre-existing golden-test failures (`investigate-bits`,
`decode-discriminate-bytes`) were present at `ec32490` before this work and are
untouched by it — verified in a separate worktree at that commit. They belong to
concurrent work in the analysis code.

This was the preparation pass for
`plans/2026-07-30-profile-variant-inheritance.md` (a design/decision doc, still
**blocked** on picking Option A/B/C/D, the ECU merge granularity, and the
write-target policy). Nothing here presupposes that decision: every commit was
justified by a defect or a duplication that existed already.

## Why

The variant work's cost is not the merge logic — it is that **profile-path
knowledge is scattered and duplicated**, so turning one root into a chain has to
be applied in N places, and every feature added meanwhile adds an N+1. Two
concrete symptoms found while surveying (2026-08-04):

- **A real bug.** `canair contribute` silently drops a profile's `groups.yaml`.
  Copying is driven entirely by `_DEFINITION_MEMBERS` (`canlib/contribute.py:57-64`
  → `profile.yaml`, `vehicle_states.yaml`, `states.yaml`, `can_buses.yaml`,
  `ecus`, `signals`) and the string `groups` appears **nowhere** in
  `canlib/contribute.py`. The bundled Ioniq ships `@charging`/`@driving`/
  `@powertrain`/`@climate`/`@body`, so contributing it upstream omits them. This
  is the failure mode of member knowledge living in four places at once.
- **Two functions named `find_ecu_file` with different semantics** —
  `canlib/ecus_edit.py:151` (locates by `tx_id`) and
  `canlib/pids_edit/_text.py:56` (locates by ECU name) — each with its own
  directory resolver and its own `ecus/*.yaml` glob.

So the prep is: **collapse each duplicated seam to one**, and make every
profile-path access site read as obviously a **READ** (a chain would search it)
or a **WRITE** (must land in exactly one root). That read/write split is the one
design constraint worth internalising now, because it is what the variant
write-target policy will turn on.

## Survey — where the single-root assumption lives (2026-08-04)

`Profile` is a frozen dataclass with two fields (`name`, `root`;
`canlib/profile.py:37-42`) and 11 derived members, every one a `self.root / …`
one-liner (`:44-110`). `meta` is a per-instance `@cached_property` (`:103`).
The active profile is an unkeyed module global `_active` (`:181`) with no reset
hook. `discover_profiles` (`:133-142`) **shadows only**, never merges.

Duplication found, by kind:

| Kind | Sites |
|---|---|
| Bundle-member lists | `Profile`'s properties (`profile.py:44-110`), `contribute._DEFINITION_MEMBERS`/`_CAPTURE_MEMBER` (`contribute.py:57-65`), `blind._STRIP_BUNDLE_MEMBERS` (`blind.py:58-65`) + literals at `blind.py:271,285-288`, `commands/profile.py::cmd_show` (`:213-284`) |
| ECU-file location | `ecus_edit.py:103,132-148,151` vs `pids_edit/_text.py:33,56-73` |
| Signals glob-and-load | `commands/signals.py:82-96`, `commands/export.py:61-74`, `commands/validate/other.py:280-292`, `commands/profile.py:263` |
| `Profile` rebuilt from a file path | `commands/validate/pids.py:257-267`, `commands/pids.py:398-402`, `pids.py:170`, `blind.py:375`, `blind.py:579`, `capture_journal.py:421` |
| Paths `Profile` does not expose | `profile.yaml` (`validate/pids.py:1299`, `commands/profile.py:220` ×2, and again inside `meta` at `profile.py:106`), `references/` (`commands/profile.py:277`), `dtc_log.yaml` (`dtc_log.py:48`) |

Cache state keyed on a profile:

| Cache | Key | Cleared by |
|---|---|---|
| `pids._cache` (`pids.py:141`) | `str(prof.ecus_dir)` (`:160`) | `clear_cache()` (`:144`), called by `pids_edit/_text.py:42` and `ecus_edit.py:239` |
| `_captures_query._ecu_index` / `_decode_fn` (`:44-45`) | **none** | **nothing** — built from `load_pids()` at `:63-71` and never invalidated |
| `log._ecu_lookup` (`log.py:30`) | **none** | **nothing** |

That last row is a live invalidation asymmetry: `clear_cache()` drops the ECU
definitions but not the indexes *derived* from them, so a `pids upsert-param`
followed by a decode preview in the same process reads a stale index.

`Profile.logs_dir` (`profile.py:99-101`) has **zero** consumers repo-wide
(verified by grep — the only hit is its own definition). Real logs go to
`SCRIPT_DIR / "logs"` (`log.py:25-26`) and `~/.config/canair/logs/canair.log`.

## Commits

Nine changes, ordered so each lands green. Verification after **every** commit:
`make check` (ruff + `ruff format --check` + `ty check` + `pytest -q`), plus
`make gen` / `make gen-check` where argparse help changes and `make docs` for
doc edits.

### 1. fix: `canair contribute` drops the profile's `groups.yaml` — `d70592e`

Standalone bug fix, no refactor. Ships independently of everything below.

- [x] `canlib/contribute.py:57-64` — add `"groups.yaml"` to `_DEFINITION_MEMBERS`.
- [x] `canlib/commands/contribute.py:69` — the `--no-captures` help text
      enumerates the members ("ecus/, profile.yaml, states, buses, signals"); add
      groups.
- [x] `make gen` — `docs/reference/cli/contribute.md:26-27` is generated from
      that help string.
- [x] Decide explicitly whether group labels/descriptions belong in the PII
      pre-flight (`canlib/pii.py:145-171` scans captures + `car_model`). Most
      likely no — they are user-authored selector names with no PII shape — but
      record the decision rather than leave it to omission.
- [x] Test (`tests/test_contribute.py`): a source profile's `groups.yaml` reaches
      `profiles/<name>/` in the prepared workspace.

### 2. refactor: expose `Profile`'s own members; delete dead `logs_dir` — `f26b357`

- [x] `canlib/profile.py` — add `meta_file` (`root/profile.yaml`),
      `references_dir`, `dtc_log_file`.
- [x] `canlib/profile.py:106` — `meta` reads `self.meta_file`.
- [x] `canlib/profile.py:99-101` — **delete** `logs_dir`.
- [x] Route the hand-built paths through the new properties:
      `canlib/commands/validate/pids.py:1299`, `canlib/commands/profile.py:220`
      (×2), `canlib/commands/profile.py:277`, `canlib/dtc_log.py:48`.
- [x] After this, `.root` survives only for display
      (`commands/profile.py:215,289`, `commands/status.py:118`,
      `commands/config.py:457`), `canlib/contribute.py:408,424`, and
      `installed_snapshot_kind` (`commands/contribute.py:167`) — which inspects
      the path's `.parts`, so a root is the right input there.
- [x] Check `docs/reference/cli/profile.md:11` still lists the components
      accurately.

### 3. refactor: single bundle-member registry — `06ad681`

- [x] `canlib/profile.py` — one declarative tuple of member records:
      `name`, `kind` (file/dir), `role` (curated / evidence / generated),
      `contributable: bool`, `blind_strip: bool`. Include the legacy
      `states.yaml` alias (honoured by `states_file`, `profile.py:68-83`) and the
      gitignored `dtc_log.yaml` (`.gitignore:55` → not contributable).
- [x] `canlib/contribute.py` — replace `_DEFINITION_MEMBERS`/`_CAPTURE_MEMBER`
      (`:57-65`) and drive the copy loop (`:407-424`) off the registry.
- [x] `canlib/blind.py` — replace `_STRIP_BUNDLE_MEMBERS` (`:58-65`) and the
      literals in `strip_profile` (`:271`, `:285-288`).
- [x] `canlib/commands/profile.py::cmd_show` (`:213-284`) — list components from
      the registry instead of by hand.
- [x] Test: a drift test asserting every `Profile` path property has a registry
      entry and vice versa. **This is what makes commit 1's class of bug
      unrepeatable** — the point of the registry.

Variant payoff: "which components are inheritable" and `profile show`'s future
inherited-vs-overridden column become table lookups (sketch step 6 of the design
doc).

### 4. refactor: one ECU-file locator — `c794d28`

- [x] New shared locator (in `canlib/pids_edit/_text.py`, or a small
      `canlib/ecu_files.py` if it reads cleaner):
      `locate_ecu_file(*, name=None, tx_id=None, profile=None, ecus_dir=None)`.
- [x] Collapse onto it: `canlib/ecus_edit.py::_resolve_dir` (`:103-108`),
      `_find_file_by_tx` (`:132-148`), `find_ecu_file` (`:151-154`); and
      `canlib/pids_edit/_text.py::_resolve_pids_dir` (`:33-39`),
      `find_ecu_file` (`:56-73`).
- [x] End the same-name/different-signature collision — rename whichever public
      name survives so `(str) -> Path` and `(int) -> (Path, str)` can't be
      confused.
- [x] Call sites already thread `pids_dir`/`ecus_dir` through, so the change is
      at the resolver, not per call: `pids_edit/params.py` (`:202,337,389,441,
      539,589,634,682,724,809,865,915,982,1053,1129`), `pids_edit/hits.py`
      (`:124,126,243,245,333,377,526`), `ecus_edit.py`
      (`:276,308,347,465,503`), `commands/pids.py:85`.
- [x] Keep `ecus_edit.register_ecu` (`:276-311`) as the **only** path that mints
      a new ECU file — that is the write-target seam the variant policy lands on.

Leaving this split is the single biggest re-refactoring risk of the variant work:
"which file owns this ECU" becomes "which **root** owns this ECU", and it should
be answered in one function.

### 5. refactor: one signals loader — `0d3cb21`

- [x] New `canlib/signals.py::load_signals(profile: Profile | None = None)`,
      matching the established shape of `load_states` (`states.py:222`),
      `load_can_buses` (`can_buses.py:70`), `load_groups` (`ecu_groups.py:77`).
- [x] Replace the four copies: `canlib/commands/signals.py:82-96` (which today
      takes **no** profile parameter and reads `active()` implicitly at `:87`),
      `canlib/commands/export.py:61-74`, `canlib/commands/validate/other.py:280-292`,
      `canlib/commands/profile.py:263`.
- [x] `canlib/signals_edit.py::_bus_path` (`:38-39`) stays the write-side
      resolver — do not fold read and write together.

### 6. fix: invalidate profile-derived caches — `2c4a2b8`

- [x] `canlib/profile.py` — add `cache_key` (today `str(self.root)`).
- [x] Key `canlib/commands/_captures_query.py:44-45` (`_ecu_index`, `_decode_fn`)
      and `canlib/log.py:30` (`_ecu_lookup`) on it, and register both with the
      invalidation `canlib/pids.py::clear_cache` (`:144-146`) already performs.
- [x] Test: build the decode index, mutate an ECU via `pids_edit`, assert the
      next `_decoded_preview` reflects the edit (fails today).

### 7. refactor: profile-keyed `load_pids` cache — `a9e2ff4`

- [x] `canlib/pids.py:160` — key `_cache` on `prof.cache_key` instead of
      `str(prof.ecus_dir)`. Byte-identical value today; one line to teach when a
      profile becomes a chain.

### 8. refactor: named ECU-document merge + duplicate-key warning — `a9e2ff4`

- [x] Extract the `.update()` loop in `canlib/pids.py::_load_dir` (`:182-193`)
      into `merge_ecu_documents(docs)`.
- [x] Warn on a duplicate top-level ECU key across files — today a second file
      declaring the same `ECU:` silently wins (last-writer-wins).
- [x] Promote it to an error in `canair validate pids`, alongside the existing
      duplicate-shipped-param-name check. **Already covered** — no change needed:
      `_duplicate_name_errors` has always errored on an ECU name/alias claimed by
      two files. The loader warning now points the user at it.

This one function is where PID-level variant merge later plugs in.

### 9. refactor: one `profile_for_path` helper — `c959682`

- [x] `canlib/profile.py::profile_for_path(path) -> Profile`.
- [x] Replace the parent-walking reconstructions:
      `canlib/commands/validate/pids.py:257-267` (`root = path.parent.parent`),
      `canlib/commands/pids.py:398-402`, `canlib/pids.py:170`,
      `canlib/blind.py:375`, `canlib/blind.py:579`,
      `canlib/capture_journal.py:421`.

The one function that must later resolve `extends:` from the discovered
`profile.yaml`.

## Conventions adopted (so new features don't add debt)

1. New bundle member → **one registry entry**, never a literal string in
   `contribute`/`blind`/`profile show`.
2. New loader → `load_x(profile: Profile | None = None)`; path from a `Profile`
   property, never `root / "…"` inline.
3. New writer → target via the shared locator; never glob a profile dir to find
   its own write target.
4. New profile-derived cache → keyed on `Profile.cache_key`, registered with the
   invalidation hook.
5. Library code calls `active()` only inside a one-line `(profile or active())`
   resolver.
6. Every profile-path access site reads as obviously a READ (a chain would search
   it) or a WRITE (lands in exactly one root).

## Docs

- `plans/2026-07-30-profile-variant-inheritance.md` — refresh "Background —
  current profile architecture" (`:42-65`) to the consolidated seams, and point
  the implementation sketch (`:210-232`) at the new single functions instead of
  the scattered ones. Note in its status header which prep landed.
- `docs/reference/cli/contribute.md`, `docs/reference/cli/profile.md` —
  regenerated by `make gen`.
- `CHANGELOG.md` — `[Unreleased]` entry for commit 1 (user-facing fix) and
  commit 6 (stale-index fix). The refactors are internal.
- `README.md` / `AGENTS.md` — no user-facing surface changes beyond commit 1;
  **confirm** rather than assume when the work lands.
- Load the **contributing-code** skill before writing any code.

## Out of scope

Deliberately no inheritance mechanism of any kind:

- No `extends:` parsing, no `bases: tuple[Path, ...]` on `Profile`, no chain
  resolution, no cycle detection. `validate_meta`
  (`commands/validate/pids.py:865-872`) already tolerates unknown top-level keys,
  so `extends:` will parse harmlessly when the real work starts — there is
  nothing to pre-build.
- **No roots-list `Profile`** and no abstract `resolve_member()` across roots.
  That *is* the variant change; a one-element-list version adds indirection to
  every path property for zero current benefit, and the read-searches-chain /
  writes-don't semantics cannot be validated without a real variant to test
  against.
- No `load_all_captures` signature change (`commands/_captures_query.py:119`) —
  whether it must span several dirs depends entirely on the unresolved
  capture-inheritance decision.
- No `profile.yaml` JSON schema.
- No Option C (parametric cell generation) groundwork.
- No mass-threading of `Profile` through `modes/`/`commands/` (`hunt.py:514`,
  `investigate.py:369`, `modes/identity.py:75`, `_live.py:838`, …). High churn,
  no variant payoff — those read `meta`, which resolves through one seam anyway.
