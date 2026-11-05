# Package layout and layering: give `canlib/` seams, and tests the same shape

Status: **DONE for phases 0 to 7 except the items under "Left over" below** (phases 0 to 5 on
2026-11-04, branch `refactor/package-layout`; phases 6 and 7 on 2026-11-05, branch
`refactor/split-layer-cycle`). The package is `src/canair/` now; this plan keeps the `canlib/`
names it was written with. Not user-facing: no command, flag, output or profile changes.
Section 9 is the part that outlives the migration: the rules, documents and gates that keep
agents (and people) on this structure afterwards.

Related plans this one absorbs or depends on:

- `plans/2026-08-05-layering-and-module-size-followups.md`: Part A (validators live in the
  command layer) is phase 2 here. Part B (`captures/step_*`) is unchanged.
- `plans/2026-08-06-command-packages-and-live-split.md`: the "a command is one module until it
  needs two" rule stays. Its open items (`connect_elm_terminal` down to transport, the third
  hex-range parser) are phase 3 here.
- `plans/2026-08-06-signal-naming-convention.md`: the `param` → `signal` identifier rename. Do it
  **after** the moves, never interleaved, so no file is renamed and rewritten in one commit.

## Decisions (2026-11-04)

| # | Question | Decision |
|---|---|---|
| 1 | Nest `ecus/`, `states/`, `variants/`, `actions/`, `schema/`, `validation/` under one `definitions/` package? | **No.** They stay top-level. The loose definition modules move *into* them. |
| 2 | TUI apps central or next to their command? | **Central**, in `tui/apps/`. |
| 3 | Test layout | **Plain mirror of `canlib/`**, plus three reserved non-mirror directories (section 7 says why those three are needed). |
| 4 | `src/` layout and `canlib` → `canair` | **Yes, as phase 4b**, right after the moves and before the docs retarget (revised 2026-11-04 after section 11 was reviewed). |
| 5 | Name of the live layer | **`live/`** |

---

## 1. Findings (measured 2026-11-04, commit `5ce77212`)

### Where the code is

| Location | Files | Lines | Share |
|---|---:|---:|---:|
| `canlib/commands/` | 146 | 37,110 | 34% |
| `canlib/modes/` | 68 | 17,534 | 16% |
| `canlib/*.py` (loose root modules) | 52 | 14,512 | 13% |
| 18 subpackages (`analysis`, `captures`, `transport`, `uds`, `yaml_edit`, …) | 160 | 38,663 | 36% |
| **Total** | 426 | ~107,800 | |

Half the code is in the two interface/orchestration layers. Another 13% sits in 52 loose modules
at the package root, with no grouping: `safety.py` next to `ansi.py` next to `contribute.py` next
to `poll_backoff.py`.

### The dependency graph has no layers

Measured with an AST import scan over `canlib/` (Appendix A):

- **38 of the ~70 top-level units form one strongly connected component** (every one of them can
  reach every other one through imports). It contains `uds`, `transport`, `captures`, `analysis`,
  `pids`, `profile`, `states`, `variants`, `yaml_edit`, `modes`, `commands` and `cli`. A layered
  design has no cycles between layers. This tree has one large knot.
- **1,177 imports of `canlib` modules sit inside functions or `if` blocks**, in 242 files. Some are
  `TYPE_CHECKING` blocks or deliberate start-up deferrals. Most exist to break an import cycle.
- **`canlib/__init__.py` is a façade** that imports `transport.wican_terminal`, `formatting`,
  `ecus`, `pids`, `safety` and `log`. So `import canlib.core_thing` loads the WebSocket transport.
  23 modules import from the package root, which puts the root into the cycle.

Examples of edges that point the wrong way (a low layer imports a high layer):

| Importer | Imports | Problem |
|---|---|---|
| `uds/unsplit_batches.py` | `captures`, `analysis.misfiled`, `ecus`, `keepmode` | the "I/O-free protocol layer" reads the capture store |
| `uds/support_map.py` | `analysis.support_map` | protocol depends on analysis |
| `uds/notation.py` | `config` | protocol reads user config |
| `expression/decoding.py`, `captures/__init__.py` | `wican.autopid_layout` | the byte-index mapping lives in the WiCAN export package |
| `pids.py`, `states/__init__.py`, `formatting.py` | `modes.multi_batch` | definitions depend on the live layer (for types) |
| `yaml_edit/{ecu,ecufile,state_rename}.py` | `commands.validate` | editing depends on the CLI layer |
| `commands/{groups,pids,states}.py` | `cli.main` | a command re-enters the CLI entry point |
| `wican/api.py` ↔ `transport/config.py` | each other | device API and transport selection are one knot |
| `profile.py` ↔ `pids.py` ↔ `variants/` | each other | bundle resolution and definition loading are one knot |
| `config.py` | `yaml_edit.rt` | user config depends on the profile editor |

### Interface code is in three places

- Textual apps live in `commands/` (`_sniff_tui.py`, `captures/step_tui.py`, `decode/plot_tui.py`),
  in `modes/` (`_monitor_tui.py` 1,602 lines, `_monitor_select_tui.py`) and in `tui/` (the shared
  kit). There is no rule for where a new TUI goes.
- `argparse.Namespace` reaches below the CLI: `modes/dispatch/*`, `modes/raw_monitor.py`,
  `modes/raw_ops.py` and `captures/dates.py` read `args.*` directly.
- Text renderers live in `formatting.py` (759 lines, which also holds the UDS response describer),
  in `modes/_monitor_render.py`, `modes/discover_render.py`, `modes/_iocontrol_render.py`, in
  `dtc/snapshot_report.py` and in each command's `render.py`.

### Paths derived from the package location

`constants.py` sets `SCRIPT_DIR = PACKAGE_DIR.parent` and treats it as "the repo root". In a
checkout it is the repo root. In an installed wheel it is `site-packages/`. Three things follow:

- The wheel force-includes `profiles/`, `templates/` and `config.example.yaml` at the **root of
  `site-packages`**, so that `SCRIPT_DIR / "profiles"` resolves in both places. Every environment
  that installs canair gets a top-level `profiles` directory.
- `log.py` writes the per-ECU response logs to `SCRIPT_DIR / "logs" / "ecu"`. In an installed copy
  that is `site-packages/logs/ecu/`. The central event log already uses the config directory.
- The install-snapshot write loss that AGENTS.md describes (two sessions of captures deleted by a
  reinstall) has the same root: one path expression means two different places.

### Smaller defects found during the survey

- `canlib/pids_edit/` is an untracked directory that holds only `__pycache__` (a leftover from the
  move to `yaml_edit/pids/`).
- `scripts/migrate_states_status.py` is a user-facing migration. `validate pids` tells the user to
  run it, but it is a loose script, not a `canair` command or a `canlib/migrations/` module.
- `tests/` imports from other test modules (`from .test_captures_step`, `from tests.test_search`,
  `from tests.test_terminal`). Shared test code belongs in a helper module.
- CI never builds or installs the wheel. Every test runs against the checkout, so a file that is
  missing from the wheel is found by a user, not by CI.

### Tests

