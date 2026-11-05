# Handoff: package layout and layering, after phase 5

Read `plans/2026-11-04-package-layout-and-layering.md` (the plan, the deviations and "Left over")
and `docs/development/architecture.md` (the layers and the placement table, generated from the
import contract and the package charters).

## State on 2026-11-04 (end of the day)

Phases 0 to 5 are done on branch `refactor/package-layout`, with the items under "Left over" in the
plan still open. The package is `src/canair/` (it was `canlib/`), the root holds only `__init__.py`
and `__main__.py`, the tests mirror the packages, and eleven import contracts run in
`uv run lint-imports`, pre-commit and CI. The one cycle left is 18 packages.

## State on 2026-11-05

Phase 6 (branch `refactor/split-layer-cycle`) split the 18-package cycle down to the three
definition packages `ecus : profile : variants`, the `:` layer section 6 of the plan keeps
anyway. The plan's phase 6 paragraph lists each cut and where it deviates from section 4.

The next steps, in the order that pays most:

1. **Move the five Textual apps** into `tui/apps/` after splitting each model from its app
   (`captures/step_model.py` from `step_tui.py`, the monitor controller from `_monitor_tui.py`).
2. **The two cycles left** (`ecus : profile : variants` and `expression : uds`). The plan's
   "Left over" item 2 lists the exact imports to cut for each.
3. The smaller items in the plan's "Left over" list.

How a cycle step went, so the next one is quick: list the module edges between the packages
still in the group (grimp: `grimp.build_graph("canair")`, then
`find_modules_directly_imported_by` per module, grouped by package), find a package that only
one or two modules inside the group import, move the imported function or constant to the
package that already owns its data (often a definition package or `core`), then pull the package
out of the `:` group in `pyproject.toml` and run `uv run lint-imports`. Edit the contract with a
script that matches the whole layers line: a plain `sed` on `"canair.x",` also rewrites the
`source_modules` lists of the forbidden contracts.

## Rules of engagement (read before touching git)

- **Work in a worktree** on a new branch from `main` (the branch of phases 0 to 5 is merged). Many
  agent sessions share the main checkout `/Users/philip/projects/canair`.
- **Pass `workdir` on every shell call.** A shell call without `workdir` runs in the main
  checkout. That already happened once: a commit meant for the worktree landed on `main` and had
  to be reset. After any commit, check `agent-git.sh branch --show-current` and
  `agent-git.sh log --oneline -3`.
- **Use `agent-git.sh` instead of `git`** for every command, and never mention it in repo content.
  Never leave changes staged.
- **Commit hooks run the whole project**: ruff, ruff format, `ty`, `lint-imports`, skill
  frontmatter. A commit with unformatted or unresolved code fails the hook, and the hook can
  leave the commit half made. Run `uv run ruff check --fix . && uv run ruff format .` first.
- **Pushing needs the user's approval** (phases 0 to 5 were approved). Merge with `--ff-only`.
  On push, the LFS pre-push hook failed with "Unable to verify locks"; set only for that command:
  `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0='lfs.https://github.com/philipkocanda/canair.git/info/lfs.locksverify' GIT_CONFIG_VALUE_0=false`.
  Do not skip the hook and do not write that into any config file. GitHub also reports a
  bypassed "required status check" rule on push; it is expected for this account.
- **Never add an `ignore_imports` entry and never raise a number in `baseline.json`** to get a
  gate green. Fix the structure, or stop and ask the user. `uv run python -m
  tests.contracts.test_structure --write` only lowers baselines and drops stale entries; it never
  adds. Renaming the key of a moved oversized module in `baseline.json` is a manual edit.
- **Hard-wrap every file at 100 columns**, and use "signal", not "parameter", in new text
  (AGENTS.md).
- The tree is public. No PII, no VINs, no security keys (AGENTS.md).

## Lessons from phases 0 to 5

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
- **A codemod that moves a package skips the files it moves.** Their absolute imports of the old
  name and their relative imports (a module that goes one level deeper) stay stale: rewrite them
  in the same commit (`ty` and `ruff` find them). Moving a whole directory one level down (as
  `commands/` to `cli/commands/` did) breaks every `..x` that pointed above it.
- **A test that finds a file through `Path(__file__)`** breaks when its test directory gets one
  level deeper. Grep `__file__` in the tests you move.
- **`test_structure --write` drops the baseline entry of a module that no longer exists.** Rename
  the key of a moved oversized module first, or its ceiling is lost and the module counts as new.
- **An import that lands in its own isort group adds a line**, and a module at its ceiling fails.
  A relative import beside the existing relative ones does not.
- **A module that runs code on import breaks `pkgutil.walk_packages`** (the keymap test imports
  every module). A `__main__.py` needs its `if __name__ == "__main__":` guard.
- **A package docstring can be a command's `--help`.** Three command packages use theirs as the
  description, so adding a charter changes the generated CLI reference.
- **The suite is 70 s on an idle machine and 280 s on a busy one** (other sessions share it). Judge
  a slowdown by load average first.
