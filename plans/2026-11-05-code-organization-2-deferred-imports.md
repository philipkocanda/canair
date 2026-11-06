# Code organization, part 2: deferred imports

Status: **DONE** (2026-11-06). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 1.
Deferred internal imports: 1,143 -> 84 in 70 files. `deferred_imports_without_reason` is 0.
What stays is a Textual import (about 40 ms each, measured), the stdlib-only imports of
`install/build_info.py`, and one `dtc/log_upgrade.py` import, each with a `# deferred:` reason.
Decision A stands: no lazy command registry. Start-up did grow, though: building the parser loads
637 canair modules (334 on 2026-11-03), and `canair --help` takes about 0.31 s (0.26 s before),
which misses the 0.30 s in Acceptance. The `parser_build_modules` ceiling in `baseline.json` now
holds the count. Per-group counts are in "As built".

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

## Tooling

Three scripts in `scripts/check/` and one in `scripts/dev/` carry steps 1 to 3. Tests are in
`tests/scripts/test_deferred_imports.py`.

- **Report.** `uv run python scripts/check/import_graph.py --deferred` prints the counts per kind
  and per package (`--depth 2` splits `cli` into `cli.commands`, `cli.args`, ...). `--files PKG`
  lists every import of a package or module with its kind (`cli.commands.scan`,
  `canair.live`, or a path). `--kind loaded` filters that list. `--no-trial` skips the import
  trial and calls every possible cycle a cycle. It runs in about 4 s.
  - The `loaded` set comes from a real run: a subprocess builds the parser
    (`canair.cli.build_parser()`) and prints `sys.modules`.
  - `cycle` means that hoisting the import joins a cycle of module-level imports. The check
    adds the module-level edge of every `loaded` import to the import graph and asks, for each
    one, whether its target reaches the importing module. So it also finds a cycle that two
    hoists close only together, which a try-it-alone test misses (hoisting the whole tree broke
    211 modules until it did). A package's load of its own submodule is not a cycle. Hoisting all
    `loaded` imports can never close a cycle, because each `cycle` import stays. The `why` text in
    a listing says whether the import also fails in a fresh interpreter when hoisted alone: the
    trial swaps the hoisted source in with an import hook (no file is written).
    `lint-imports` is not a separate test: grimp already counts function-level imports, so
    hoisting never changes its result.
  - `heavy` is decided statically: the target reaches `textual`, `cantools` or `argcomplete`
    through module-level imports.
- **Hoist.** `uv run python scripts/dev/hoist_deferred.py PKG [PKG...] [--write]` is a dry run
  unless `--write` is given. It hoists exactly the `loaded` imports of those packages, lets ruff
  sort the import block (an import from a module the file already imports merges into that line),
  keeps a trailing comment, deletes a `# deferred:` comment above a hoisted import and prints a
  `note` for any other comment above one (it may describe a cycle that no longer exists: reread
  it). It skips and explains: an import in `try`, a name that is also bound at module level or
  under `TYPE_CHECKING`, a name that two statements import from different places, a builtin name,
  a block it would leave empty, and a module that would pass its size ceiling. A second run finds
  nothing. After `--write` it imports every touched module and every module they now import in a
  fresh interpreter, and lists test lines that patch a hoisted name (`patch targets`): a test that
  patches `canair.a.name` no longer affects a module that imports `name` at the top, so patch the
  importing module.
- **The gate.** `deferred_imports_without_reason` in `baseline.json` counts function-level
  imports of canair modules with no `# deferred:` comment on a line of the statement or in the
  comment block that ends on the line above it. `test_deferred_imports_state_a_reason` fails when
  it grows; `test_structure --write` lowers it. An import under `if TYPE_CHECKING:`, in a
  module-level `try` or `if`, or in a class body is not counted (the older
  `deferred_internal_imports` ceiling still counts those, 75 of its 1,017).

Work through a package with: `--files PKG` (read), `hoist_deferred.py PKG --write`, then
`uv run ruff check . && uv run ruff format .`, `uv run ty check`, the tests of the package, and
`import_graph.py --deferred --files PKG` for what is left. Give each remaining import a
`# deferred: <reason>` comment (a measured cost, or `cycle with <package>, see plans/<plan>.md`).
Then run `uv run python -m tests.contracts.test_structure --write` to lower both ceilings.

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

## As built

Measured on `5f7c050c` (before) and after the pilot commit. The numbers differ from the findings
above because the scan now counts only imports inside a function (942; the old ceiling also counts
75 imports under `TYPE_CHECKING`, in module-level `try`/`if` blocks and in class bodies), and
because the tree moved since `0ce4f742`. Building the parser loads 539 of 658 modules.

