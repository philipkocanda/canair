# Dependency upgrade: security re-lock, tool bumps and honest floors

Status: **DONE** (2026-11-06), one commit per phase on `main`. The on-device check at the end
is open. "Today" and the findings below describe the tree before the work. Each finding was
measured in a throwaway copy of the tree (the full suite, `ty`, `ruff`, `lint-imports`,
`mkdocs --strict`, `validate all` on every bundled profile, and DBC exports compared byte for
byte). Not every number was measured on CI's Linux runner.

Paths are relative to `src/canair/` unless they start with a top-level directory. Point at code
by file and symbol: line numbers drift.

## Why

- `uv audit` reports 35 known vulnerabilities in four locked packages. Two of them ship to users.
- A fresh install of canair already resolves `cantools` 45.1.2, and `canair export dbc` then
  fails on any CAN FD frame. `uv.lock` pins 42.0.3, so CI does not see this.
- The dev tools (`ruff`, `ty`, `pytest`) are several releases behind. Each one brings changes that
  fail a gate, so a plain `uv lock --upgrade` gives a red CI.
- Several floors in `[project] dependencies` are lower than the code needs. A user resolver may
  pick a version that cannot run canair.

## Baseline (current lock, current tree)

- Full suite: one failure, `tests/contracts/test_structure.py::test_deferred_imports_do_not_grow`
  (87 deferred imports, ceiling 84).
- `ty check`: one error, in `live/dtc/clear.py` (the return type of the clear function).

Both come from uncommitted work in the tree, not from dependencies. This plan does not fix them.
The new counts below exclude them.

## Findings

### 1. `export dbc` breaks with `cantools` ≥ 44 (affects users now)

`cli/commands/export.py::_dbc_specifics` imports two private names:

- `ATTRIBUTE_DEFINITION_VFRAMEFORMAT` from `cantools.database.can.formats.dbc`
- `DbcSpecifics` from `cantools.database.can.formats.dbc_specifics`

cantools 44.0.0 turned the `dbc` module into a package (its "DBC cleanup" parts 10–12). The
constant now lives in the private `dbc_loader` sub-module, and `formats/dbc_specifics.py` is gone.
The function runs only when a message is CAN FD, so the bundled profiles export without error,
but four tests fail with `ImportError`:

- `tests/cli/commands/test_dbc_roundtrip.py::TestFrameFormat` (two tests)
- `TestExport::test_little_endian_without_length_gets_smallest_fit` in the same file
- `tests/scripts/test_check_dbc_corpus.py::test_rich_dbc_round_trips_clean`

A fresh `uv pip install` of the clone resolves `cantools` 45.1.2 and reproduces the
`ImportError`, because an install does not read `uv.lock`. I checked this with `uv pip install`.
`uv tool install` probably resolves the same way, but I did not check it.

**Fix.** Build the attribute definition from the names that the `dbc` package exports in
`__all__` (`DbcAttributeDefinition`, `DbcSpecifics`), instead of importing a private constant.
The values are the ones cantools uses for `VFrameFormat`:

```python
from cantools.database.can.formats.dbc import DbcAttributeDefinition, DbcSpecifics

vframeformat = DbcAttributeDefinition(
    name="VFrameFormat",
    default_value="StandardCAN",
    kind="BO_",
    type_name="ENUM",
    choices=[...],  # the 16 names, from StandardCAN to ExtendedCAN_FD
)
```

With this change the DBC, signal and interop tests pass on 45.1.2 (314 passed). The DBC exports
of `hyundai-ioniq-ae-ev`, `hyundai-kona-os-ev` and `nissan-leaf-ze1-ev` are identical under 42.0.3
and 45.1.2. 43.0.2 does not export `DbcAttributeDefinition`, and 44.0.0 does, so the floor
becomes `cantools>=44`.

Other cantools changes up to 45.1.2 do not touch canair. 45.0.0 deprecates the diagnostics
(CDD) part, which canair does not use. 45.1.2 reverts a 45.1.0 change that made the DBC loader
stricter about unused `VAL_` entries.

### 2. Security advisories (`uv audit`)

