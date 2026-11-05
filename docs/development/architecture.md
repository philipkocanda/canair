# Architecture: where code belongs

canair is one CLI over a stack of layers. A layer may import from the layers below it and never
from the layers above. This page says what each package is for and where new code goes. The
rules behind it, and the migration that gets the tree there, are in
[`plans/2026-11-04-package-layout-and-layering.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-11-04-package-layout-and-layering.md).

!!! note "Migration state"
    The code lives in `src/canair/`. The 52 loose modules that used to sit at its root are in
    packages now. What is left is listed in the plan: the cycle between `ecus/`, `profile/` and
    `variants/` (and a small one between `expression/` and `uds/`), and the Textual apps that
    still sit in `live/` and `cli/`.

## Layers

<!-- BEGIN GENERATED: layers -->

Top to bottom. A package imports only from the layers below its own.

| Layer | Packages | May import |
|---|---|---|
| 1 | `cli/` | the layers below |
| 2 | `contribute/` | the layers below |
| 3 | `live/` | the layers below |
| 4 | `yaml_edit/` | the layers below |
| 5 | `validation/` | the layers below |
| 6 | `interop/`, `actions/`, `broadcast/`, `migrations/`, `analysis/`, `dtc/` | the layers below, not each other |
| 7 | `transport/`, `captures/` | the layers below, not each other |
| 8 | `states/`, `query/`, `render/`, `install/` | the layers below, not each other |
| 9 | `privacy/` | the layers below |
| 10 | `ecus/`, `profile/`, `variants/` | each other (one cycle still to split) and the layers below |
| 11 | `tui/`, `device/` | the layers below, not each other |
| 12 | `safety/` | the layers below |
| 13 | `expression/`, `uds/` | each other (one cycle still to split) and the layers below |
| 14 | `config/` | the layers below |
| 15 | `core/` | the layers below |

<!-- END GENERATED: layers -->

The first three layers are the interface and the live operations. The profile editors
(`yaml_edit/`) and the validators they run after each write (`validation/`) sit below them and
above everything they read. Then come the packages that compute over the store and the
definitions, the store and the transport, and the definitions themselves. A layer marked "one
cycle still to split" still imports itself: `ecus/`, `profile/` and `variants/` (a profile knows
which car it describes, and loading the definitions applies that car), and `expression/` with
`uds/`. Everything else is ordered, and `lint-imports` fails on a wrong-way import.

Some packages sit higher than their job needs (`actions/`, `broadcast/`, `install/`), because they
import a package of the `ecus/`, `profile/`, `variants/` cycle. They move down when it splits.

`render/` sits below `live/` rather than beside the CLI, because live operations print through it.

## Packages

<!-- BEGIN GENERATED: packages -->

| Package | What it is | Belongs | Not here | Imports |
|---|---|---|---|---|
| `actions/` | Profile-defined actions: named, guarded requests a profile declares in ``actions.yaml``. | parsing a profile's ``actions.yaml`` into guarded actions and planning their requests. | running one against the car (``live/action.py``) and the ``canair action`` command. | ``safety``, ``uds``, ``ecus`` and ``core``. |
| `analysis/` | Offline analysis over captures and definitions: alignment, correlation, counters, mirrors, stats. | pure computation over loaded captures and profile definitions, with no device, no flags and no output: time alignment and fill, correlation, mirrors, counters, support maps, triage, the blind-rediscovery strip (``blind``), and physical-plausibility bands (``physical``). | reading the store (``captures/``), rendering (the command's ``render.py``) and anything that talks to the car (``live/``). | the store (``captures``), the definitions it computes over (``ecus``, ``states``, ``variants``, ``profile``, ``query``, ``privacy``), ``expression``, ``uds`` and ``core``. Nothing in those imports it back. |
| `broadcast/` | Reading a profile's broadcast signal maps (``signals/<bus>.yaml``). | loading the broadcast signal maps of a profile. | the checks (``validation/signals.py``), edits (``yaml_edit/signals.py``) and DBC import/export (the ``import``/``export`` commands). | ``profile``. The structural check (``validation/signals.py``) imports this package's readers, never the other way round. |
| `captures/` | The capture store: saving (here), the file format, reading back, journal and merge driver. | per-date capture files: format, store, journal, merge driver, saved state timelines. | other computing over captures (``analysis/``), the DTC log (``dtc/``), flag parsing. | the definitions (``states``, ``ecus``, ``variants``, ``profile``, ``privacy``) and below. |
| `cli/` | ``canair`` — unified CAN/UDS/KWP2000 diagnostic reverse-engineering CLI. | the interface: the argument parser, the subcommand modules under ``cli/commands/`` (flags, output and glue only) and the live runtime that connects a device and runs a command's operation. | anything a test could call without the CLI (it goes to the package that owns it) and Textual apps (``tui/``). | every package below it. It is the only code that imports ``argparse``. |
| `config/` | User/host configuration (XDG-aware) for canair. | reading and writing the user config file and the device registry in it. | the config directory path (``core.paths``) and what a key means to a feature. | ``core`` only. |
| `contribute/` | Orchestration for ``canair contribute`` — one-command profile pull requests. | the git/gh orchestration, the source-checkout clean-up (``source``), the log scrub (``logs``), the personal-data scan the PII gate runs (``pii``) and the PR summary (``summary``). | user interaction and the pre-flight gates (``cli/commands/contribute.py``, helpers). | ``profile``, ``privacy``, ``captures``, ``ecus``, ``config``, ``install`` and ``core``. |
| `core/` | Generic helpers that need nothing else from canair. | code with no knowledge of vehicles, profiles, captures or transports that several packages share: the path roots and config directory (``paths``), how YAML is read and round-tripped (``yaml_io``, ``yaml_rt``), the error a rejected edit raises (``edit_errors``, shared by the editors and by the loaders whose errors they report), hex id ranges, text scanning, stop signals, the terminal's size (``terminal``), the key that skips a long step (``skip_key``), and the central diagnostics event log (``eventlog``). | anything that imports another canair package, which belongs in the package it serves. The per-ECU response log reads the ECU registry, so it is ``transport/ecu_log.py``. | the standard library and third-party packages only, never another canair package. |
| `device/` | The adapter as a managed device, not a byte pipe. | the WiCAN's HTTP API, firmware generation and protocol mode (``wican``), and the connection mutex that keeps two sessions off its one connection (``lock``, ``lock_watchdog``). | moving CAN bytes (``transport/``) or building the AutoPID profile (``interop/``). | ``config`` and ``core``. It never imports ``transport``, which imports it. |
| `dtc/` | DTC (diagnostic trouble code) reading, history, and interpretation. | the DTC log and the upgrade that brings an older one up to date on first use (``log_upgrade``), meanings, history, scope and snapshot decoding (data about codes). | reading or clearing codes on the car (``live/dtc/``) and the ``canair dtc`` command. | ``captures``, the definitions (``ecus``, ``variants``, ``profile``, ``privacy``), ``render``, ``uds`` and ``core``. |
| `ecus/` | The ECU registry and the per-ECU PID definitions. | ECU, PID, ``dtcs:`` and ``wake:`` definitions, CAN buses, DID families, categories. | the bundle (``profile``), checks (``validation``), edits (``yaml_edit``), scans (``dtc``). | ``profile``, ``variants``, ``safety``, ``uds``, ``core``. |
| `expression/` | WiCAN expression evaluator — the public entry point. | evaluating a WiCAN expression over response bytes and decoding typed values. | where a response comes from (``transport/``) or which signals a profile defines (``ecus/``). | ``uds`` and ``core`` only. |
| `install/` | The install: which copy is running, its version and build, and whether a newer one exists. | the running copy's origin (checkout, tool snapshot), version and build info, and the once-a-day update check behind ``canair update``. | the update command itself (``cli/commands/update.py``) and user config (``config/``). | ``config``, ``core`` and ``profile`` (to tell which profiles a reinstall would delete). |
| `interop/` | Formats canair does not own: DBC files, foreign frame logs, WiCAN AutoPID JSON, the docs explorer. | reading or writing an external format (``dbc``, ``frame_log``, ``autopid``, ``explorer``) so the rest of canair never learns a foreign shape. | canair's own file formats (``captures/``, ``ecus/``) and the commands that call these (``cli/commands/import_*``, ``cli/commands/export.py``, ``cli/commands/wican.py``). | any package below it. Decoding code does not import it back: an import contract pins that ``expression`` and ``captures`` stay free of it. |
| `live/` | Live operations against the car: one coroutine per job, over a connected ``Terminal``. | device orchestration over the ``Terminal`` protocol, the monitor's controller and recorder, the step executor, scans, DTC reads, identity, IOControl and routines. | flags (``cli/commands/``), choosing a transport (``transport/``), what may be sent (``safety/``) and the selector and step grammar (``query/``). | ``transport`` (the protocol only), ``safety``, ``captures``, ``ecus``, ``query``, ``analysis``, ``uds`` and below. It does not import ``cli``. |
| `migrations/` | One-shot data/config migration shims (captures, device config, discover rows). | one-time rewrites of stored data and config (captures, devices, discover rows). | anything a normal run needs; a migration runs once, from a ``canair`` command. The DTC log upgrades itself on first use, so its upgrade is ``dtc/log_upgrade.py``. | the stores it rewrites (``captures``, ``config``) and the definitions they name. |
| `privacy/` | What must never leave the machine, and what to mask on screen. | the ``sensitive: true`` rule (``sensitive``: mask on screen, never journal, save or export). | the contribute-time scan for personal data in a profile (``contribute/pii.py``), reading the flag off a definition (``ecus/sensitivity.py``) and where a profile lives (``profile``). | the loaded definitions (``ecus``, ``profile``), ``uds`` and ``core``. |
| `profile/` | Vehicle profile resolution. | finding, resolving, layering and creating a profile bundle (this module, ``scaffold``), and what cars call a profile (``names``). | parsing a bundle's definitions (``ecus/``, ``states/``, ``variants/``, ``broadcast/``), checking them (``validation/``) or editing them (``yaml_edit/``). | ``config``, ``core`` and ``uds``. |
| `query/` | ECU / PID selection mini-language. | the selector grammar and matching (this module) and the ``read``/``monitor`` step grammar built on it (``steps``). | running a step against the car (``live/multi*``) or ``@group`` definitions (``ecus/``). | ``ecus`` (to resolve names) and ``core``. |
| `render/` | Text for the terminal: the colour palette and the shared result formatters. | the ANSI palette and its colour gating (``ansi``) and the formatters several packages print through: hex lines, byte rulers, parameter tables, UDS response blocks (``formatting``). | a command's own output (its ``render.py``), Textual apps (``tui/``) and decoding itself (``expression/``, ``uds/``). | ``expression``, ``privacy``, ``uds`` and ``core``. It sits below ``live`` because live operations print through it. |
| `safety/` | What may be sent to the car. | the command blocklist and session rules (``blocklist``), the make-specific quirks that widen them for one protocol or grant an action one exact request (``quirks``), and the read/release/actuate classifier (``actuation``). | moving bytes (``transport/``), the actions themselves (``actions/``) or their live run (``modes/action.py``). | ``uds`` and ``core``. The blocklist is the one place a refusal is decided, so transports and live operations depend on it and it depends on neither. |
| `states/` | Vehicle operating-state definitions + auto-suggestion. | the state vocabulary, ``when:`` predicates (Kleene logic) and suggestion. | edits (``yaml_edit/``) and inference from captures (``captures/state_inference.py``). | ``expression`` and ``core``; ``refs`` also reads ``ecus``, ``variants`` and ``profile``. |
| `transport/` | Alternate CAN transports for canair (raw-CAN backends). | moving bytes to and from the car, ISO-TP framing, the ELM327 and raw SLCAN engines, the per-exchange policies (keepalive, link latency, timeouts, frame counts), running a wake ritual (defined in ``ecus/wake.py``), the transport choice and fallback, the session manager and the per-ECU response log. | what a live operation does with a terminal (``live/``), what may be sent (``safety/``) and the adapter's HTTP API (``device/``). | ``safety``, ``uds``, ``ecus``, ``device``, ``config`` and ``core``. Operations see only the ``Terminal`` protocol, never a concrete terminal. |
| `tui/` | The Textual kit, and the arrow-key list picker of the ``canair profile`` command. | the shared Textual kit: key roles, help, modals, scrolling, status and widgets. | a whole app (they live next to their command or in ``live/``) and any non-Textual code. | ``core`` and Textual. Only this kit and the apps built on it import ``textual``. |
| `uds/` | The diagnostic request/response protocol layer (domain A), I/O-free. | protocol facts and decoding of request/response bytes, with no I/O. | reading or writing recordings or definitions (``captures/``, ``ecus/``), user config (``config.py``), moving bytes (``transport/``), and deciding what may be sent (``safety``). | the standard library and ``core`` only. ``tests/contracts`` pins that it reads no data store, config or transport. |
| `validation/` | Profile validators: files and parsed data in, errors and warnings out. | the checks behind ``canair validate`` (pids/ecus, captures, states, signals, variants, CAN buses, groups, actions) and the post-write guard the profile editors call. | the ``canair validate`` parser and its dispatch (``cli/commands/validate/``), and the edits themselves (``yaml_edit/``). | the layers below it: the definition packages, ``captures`` and ``analysis`` (the capture checks), ``interop`` and ``transport`` (rules they own), ``uds``, ``expression`` and ``safety``. Never ``yaml_edit``, which imports this package, nor ``live`` or ``cli``. |
| `variants/` | The variants of one car: ``variants.yaml``. | loading ``variants.yaml``, ECU revisions, fingerprints and the variant selection. | editing them (``yaml_edit/``) and probing the car (``live/revision_probe.py``). | ``profile``, ``ecus``, ``uds`` and ``core``. |
| `yaml_edit/` | Everything that writes a profile's YAML: the editors, their shared write path and helpers. | every write to a profile's definition YAML: the editors and their commit path. | reading definitions (``ecus/``, ``states/`` ...) and the ``canair pids``/``signals`` commands. | the layers below it: ``validation``, the definition packages, ``captures`` (a rename rewrites recorded history), ``render``, ``install`` and ``core``. Never ``live`` or ``cli``. |

<!-- END GENERATED: packages -->

## Where does my code go?

| If the code … | It goes in |
|---|---|
| defines flags, reads `args`, prints a command's output | `cli/commands/<cmd>/` |
| is a Textual app, screen or widget | `tui/apps/<app>/` (shared: `tui/kit/`) |
| turns results into text for both CLI and TUI | `render/` (a command-only renderer stays in its `render.py`) |
| talks to the car over a `Terminal` | `live/<family>/` |
| moves bytes, frames ISO-TP, drives an ELM327 | `transport/` |
| calls the adapter's HTTP API, or guards the connection | `device/` |
| decides whether a request may go on the bus | `safety/` |
| describes protocol bytes with no I/O | `uds/` |
| evaluates a WiCAN expression | `expression/` |
| parses a profile's definition YAML | `ecus/`, `states/`, `variants/`, `broadcast/`, `actions/` |
| finds, creates or layers a profile bundle | `profile/` |
| checks a profile | `validation/` |
| edits a profile's YAML | `yaml_edit/` |
| reads or writes recordings | `captures/` (DTC log: `dtc/`) |
| computes over captures or definitions, with no device | `analysis/` |
| parses a selector or a query step | `query/` |
| reads or writes a format canair does not own | `interop/` |
| opens or updates the upstream PR | `contribute/` |
| masks, withholds or scrubs data | `privacy/` |
| reads or writes user config or the device registry | `config/` |
| reports the install, version or updates | `install/` |
| rewrites stored data once | `migrations/` |
| is generic and needs nothing from the package | `core/` |
| is developer tooling that does not ship | `scripts/` |

If two rows fit, choose the lower layer. If none fits, stop and ask: a new top-level package
needs a plan that names its layer.

## Rules

1. **No new module at the `src/canair/` root.** Use the table above.
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