`tests/` is flat: **306 files**, ~6,900 test functions, plus `fixtures/` and `js/`. Names follow
the module loosely (`test_elm327_pipe.py`, `test_monitor_record.py`), but nothing maps a test to its
subject. Classified by the package each test file imports most:

| Subject | Test files |
|---|---:|
| `commands.*` | 58 |
| `canlib` root façade (subject unclear) | 30 |
| `transport` | 25 |
| `modes.*` | 22 |
| `captures` | 19 |
| `yaml_edit` | 18 |
| `uds` | 17 |
| `analysis` | 16 |
| ~35 other packages and root modules | ~100 |

66 test files import from more than three top-level packages. Those are integration tests that
are named and placed like unit tests.

---

## 2. What `modes/` is, and why it feels arbitrary

`modes/` is the **live-device operation layer**: each `mode_*` coroutine takes a connected
`Terminal` and runs one operation against the car (read a PID set, poll a monitor, sweep a range,
read DTCs, actuate an output). The name is a fossil of the original single-script CLI, where one
argument namespace carried mode flags (`--scan`, `--monitor`, `--dtc`, `--identity`) and the script
picked a mode by which flag was set.

That design still runs today:

1. Each live command (`read`, `monitor`, `scan`, `io`, …) builds a partial namespace.
2. `commands/_live/defaults.py` backfills **60 attributes** so every namespace has every mode
   selector (`param`, `ecu`, `raw`, `scan`, `skm_wakeup`, `identity`, `discover`, `dtc`, …).
3. `commands/monitor.py` sets `args.multi` and `args.monitor = args.interval`, so the dispatcher
   will route it to the monitor.
4. `run_live` connects, then `modes/dispatch/__init__.py::dispatch_mode` walks a table of 16
   `(handler, predicate)` pairs over the namespace. **Order matters**: `multi`+`monitor` must be
   tested before bare `multi`.

So a subcommand that already knows which operation it wants encodes that choice as flags, and a
dispatcher decodes it again. That indirection is the main reason the boundary between `commands/`
and `modes/` is hard to see.

`modes/` also mixes four kinds of code that have different dependencies:

| Kind | Examples |
|---|---|
| Device orchestration (the real content) | `multi_exec.py`, `monitor.py`, `scan.py`, `iocontrol.py`, `dtc/`, `discover.py` |
| Pure logic with no I/O | `multi_parse.py` (step grammar), `multi_batch.py`, `monitor_select.py`, `_monitor_state.py` |
| Presentation | `_monitor_tui.py`, `_monitor_select_tui.py`, `_monitor_render.py`, `discover_render.py`, `_iocontrol_render.py` |
| CLI glue | `dispatch/*` (reads `args.*`), `raw_ops.py`, `raw_monitor.py` |

The package becomes `live/` (decision 5). It keeps only the first kind. The other three go to
`query/`, `render/`, `tui/apps/` and `cli/`.

---

## 3. Principles

These follow common Python practice for an application package (the PyPA packaging guide, the
layered "ports and adapters" style, and what tools such as `import-linter` enforce).

1. **Group by responsibility, then name the package after it.** A reader who knows what the code
   does should be able to guess its path. No `utils`/`misc` bins: a leaf of generic helpers is
   `core/`, and it has a contract (it imports nothing from `canlib`).
2. **Layers, enforced by a tool.** Dependencies point down. `import-linter` checks the contract in
   CI and in the pre-commit hook, so the layering cannot decay silently again. Siblings in one
   layer are independent unless the contract says otherwise.
3. **The interface is a thin outer layer.** Only `cli/` imports `argparse`. Only `tui/` imports
   `textual`. Operations below take typed arguments (dataclasses or keyword arguments) and return
   data. Renderers turn data into text.
4. **Move loose modules into existing homes before you invent new packages.** `uds/`, `captures/`,
   `analysis/`, `transport/`, `ecus/`, `states/`, `variants/` and `yaml_edit/` are good packages
   already. Most of the work is to give the 52 root modules a home and to cut the wrong-way edges.
5. **Tests mirror the source tree at package level.** The tests for
   `canlib/transport/elm327/pipe.py` are in `tests/transport/elm327/`. File-for-file mirroring is
   not required.
6. **A package `__init__.py` exports the package API.** It is not a compatibility shim. The root
   `canlib/__init__.py` exports only `__version__`.
7. **No compatibility aliases for moved modules.** canair is an application, not a library: no
   external code imports `canlib.*`. Every call site is rewritten in the commit that moves a module
   (the same rule the captures split used). The one external entry point,
   `canair = "canlib.cli:main"`, stays valid.

---

## 4. Target layout

Layers from top (interface) to bottom (leaf). A module may import from its own layer only where the
contract allows it, and from any layer below.

