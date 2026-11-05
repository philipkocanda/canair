# Code organization, part 2: deferred imports

Status: **PROPOSED** (2026-11-05). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 1.

## Goal

Make rule 8 of the architecture page true: "A deferred (function-level) import needs a reason: a
measured start-up cost or an optional dependency, said in a comment. A deferral that only hides
a cycle is a layering bug."

Today the rule has a ceiling (`deferred_internal_imports: 1143` in `baseline.json`) but no check
for the reason. So the count stays where it is.

## Findings (at `0ce4f742`)

- `scripts/check/import_graph.py` counts **1,143 deferred internal imports in 249 files**. An
  AST scan of function-level imports only finds 1,079. About 42 of these have a comment on the
  same line or the line before.
- By package: `cli/` 505, `live/` 169, `validation/` 69, `yaml_edit/` 59, `captures/` 46,
  `dtc/` 38, `ecus/` 24, `transport/` 23.
- **Most of them save no start-up time.** `cli/commands/__init__.py::iter_command_modules`
  imports every command module to build the parser. That loads **343 of the 484 canair
  modules** in about 160 ms. **837 of the 1,079** function-level imports name a module that is
  already loaded at that point. In `cli/`, it is 362 of 505.
- `uv run canair --help` takes about 0.24 s in total.
- The third-party packages that the parser build does **not** load are `textual` (about 65 ms
  to import `textual.app`), `cantools` (about 56 ms) and `argcomplete` (about 7 ms). `rich`,
  `ruamel.yaml`, `python-can`, `jsonschema`, `requests` and `websockets` are already loaded.
  There are 27 deferred `rich` imports and 23 deferred `json` imports. These save nothing.
- Some deferrals hide a cycle. Known examples: `ecus/pids.py` imports `canair.profile` in five
  functions (sub-plan 3). `profile/__init__.py` imports `canair.ecus.pids` in `set_active` and
  `replace_active` (sub-plan 3). `live/monitor.py` has a module `__getattr__` that imports
  `_monitor_run` (sub-plan 1 removes it).

## Design

### Classify every deferred import

Extend `scripts/check/import_graph.py` with a `--deferred` report. For each function-level
import, it prints one of these kinds:

| Kind | Test | Action |
|---|---|---|
| `loaded` | The target is in the set of modules that building the parser loads | Hoist it to module level |
| `heavy` | The target is `textual`, `cantools`, `argcomplete`, or a canair module that imports one of them | Keep it, with a `# deferred:` comment that gives the measured cost |
| `cycle` | Hoisting it makes `import canair.<module>` fail, or makes `lint-imports` fail | Leave it, and list it in the sub-plan that removes the cycle |
| `other` | Anything else | Decide by hand |

The `loaded` set must come from a real run (`iter_command_modules()` then `sys.modules`), not
from a static guess.

### The reason comment

A deferred import that stays has a comment on the same line or the line before it, in one fixed
form:

```python
    # deferred: textual adds ~65 ms to start-up
    from canair.tui.apps.monitor import run_monitor_app
```

`# deferred: cycle with <package>, see plans/<plan>.md` is allowed while that plan is open.

### The gate

Add `deferred_imports_without_reason` to `baseline.json`, as a ceiling, at today's count. Add a
test in `tests/contracts/test_structure.py` that counts deferred imports with no `# deferred:`
comment, and fails when the count grows. Keep the existing total ceiling too, and lower both as
the work lands.

## Steps

1. Add the `--deferred` report. Commit the report output for `0ce4f742` in this plan's status
   line (counts per kind and per package).
2. Add the comment form, the new ceiling and its test.
3. **Hoist the `loaded` imports, one package per commit**, starting with the lowest layer
   (`core/` up to `cli/`). Use a codemod (LibCST). Do not change anything else in the commit.
   After each commit, run `python -c "import canair.<pkg>"` for every package, the full suite,
   and `ruff check`. `ruff --fix` can delete an import that the codemod broke. Read its output.
4. **Measure start-up after each package.** Record `canair --help` and the time to build the
   parser (`python -X importtime`). If one step adds more than 20 ms, revert that step and
   classify its imports as `heavy`.
5. **Comment the `heavy` imports.** Give each one its measured cost.
6. **List the `cycle` imports** in sub-plans 3, 4 and 6, at the step that removes each cycle.
   Those sub-plans hoist them.
7. Lower both ceilings.

## Decision A: a lazy command registry

A lazy registry would let `canair --help` and tab completion load only the parser declarations,
and load a command's implementation only when the command runs. Each command would need a light
module with its `add_parser` and a separate module with its `run`.

**Recommendation: not now.** The whole CLI starts in 0.24 s, and the parser build already loads
most of the package. Hoisting the `loaded` imports does not change what loads. Measure again
after step 4. If `canair --help` is over 0.4 s, write a separate plan for the registry.

## Acceptance

- Every deferred import that stays has a `# deferred:` comment.
- `deferred_imports_without_reason` is 0, or lists only the `cycle` imports that sub-plans 3, 4
  and 6 remove.
- `canair --help` is not slower than 0.30 s on an idle machine.

## Risks

- **Import order changes.** A hoisted import runs at module load. A module that does work on
  import, or a test that patches a name before import, can behave differently. The full suite
  and the per-package import check find most cases.
- **Order-dependent test failures.** The layout handoff describes them. If a full run fails and
  the failing test passes alone, bisect the worker's file order.
