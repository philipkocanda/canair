# Code organization: the work after the package layout

Status: **DONE** (2026-11-06) on the branch `refactor/code-organization`, not yet merged into
`main`. See "Result" at the end for the measured numbers, the order used and what is left. Not
user-facing: no command, flag, output or profile changes.

This plan comes from a review of the code organization at commit `0ce4f742`. The layering of
`plans/2026-11-04-package-layout-and-layering.md` holds: 11 import contracts pass with no
`ignore_imports` entries, every package has a charter, and only `cli/` and `tui/` import
`argparse` and `textual`. The open problems are inside the packages: oversized modules, logic in
`__init__.py` files, a process-global "active profile", about 1,100 deferred imports, and
documentation that names code that has moved.

The work is in ten sub-plans. Each one can land on its own, in its own branch.

## Sub-plans

| # | Plan | Size | Depends on |
|---|---|---|---|
| 1 | [Gates and drift](2026-11-05-code-organization-1-gates-and-drift.md) | small | none |
| 2 | [Deferred imports](2026-11-05-code-organization-2-deferred-imports.md) | medium | 1 |
| 3 | [Pass the profile explicitly](2026-11-05-code-organization-3-explicit-profile.md) | large | 1 |
| 4 | [The `expression`/`uds` cycle](2026-11-05-code-organization-4-expression-uds-cycle.md) | small | 1 |
| 5 | [Sub-packages in `live/` and `tui/apps/`](2026-11-05-code-organization-5-live-and-tui-subpackages.md) | medium | 2 |
| 6 | [Split the monitor](2026-11-05-code-organization-6-monitor-split.md) | large | 5 |
| 7 | [Package `__init__.py` bodies](2026-11-05-code-organization-7-package-init-bodies.md) | medium | 3 (for `profile/` only) |
| 8 | [Public names across packages](2026-11-05-code-organization-8-public-names.md) | small | 7 |
| 9 | [Thin command modules](2026-11-05-code-organization-9-thin-commands.md) | medium | 8 (for the query helper only) |
| 10 | [Cut the agent guide](2026-11-05-code-organization-10-agent-guide-cut.md) | medium | 5, 6, 7, 9 |

Recommended order: 1, then 2 and 4, then 3, then 5 and 6, then 7, 8 and 9, and 10 last. Plan 10
is last because plans 5 to 9 move the code that `AGENTS.md` points at.

## Findings (measured at `0ce4f742`)

The numbers come from `scripts/check/import_graph.py` and small AST scans. Each sub-plan repeats
the numbers it acts on.

| Measure | Now | Sub-plan |
|---|---:|---|
| `make structure` on `main` | **fails** (2 ceilings exceeded) | 1 |
| Deferred internal imports | 1,143 in 249 files | 2 |
| Deferred internal imports with a comment that gives a reason | about 42 | 2 |
| Canair modules that building the CLI parser loads | 343 of 484 | 2 |
| Calls to `profile.active()` or a loader with no profile, outside `cli/` | about 70 | 3 |
| Import cycles between packages | 2 (`ecus`/`profile`/`variants`, `expression`/`uds`) | 3, 4 |
| Files in `live/` with no sub-package | 62 | 5 |
| `MonitorController` | 75 methods, 1,009 lines | 6 |
| `MonitorApp` | 45 methods, 756 lines | 6 |
| `__init__.py` files over 500 lines | 6 (largest: `states/`, 987) | 7 |
| Imports of an underscore name from another package (`src/` only) | 17 names at 29 sites | 8 |
| Share of all lines in `cli/` | 29% (31,900 lines) | 9 |
| Modules over 500 lines | 44 | 6, 7, 9 |
| `AGENTS.md` | about 1,370 lines, 187 `src/canair` paths | 10 |

## Relation to other plans

- **`plans/2026-11-04-package-layout-and-layering.md`.** This plan continues its "Left over"
  list. Left-over item 2 (the two cycles) is sub-plans 3 and 4. Item 4 (the `live/`
  sub-packages) is sub-plan 5. Item 5 (the `AGENTS.md` path cut) is sub-plan 10. The rules of
  its section 9 apply to every sub-plan here.
- **`plans/2026-11-05-one-session-owner.md`** (proposed) changes `MonitorSessions` and the raw
  monitor. Sub-plan 6 must land after it, or the two must agree on who edits those files first.