```text
canlib/
├── __init__.py          __version__ only
├── __main__.py          python -m canlib
│
│   ── interface ───────────────────────────────────────────────────────────────
├── cli/                 the only argparse user
│   ├── __init__.py      main()  (today cli.py)
│   ├── commands/        today's commands/, one module or package per subcommand
│   ├── args/            shared flag helpers: _join, _can_args, _hexarg, _group, _categories,
│   │                    _domain, _hints, scope flags (from captures/dates.py), live parser
│   ├── live/            today's commands/_live: build options, connect, run an operation
│   └── prompts.py       first_run, grid_prompt (interactive questions on a TTY)
├── tui/                 the only textual user
│   ├── kit/             today's tui/: keys, help, modals, scroll, status, widgets, skip_key
│   └── apps/            monitor/, monitor_select/, captures_step/, decode_plot/, sniff/, iocontrol/
├── render/              text and Rich renderers shared by cli/ and tui/ (display half of
│                        formatting.py, monitor/discover/iocontrol/snapshot renderers, ansi palette)
│
│   ── applications over data ─────────────────────────────────────────────────
├── contribute/          contribute, contribute_source, contribute_logs, contribute_summary,
│                        the gates (from commands/_contribute_gates.py)
├── interop/             external formats: dbc/ (repair, import, export), frame_log (today
│                        captures/from_log), autopid/ (today wican/autopid_*), explorer/
├── yaml_edit/           unchanged: the one edit funnel for definitions
│
│   ── live device operations (today's modes/) ───────────────────────────────
├── live/                no argparse, no textual; each operation takes a Terminal + options
│   ├── session.py       run_session_guarded and error classification (today dispatch/__init__)
│   ├── revisions.py     revision_probe
│   ├── frame_counts.py  response_frames write-back
│   ├── read/            multi, multi_exec, multi_batch, multi_repl, param, raw
│   ├── monitor/         controller, record, reconnect, link_events, selectors, stats, state,
│   │                    schedule (poll_schedule), backoff (poll_backoff)
│   ├── scan/            range, sessions, routines, iocontrol (UDS + KWP), discovery_scan,
│   │                    presets (scan_presets), progress (scan_state)
│   ├── discover/        discover, functional sweep
│   ├── identity/        identity, records, decode
│   ├── dtc/             today's modes/dtc/
│   ├── actuate/         iocontrol, routines, action run
│   └── elm/             repl (interactive), skm_wakeup, tester
│
│   ── framework: reaching the bus ────────────────────────────────────────────
├── transport/
│   ├── protocol.py channel.py errors.py stats.py timeouts.py link_latency.py keepalive.py
│   ├── config.py fallback.py functional.py timing.py session_manager.py wake.py
│   ├── elm327/          terminal, pipe, frame_count, addressing, session, eeprom, commands,
│   │                    functional, tcp, wican (today wican_terminal.py)
│   └── raw/             slcan_tcp, isotp_stack, isotp_params, terminal (raw_terminal),
│                        uds_client (uds_raw), functional (functional_raw)
├── device/              the adapter as a managed device, not a byte pipe
│   ├── wican/           api, firmware, mode
│   ├── lock.py          connection mutex (today lock.py)
│   └── lock_watchdog.py
│
│   ── framework: data ────────────────────────────────────────────────────────
├── analysis/            pure offline analysis (today) plus: the compute halves of commands
│                        (correlate calc, decode calc, coverage analysis, search findings,
│                        investigate counters/support_map bridges, research gaps),
│                        state inference (today states/infer.py), physical/ (physical_bands,
│                        grid_regions, unit_guess)
├── captures/            the capture store (today) plus keepmode, can_logs (domain-B store);
│                        no argparse (scope flags move to cli/args)
├── dtc/                 unchanged: a vertical domain (definitions, log, snapshots, history)
├── query/               the selection language: selectors (query.py), steps (multi_parse),
│                        @group expansion (ecus/ecu_groups.expand_group_refs)
│
│   ── framework: the car (decision 1: these stay top-level) ─────────────────
├── ecus/                today's ecus/ plus the PID definition loader (today pids.py),
│                        can_buses, signal_category, did_families
├── broadcast/           domain-B definitions: the signals/<bus>.yaml loader (today signals.py)
├── states/              vocabulary, predicates, refs, spans (inference moves to analysis/)
├── variants/            unchanged
├── actions/             unchanged (the model; the live run is live/actuate/)
├── validation/          absorbs the validators from commands/validate/
├── schema/              unchanged (data files, no Python)
├── profile/             where a profile is: resolve, layers, bundle members, names, scaffold
│                        (profile_create), blind strip (blind.py)
├── privacy/             sensitive (mask/withhold), pii (contribute gate)
├── safety/              what may be sent: blocklist and session policy (safety.py), quirks,
│                        actuation classifier
│
│   ── protocol and decoding (I/O-free) ───────────────────────────────────────
├── expression/          unchanged: the WiCAN expression language
├── uds/                 unchanged scope plus addressing, frame_counts (the ledger), the UDS
│                        response describer (from formatting.py), the byte mapping (today
│                        wican/autopid_layout.py)
│
│   ── environment ────────────────────────────────────────────────────────────
├── config/              user config (today config.py) plus devices.py (the device registry
│                        that both transport/ and device/ read)
├── install/             install_context, update_check, build_info
├── migrations/          unchanged, plus the two loose scripts/migrate_states_*.py
│
│   ── leaf ───────────────────────────────────────────────────────────────────
└── core/                imports nothing from canlib: yaml_io, textscan, id_ranges,
                         paths (today constants.py), eventlog (today log.py), stop_signals
```

The root holds 2 modules instead of 52.

### Where each loose root module goes

| Module | Target |
|---|---|
| `actuation` | `safety/actuation.py` |
| `addressing` | `uds/addressing.py` |
| `ansi` | `render/ansi.py` (or `core/` if non-render code still needs it) |
| `blind` | `profile/blind.py` |
| `build_info`, `install_context`, `update_check` | `install/` |
| `can_buses`, `did_families`, `pids`, `signal_category` | `ecus/` |
| `signals` | `broadcast/__init__.py` |
| `can_logs`, `keepmode` | `captures/` |
| `cli` | `cli/__init__.py` |
| `config` | `config/__init__.py` (+ `config/devices.py`) |
| `constants` | `core/paths.py` (the URLs move to `install/` or stay with paths) |
| `contribute*` | `contribute/` |
| `dbc_repair` | `interop/dbc/repair.py` |
| `first_run`, `grid_prompt` | `cli/prompts.py` (pure parts to `profile/` and `config/`) |
| `formatting` | split: `uds/describe.py` + `render/` |
| `frame_counts` | `uds/frame_counts.py` |
| `grid_regions`, `physical_bands`, `unit_guess` | `analysis/physical/` |
| `id_ranges`, `textscan`, `yaml_io`, `stop_signals` | `core/` |
| `lock`, `lock_watchdog` | `device/` |
| `log` | `core/eventlog.py`; the per-ECU log moves from `SCRIPT_DIR/logs` to the config dir |
| `pii`, `sensitive` | `privacy/` |
| `poll_backoff`, `poll_schedule` | `live/monitor/` (both stay leaves, see note below) |
| `profile`, `profile_create`, `profile_names` | `profile/` |
| `query` | `query/selectors.py` |
| `quirks`, `safety` | `safety/` |
| `response_frames` | `live/frame_counts.py` |
| `scan_presets`, `scan_state` | `live/scan/` |
| `session_manager`, `timing`, `wake` | `transport/` |

Note on `poll_schedule`: the parser imports its vocabulary at start-up, so it must stay free of
heavy imports. A module in `live/monitor/` can stay a leaf, but `live/monitor/__init__.py` must not
import the controller. If that is too fragile, keep the vocabulary in `query/` or `cli/args/`.

---

## 5. The seams

Each seam is a boundary with a one-sentence rule. The rules go into `import-linter` (section 6) so
CI checks them.

1. **Interface | everything else.** Only `cli/` sees `argparse`. Only `tui/` sees `textual`. Code
   below takes typed options and returns data. `tui/` never imports `cli/`. `cli/` launches `tui/`
   apps.
2. **Presentation | operation.** An operation returns results. `render/` turns results into text
   for both interfaces. This removes the `formatting` → `modes` edge and the TUI files from
   `modes/`.
3. **Command selects operation directly.** The flag namespace and `dispatch_mode` go away. Each
   live command passes its operation to the runtime, for example
   `run_live(args, operation=live.monitor.run, options=MonitorOptions(...))`. `CANAIR_DEFAULTS`
   shrinks to the transport and session options that every live command really shares. This is the
   largest behaviour-neutral change in the plan and it is phase 3.
4. **Live operation | transport.** Operations see only the `Terminal` protocol (already true and
   checked by `ty`). Transport never imports `live/`.
5. **Transport | device.** `transport/` moves bytes. `device/` manages the adapter (HTTP API,
   firmware generation, protocol mode, connection mutex). Both read `config/devices.py`. This
   removes the `wican.api` ↔ `transport.config` cycle.
6. **ELM327 | raw.** `transport/elm327/` and `transport/raw/` are independent siblings. Shared
   policy (`keepalive.py`, `stats.py`, `link_latency.py`) lives in the transport base, so a second
   copy of a policy in one sibling shows up as a contract violation.
7. **Safety is one package.** Everything that decides "may this request go on the bus" (blocklist,
   session rule, functional allow-list, quirk grants, actuation classifier) is in `safety/`.
   Transport and live depend on it. It depends only on `uds/` and the definition packages.
