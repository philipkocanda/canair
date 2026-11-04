# Handoff: package layout and layering, phases 3 onwards

Read this first, then `plans/2026-11-04-package-layout-and-layering.md` (the plan, with the
reasoning) and `docs/development/architecture.md` (the layers and the placement table). This
file says where the work stands, what to do next, and what already went wrong once.

## State on 2026-11-04

Phases 0, 1 and 2 are done, merged and pushed. `origin/main` is at `d76b7b57`
(`docs(plans): record the measured progress of the layout plan after phase 2`). Nothing is left
unpushed from those phases.

| Measure | Start | Now |
|---|---:|---:|
| Units in the largest import cycle | 38 | 26 |
| Import-linter `ignore_imports` entries | 30 | 8 |
| Test files directly in `tests/` | 306 | 50 |
| Packages with a charter | 0 | 2 (`core`, `validation`) |
| Root modules in `canlib/` | 52 | 52 |
| Tests | 10,421 | 10,425 (+9 skipped) |

What exists now, from the plan's point of view:

- **Gates.** Nine import contracts in `[tool.importlinter]` (`pyproject.toml`), run by
  `uv run lint-imports`, pre-commit and CI. `tests/contracts/test_structure.py` pins root modules,
  module size, deferred imports, package charters, flat tests and test-to-test imports to
  `tests/contracts/baseline.json`. `make structure` runs both in seconds.
