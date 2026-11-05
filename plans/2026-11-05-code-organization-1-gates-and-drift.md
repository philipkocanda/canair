# Code organization, part 1: gates and drift

Status: **DONE** (2026-11-05, branch `refactor/co-1-gates`; see "As built"). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on nothing. Every
other sub-plan depends on this one, because it makes `main` green again.

## Goal

Make `make structure` pass on `main`, make the pre-commit hook run the same gates as CI, and
correct the text that names code which has moved. Add one gate so that path references in the
agent documents cannot go stale again.

## Findings (at `0ce4f742`)

1. **`make structure` fails on `main`.** Two modules exceed their ceilings in
   `tests/contracts/baseline.json`:

   | Module | Ceiling | Now | Grew in |
   |---|---:|---:|---|
   | `canair.tui.apps.monitor` | 1,602 | 1,624 | `549e95f4` (+22) |
   | `canair.cli.commands.scan` | 618 | 624 | `816da70b` (+6) |

2. **The pre-commit hook does not run the structure tests.** `.pre-commit-config.yaml` runs
   `lint-imports`, but not `tests/contracts/test_structure.py`. CI runs the whole suite, so CI
   finds a size breach after the commit exists. `AGENTS.md` says that pre-commit runs
   `make structure`. That is not true today.
3. **Charters and docstrings that are out of date:**
   - `safety/__init__.py` names `modes/action.py`. The file is `live/action.py`.
   - The summary line of `transport/__init__.py` says "Alternate CAN transports for canair
     (raw-CAN backends)". It is the only transport package, and it holds the ELM327 path too.
   - `live/_monitor_run.py` says that `mode_monitor` "is re-exported from
     `canair.live.monitor` for backward-compatible imports". Rule 9 of the architecture page
     forbids compatibility aliases. The re-export is a module `__getattr__` at the end of
     `live/monitor.py`, and it also hides an import cycle between the two modules.
4. **`AGENTS.md` uses module names from before the package move:**
   `capture_store.load_all_captures`, `captures_merge.session_key`,
   `captures_merge.session_anchor`, `capture_migrate.py`, `capture_io.parse_capture_text` and
   `capture_io.capture_rx`. The modules are now `captures/store.py`, `captures/merge.py`,
   `migrations/` and `captures/io.py`.
5. **The contributing-code skill has stale paths:** `src/canair/captures_merge.py`,
   `src/canair/capture_io.py` and `tests/test_safety.py`.
6. **The placement table in `docs/development/architecture.md`** sends code to
   `live/<family>/`, `tui/apps/<app>/` and `tui/kit/`. Only `live/dtc/` exists. The table
   describes the target of the layout plan, not the tree.
7. **`analysis/xanalysis.py`** has a name that does not say what it does. Its docstring says
   "Cross-signal analysis engine: correlation matrix + byte hunting". It is the shared core of
   `correlate` and `hunt`.
8. **`src/canair/cli/commands/_live/`** is an untracked directory that holds only `.pyc` files,
   left over from the move to `cli/live/`.

## Steps

One commit for each step.

1. **Bring the two modules back under their ceilings.** Do not raise a ceiling.
   - `tui/apps/monitor.py`: move `SaveDialog` and `EditParamDialog` (about 290 lines) to a new
     module `tui/apps/monitor_dialogs.py`. This is a move only. Sub-plan 5 later moves the file
     into `tui/apps/monitor/`.
   - `cli/commands/scan.py`: read the diff of `816da70b` and take out one concern that is not
     flag parsing. If nothing fits, merge two import lines. Lower both ceilings with
     `uv run python -m tests.contracts.test_structure --write`.
2. **Add the structure tests to the pre-commit hook.** Add a commit-stage hook that runs
   `uv run pytest -n0 -q tests/contracts/test_structure.py`. It takes about 3 s. Give it
   `types_or: [python, pyi, toml, json]` and `pass_filenames: false`. Then the `AGENTS.md`
   sentence is true.