| Package | Locked | Fixed in | Pulled in by | Ships to users |
|---|---|---|---|---|
| `certifi` | 2026.6.17 | 2026.10.18 | `requests` | yes |
| `urllib3` | 2.7.0 | 2.8.0 | `requests` | yes |
| `virtualenv` | 21.7.0 | 21.14.9 | `pre-commit` | no (dev group) |
| `filelock` | 3.32.0 | 4.2.1 | `virtualenv` | no (dev group) |

`websockets` 17.4 also fixes a denial of service: `recv()` now rejects a message split into more
than 4096 fragments. `uv audit` does not list it yet (its CVE is pending). After a full
`uv lock --upgrade`, `uv audit` reports no known vulnerabilities.

### 3. `pytest` ≥ 9.1 breaks the event-log tests (a latent bug in canair)

With pytest 9.1.1 or 9.2.0, all event-log tests in `tests/core/test_eventlog.py` read back an
empty log. 9.0.3 passes.

The cause is in canair. pytest 9.1 attaches its log-capture handler to every logger whose
`propagate` is `False`, and `core/eventlog.py::_get_event_logger` sets that on `canair.events`.
`_get_event_logger` then adds its `RotatingFileHandler` only `if not logger.handlers`. pytest's
handler is already present, so canair never opens its file. Any other library that attaches a
handler to the logger has the same effect in production.

**Fix.** Look for canair's own file handler, not for any handler. A check for a
`RotatingFileHandler` makes the event-log tests pass (9 passed). Also make the `isolated_log`
fixture close the handlers it removes. It clears them without a close, and that causes
`ResourceWarning: unclosed file` warnings.

Other pytest 9.2 changes that canair may notice: `indirect`, `ids` and `scope` of
`parametrize` are keyword-only, and parametrized tests may run in a different order. The suite
passes with both.

`pytest-asyncio` 1.4.1 prints `asyncio_default_fixture_loop_scope is unset` as a
`PytestDeprecationWarning`. Set it to `"function"` in `[tool.pytest.ini_options]`.

### 4. `ty` 0.0.63 → 0.0.99: 15 errors and 27 warnings, and warnings now fail

ty 0.0.99 exits with code 1 when it finds only warnings (`--exit-zero-on-warning` turns that
off). So CI fails on all 42 new diagnostics, not only on the errors.

Errors:

| Count | Rule | Where | Fix |
|---|---|---|---|
| 7 | `invalid-attribute-override` | `BINDINGS` in 9 files under `tui/` (28 sites) | Annotate `ClassVar[list[BindingType]]`, the type Textual declares. Tested: TUI and keymap tests pass (424). |
| 4 | `invalid-argument-type` | `contribute/summary.py::_key` and `_add`, `validation/captures.py::_has_spans` | They take `dict` and receive the `CaptureSession` TypedDict. Annotate `CaptureSession` or `Mapping[str, Any]`. |
| 1 | `invalid-argument-type` | `cli/commands/coverage/__init__.py`, `entry.update(analyze_pid(...))` | Make the entry type and `PidAnalysis` agree. |
| 3 | `invalid-argument-type`, `unsupported-operator` | `interop/explorer/layout.py`, the node dicts in the layout function | Give a node a `TypedDict`, so `lanes` is `list[int]` and `col` is `int`. |

Warnings:

- 18 `deprecated`: `@contextmanager` or `@asynccontextmanager` functions annotated
  `-> Iterator[...]`. Change them to `-> Generator[...]` (a mechanical change).
- 5 `redundant-condition`: in `captures/journal.py`, `captures/save.py`, `captures/store.py` and
  `live/monitor/record.py` (twice). Read each one: a condition that is always true can hide dead
  code.
- 1 `truthiness-test-of-iterable` in `analysis/support_fields.py`: an `Iterable[int]` used as a
  boolean. A generator is always truthy, so this may be a real bug.
- 2 `unused-ignore-comment` (`captures/build.py`, `core/yaml_rt.py`) and 1 `redundant-cast`
  (`core/id_ranges.py`): delete them.

### 5. `ruff` 0.15.11 → 0.17.4: one lint and ten Markdown files