| package | loaded | heavy | cycle | other | total | after the pilot (total) |
|---|---:|---:|---:|---:|---:|---:|
| cli | 294 | 4 | 2 | 147 | 447 | 447 |
| live | 111 | 0 | 0 | 35 | 146 | 146 |
| validation | 46 | 0 | 0 | 13 | 59 | 59 |
| yaml_edit | 34 | 0 | 0 | 19 | 53 | 53 |
| captures | 28 | 0 | 1 | 8 | 37 | 37 |
| dtc | 23 | 0 | 0 | 12 | 35 | 35 |
| analysis | 19 | 0 | 1 | 4 | 24 | 24 |
| transport | 15 | 0 | 5 | 2 | 22 | 22 |
| contribute | 15 | 0 | 0 | 2 | 17 | 17 |
| ecus | 10 | 0 | 4 | 2 | 16 | 16 |
| install | 9 | 0 | 4 | 2 | 15 | 15 |
| tui | 12 | 0 | 0 | 1 | 13 | 13 |
| actions | 9 | 0 | 0 | 1 | 10 | 10 |
| variants | 8 | 0 | 0 | 1 | 9 | 9 |
| device | 8 | 0 | 0 | 0 | 8 | **0** |
| expression | 5 | 0 | 0 | 0 | 5 | **0** |
| privacy | 5 | 0 | 0 | 0 | 5 | 5 |
| states | 5 | 0 | 0 | 0 | 5 | 5 |
| query | 4 | 0 | 0 | 0 | 4 | 4 |
| config | 2 | 0 | 0 | 0 | 2 | **0** |
| migrations | 2 | 0 | 0 | 0 | 2 | 2 |
| profile | 2 | 0 | 0 | 0 | 2 | 2 |
| render | 2 | 0 | 0 | 0 | 2 | 2 |
| safety | 1 | 0 | 0 | 1 | 2 | **0** |
| broadcast | 1 | 0 | 0 | 0 | 1 | 1 |
| uds | 1 | 0 | 0 | 0 | 1 | **0** |
| **total** | **671** | **4** | **17** | **250** | **942** | **924** |

- `core/` had none. The pilot moved 17 `loaded` imports in 8 files, plus one `other` by hand:
  `safety/blocklist.py` imported `safety.quirks`, which the parser build does not load but which
  costs 0.7 ms (it imports only `uds`). No deferred import is left in those six packages, so none
  needed a `# deferred:` comment.
- The gate counts: `deferred_internal_imports` 1,017 -> 999, `deferred_imports_without_reason`
  942 -> 924 (the new ceiling started at 942).
- Start-up, 40 alternating runs of `python -m canair --help` on a loaded machine: median 243.9 ms
  before, 243.7 ms after. The set of modules the parser build loads is identical except for
  `canair.safety.quirks`. `import canair.cli` takes about 27.5 ms in both.
- The 17 `cycle` imports sit in `cli/commands/captures/__init__.py` (2, importing its own
  submodules), `captures/save.py` (1), `analysis/physical/bands.py` (1), `ecus/{pids,registry}.py`
  (4), `install/{build_info,context}.py` (4) and `transport/{fallback,raw/terminal,raw/uds_client,
  elm327/addressing}.py` (5). Only `captures/save.py` and `analysis/physical/bands.py` fail to
  import when hoisted alone. The other 15 import fine alone but join a cycle with another hoist
  or with an existing module-level import: most are two-module cycles inside one package (for
  example `install/build_info.py` and `install/context.py` each defer an import of the other).
  Sub-plans 3, 4 and 6 do not name them, so whoever works on that package breaks the cycle (or
  comments the import `# deferred: cycle with <module>, see plans/<plan>.md`).
- `heavy` is only the four `cli` imports of a `tui/apps/*` Textual app. The 246 `other` imports
  are mostly modules that the parser build does not load (`dtc/log`, the `live/` runners, the
  `yaml_edit` editors). For each, measure the cost (`python -X importtime`) before commenting it
  or hoisting it. 55 of the `other` sit in a `try` block; the tool never moves those.
- Applying the tool to the whole tree in a scratch run (reverted, at `48cdafab`) hoisted 661
  imports in 221 files. `ruff`, `ty`, `lint-imports` and a fresh import of all 320 touched
  modules were clean. The suite then failed in about 110 tests. Those I read patch the old
  location of a hoisted name (`config.devices.fallback_settings`, `install.build_info`,
  `ecus.pids.load_pids` and the like). Some of those tests then touch real state: that run
  rewrote two bundled ECU files, and one `tests/live` run hung. So hoist one package at a time,
  fix its `patch targets` in the same commit, run its tests under `timeout`, and look at
  `git status` for stray files. The pilot alone failed 8 tests, which `patch targets` named.