- **Rules for agents** are in AGENTS.md ("Code structure"), the contributing-code skill ("Placing
  and moving code") and `docs/development/architecture.md`. Follow them; they apply to you too.
- **Test layout** mirrors the packages: `tests/<package>/`, plus `support/` (helpers),
  `integration/`, `contracts/` and `scripts/`. 50 tests of root modules stay flat until the
  module moves (they are listed in `baseline.json` as `flat_tests`).
- **New packages** so far: `core/` (leaf: `yaml_rt`, `oserrors`), `validation/` (the validators).
  `expression/results.py`, `actions/plan.py`, `uds/describe.py`, `uds/support_marks.py`,
  `uds/wican_layout.py`, `analysis/unsplit_batches.py`, `transport/reboot.py` are new modules in
  existing packages.
- **Tooling.** `scripts/check/import_graph.py` measures cycles, deferred imports, root modules and
  oversized modules. `scripts/refactor/move_symbols.py` is the codemod (see "Moving code").

Still open from phase 2 (small, independent, do them when convenient):

1. The three `commands` → `cli.main` re-entries (`commands/groups.py`, `pids.py`, `states.py`
   import `canlib.cli.main`). No contract covers it yet; it matters once `cli/` is a layer.
2. `validation/` debt: the `_run_*` functions print and return exit codes from library code;
   `validation/pids.py` is 1,854 lines; `validation/pids.py` imports `yaml_edit` for one constant
   (`RETIRED_FIELD`) while `yaml_edit` imports `validation/`. Fix by moving the constant down and
   the runners into `commands/validate/`.
3. `scripts/migrate_states_status.py` and `scripts/migrate_states_uppercase.py` should become
   `canlib/migrations/` modules run from a `canair` command. That adds a user-facing command, so
   ask the user first.

## Rules of engagement (read before touching git)

- **Work in the worktree** `/Users/philip/.local/share/opencode/worktree/625e5c/package-layout`
  (branch `refactor/package-layout`, already merged; keep using it or make a new branch from
  `main`). Many agent sessions share the main checkout `/Users/philip/projects/canair`.
- **Pass `workdir` on every shell call.** A shell call without `workdir` runs in the main
  checkout. That already happened once: a commit meant for the worktree landed on `main` and had
  to be reset. After any commit, check `agent-git.sh branch --show-current` and
  `agent-git.sh log --oneline -3`.
- **Use `agent-git.sh` instead of `git`** for every command, and never mention it in repo content.
  Never leave changes staged.
- **Commit hooks run the whole project**: ruff, ruff format, `ty`, `lint-imports`, skill
  frontmatter. A commit with unformatted or unresolved code fails the hook, and the hook can
  leave the commit half made. Run `uv run ruff check --fix . && uv run ruff format .` first.
- **Pushing needs the user's approval.** The user approved the push of phases 0 to 2 only.
  Merge with `--ff-only`. On push, the LFS pre-push hook failed with "Unable to verify locks";
  set only for that command: `GIT_CONFIG_COUNT=1
  GIT_CONFIG_KEY_0='lfs.https://github.com/philipkocanda/canair.git/info/lfs.locksverify'
  GIT_CONFIG_VALUE_0=false`. Do not skip the hook and do not write that into any config file.
  GitHub also reports a bypassed "required status check" rule on push; it is expected for this
  account.
- **Never add an `ignore_imports` entry and never raise a number in `baseline.json`** to get a
  gate green. Fix the structure, or stop and ask the user. `uv run python -m
  tests.contracts.test_structure --write` only lowers baselines and drops stale entries; it never
  adds. Renaming the key of a moved oversized module in `baseline.json` is a manual edit.
- **Hard-wrap every file at 100 columns**, and use "signal", not "parameter", in new text
  (AGENTS.md).
- The tree is public. No PII, no VINs, no security keys (AGENTS.md).

## Phase 3: remove the flag dispatch

**Goal.** Each live command calls its operation directly with typed options. After this phase no
module below `commands/` reads an `argparse.Namespace`, `dispatch_mode` and its 16-predicate table
are gone, and `CANAIR_DEFAULTS` (60 attributes) has shrunk to the options every live command
really shares.

**How it works today** (read these files first):

1. A live command (`read`, `monitor`, `scan`, `discover`, `io`, `routines`, `action`, `dtc`,
   `identity`, `raw`, `repl`) builds a partial namespace, calls
   `commands/_live/parser.py::finalize_live_parser`, which backfills the rest from
   `commands/_live/defaults.py::CANAIR_DEFAULTS`, and ends in
   `commands/_live/runtime.py::run_live`.
2. `monitor` and `read` rewrite the namespace (`args.multi = [...]`, `args.monitor = args.interval`)
   so the dispatcher picks the right handler.
3. `run_live` locks the device, connects (`async_main`, `_live/connect.py`), and calls
   `modes/dispatch/__init__.py::run_session_guarded`, which calls `dispatch_mode`. That walks
   `_DISPATCH`, a tuple of `(handler, predicate)` pairs where **order matters** (`multi`+`monitor`
   before bare `multi`). The handlers are in `modes/dispatch/{reads,scans,actuators,diagnostics,
   multi,elm_only}.py`; each unpacks `args.*` into keyword arguments for a `mode_*` coroutine.
4. The raw `slcan-tcp` path is separate: `modes/raw_ops.py` and `modes/raw_monitor.py` also read
   `args.*`, and call `run_session_guarded` themselves.

**Everything outside `commands/` that still reads the namespace** (found with
`grep -rlE "\bargs\.[a-z_]+|getattr\(args" canlib | grep -v ^canlib/commands/`):
`captures/dates.py`, `first_run.py`, `keepmode.py` (`wants_save`, `keep_mode_from_args`),
`lock.py`, `modes/dispatch/*`, `modes/monitor_reconnect.py`, `modes/raw_monitor.py`,
`modes/raw_ops.py`, `modes/revision_probe.py` (`targeted_ecus`), `transport/config.py`,
`transport/timeouts.py`, `cli.py`. Not all of these need to change in phase 3: `cli.py` is the
interface, and `first_run`, `lock` and `captures/dates.py` need individual decisions. The
`argparse` contract has 3 debt entries (`captures.dates`, `modes.dispatch`,
`modes.dispatch.ranges`); the phase is done when those are gone.

**Suggested approach** (small steps, each one green on its own):

1. **Write the characterization first.** `tests/modes/test_dispatch_table.py` pins the table's
   order and membership; `tests/commands/test_live_dispatch.py` and the per-mode tests cover
   transports. Before deleting the table, add a test that maps each live subcommand's parsed
   arguments to the operation it reaches, so the replacement is checked against today's behaviour.
2. **Define options dataclasses** next to each operation (for example `MonitorOptions` in the
   monitor module, `ScanOptions`, `DtcOptions`). Plain frozen dataclasses with the fields the
   handler passes today. Keep the `mode_*` signatures where they are and add the dataclass as a
   thin layer first, if that is easier to land in steps.
3. **Replace `dispatch_mode` with a direct call.** For example
   `run_live(args, operation=...)`, where each command module supplies a coroutine that builds
   its options from `args` (this builder is CLI code and stays in `commands/`) and awaits the
   operation. `run_session_guarded` keeps its job (error classification, `--recover` hint,
   frame-count persistence, revision probing) but takes the operation as a parameter instead of
   the namespace.
4. **Move the argument reading up.** `wants_save`, `keep_mode_from_args` and `targeted_ecus` read
   the namespace; give them options-based equivalents and call them from `commands/`.
5. **Shrink `CANAIR_DEFAULTS`** to the connection and session options (`wican`, `transport`,
   `wait`, `timeout`, `json`, `verbose`, `save`, `label`, `state`, `notes`, `force`, ...). Drop the
   mode-selector attributes (`param`, `ecu`, `raw`, `scan`, `skm_wakeup`, `identity`, `discover`,
   `dtc`, `iocontrol`, `routines`, `multi`, `action`, ...).
6. **Unify the two hex-range parsers** (`modes/dispatch/ranges.py::parse_range` raises
   `argparse.ArgumentTypeError`; `modes/iocontrol_scan.py::_parse_range_str` returns `None`).
   Their error contracts differ, so this is a behaviour change: do it in its own commit with its
   own tests.
7. Remove the `argparse` debt entries as the modules stop importing it; lower the baselines.

**Traps in this phase:**

- `raw_ops.py` and `raw_monitor.py` bypass `dispatch_mode` for the pipelined raw client. They need
  the same treatment, or the raw transport keeps reading flags.
- `commands/monitor.py` sets `args.multi`/`args.monitor` purely for the dispatcher; the
  `run_live` notation resolution (`args.notation = resolve_notation(...)`) was added in phase 2
  for the same reason and goes away with the namespace.
- Several commands rely on a selector being absent meaning "fall through to the interactive
  REPL" (`handle_interactive` is the fallback after the table). `tests/modes/test_dispatch_table.py`
  documents the silent-fall-through hazard; keep an equivalent check.
- The live path must stay transport-agnostic: operations take the `Terminal` protocol only
  (checked by `ty`). Do not import `WiCANTerminal` or `RawTerminal` in an operation.
- Every `--help` must stay byte-identical: `uv run python scripts/gen/gen_cli_reference.py
  --check` is the proof.

## Phase 4: the moves, one package per commit

Order (leaf first), from the plan: `core` (finish: `yaml_io`, `textscan`, `id_ranges`,
`stop_signals`, `constants`, `log`) → `uds` additions (`addressing`, `frame_counts`) → `safety`
(`safety`, `quirks`, `actuation`) → `privacy` (`sensitive`, `pii`) → `profile` and the definition
packages (`pids`, `can_buses`, `signals` → `broadcast`, `did_families`, `signal_category` into
`ecus/`) → `config`/`install` → `query` → `captures` additions → `analysis` additions → `device`
→ `transport/{elm327,raw}` → `live` (rename of `modes`) → `render` → `tui/apps` → `interop` →
`contribute` → `cli` (rename of `commands`). The per-module table is section 4 of the plan.

**Each move commit does all of this and nothing else:**

1. `git mv` the module or package. Move its test directory/files in the same commit
   (`tests/<new package>/`), and delete its entry from `flat_tests` in `baseline.json`.
2. Run the codemod, then `ruff check --fix`, `ruff format`, `ty`:

   ```bash
   uv run python scripts/refactor/move_symbols.py rename-module canlib.old.mod canlib.new.mod
   uv run python scripts/refactor/move_symbols.py move-names \
       canlib.old.mod canlib.new.mod NAME [NAME ...]
   uv run python scripts/refactor/move_symbols.py retarget-paths canlib/old/mod.py canlib/new/mod.py
   ```

   `rename-module` and `move-names` rewrite imports and `monkeypatch.setattr("canlib.…")` strings
   in `canlib/`, `tests/` and `scripts/`. `retarget-paths` rewrites file paths in docs, skills,
   AGENTS.md and comments. The docstring of the script lists what it cannot do.
3. Edit by hand what the codemod cannot reach: the module names in `[tool.importlinter]`, the
   keys in `tests/contracts/baseline.json`, and profile data that quotes a path (leave profile
   data alone).
4. Give the package a **charter** in its `__init__.py` docstring (`Belongs:` / `Not here:` /
   `Imports:`); `test_structure.py` requires it for any package not in the baseline list.
5. Add the package to the `layers` contract. The `layers` contract of plan section 6 could not
   be written in phase 0 because the target packages did not exist; add it with the first move
   and extend it per move. The enumerated `source_modules` lists in the existing contracts name
   every top-level unit, so a new unit (like `canlib.core` was) must be added to each.
6. Remove the `ignore_imports` entries the move fixes, then
   `uv run python -m tests.contracts.test_structure --write`.
7. Run the whole gate set (below). Commit with a message that says "pure move" and names the
   codemod command.

**Conflicts.** Phase 4 rewrites imports in almost every file, so it conflicts with any other
session editing `canlib/`. Run `agent-git.sh fetch` and rebase or merge `main` into your branch
before every move commit, keep each move small, and merge to `main` often. If `main` moved a
file you are moving, redo the move on top rather than resolving by hand.

## Phase 4b: `src/canair/`

`git mv canlib src/canair`, rewrite `canlib` to `canair` everywhere (imports, strings, paths,
`pyproject.toml`, `mkdocs.yml` hooks, CI, pre-commit, docs, skills, AGENTS.md), set hatch
`packages = ["src/canair"]`, move the bundled `profiles/`, `templates/` and
`config.example.yaml` into the package and find them with `importlib.resources` (fallback to the
repo root in a checkout), and add a CI job that builds the wheel, installs it in a clean
environment and runs `canair --help`, `canair profile list` and `canair validate`. The reasons
(name collision with Kvaser's `canlib` on PyPI, the `SCRIPT_DIR` two-meanings bug, CI never
testing the wheel) are in section 11 of the plan. `canlib/constants.py` is where the path
shortcuts live (`SCRIPT_DIR = PACKAGE_DIR.parent`); `install_context.py` and `canair update`
depend on them, so they need tests of their own.

## Phase 5: finish

`ignore_imports` empty (or each entry with a reason and a plan reference), AGENTS.md's 180
`canlib/…` path references cut down to what an agent needs always (the rest points at the
architecture page), the generated architecture page (`scripts/gen/gen_architecture.py`, section
9.2, which needs charters on every package), then the `param` → `signal` identifier rename from
`plans/2026-08-06-signal-naming-convention.md` on the stable tree.

## Gates before every commit

```bash
cd /Users/philip/.local/share/opencode/worktree/625e5c/package-layout   # or your own worktree
uv run ruff check --fix . && uv run ruff format .
uv run ty check
uv run lint-imports
uv run pytest -q                                   # ~70 s, parallel; use -n0 for one file
uv run python scripts/gen/gen_cli_reference.py --check
uv run python scripts/check/import_graph.py        # the trend numbers
```

For a change that touches user-facing text or the docs also run
`uv run --group docs mkdocs build --strict --site-dir <tmp>`.

## Lessons from phases 0 to 2

- **Order-dependent test failures appear as the layout changes**, because xdist `loadscope`
  groups tests by module and moving a file changes which tests share a worker. One appeared in
  phase 2: `monkeypatch.setattr(ansi, "BOLD", ...)` on a name served by a module `__getattr__`
  left a real attribute behind and broke a later colour test about a third of the time. If a
  full run fails but the failing test passes alone, bisect the worker's file order: take the
  files the worker ran from a `pytest -v` log, run them serially with `-n0`, then halve the list.
- **`ruff --fix` hides codemod damage.** If the codemod drops an alias, `ruff --fix` deletes the
  now-"unused" import and the error shows up as `F821 Undefined name` or a `ty` diagnostic. Read
  the `ruff check` output after `--fix`.
- **Size ceilings bite on import lines.** Splitting an import across two lines can push a module
  of 501 lines to 502 and fail `test_modules_stay_small`. Merge the imports or remove a real
  redundancy; do not raise the ceiling.
- **The structure test runs `lint-imports` in a subprocess** on purpose, because running it in
  process changed global logging and terminal state.
- **Wrong-direction contracts happen.** The first `transport` → `wican` contract was backwards
  for the target layering (the device API sits below the transport). Check a contract against
  `docs/development/architecture.md` before spending effort satisfying it.
- **Do not run a full-suite loop in the background while measuring flakes**; the load changes
  the result. Run it in the foreground, or use a throwaway worktree.
- **Plans are historical.** `plans/` and `CHANGELOG.md` keep their old paths; the codemod skips
  them on purpose. Update the layout plan's status as you finish a phase (the "Measured after
  phase 2" table in section 10 is the pattern).