canair selects its rules explicitly, so the new default rule set in 0.16 has no effect.

- **Lint.** `RUF036` (`None` must be last in a union) is stable from 0.16. It finds one site:
  `transport/elm327/addressing.py`, `str | None | _Unknown`. `ruff check --fix` corrects it.
- **Markdown.** From 0.16, `ruff format` also formats Python code blocks in Markdown. It checks
  227 more files, and 10 of them (all in `plans/`) would change. The changes remove deliberate
  column alignment of comments and add blank lines. The pre-commit hooks pass only `python` and
  `pyi` files to ruff, so this would fail in CI and never locally.

### 6. `websockets` 16 → 17.4: no code change

canair is a client (`transport/elm327/channel.py`). It calls `websockets.connect` with
`ping_interval`, `ping_timeout` and `open_timeout`, `send(str)`, `recv()`, and it catches
`ConnectionClosed` and `WebSocketException`. None of the 17.0 breaking changes apply: Python ≥
3.11, keyword-only `send(text=)` and `ping(ack_on_close=)`, `socket` renamed to `sock` in the
threading API. The full suite passes on 17.4. I did not test on a WiCAN device.

### 7. The declared floors are not true

I installed each floor alone, with every other package at its latest version, and ran the full
suite:

| Dependency | Floor | Result at the floor | Lowest that passed | Proposal |
|---|---|---|---|---|
| `textual` | `>=1.0` | more than 50 failures (the run stopped at `--maxfail=50`) | 4.0 (3.0: 5 TUI failures) | `>=4.0` |
| `rich` | `>=13.0` | Cannot install with `textual>=1.0` (needs ≥13.3.3). 13.3.3: 2 failures in `tests/render/test_sniff_table.py` | 14.2 (textual 8 also needs it) | `>=14.2` |
| `websockets` | `>=12.0` | 64 failures and errors in `tests/transport/`: no `websockets.asyncio` | 13.0 | `>=13.0` |
| `cantools` | `>=39.0` | see finding 1 | 44.0 | `>=44` |
| `jsonschema` | `>=4.18` | passes | — | keep |
| `ruamel.yaml` | `>=0.18` | passes | — | keep |
| `argcomplete` | `>=3.0` | passes | — | keep |

I did not test `python-can`, `can-isotp`, `pyyaml` or `requests`. `requests>=2.0` is from 2013,
so that floor is probably not true either. I tested each floor alone, not all floors together.

### 8. Low risk, no impact found

`argcomplete` 3.7.2, `import-linter` 2.16, `pre-commit` 4.7.0, `mkdocs-material` 9.8.0 (inside
the `<10` cap), and the transitive docs packages (`pymdown-extensions` 12, `backrefs` 8,
`watchdog` 7, `python-dateutil` 4). `mkdocs build --strict` passes. `lint-imports` keeps all 11
contracts. `validate all` passes on all six bundled profiles.

`wrapt` stays at 1.17.3. `python-can` 4.6.1 holds it below 2, and no action is needed.

### 9. GitHub Actions

| Action | Pinned | Latest | Breaking change |
|---|---|---|---|
| `astral-sh/setup-uv` | `v9.0.0` | `v10.3.3` | v10 turns off `enable-cache: auto` for `pull_request_target`, `workflow_run` and `release`. canair sets `enable-cache: true` and runs on `push` and `pull_request`, so this has no effect. |
| `googleapis/release-please-action` | `v4` | `v5.0.0` | Node 24 runtime only. |
| `actions/checkout` | `v7` | `v7.0.1` | none (the `v7` tag follows it) |
| `actions/upload-pages-artifact`, `actions/deploy-pages` | `v5` | `v5` | current |

## Decisions

1. **Markdown formatting.** Excluded from `ruff format` (`[tool.ruff.format] exclude =
   ["*.md"]`). Plans are records, and their code blocks are illustrations whose alignment is
   deliberate.
2. **Floors.** The lowest version that passed (finding 7), not the current major version. This
   keeps the floors true without excluding versions that work.
3. **ty dev floor.** `ty>=0.0.99`. The lock is the pin, the same as for `ruff>=0.17`, `pytest>=9.2`
   and `pytest-asyncio>=1.4`.