- Group h4 (`validation`, `yaml_edit`, `captures`, `dtc`, `analysis`): 208 function-level imports
  (150 `loaded`, 2 `cycle`, 56 `other`) -> 1, which has a reason. All 56 `other` cost under
  4 ms (the whole `yaml_edit` router 2.7 ms), so they moved too, as did 8 in `try` blocks that
  only guarded a swallowed error. Three cycles broke with a small move each:
  `save_session_journaled` went from `captures/save.py` to `captures/oneshot.py` (the journal
  imports `save`; the plan 7 table that lists it under `save.py` needs that change), the format
  version and writer went from
  `dtc/log.py` to `dtc/log_format.py`, and the band table and label function went from
  `analysis/physical/bands.py` to `analysis/physical/default_bands.py`. The one import left,
  `migrate_all()` -> `log_file` in `dtc/log_upgrade.py`, says why. `canair --help`, 40 alternating
  runs against `a56df317`: median 258.8 ms before, 261.8 ms after. Ceilings: `without_reason`
  924 -> 716.
- Not done: steps 3 to 7 for the 20 packages with imports left (above), the `# deferred:`
  comments, and lowering the ceilings to the end state. Decision A (a lazy command registry)
  stays open: `canair --help` takes about 0.24 s.
- `live/` (group h3): 146 deferred imports (111 `loaded`, 35 `other`) -> 0, in 6 commits. The 35
  `other` cost about 1 ms each after the parser build (none reaches `textual`, `cantools` or
  `argcomplete`, and 23 sat in a `try` block that guards only the call), so all were hoisted by
  hand (`codemods/h3/hoist_other.py`, then ruff). No `# deferred:` comment was needed and no cycle
  was found. 14 test files patched a moved name; the one trap was `tests/integration/
  test_wican_firmware.py`, whose `wf.detect` patch must also reach `raw_ops.detect`, and which
  must import `raw_ops` first, or a first import under the patch binds the fake. `canair --help`:
  274.6 ms before, 273.2 ms after (40 alternating runs).
- `cli/commands` e to z (agent h2): 227 -> 2 function-level imports (142 `loaded`, 83 `other`,
  2 `heavy`), `deferred_imports_without_reason` 0 there. The 83 `other` were 71 outside and 12
  inside a `try` that guards the call, not the import (moved by hand). The 2 left are the Textual
  apps of `monitor` and `sniff`, commented `# deferred: textual adds ~40 ms`. Every hoisted
  target costs under 2 ms after the parser build (`python -X importtime`), but 40 more modules
  now load at parser build: `canair --help` goes from 278.5 to 290.8 ms (60 alternating runs,
  median, busy machine, base tree at `a56df317`). `investigate/support_map_report.py` imported
  `PidScan` from `support_map` while `support_map` imported the report: the deferral hid a cycle,
  fixed by importing `PidScan` under `TYPE_CHECKING`. A test that patches `canair.x.name` stops
  working once a command imports `name`: 12 test files now patch the new home, some of which had
  passed unchanged because the stub only guarded real state (a states test rewrote a bundled
  `vehicle_states.yaml` until its patch was fixed). `update` reads `canair.__version__` at call
  time, so a patched version still applies.
- Group h5 (`transport`, `contribute`, `ecus`, `install`, `tui`, `actions`, `variants`, `privacy`,
  `states`, `query`, `migrations`, `profile`, `render`, `broadcast`; `interop` had none): 152
  imports -> 3, none without a reason. The three left are in `install/build_info.py`, with a
  `# deferred:` comment: `hatch_build.py` loads that module by path without canair installed, so
  its top level imports only the standard library (two of them also close a cycle with
  `install/context.py`, which imports `build_info` at the top). The five `transport` and four
  `ecus` imports the report calls cycles were not cycles: the transport ones met only through
  the package `__init__` and import fine at the top. The `ecus` ones were real: `ecu_index_key`
  moved from `ecus/pids.py` to `ecus/names.py`, so `pids` no longer imports `names` and
  `registry` imports `pids` at the top. Start-up (40 alternating runs): 231.4 ms before,
  234.5 ms after.
- Group h1 (`cli/__init__`, `profile_context`, `cli/args`, `cli/live`, `cli/commands` `_edit`,
  `_hints`, `_promote` and the `a` to `d` commands): 213 function-level imports of canair
  modules before, 3 after, all three `heavy` (Textual, ~40 ms) with a `# deferred:` comment. 154
  were `loaded`, the rest `other`. The `other` imports pull 46 more modules into the parser build
  (`live.dtc`, `dtc.*`, `migrations.*`, `contribute.*`, `actions`); together they cost about 11 ms
  of import time, none above 1 ms, and `python -m canair --help` goes from a median of 225 ms to
  237 ms (30 alternating runs). The `captures/__init__.py` "cycle" went away: its own submodules
  now import at the top, after `.query`. Real cycle seen outside the group: `dtc/log.py` and
  `dtc/log_upgrade.py` import each other (now reported as `cycle`).