8. **Protocol is I/O-free and data-free.** `uds/` and `expression/` import only `core/`. The
   offending functions move up: `unsplit_batches`' capture walk to `captures/` or `analysis/`, the
   support-map scoring stays in `analysis/`, the byte-index mapping moves down from
   `wican/autopid_layout.py` into `uds/`, and `notation.py` gets its default from the caller instead
   of reading `config`.
9. **Definitions | the store | analysis.** The definition packages (`ecus/`, `states/`, `variants/`,
   `broadcast/`, `actions/`) parse a profile's YAML. `captures/` reads and writes recordings.
   `analysis/` computes over both. `states/infer.py` (backfill from captures) moves to `analysis/`.
   The vocabulary and the Kleene predicates stay in `states/`.
10. **Editing | validation.** `yaml_edit/` calls `validation/`, never `cli/commands/validate`. This
    is Part A of the layering follow-ups plan.
11. **Interop is one package.** Everything that reads or writes a format canair does not own (DBC,
    frame logs, WiCAN AutoPID JSON, the docs-site explorer JSON) is in `interop/`.
12. **Commands own parse + render + glue only.** A command package holds `parser.py`, `render.py`
    and an entry module. Anything a test would call without the CLI belongs in the library. This
    extends the rule from the command-packages plan, and moves about 5k lines of compute out of
    `commands/` (correlate `calc`, decode `calc`/`analysis`, coverage `analysis`/`payloads`,
    search `findings`/`haystack`/`needle`, investigate `counters`/`support_*`, research `gaps`).

---

## 6. Enforcement: `import-linter`

Add `import-linter` to the `dev` group, a `[tool.importlinter]` section in `pyproject.toml`, a
pre-commit hook and a CI step. Sketch (names follow section 4):

```toml
[tool.importlinter]
root_package = "canlib"
unmatched_ignore_imports_alerting = "error"   # a fixed edge must leave the ignore list

[[tool.importlinter.contracts]]
name = "layers"
type = "layers"
layers = [
    "canlib.cli",
    "canlib.tui",
    "canlib.render",
    "canlib.contribute | canlib.interop | canlib.yaml_edit",
    "canlib.live",
    "canlib.transport | canlib.analysis",
    "canlib.device | canlib.captures | canlib.dtc | canlib.query",
    "canlib.validation",
    "canlib.ecus : canlib.states : canlib.variants : canlib.broadcast : canlib.actions : canlib.profile",
    "canlib.privacy | canlib.safety",
    "canlib.expression",
    "canlib.uds",
    "canlib.config | canlib.install | canlib.migrations",
    "canlib.core",
]
ignore_imports = [
    # every current violation, listed one by one; the list may only shrink
]

[[tool.importlinter.contracts]]
name = "only the TUI imports textual"
type = "forbidden"
source_modules = ["canlib"]          # minus canlib.tui, via ignore_imports or a split contract
forbidden_modules = ["textual"]

[[tool.importlinter.contracts]]
name = "ELM327 and raw transports are independent"
type = "independence"
modules = ["canlib.transport.elm327", "canlib.transport.raw"]
```

- `A | B` means independent siblings. `A : B` means siblings that may import each other. The
  definition packages start as one non-independent layer because `profile` ↔ `pids` ↔ `variants`
  is a real cycle today. Separating them (bundle paths and variant selection below, definition
  parsing above) is a follow-up, once the move makes the knot visible.
- The exact order of `migrations`, `config` and `dtc` needs one more measurement pass: `dtc` imports
  `migrations`, and `config` imports `yaml_edit.rt`.
- **Ratchet, not big bang.** Phase 0 adds the contracts with every current violation in
  `ignore_imports`. Each later commit removes entries. The same ratchet pattern already gates
  `validate captures --max-untimed`.
- `argparse` cannot be a `forbidden` contract cleanly (it is stdlib, and other packages import it
  for type hints). A contract test checks it instead (section 9.3).

---

## 7. Tests: a plain mirror, with three reserved directories

Decision 3 is a plain mirror. I checked it against the split layout (`unit/` + `cli/` + `tui/` +
`integration/` + `contracts/`) and the plain mirror wins. Most of what the split layout added is
already in the mirror: `cli/` and `tui/` are source packages, so their tests are at `tests/cli/`
and `tests/tui/` anyway. The `unit/` prefix only made `pytest tests/unit` possible, and a marker
does that job.

A **pure** mirror has one significant downside: tests that have no single subject get forced into
an arbitrary package directory. That is how 66 cross-package tests ended up looking like unit
tests today. So the layout reserves three directories that are not mirrors:

```text
tests/
├── conftest.py          suite-wide isolation only (config dir, network guard, pinned car)
├── support/             RESERVED: importable helpers (_fakes, _golden, _isotp_frames, builders)
├── integration/         RESERVED: several layers together (ELM327 emulator, blind rediscovery,
│                        multi-vehicle sweeps, the 66 files that import more than three packages)
├── contracts/           RESERVED: repo-wide invariants (TUI keymap roles, suite isolation,
│                        network guard, schema enum pins, generated docs current, skills valid,
│                        the structure gates of section 9.3)
├── fixtures/            unchanged (data)
├── js/                  unchanged (node tests for the explorer)
│
│   everything else mirrors canlib/:
├── cli/commands/<cmd>/  parser, exit codes, golden output, --help
├── tui/apps/<app>/      Textual pilot tests
├── live/monitor/  live/scan/  …
├── transport/elm327/  transport/raw/  …
├── uds/  expression/  safety/  ecus/  states/  variants/  captures/  analysis/  …
```

Rules:

- **Mirror directories, not files.** `tests/transport/elm327/test_pipe.py` and
  `test_prompt_accounting.py` can both test `pipe.py`. A test file is named for the behaviour it
  pins, inside the directory of the package it tests.
- **A mirrored test imports its own package.** A test whose subject spans several packages goes in
  `integration/`. A test of a repo-wide rule goes in `contracts/`.
- **Test modules never import other test modules.** Shared code goes to `tests/support/`.
- **No source package may use a reserved name** (`support`, `integration`, `contracts`,
  `fixtures`, `js`). The structure gate checks it.
- **pytest settings.** Use `--import-mode=importlib` with `pythonpath = ["."]` (the pytest
  recommendation for new layouts). Duplicate basenames across directories are then safe, and
  `tests.support` stays importable. Keep `--dist loadscope`.
- **Markers** for the subsets people run on their own: `integration`, `tui`, `slow`. CI still runs
  everything.
- `[tool.ty.src]` stays on `canlib` only, unchanged.

The test move can start before the source moves: phase 1 puts each existing test file into the
directory of its *current* subject, using the classification in section 1. After that, a source
move also moves its test directory, in the same commit.

---

## 8. Build tooling and repository root

`scripts/` mixes four kinds of file. Group them, and keep the name `scripts/` (CI, pre-commit,
`mkdocs.yml`, RELEASING.md and two skills reference it).

```text
scripts/
├── gen/        gen_cli_reference.py, gen_screenshots.py, gen_autopid.py, gen_architecture.py (new)
├── check/      check_dbc_corpus.py, check_lfs_objects.py, validate_skills.py, lfs_pre_push.sh,
│               import_graph.py (new)
├── fetch/      fetch_can_corpus.py
├── research/   soc_mirror_scan.py, blind_rediscovery.py, blind_prompt.md
└── mkdocs_hooks/  unchanged
```