4. **GitHub Actions.** Updated in the same series.

## Phases (as built)

One commit per phase, Conventional Commits. release-please writes `CHANGELOG.md` from the commit
messages, so no phase edits it. The pre-commit hooks run `uv run`, which syncs the environment to
the lock at that commit, so each commit was checked with the tool versions it locks.

1. **`fix(export)`**: build the `VFrameFormat` definition from cantools' public names, floor
   `cantools>=44.0`, lock 45.1.2 (finding 1).
2. **`fix(deps)`**: security re-lock of `certifi`, `urllib3`, `websockets`, `pre-commit`,
   `virtualenv` and `filelock`. `uv audit` reports none afterwards.
3. **`fix(eventlog)`**: look for canair's own file handler, close the handlers the test fixture
   drops, and add a test that attaches a foreign handler first. The test fails without the fix.
   Then **`chore(deps)`**: pytest 9.2, pytest-asyncio 1.4, and
   `asyncio_default_fixture_loop_scope = "function"` (finding 3).
4. **`chore(deps)`**: ruff 0.17, `RUF036`, and the Markdown exclusion (finding 5).
5. **ty 0.0.99** (finding 4), in five commits so each change reads on its own:
   - `fix(analysis)`: `known_fields` converts to a frozenset before it tests for empty.
     Both callers already filtered, so no result changes.
   - `refactor`: drop conditions that cannot be false (sessions and capture files; the monitor
     recorder's `hex_history` and `save_history`, now always dicts).
   - `refactor(types)`: `Mapping[str, Any]` for the session helpers, a dict display for the
     coverage entry, and a `TypedDict` for the diagram nodes.
   - `refactor`: `-> Generator[...]` on every context manager.
   - `refactor(tui)`: `ClassVar[list[BindingType]]`.
   - `chore(deps)`: the bump and the `ty>=0.0.99` floor. The redundant cast and the two
     `ty: ignore` comments go here, because the older ty still needs them.
6. **`fix(deps)`**: `textual>=4.0`, `rich>=14.2`, `websockets>=13.0`. The whole suite passes with
   those three and `cantools` 44.0.0 installed together.
7. **`ci`**: `setup-uv` v10.3.3 and `release-please-action` v5.
8. **`chore(deps)`**: `uv lock --upgrade` for the rest (finding 8).

Differences from the proposal: the `known_fields` change is a `fix` in its own commit, as the
proposal required for a possible bug. The `redundant-condition` warnings turned out to be dead
conditions with no bug behind them, so they are one `refactor` commit.

## Verification

- `uv lock --check`, `uv audit`
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run lint-imports`
- `uv run pytest -q`, `make structure`, `make gen-check`, `make docs`
- `uv run canair --profile <name> validate all` for each bundled profile, with CI's ratchets
- After phase 1: a fresh `uv pip install` of the clone into a clean venv, then `canair export
  dbc` for a profile that has a CAN FD message. This is the path that CI does not cover. Done:
  the install resolves `cantools` 45.1.2, `_dbc_specifics` builds the definition without an
  `ImportError`, and the Ioniq export works. No bundled profile has a CAN FD message, so the dump
  itself is covered by `tests/cli/commands/test_dbc_roundtrip.py` only.
- After phases 2 and 6 (**open**): on a WiCAN Pro, `canair --transport wican-ws status` and one
  `read`, to check `websockets` 17 against the device. The suite has no socket access off the
  loopback.

## Out of scope

- **A CI job that tests the floors** (`uv sync --resolution lowest-direct`). It is the only way
  to keep floors true over time. Propose it separately.
- **Order-dependent DTC tests.** In two of about a dozen full runs, a test in
  `tests/dtc/test_dtc.py::TestLogOffline` or `tests/live/test_dtc_snapshot_mode.py::TestClear`
  failed once. Neither failure came back when I ran it again. Both files have uncommitted changes
  in the tree.
- **Other `ResourceWarning`s.** The suite emits about 150 `unclosed file` warnings with
  `-W default`. I did not trace them past the event-log fixture.