3. **Correct the charters and the docstring.** Correct `safety/` and `transport/`. Point the
   two callers of `canair.live.monitor.mode_monitor` (`cli/live/handlers/multi.py` and
   `live/raw_monitor.py`) at `canair.live._monitor_run`. Delete the module `__getattr__` and the
   "backward-compatible" sentence. Regenerate the architecture page. The CLI then imports a
   module whose name starts with an underscore. That is temporary: sub-plan 5 renames it to
   `live/monitor/run.py`.
4. **Correct `AGENTS.md` and the skill.** Replace the six stale dotted names and the three stale
   paths. Do not restructure `AGENTS.md` here. Sub-plan 10 does that.
5. **Correct the placement table.** Write the rows as the tree is today. Add one line that
   points at sub-plan 5 for the `live/` and `tui/apps/` sub-packages. Remove `tui/kit/`
   (decision C of the umbrella plan).
6. **Rename `analysis/xanalysis.py` to `analysis/cross_signal.py`.** This is a pure rename, with
   a codemod for imports and `monkeypatch` strings. Rename its test file to match.
7. **Delete `src/canair/cli/commands/_live/`.** It is untracked, so no commit is needed. Do it
   in the main checkout and in each worktree.
8. **Add a path check for the agent documents.** Write `scripts/check/doc_paths.py`. For
   `AGENTS.md`, `.claude/skills/*/SKILL.md` and `docs/development/*.md`, it checks that:
   - every `src/canair/…/x.py` path exists;
   - every `path::symbol` reference names a top-level name in that module (an AST scan);
   - every `tests/…` path exists.

   Run it in CI and at pre-push, beside `gen_architecture.py --check`. Skip `plans/` and
   `CHANGELOG.md`, because those are historical records. Start with no baseline: steps 4 and 5
   leave nothing to report. If there is a match that is not in scope, fix the text, not the
   check.

## Acceptance

- `make structure` passes on the branch.
- `scripts/check/doc_paths.py` reports nothing.
- `scripts/gen/gen_cli_reference.py --check` reports no change.
- The new hook fails a commit that makes a module grow past its ceiling. Check this once by hand
  with a throwaway commit.

## Out of scope

- The bare dotted names in `AGENTS.md` that are not paths (for example `MonitorController.rebind`).
  The path check cannot resolve them reliably. Sub-plan 10 removes most of them.

## As built

- **Step 1** was already done on the integration branch before this work started:
  `tui/apps/monitor_dialogs.py` and `monitor_overlays.py` exist, `monitor.py` is 919 lines and
  `cli/commands/scan.py` is 618. `make structure` passed at the start, so no commit was needed.
- **Step 2.** The `structure-tests` hook runs `uv run pytest -n0 -q
  tests/contracts/test_structure.py` (2.6 s). A throwaway commit that added 12 lines to
  `analysis/counters.py` failed in this hook, as the acceptance list asks.
- **Step 3.** As planned. `live/monitor.py` lost its `__getattr__` (15 lines).
- **Step 4.** The paths in `AGENTS.md` now start with `src/canair/`, so the path check covers
  them.
- **Step 5.** The row for `cli/commands/` says "`<cmd>.py`, or `<cmd>/` when it has several
  modules", because most commands are one file.
- **Step 6.** One commit. The oversize-baseline key and the test file moved with the module. The
  test file is now `tests/analysis/test_cross_signal.py` (it was in `tests/cli/commands/`, where it
  did not mirror the code). Codemod: `codemods/p1/rename_xanalysis.sh` (in the shared temporary
  directory).
- **Step 7** did not apply: the directory does not exist in the worktree.
- **Step 8.** `scripts/check/doc_paths.py` runs in CI, at pre-push, in `make structure` and as
  a test (`tests/scripts/test_doc_paths.py`, which also scans the repository). It started with
  235 `src/canair` references and found 5 stale ones in two skills and `docs/development/index.md`
  (the other stale paths were fixed by steps 4 and 6). It checks only paths that start with
  `src/canair/` or `tests/`, so a relative form such as `captures/io.py::x` is not checked.
