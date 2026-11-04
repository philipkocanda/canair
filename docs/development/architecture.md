# Architecture: where code belongs

canair is one CLI over a stack of layers. A layer may import from the layers below it and never
from the layers above. This page says what each package is for and where new code goes. The
rules behind it, and the migration that gets the tree there, are in
[`plans/2026-11-04-package-layout-and-layering.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-11-04-package-layout-and-layering.md).

!!! note "Migration in progress"
    The tree is moving from a flat `canlib/` of 52 loose modules to the layout below. Until a
    package has moved, put new code in the package listed in the **Until it moves** column. The
    `import-linter` contracts in `pyproject.toml` already enforce the boundaries that hold today.

## Layers

From the interface at the top to the leaf at the bottom.

| Layer | Packages (target) | Role |
|---|---|---|
| Interface | `cli/`, `tui/`, `render/` | Parse flags, run Textual apps, turn results into text. The only code that imports `argparse` (`cli/`) or `textual` (`tui/`). |
| Applications | `contribute/`, `interop/`, `yaml_edit/` | Work over profile data: open the upstream PR, read and write foreign formats, edit definitions. |
| Live operations | `live/` | One coroutine per job against the car (read, monitor, scan, DTC, actuate). Takes a `Terminal` and typed options. No `argparse`, no `textual`. |
| Reaching the bus | `transport/` (`elm327/`, `raw/`), `device/` | Move bytes and frame ISO-TP. Manage the adapter: its HTTP API, firmware, protocol mode, connection lock. |
| Data | `analysis/`, `captures/`, `dtc/`, `query/` | Offline computation, the recording store, the DTC log, the selector language. |
| The car | `ecus/`, `states/`, `variants/`, `broadcast/`, `actions/`, `validation/`, `profile/`, `privacy/`, `safety/` | What a profile says and where it is. What may be sent. What must never leave. |
| Protocol | `expression/`, `uds/` | Wire-format knowledge with no I/O and no data store. |
| Environment | `config/`, `install/`, `migrations/` | User config, the install and its updates, one-time data migrations. |
| Leaf | `core/` | Generic helpers. Imports nothing from the package. |

## Where does my code go?

| If the code … | Target | Until it moves |
|---|---|---|
| defines flags, reads `args`, prints a command's output | `cli/commands/<cmd>/` | `commands/<cmd>/` |
| is a Textual app, screen or widget | `tui/apps/<app>/` (shared: `tui/kit/`) | `tui/` for shared widgets; the app next to its command or mode |
| turns results into text for both CLI and TUI | `render/` | `formatting.py`, or the mode's `*_render.py` |
| talks to the car over a `Terminal` | `live/<family>/` | `modes/` |
| moves bytes, frames ISO-TP, drives an ELM327 | `transport/` | `transport/` |
| calls the adapter's HTTP API, or guards the connection | `device/` | `wican/`, `lock.py` |
| decides whether a request may go on the bus | `safety/` | `safety.py`, `quirks.py`, `actuation.py` |
| describes protocol bytes with no I/O | `uds/` | `uds/` |
| evaluates a WiCAN expression | `expression/` | `expression/` |
| parses a profile's definition YAML | `ecus/`, `states/`, `variants/`, `broadcast/`, `actions/` | the same, plus `pids.py`, `signals.py` |
| finds, creates or layers a profile bundle | `profile/` | `profile.py`, `profile_create.py` |
| checks a profile | `validation/` | `commands/validate/` |
| edits a profile's YAML | `yaml_edit/` | `yaml_edit/` |
| reads or writes recordings | `captures/` (DTC log: `dtc/`) | `captures/`, `dtc/` |
| computes over captures or definitions, with no device | `analysis/` | `analysis/` |
| parses a selector or a query step | `query/` | `query.py`, `modes/multi_parse.py` |
| reads or writes a format canair does not own | `interop/` | `commands/import_*`, `dbc_repair.py` |
| opens or updates the upstream PR | `contribute/` | `contribute*.py` |
| masks, withholds or scrubs data | `privacy/` | `sensitive.py`, `pii.py` |
| reads or writes user config or the device registry | `config/` | `config.py` |
| reports the install, version or updates | `install/` | `install_context.py`, `update_check.py` |
| rewrites stored data once | `migrations/` | `migrations/` |
| is generic and needs nothing from the package | `core/` | `yaml_io.py`, `textscan.py`, `id_ranges.py` |
| is developer tooling that does not ship | `scripts/` | `scripts/` |

If two rows fit, choose the lower layer. If none fits, stop and ask: a new top-level package
needs a plan that names its layer.

## Rules

1. **No new module at the `canlib/` root.** Use the table above.
2. **Put code in the lowest layer that can hold it.** If it needs no device, it is not a live
   operation. If it needs no flags, it is not in `cli/`. If it does no I/O, it may be protocol or
   leaf code.
3. **Only the CLI imports `argparse`. Only the TUI imports `textual`.** Code below takes typed
   options and returns data.
4. **Never add an `ignore_imports` entry to make a contract pass.** Move the function down, pass
   the value in, or split the module. If that is impossible, ask the user. An accepted entry
   carries a one-line reason and a plan reference.
5. **A package's `__init__.py` docstring is its charter** (see below).
6. **Tests go in the directory that mirrors the code they test.** Tests with several subjects go in
   `tests/integration/`, repo-wide invariants in `tests/contracts/`, shared helpers in
   `tests/support/`. A test module never imports another test module.
7. **A new module stays under 500 lines.** Split a growing one by concern in the same change.
8. **A deferred (function-level) import needs a reason**: a measured start-up cost or an optional
   dependency, said in a comment. A deferral that only hides a cycle is a layering bug.
9. **A move is mechanical and alone**: `git mv` plus a codemod, one package per commit, no
   compatibility aliases, no logic change.

## Package charters

Every package's `__init__.py` docstring says what the package is, in this form:

```python
"""One-line summary.

Belongs: what code lives here.
Not here: what looks similar but lives elsewhere, and where.
Imports: the layers this package may import from.
"""
```

`tests/contracts/test_structure.py` requires a charter on every package that is not in its
baseline list. The baseline starts with every existing package and shrinks as packages gain a
charter or move.

## How the rules are enforced

| Gate | Catches | Runs |
|---|---|---|
| `uv run lint-imports` (`[tool.importlinter]` in `pyproject.toml`) | a wrong-way import, `argparse` or `textual` outside the interface, the protocol layer reaching a data store | pre-commit, CI |
| `tests/contracts/test_structure.py` | a new root module, a module over the size limit, more deferred imports, more `ignore_imports` entries, a package without a charter, a test importing a test | `pytest`, CI |
| `scripts/check/import_graph.py` | trend numbers: import cycles between packages, deferred imports, root modules, oversized modules | on request |

`make structure` runs the first two in a few seconds.

The two baselines work as ratchets. A list baseline (root modules, packages without a charter,
test-to-test imports) fails when the tree gains an entry and when an entry stops applying, so a fix
has to leave the list. A ceiling baseline (module sizes, deferred imports, `ignore_imports`) fails
when the number grows. Lower a ceiling with
`uv run python -m tests.contracts.test_structure --write`. That command never raises a baseline.
Raising one is a decision for the user and shows in the diff.
