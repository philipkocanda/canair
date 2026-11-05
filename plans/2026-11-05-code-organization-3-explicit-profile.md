# Code organization, part 3: pass the profile explicitly

Status: **PART A DONE** (2026-11-05); part B proposed. Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 1.
Continues left-over item 2 of `plans/2026-11-04-package-layout-and-layering.md` (the
`ecus : profile : variants` half).

## Goal

1. Remove the import cycle between `ecus/`, `profile/` and `variants/`, so that each one gets a
   layer of its own: `ecus` above `variants` above `profile`.
2. Stop library code from finding the car through a process-global "active profile". Library
   functions take the profile as an argument. Only `cli/` holds the active profile.

The two goals share a cause. The global and the functions that set it live in `profile/`, and
setting it applies the variant (`variants/`) and clears the definition cache (`ecus/`). Those are
two of the upward edges that make the cycle.

## Findings (at `0ce4f742`)

### The cycle

Import counts between the three packages (most of them are function-level imports):

| From | To | Imports | What for |
|---|---|---:|---|
| `ecus` | `profile` | 13 | `active()`, `profile_for_path`, `read_meta`, `ProfileError`, the `Profile` type |
| `ecus` | `variants` | 7 | `load_variants`, `selected_variant`, revision `apply`/`resolve` |
| `profile` | `variants` | 4 | `Profile.meta` applies the variant's overrides; `set_active`/`active` call `apply_variant` |
| `profile` | `ecus` | 3 | `Profile.revisions` calls `revision_resolutions`; `set_active`/`replace_active` call `clear_cache` |
| `variants` | `profile` | 11 | the `Profile` type, `ProfileError`, `active`, `names` |
| `variants` | `ecus` | 2 | `selection.py` and `tags.py` call `load_raw_pids` and `revision_resolutions` |

The edges that go the wrong way for the target order (`ecus` → `variants` → `profile`) are
`profile` → `variants`, `profile` → `ecus` and `variants` → `ecus`.

### The global

- `profile/__init__.py` holds `_active: Profile | None` with `set_active`, `replace_active` and
  `active`. `active()` resolves a profile on first use if nothing set one.
- Outside `cli/`, about **70 calls** use the global: `active()`, or `load_pids()`,
  `load_raw_pids()` or `revision_resolutions()` with no profile. They are in 14 packages. The
  most are in `validation/other.py` (8), `captures/store.py` (4), `captures/__init__.py` (4),
  `live/revision_probe.py` (3) and `ecus/pids.py` (3).
- Two places outside `cli/` change the global. `live/revision_probe.py` calls
  `replace_active` after it detects a revision. `variants/selection.py` reads the private
  `profile._active` and calls `replace_active`.
- `load_pids(path=None)` has two meanings. With no argument, it returns the active car's
  definitions, resolved for its variant and revisions, and cached. With a path, it returns the
  files as written, not cached.
- The definition caches in `ecus/pids.py` (`_cache`, `_resolution_cache`) are keyed by
  `Profile.cache_key`. Three derived caches register a clear hook:
  `captures/store.py` (the decode index), `transport/ecu_log.py` (the ECU lookup) and
  `privacy/sensitive.py` (the sensitive sets). `set_active` clears all of them when the
  profile changes.

## Design

### `Profile` becomes a value

A `Profile` keeps what it has today: name, root, overlays, variant, variant source, revision
pins, revision detections, and the paths that derive from them. It loses the two properties
that compute over other packages:

| Today | After |
|---|---|
| `Profile.meta` (applies the variant's `profile_overrides:`) | `variants.effective_meta(profile)`. `profile.read_meta(path)` stays for the raw file. |
| `Profile.revisions` | `ecus.pids.determined_revisions(profile)` |

Remove `Profile.meta` instead of changing what it returns. A caller that still reads `.meta`
then fails at once, and does not get the raw settings without a warning. There are about 16
users of `.meta` and 12 of `.revisions`.

### Loaders take the profile

| Today | After |
|---|---|
| `load_pids()` | `load_pids(profile)`: resolved for the car, cached by `cache_key` |
| `load_pids(path)` | `load_pids_dir(path)`: the files as written, not cached |
| `load_raw_pids(profile=None)` | `load_raw_pids(profile)` |
| `revision_resolutions(profile=None)` | `revision_resolutions(profile)` |

Every public function in a package below `cli/` that needs the car takes `profile: Profile` as
a keyword argument. A function that only needs the definitions may take them instead. Do not
add a default that falls back to the global.

### The active profile lives in the CLI

`set_active`, `replace_active`, `active` and `_active` move to `cli/profile_context.py`.
`cli/__init__.py::main` sets it once. A command reads it and passes it down.

- `live/revision_probe.py::detect_revisions` returns the new `Profile` and does not set
  anything. The runtime in `cli/live/` stores it.
- `variants/selection.py` returns the stripped `Profile` to its caller and does not read
  `profile._active`.

### Caches

The definition caches are already keyed by `cache_key`, so a profile switch cannot return the
wrong car's definitions. Check the three derived caches. Key each one by `cache_key` if it is
not keyed already. Then `set_active` does not need to call `clear_cache`. The editors still
call `clear_cache` after a write. That is a downward import (`yaml_edit` → `ecus`), so it is
allowed.

## Steps

### Part A: cut the cycle

One commit for each step. Run `uv run lint-imports` after each one.

1. **`Profile.revisions` to `ecus.pids.determined_revisions(profile)`.** Rewrite the 12 users.
2. **`Profile.meta` to `variants.effective_meta(profile)`.** Rewrite the 16 users. Remove the
   property.
3. **Key the derived caches by `cache_key`**, and remove the `clear_cache` calls from
   `set_active` and `replace_active`. Add a test that switches between two fixture profiles in
   one process and reads each one's definitions, decode index and sensitive sets.
4. **Capture tags to `captures/`.** The part of `variants/tags.py` that reads definitions
   (`load_raw_pids`, `revision_resolutions`) moves to `captures/tags.py`. `selected_variant`
   and the scope keywords stay in `variants/`.
5. **The identity hint.** `variants/selection.py` calls `load_raw_pids` for the `canair
   identity` hint (`_identity_hint`). Move the hint to the command that prints it.
6. **`set_active`/`active` stop calling `apply_variant`.** The CLI calls
   `apply_variant(resolve_profile(...))` and passes the result to `set_active`.
7. **Split the layer.** In `pyproject.toml`, replace `canair.ecus : canair.profile :
   canair.variants` with three layers, top to bottom. Regenerate the architecture page and
   update its "Migration state" note.
8. **Move the packages that waited for this.** The architecture page says `actions/`,
   `broadcast/` and `install/` sit higher than they need to, because they import a package of
   the cycle. Measure each one and move its line in the contract down where it fits.

### Part B: remove the global from library code

1. **Add a ratchet.** An AST check in `tests/contracts/test_structure.py` counts, outside
   `cli/`, calls to `active()` and calls to `load_pids`, `load_raw_pids` and
   `revision_resolutions` with no profile argument. Add `ambient_profile_calls` to
   `baseline.json` at today's count.
2. **Add the explicit forms** (`load_pids(profile)`, `load_pids_dir(path)`) next to the old
   ones. Keep the old forms working until step 4.
3. **Convert one package per commit, lowest layer first:** `ecus`, `variants`, `states`,
   `privacy`, `broadcast`, `actions`, `captures`, `dtc`, `analysis`, `validation`,
   `yaml_edit`, `contribute`, `live`, then `tui`. In each commit, the package's public
   functions gain `profile`, and their callers pass it. Lower the ceiling.
4. **Remove the old forms.** `load_pids()` with no argument and the `profile=None` defaults go
   away. `ambient_profile_calls` reaches 0.
5. **Move the active profile to `cli/profile_context.py`** (see the design). After this step,
   no module outside `cli/` can call `active()`, because it no longer exists below `cli/`.
6. **Tests.** Library tests build their profile with a helper in `tests/support/`, from a
   fixture under `tests/fixtures/profiles/`. They do not depend on the car that conftest pins.

## Decision B: `Profile` or a `Definitions` value

A `Definitions` value would hold the loaded, resolved definitions and the indices that every
consumer builds from them (`build_ecu_index`, `build_param_index`, and more). It would remove
the repeated index builds and most of the derived caches.

**Recommendation: pass `Profile` now.** It is the smaller change, and the loaders keep their
cache. A `Definitions` value goes with typed definitions, which the umbrella plan puts out of
scope.

## Acceptance

- `lint-imports` passes with `ecus`, `variants` and `profile` in three separate layers.
- `scripts/check/import_graph.py` reports one cycle (`expression`/`uds`, until sub-plan 4).
- `ambient_profile_calls` is 0, and `active()` exists only in `cli/`.
- `--help` and every golden test output are unchanged.

## Risks

- **A caller that used the wrong profile.** Some functions read the global while their caller
  holds a different profile: an explicit `--dir`, the contribute workspace copy, a layered
  overlay. Passing the profile explicitly can change which car such a call reads. That is a bug
  fix, not a refactor. Put each case in its own commit, with a test, and say so in the commit
  message.
- **The number of call sites.** Part B changes about 70 calls and the signatures of the
  functions around them. Each package commit must stay mechanical. If a commit needs logic
  changes, split it.
- **Monkeypatched globals in tests.** Tests that patch `canair.profile.active` or
  `canair.profile._active` break in step 5. Find them first with
  `grep -rn "profile.active\|profile._active" tests`.

## As built, part A (2026-11-05, branch `refactor/co-3a-cycle`)

Part A is done: `ecus`, `variants` and `profile` are three layers in `[tool.importlinter]`, top to
bottom, and `scripts/check/import_graph.py` reports no cycle (sub-plan 4 split `expression`/`uds`). The edges
re-measured before the work were the plan's: `ecus` to `profile` 5 modules (stays, allowed),
`ecus` to `variants` 4 (stays, allowed), `profile` to `variants` 1, `profile` to `ecus` 1,
`variants` to `profile` 4 (stays, allowed), `variants` to `ecus` 2. The last three wrong-way
edges are now 0.

What differs from the plan:

- **A1 and A2 are one commit.** `Profile.revisions` and `Profile.meta` were removed together
  (the structure gates fail on a size ceiling when they are split). `determined_revisions` is
  in `ecus/revisions.py`, not `ecus/pids.py`, because `ecus/pids.py` sits at its size ceiling.
  `effective_meta` is in `variants/__init__.py`. One caller read the property through
  `getattr(profile, "meta", None) or {}` (`validation/ecu_file.py`) and would have fallen back
  to `{}` without a sound; it calls `effective_meta` now.
- **A3.** The decode index (`captures/store.py`) and the ECU lookup (`transport/ecu_log.py`)
  were single slots; both are keyed by `Profile.cache_key` now. The sensitive sets were already
  keyed by profile root, which matches the raw definitions they are built from. The editors
  still clear all of them after a write. `tests/ecus/test_pids_cache.py` switches between two
  fixture profiles with no `clear_cache` and reads each one's definitions, decode index, ECU
  lookup and sensitive set. `dtc/snapshot_decode.py::live_reply_length` is an `lru_cache` over
  `(ecu, pid)` that reads the active profile's captures and is not keyed by profile. It was
  not cleared by a switch before either. Part B fixes it when it takes a profile argument.
- **A4.** Only `capture_tag` and the two attribution tables moved to `captures/tags.py`. The
  scope keywords, `selected_variant` and the readers' rules stay in `variants/tags.py`.
- **A5.** `require_variant` takes an optional `hint` callback. `cli/car.py` has the identity
  hint and `require_car(what)`, which `cli/live/runtime.py`, `sniff` and `wican autopid` call.
- **A6.** The plan had the CLI pass a varianted profile to `set_active`. That would make `main`
  resolve a profile for every command, including `config` and `completion`, which need none,
  and a bad `profile_settings` entry would then block the command that fixes it. Instead
  `profile/` holds a variant applier (`use_variant_applier`, default: none), `main` installs it
  through `cli/profile_context.install()`, and `set_active`/`active` call it. The lazy
  `active()` behaves as before. The test suite installs it in its per-test profile reset.
  Part B drops the hook when `active()` moves to `cli/profile_context.py`, where
  `apply_variant` can be called directly. `variants/selection.py::require_named_car` still
  reads `profile._active` and calls `replace_active`; that is part B (design: return the
  stripped `Profile`).
- **A7.** Done as written. `variants` and `profile` charters no longer list `ecus`, `uds`.
- **A8.** Measured and moved: `actions/` imports `states`, `query`, `ecus`, `profile`, `safety`,
  `uds`; it joined the layer of `transport`/`captures`. `broadcast/` (imports `profile`,
  `core`) and `install/` (imports `profile`, `config`, `core`) form a layer just above
  `profile`. Left where they are: `interop`, `migrations`, `analysis`, `dtc` (each imports the
  store).

Size ceilings were tight, so a few lines were cut elsewhere to keep every module at or under its
ceiling (the stale history paragraph of `captures/store.py`, the long comment of
`profile/__init__.py`). The deferred-import count went from 1131 to 1120.