- `migrate_states_status.py` and `migrate_states_uppercase.py` move into `canlib/migrations/` and
  run from a `canair` command (`canair profile migrate …` or the existing `captures migrate`
  family). `validate pids` then tells the user to run a command, not a script.
- Delete the untracked `canlib/pids_edit/` leftover.

---

## 9. Keeping the structure: rules, documents and gates for agents

The migration fixes the tree once. Without the measures below, it decays again at the rate the
tree grew: one convenient import at a time. Agents follow two things reliably: instructions that are
always in context, and checks that fail. Prose in a document they might not open does not hold.
So every rule has **one home**, a short form in AGENTS.md, and **a gate** where a gate is possible.

### 9.1 The rules

These go into AGENTS.md as a short section ("Code structure") that replaces the scattered per-file
path prose over time. Each rule has a gate in 9.3.

1. **No new module at the `canlib/` root.** Find the package with the placement table (9.2).
2. **Put code in the lowest layer that can hold it.** If it needs no device, it is not in `live/`.
   If it needs no flags, it is not in `cli/`. If it does no I/O, it may be in `uds/` or `core/`.
3. **Only `cli/` imports `argparse`. Only `tui/` imports `textual`.** Code below takes typed
   options and returns data.
4. **Never add an `ignore_imports` entry to make a gate pass.** Fix the edge: move the function
   down, pass the value in, or split the module. If that is not possible, stop and ask the user.
   An entry that the user accepts carries a one-line reason and a plan reference.
5. **A new top-level package needs a plan** (`plans/…`) that says which layer it sits in and why
   no existing package fits, plus its line in the `import-linter` layers.
6. **A package's `__init__.py` docstring is its charter.** It states what belongs, what does not
   and what it may import. Update it in the same commit when a package's scope changes.
7. **Tests go in the mirror directory of the code they test.** Cross-package tests go in
   `tests/integration/`, repo-wide invariants in `tests/contracts/`, shared helpers in
   `tests/support/`.
8. **Module size.** A new module stays under 500 lines. A module that grows past it gets split by
   concern in the same change (a command becomes a package, as the command-packages plan says).
9. **A deferred (function-level) import needs a reason.** Either a measured start-up cost or an
   optional dependency, said in a comment. A deferral that only hides a cycle is a layering bug.
10. **Moves are mechanical and alone.** `git mv` plus a codemod, one package per commit, no
    compatibility aliases, no logic change in the same commit.

### 9.2 Where each rule lives

| Home | Content | Kept current by |
|---|---|---|
| `AGENTS.md` → "Code structure" | the ten rules above, one line each, plus a pointer to the architecture page | always in context; short on purpose |
| `docs/development/architecture.md` | the layer diagram, one line per package (purpose + allowed imports), and the placement table below | **generated** by `scripts/gen/gen_architecture.py` from the package docstrings and `[tool.importlinter]`; CI runs `--check`, like the CLI reference |
| Each package `__init__.py` docstring | the charter: belongs / does not belong / may import | the structure gate checks that it exists and has the three parts |
| `.claude/skills/contributing-code/SKILL.md` | the procedure: "placing new code" and "moving code" (9.4) | reviewed with this plan; points at the generated page instead of copying it |
| `pyproject.toml` `[tool.importlinter]` | the machine contract | `lint-imports` in pre-commit and CI |

The placement table (the hand-written part of the architecture page):

| If the code … | it goes in |
|---|---|
| defines flags, reads `args`, prints a command's output | `cli/commands/<cmd>/` (shared flag helpers: `cli/args/`) |
| is a Textual app, screen or widget | `tui/apps/<app>/` (shared widgets: `tui/kit/`) |
| turns results into text for both CLI and TUI | `render/` |
| talks to the car over a `Terminal` | `live/<family>/` |
| moves bytes, frames ISO-TP, drives an ELM327 | `transport/` (`elm327/` or `raw/` if it is specific to one) |
| calls the adapter's HTTP API, or guards the connection | `device/` |
| decides whether a request may go on the bus | `safety/` |
| describes protocol bytes with no I/O | `uds/` |
| evaluates a WiCAN expression | `expression/` |
| parses a profile's definition YAML | `ecus/`, `states/`, `variants/`, `broadcast/`, `actions/` |
| finds, creates or layers a profile bundle | `profile/` |
| checks a profile | `validation/` |
| edits a profile's YAML | `yaml_edit/` |
| reads or writes recordings | `captures/` (the DTC log: `dtc/`) |
| computes over captures or definitions, with no device | `analysis/` |
| parses a selector or a query step | `query/` |
| reads or writes a format canair does not own | `interop/` |
| opens or updates the upstream PR | `contribute/` |
| masks, withholds or scrubs data | `privacy/` |
| reads or writes user config or the device registry | `config/` |
| reports the install, version or updates | `install/` |
| rewrites stored data once | `migrations/` |
| is generic and needs nothing from `canlib` | `core/` |
| is developer tooling that does not ship | `scripts/` |

### 9.3 Gates

| Gate | Catches | Runs in |
|---|---|---|
| `lint-imports`: layers, `forbidden` (textual), `independence` (elm327/raw) | a wrong-way import | pre-commit, CI |
| `unmatched_ignore_imports_alerting = "error"` | an ignore entry whose edge is gone (so the list really shrinks) | pre-commit, CI |
| `tests/contracts/test_structure.py` (new, AST only, fast) | see the list below | `pytest`, so pre-commit and CI |
| `scripts/gen/gen_architecture.py --check` | the architecture page out of date with the docstrings or the contract | pre-push, CI |
| `scripts/check/import_graph.py --report` | trend numbers: SCC size, deferred imports, root modules, oversized modules | CI log (informational), and on request |

`tests/contracts/test_structure.py` checks:

1. The `canlib/` root holds only `__init__.py` and `__main__.py`, and `__init__.py` defines only
   `__version__`.
2. Every package `__init__.py` has a docstring with the three charter parts.
3. No module outside `canlib.cli` imports `argparse`, except `TYPE_CHECKING` imports listed in a
   baseline that may only shrink.
4. Every directory under `tests/`, except the reserved ones, matches a package under `canlib/`.
   Every test file in a mirrored directory imports from its own package.
5. No test module imports another test module.
6. No source package uses a reserved test-directory name.
7. **Ratchets with a committed baseline**, in the same style as `--max-untimed`: the number of
   `ignore_imports` entries, the list of modules over 500 lines (each with its current size, which
   may not grow) and the number of deferred internal imports. A change may lower a baseline and
   must never raise one. Raising one needs the user's approval, which shows in the diff.

### 9.4 The agent procedure (in the contributing-code skill)

Placing new code:

1. Find the package in the placement table. If two packages fit, choose the lower layer.
2. Read that package's charter docstring. If the code does not fit the charter, stop and ask.
3. Write the code. Write the tests in the mirror directory.
4. Run `uv run lint-imports` and `uv run pytest -n0 tests/contracts`.
5. If a gate fails, change the structure, not the gate (rule 4).

Moving code:

1. One package per commit, `git mv`, then the codemod for imports and `monkeypatch` strings.
2. Move its tests in the same commit.
3. Remove the entries it fixes from `ignore_imports` and lower the baselines.
4. Regenerate the architecture page.
5. Check that every `--help` is byte-identical, then run the full suite.

### 9.5 Review

- `make structure` runs the gates of 9.3 in one go, for an agent that wants one command.
- The import-graph report runs in CI and prints its numbers, so a slow drift in deferred imports
  shows up before the ratchet fails.
- A plan that adds a package or moves code states where the code goes, using the placement table.

---

## 10. Sequencing

Every commit is green on its own: full suite (parallel and `-n0`), `ruff`, `ruff format`, `ty`,
`lint-imports`, `--help` byte-identical for every parser, `scripts/gen_cli_reference.py --check`,
`mkdocs --strict`. Moves use `git mv` so history follows the file, and a codemod (LibCST, or
`rope`'s move refactoring) rewrites imports **and** the dotted strings in
`monkeypatch.setattr("canlib.…")` (222 such strings in 51 test files). No commit both moves and
edits a module.

**Phase 0: guardrails (no moves). DONE.**

1. `scripts/check/import_graph.py` reproduces the section 1 numbers (38-unit cycle, 1,177 deferred
   imports) and is what `tests/contracts/test_structure.py` reads.
2. `import-linter` with eight contracts written against the packages that exist today: nothing
   below the interface imports `cli`/`commands`/`modes`, `textual` only in the TUI, `argparse` only
   in the CLI, `uds` reads no data store, decoding and transport do not import `wican`, `config`
   does not import `yaml_edit`. The 30 current violations are the `ignore_imports` debt list. The
   `layers` contract of section 6 cannot be written yet, because most target packages do not
   exist: each phase 4 move adds its package to it. The `argparse` check is a contract here, so
   the test of section 9.3 item 3 is not needed.
3. `tests/contracts/test_structure.py` with `baseline.json` set to today's tree. It also runs
   `lint-imports`, so plain `pytest` is a complete gate.
4. **No `--help` snapshots.** `scripts/gen_cli_reference.py --check` already pins every parser's
   help text, in pre-push and CI.
5. `docs/development/architecture.md` (hand-written for now), the "Code structure" section of
   AGENTS.md, the "Placing and moving code" section of the contributing-code skill, `make
   structure`, a pre-commit hook and a CI step. The generator of section 9.2 waits until the
   packages have charters (phase 5), because there is nothing to generate it from yet.

**Phase 1: cheap wins. DONE.**

1. `canlib/__init__.py` holds only `__version__`. Only six modules used a re-exported name.
2. 254 of the 306 flat tests moved into `tests/<package>/`; `support/`, `integration/`,
   `contracts/` and `scripts/` exist; pytest runs in `importlib` mode; no test imports another
   test. The 50 tests of root modules stay flat until their module moves (a baseline list).
3. `scripts/` is grouped into `gen/`, `check/`, `fetch/` and `research/`. The two states
   migrations stay at the top level: making them `canair` commands is a user-facing addition and
   waits for a decision. `canlib/pids_edit/` was an untracked leftover in the main checkout only.
4. The per-ECU response logs go to `~/.config/canair/logs/ecu/`, beside the event log.

**Phase 2: cut the wrong-way edges in place (no moves yet).** Debt list 30 → 12 entries.

1. DONE. `ResultEntry`/`EcuFrame` live in `expression/results.py`, the batch-size policy in
   `uds/multi_did.py`.
2. DONE, with debt. The validators are in `validation/` and `commands/validate/` is only the
   parser. Still open: the `_run_*` runners print and return exit codes from `validation/`;
   `validation/pids.py` is 1,854 lines; `validation/pids.py` imports `yaml_edit` for one
   constant while `yaml_edit` imports `validation/`. Action planning moved to `actions/plan.py`.
3. OPEN. The three `commands` → `cli.main` re-entries (inside the interface layer, so no
   contract depends on it).
4. DONE. `uds/` reads no data store, config or transport. `unsplit_batches` moved to
   `analysis/`, the marking rule to `uds/support_marks.py`, the byte mapping to
   `uds/wican_layout.py`, `decode_uds_response` to `uds/describe.py`, and the `--notation` flag
   helpers to `commands/_notation.py`.
5. OPEN. The device registry split (4 `transport` → `wican` entries).
6. DONE. `yaml_edit/rt.py` is `core/yaml_rt.py`, the first member of the leaf package; `config`
   no longer imports `yaml_edit`.

Measured after phase 2 (`scripts/check/import_graph.py`, 2026-11-04):

| Measure | Before | After |
|---|---:|---:|
| Units in the largest import cycle | 38 | 26 (`cli`, `commands`, `modes`, `uds`-side and `config` are out) |
| `ignore_imports` debt entries | 30 | 8 (`textual`: 5, `argparse`: 3) |
| Test files directly in `tests/` | 306 | 50 (tests of root modules) |
| Packages with a charter | 0 | 2 (`core`, `validation`) |
| Deferred internal imports | 1,177 | 1,171 |
| Root modules | 52 | 52 (they move in phase 4) |

**Phase 3: remove the flag dispatch. DONE.**

1. A command passes `run_live(args, operation)` a `LiveOperation` (handler, monitor options, whether
   `--save` means anything, which scan types to announce). `modes/dispatch/` is gone; the
   handlers are `commands/_live/handlers/` and `modes/session.py` (now `live/session.py`) takes the
   operation plus a typed `SessionOptions`. `CANAIR_DEFAULTS` (60 attributes) is gone: every flag
   the runtime reads is defined by `add_connection_args`, and
   `tests/cli/commands/test_live_operations.py` pins which operation each command reaches and that
   its parser defines every `args.x` the handler reads directly.
2. `MonitorOptions`, `ConnectionOptions`, `TransportOverrides` and `Scope` carry what the monitor,
   the raw path, the transport selection and the capture scope read off the namespace.
3. The three hex-range parsers are one (`id_ranges.parse_id_range`). Behaviour change: a backwards
   range flag is refused, and a `0x`-prefixed profile entry is dropped.
4. Fixed on the way: `dtc --save/--label/--state` was refused on the ELM327 transports. Removed:
   the unreachable `ecu` and `skm-wakeup` handlers and `mode_ecu`; `log_command(...)` calls that
   matched nothing.
5. The `argparse` contract has no ignored imports.

**Phase 4: moves, one package per commit, in dependency order (leaf first). DONE.** Deviations
from the target layout, each because the plan's placement would have broken a contract:

- `blind.py` is `analysis/blind.py` (it imports analysis and captures), not `profile/blind.py`.
- `response_frames.py` is `ecus/response_frames.py`: interop depends on it and may not import `live`.
- `privacy/` sits with the definitions: `sensitive` and `pii` read the loaded definitions.
- `render/` (`ansi`, `formatting`) sits below `live/`, because live operations print through it.
- `poll_backoff`, `poll_schedule`, `scan_presets` and `scan_state` are flat modules in `live/`;
  `live/__init__` imports nothing, so `poll_schedule` is still cheap for the parser.
- `live/` keeps its flat module layout (`live/monitor.py`, `live/scan.py`, ...); the sub-packages
  of section 4 (`read/`, `monitor/`, `scan/` ...) are not made.
- `commands/_live/` is still `cli/commands/_live/`; the shared flag helpers (`_scope.py`,
  `_join.py`, ...) are still `cli/commands/_*.py`, not `cli/args/`.
- `profile.py`, `config.py`, `query.py`, `contribute.py` became the `__init__` of their packages, so
  every caller and `monkeypatch` string kept its name. `broadcast` is `signals.py` renamed.

**Phase 4b: `src/canair/`. DONE.** `canlib` is `canair` and lives in `src/canair/`. The bundled
`profiles/`, `templates/` and `config.example.yaml` stay in the repo, and the wheel maps them to
`canair/_bundled/`; `core/paths.py` resolves them (`DATA_ROOT`), and `REPO_ROOT` is `None` in an
installed copy. A CI job builds the wheel, installs it in a clean environment and runs it.

**Phase 5: finish. DONE except as listed.** The layers contract (`The layers hold`) is written
against the tree as it is: `cli`, `contribute`, `live`, one group of 18 packages that still import
each other, then `tui`/`device`, `safety`, `expression`/`uds`, `config`, `core`.
`scripts/gen/gen_architecture.py` generates the layer and package tables of the architecture page
from that contract and the charters (CI and pre-push run `--check`).

Measured after phase 5 (`scripts/check/import_graph.py`, 2026-11-04):

| Measure | Start | After phase 2 | After phase 5 |
|---|---:|---:|---:|
| Units in the largest import cycle | 38 | 26 | 18 |
| `ignore_imports` entries | 30 | 8 | 5 (all `textual`) |
| Test files directly in `tests/` | 306 | 50 | 0 |
| Root modules in the package | 52 | 52 | 0 |
| Packages without a charter | all | all but 2 | 3 (command help text is built from them) |
| Deferred internal imports | 1,177 | 1,171 | 1,159 |

**Phase 6: split the cycle. DONE down to the three definition packages** (2026-11-05, branch
`refactor/split-layer-cycle`). Each step cut the imports of one package from inside the group and
gave it a layer of its own. The contract now reads, top to bottom: `cli`, `contribute`, `live`,
`yaml_edit`, `validation`, `interop | actions | broadcast | migrations | analysis | dtc`,
`transport | captures`, `states | query | render | install`, `privacy`,
`ecus : profile : variants`, `tui | device`, `safety`, `expression : uds`, `config`, `core`
(phase 7 then moved `tui` up to the layer right below `cli`).
Some packages sit higher than section 6 puts them (`actions`, `broadcast`, `install`), because
they import a package of the knot; they move down when it splits. The cuts, and where they
deviate from section 4:

- `EditError` is `core/edit_errors.py` (actions and states subclass it for their loader errors).
- The response-frame write-back (`persist`) is `yaml_edit/response_frames.py`; the reading and
  the diff stay in `ecus/response_frames.py`.
- `RETIRED_FIELD` (the retired `ha_class` name) is in `ecus/signal_category.py`.
- `broadcast` no longer re-exports `signal_byte_span` from `validation/signals.py`.
- The DTC log upgrade is `dtc/log_upgrade.py`, not a migration: the log upgrades itself on first
  use in a normal run.
- `PayloadSample` is in `captures/payload_corpus.py`, where the corpus builds the samples.
- The `dtcs:` section shape is `ecus/dtc_definitions.py` and the `wake:` block resolver is
  `ecus/wake.py`: both are parsed while `ecus/pids.py` loads the definitions. The session
  manager still carries a wake plan out.
- State inference is `captures/state_inference.py`, not `analysis/`: every save annotates the
  new session with its state timeline, so the store needs it.
- The PII scan is `contribute/pii.py`, not `privacy/`: only the contribute gate runs it, and it
  reads the store.
- Reading the `sensitive:` flag off a definition is `ecus/sensitivity.py`; masking and
  withholding stay in `privacy/sensitive.py`.

**Phase 7: the Textual apps. DONE** (2026-11-05, same branch). The five apps are in
`tui/apps/` and `tui/` is the layer right below `cli/`, so only `tui/` imports Textual and the
`textual` ignores are gone. The steps:

- The terminal size and the skip-key listener, which `live/` read from `tui/`, are
  `core/terminal.py` and `core/skip_key.py`.
- `mode_monitor` runs the screen `MonitorOptions.interactive` names; the monitor command passes
  the Textual app. `pick_selectors` (which runs the picker) moved with the picker.
- The models: `SniffStats` is `analysis/sniff_stats.py` and the sniff table
  `render/sniff.py`. The decode plot's `PlotModel` and the capture stepper's `StepModel` are
  view models that their commands also render off a TTY, so they sit beside their apps in
  `tui/apps/` (`decode_plot_model.py`, `captures_step_model.py`, plus the modules they draw
  with), in modules without Textual. Section 4 had `captures/step_model.py`, but the stepper's
  gap labels need `analysis/`, which sits above `captures/`.
- The data the stepper shares with the captures commands moved down: the entry keying and
  session grouping are `captures/entries.py`, the write-layer rule `captures/layers.py`.

Measured at the end of 2026-11-05 (`scripts/check/import_graph.py`):

| Measure | After phase 5 | After phase 7 |
|---|---:|---:|
| Units in the largest import cycle | 18 | 3 (`ecus`, `profile`, `variants`) |
| `ignore_imports` entries | 5 | 0 |
| Modules outside `tui/` that import Textual | 5 | 0 |
| Packages without a charter | 3 | 0 |
| Deferred internal imports | 1,159 | 1,151 |

Left over:

1. (Done in phase 7: the Textual apps are in `tui/apps/`.)
2. Two cycles are left. **`ecus : profile : variants`** is the knot section 6 already keeps as one
   layer: a `Profile` knows its car (`Profile.meta` applies the variant's overrides,
   `Profile.revisions` asks `ecus.pids`, `set_active`/`active` apply the variant and clear the
   definition cache), and the load seam in `ecus/pids.py` applies variants and revisions. To get
   `ecus` out above the other two, cut: `Profile.revisions` (three callers) to a function in
   `ecus`; the `clear_cache` calls in `set_active`/`replace_active` to a callback that `ecus.pids`
   registers; `variants/tags.py` (capture tags, it reads the loaded definitions) to `captures/`,
   with `selected_variant` and the scope keywords staying in `variants/`; and the `canair
   identity` hint in `variants/selection.py::_identity_hint`. Splitting `profile` from
   `variants` after that means the `Profile` object stops knowing its variant, a larger design
   change. **`expression : uds`**: `uds/notation.py` and `uds/inspect_bytes.py` build WiCAN
   expressions with `expression.signed_range_is_exact`. Move both to `expression/`, not the
   guard down: `expression/nodes.py` keeps the guard beside the sign-extension ladder on
   purpose. `notation.subfunction_bytes_for_pid` goes to `uds/layout.py` first
   (`uds/support_map.py` needs it). About 40 importers change, and several are at their size
   ceiling (`cli/commands/hunt.py`, `live/_monitor_render.py`), where a split import line adds
   one.
3. The open items from phase 2: the `validation/` debt (the `_run_*` runners check and print in
   one function, so moving them to `cli/commands/validate/` means separating the check from the
   report first; `validation/pids.py` is 1,848 lines), and the two `scripts/migrate_states_*.py`
   as `canair` commands (user-facing, needs a decision). The `cli.main` re-entries are gone
   (they were unused `python -m` blocks).
4. The `live/` sub-packages of section 4 (`read/`, `monitor/`, `scan/` ...). The flag helpers
   are `cli/args/` and the live-command runtime is `cli/live/` (2026-11-05).
5. AGENTS.md still names about 180 `src/canair/...` paths. Cut it to what an agent needs always and
   point the rest at the generated page.
6. The `param` to `signal` identifier rename of `plans/2026-08-06-signal-naming-convention.md`.
7. (Done 2026-11-05: `coverage`, `research` and `import uds` have charters, placed before the
   `Examples:` their help epilog is cut from.)

---

## 11. The `src/` layout and the `canair` import name (phase 4b)

Decision 4 accepts both, as phase 4b. This section records why.

### What changes

- **`src/` layout:** `canlib/` moves to `src/canair/`. The repo root then holds the project
  (`profiles/`, `templates/`, `docs/`, `scripts/`, `tests/`) and `src/` holds only what ships.
- **Import name:** `import canlib` becomes `import canair`, so the distribution, the command and
  the import share one name.
- **Packaged data** goes with it: the wheel maps `profiles/`, `templates/` and
  `config.example.yaml` into the package (`canair/_bundled/…`), and code finds them with
  `importlib.resources`. In a checkout, the repo root stays the source.

### Why it is worth it

1. **There is already a different `canlib` on PyPI.** Kvaser publishes its CANlib wrapper as
   `canlib` (version 1.34.0, released 2026-09-29), and it installs a top-level `canlib` package.
   Both packages write to the same directory in `site-packages`. A canair user with a Kvaser
   interface who installs both gets one package's files over the other's, and pip installs both
   without an error. canair's users are exactly the people who own CAN hardware, so this is not
   hypothetical. The rename removes the collision. Nothing else does.
2. **It removes the "two places, one path" class of bug.** `SCRIPT_DIR = PACKAGE_DIR.parent` means
   "repo root" in a checkout and "`site-packages`" in a wheel. Section 1 lists three results: a
   top-level `profiles/` directory in every environment, per-ECU logs under
   `site-packages/logs/`, and the snapshot write loss in AGENTS.md. With a `src/` layout,
   `PACKAGE_DIR.parent` is `src/`, so the shortcut stops working in a checkout as well, and every
   path must say what it means: shipped data (`importlib.resources`), user state (the config
   directory) or the repo (only for developer tooling). The bug class becomes impossible to write.
3. **Tests run against what ships.** In a flat layout, the repo root is on `sys.path`, so tests
   import the working tree even when the installed package differs from it. With `src/`, the tests
   import the installed (editable) package. Add one CI job that builds the wheel, installs it in a
   clean environment and runs `canair --help`, `canair profile list` and `canair validate`. Then a
   file missing from the wheel fails CI instead of failing on a user's machine. Today CI never
   builds the wheel at all.
4. **One name instead of three.** Distribution `canair`, command `canair`, import `canlib`: today a
   reader must learn that they are the same thing. Tracebacks, `python -m canlib`, `ty` messages
   and log records all say `canlib` for a tool everyone calls canair. `__version__` already reads
   `importlib.metadata.version("canair")`, which shows the mismatch.
5. **The root of the repo says what ships.** Today `canlib/` sits beside `profiles/`, `references/`
   (third-party checkouts, some with their own Python packages), `wican-fw/`, `logs/` and
   `scripts/`. With `src/`, a reader and every tool (ruff, ty, import-linter, coverage) has one
   explicit source root.
6. **It is the default.** The PyPA packaging guide, `uv init --package` and hatch's templates all
   use `src/`. A contributor who has used any modern Python project expects it.

### Why soon after phase 4, not later

- **The marginal cost is lowest right after phase 4.** Phase 4 rewrites every import in the tree
  and every `monkeypatch` string, with a codemod. The rename is the same codemod with one more
  rule. Done separately later, it is a second full churn of all 426 files, 306 test files, and
  the ~250 path references in AGENTS.md, skills and docs.
- **The cost grows with every new reference.** Each plan, skill and doc that cites `canlib/…` adds
  to the retarget list. Phase 5 retargets the docs once. Renaming after phase 5 retargets them
  twice.
- **The risk is small and visible.** canair is an application: no external code imports
  `canlib`. The entry point changes from `canlib.cli:main` to `canair.cli:main`, and `canair
  update` reinstalls through `uv tool install`, which replaces the entry point script. The git
  merge driver and the pre-commit hooks call commands and script paths, not module paths.

### What it would cost

- One codemod pass (imports, `monkeypatch` strings, `[tool.*]` paths in `pyproject.toml`,
  `mkdocs.yml` hook paths, the CI workflow).
- The packaged-data change and its tests (`install_context.installed_snapshot_kind`, `canair
  update`, `profile adopt`), which is worth doing on its own merits (point 2).
- One wheel-install CI job (point 3).
- A release note. No user action is needed, because users run the `canair` command, not the
  import.

Placement: phase 4b, between the moves and the doc retarget. Until then, rule 1 of 9.1 already
prevents new root-level path shortcuts.

---

## 12. Risks

- **Concurrent sessions.** Several agent sessions share this working copy (one added
  `transport/keepalive.py` while this plan was being written). A move commit conflicts with any
  in-flight edit to a moved file. Do the work on a branch in a separate worktree, keep each commit
  small, and land phase 4 commits on a quiet tree.
- **Lazy imports hide order.** 1,177 deferred imports mean an import error can appear only when a
  rare path runs. The full suite plus `python -c "import canlib.<pkg>"` for every package after each
  move is the check. Convert a deferred import to a top-level one when the layer allows it, but in a
  separate commit from the move.
- **Start-up time.** Some deferred imports exist to keep `canair --help` and tab completion fast.
  Measure `canair --help` before and after phase 1 and phase 4. Keep a deferral that saves
  measurable time, and comment why (rule 9).
- **`argcomplete`** resolves `canlib.cli:main` at completion time. The entry point does not change,
  but test completion after phase 4.
- **Review cost.** A move commit is large but mechanical. State in each message that it is a pure
  move, and give the codemod command so a reviewer can rerun it.
- **Gates that are too strict get bypassed.** The baselines in 9.3 start at today's values, so the
  gates pass on day one. They only stop things from getting worse.

---

## Appendix A: import-graph measurement

The numbers in section 1 come from an AST scan: for every module under `canlib/`, collect every
`import canlib…` and `from …` (relative imports resolved), map each side to its top-level unit
(`canlib.<x>`, or `canlib.commands.<x>`/`canlib.modes.<x>` one level deeper), aggregate the edges,
and run Tarjan's algorithm for strongly connected components. Edges from or to the root façade are
excluded from the SCC count. Phase 0 commits this script as `scripts/check/import_graph.py` so the
numbers can be recomputed, and 9.3 runs it in CI.