- **`plans/2026-11-03-yaml-edit-router.md`** decided to keep both YAML edit engines (text
  surgery and ruamel) and not to make the `pids` parser table-driven. This plan keeps both
  decisions. Sub-plan 9 splits the `pids` parser into modules but keeps its declarations as
  they are.
- **`plans/2026-08-06-signal-naming-convention.md`** (the `param` to `signal` rename) must not
  be mixed with any move here. Do it before or after a sub-plan, never in the same commit.
- **`plans/2026-11-05-cli-tui-standards.md`** adds `ui_standards` baselines to
  `tests/contracts/baseline.json`. Sub-plans 5, 6 and 9 move files that those baselines name.
  Rename the keys by hand in the same commit, as the layout plan says for `oversized_modules`.

## Rules for every sub-plan

These are the rules of the layout plan (section 9 and the handoff), repeated because each
sub-plan depends on them:

1. Work in a separate worktree, on a branch from `main`. Other sessions share the main checkout.
2. A move is mechanical and alone: `git mv` or a cut-and-paste of whole functions, plus a
   codemod for imports and `monkeypatch` strings. No logic change in the same commit. No
   compatibility alias for the old path.
3. Never add an `ignore_imports` entry and never raise a number in `baseline.json`. A new
   ratchet starts at the current count. A new key is not a raise.
4. Every commit is green: full suite (parallel and `-n0`), `ruff`, `ruff format`, `ty`,
   `make structure`, and `scripts/gen/gen_cli_reference.py --check` with no change to `--help`.
5. After a move, grep the moved files for `^\s*from \.` at every indent. A deferred relative
   import inside a `try` block can break with no error.
6. Update the status line of the sub-plan as each step lands, with the measured numbers.

## Open decisions

Each sub-plan lists its own decisions with a recommendation. The ones that change the shape of
the code:

| # | Question | Recommendation | Sub-plan |
|---|---|---|---|
| A | Add a lazy command registry, so that a command's module loads only when it runs? | Not now. Measure again after the hoist. | 2 |
| B | Library functions take a `Profile`, or a new `Definitions` value? | `Profile` now. `Definitions` can follow. | 3 |
| C | Create `tui/kit/`, as the placement table says? | No. Correct the table instead. | 5 |
| D | When an `__init__.py` body moves out, re-export its names, or rewrite every caller? | Rewrite every caller. | 7 |
| E | Remove the pass-through properties of `MonitorController`? | Yes. | 6 |

## Not in this plan

- **Typed definitions.** `load_pids()` returns nested `dict`s, and each consumer builds its own
  index from them (`build_ecu_index`, `build_param_index`, `build_iocontrol_index`, and more).
  A typed model would be a large change to every package. It needs its own plan.
- **One YAML edit engine.** See the yaml-edit-router plan.
- **The duplicated keepalive code** of the raw and ELM327 paths. See the one-session-owner plan.

## Synced with main

2026-11-06: `main` at `1f2c1823` is merged into the integration branch (a merge commit, not a
rebase). `main` had ten commits the refactor lacked: the discover functional-id fix, the
ReadMemoryByAddress work (`memory` command, `scan memory`, `live/memory/`, `memory/`,
`uds/memory.py`, `yaml_edit/memory.py`, `privacy` and `validation` changes), `pids set-isotp`,
and profile data.

Resolved by hand:

- **`scan`.** `main` split it by kind and the refactor into `parser/plan/wizard/run`. The
  package has both: main's `probes.py` and `memory_kind.py`, a new `range_parser.py`, and the
  refactor's `plan.py`, `wizard.py` and `run.py`. The three probe parsers in the refactor's
  `parser.py` were identical to main's `probes.py`, so main's file is the one kept.
- **`pids`, `ecu`.** Main's edits to the old single modules (the `pid_status` import,
  `set-isotp`, the `23/ADDRESS+SIZE` token in `run`, `--software` on `set-fingerprint`, a
  shorter docstring) are applied to the packages. `int_or_clear` moved to `pids/common.py`.
- **`memory` command.** Its parser moved to `parser.py`, so `__init__.py` is a charter.
- **`live/software_version.py`** moved to `live/identity/software.py`; `live/` keeps its
  sub-package layout and names `memory` as a family. New live tests sit in
  `tests/live/{memory,discover,read}/`.
- **Renamed modules.** Main's new code imported modules the refactor had moved
  (`live.progress_line` → `render.progress`, `live.discover` → `live.discover.physical`,
  `Profile.revisions` → `ecus.revisions.determined_revisions`); it now uses the new paths.
- **Contract, baseline, generated pages.** `canair.memory` joins the analysis layer in the
  layer contract. `baseline.json` took the refactor's keys, and `--write` lowered four ceilings.
  The architecture page is regenerated. The CLI reference for the old commands is main's.

Later sub-plans that move `live/` or `cli/` modules should merge `main` first: `main` now
has three more files in `cli/commands/` (`memory/`) and one more `live/` family.

## Result

All ten sub-plans are done on `refactor/code-organization` (merged with `main` at `1f2c1823`).
Gates on the final tip: ruff, `ty`, `lint-imports`, `make structure`, the CLI reference,
architecture and screenshot checks, `mkdocs build --strict` and the full suite (11,343 passed,
22 skipped).

| Measure | Before (`0ce4f742`) | After |
|---|---:|---:|
| Deferred internal imports | 1,143 in 249 files | 84 in 70 files, each with a reason |
| Import cycles between packages | 2 | 0 |
| Calls to the global active profile outside `cli/` | about 65 | 0 |
| Cross-package imports of an underscore name | 17 names at 29 sites | 0 (gated) |
| Modules over 500 lines | 44 | 20 |
| Command modules over 500 lines | 12 | 0 |
| `__init__.py` files over 500 lines | 6 | 0 |
| `AGENTS.md` | about 1,370 lines, 187 `src/canair` paths | 436 lines, 7 paths (gated) |
| `canair --help` | about 0.24 s | about 0.26 s |

### Order used

The plan's order was changed to avoid conflicts between agents. The moves (sub-plans 1, 3A, 4,
5, 9) ran first, `main` was merged in, then 6, 7, 8 and 3B, and the deferred-import hoist
(sub-plan 2) ran after them in parallel on disjoint packages, so that it did not conflict
with any move. Sub-plan 10 ran last.

Decisions taken: A (no lazy command registry), B (pass `Profile`), C (no `tui/kit/`), D (callers
rewritten, no re-exports), E (forwarding properties removed).

### New gates

`doc_paths.py` (agent documents name code that exists), the structure tests in the pre-commit
hook, `deferred_imports_without_reason`, `cross_package_private_imports`, `ambient_profile_calls`,
`agents_md_lines` and `agents_md_code_paths`, and the contract tests for cheap imports.

### Left over

1. **Step B6 of sub-plan 3 is partial.** Most library tests still get their car from the suite's
   pinned profile through `cli.profile_context.active()`. Move them to fixtures under
   `tests/fixtures/profiles/` with `tests/support/profiles.py`.
2. **Monitor collaborators keep a back-reference to the controller** (`self.c`). Principle 3
   of sub-plan 6 is not done. Sub-plan 6 should be revisited together with
   `plans/2026-11-05-one-session-owner.md`, which changes the same classes.
3. **20 modules are still over 500 lines**, led by `yaml_edit/pids/params.py` (1,042),
   `analysis/cross_signal.py` (971), `analysis/align.py` (931) and
   `transport/elm327/terminal.py` (835). The ratchet holds them; none has a plan.
4. **Tests that patch a stale name can write into a bundled profile.** It happened three times
   during the hoist (`bms.yaml` twice, `vehicle_states.yaml`). Each was reverted. A test
   fixture that makes the bundled profiles read-only would turn this into a failure.
5. **`scripts/dev/hoist_deferred.py`** should skip modules that `hatch_build.py` loads by path.
6. **`__init__.py` bodies over 100 lines remain** in `cli/__init__.py` (401), several
   `cli/commands/*/__init__.py` (up to 455) and `yaml_edit/pids/__init__.py` (116). They were
   outside sub-plan 7's list.
7. **Flaky tests seen under load:** `tests/integration/test_elm327_emulator.py` (only when the
   emulator is installed) and
   `tests/live/test_revision_detection.py::test_the_refusal_points_at_identity`.
8. The branch has not been merged into `main` or pushed. `main` had not moved since the merge of
   `1f2c1823`. A later merge needs `main` merged into the branch first, because `cli/` and `live/`
   have been reorganized.
