# Command packages: adopt the trigger rule, split `_live.py`

Status: **PLANNED** (2026-08-06). Internal refactor — **no user-facing change in
any commit**. Every view's stdout stays byte-identical, every `--json` shape
unchanged, every `--help` unchanged (gated by `make gen-check`). Consequently
nothing lands in `CHANGELOG.md` or `README.md`; the only prose to touch is
`AGENTS.md` + three skills carrying module paths that move.

Answers the question *"should all commands be in their own package, like
`captures`?"* — **no**, and this plan writes down the rule that replaces
"everything" with "when".

Follows `plans/2026-08-05-captures-command-package-split.md` (DONE) and picks up
one of the two items deferred in
`plans/2026-08-05-layering-and-module-size-followups.md` (the `_live.py` inversion
is *adjacent to* its Part A, not the same code).

---

## The rule

> **A command is one module until it needs two. The moment it needs a second, it
> becomes a package** — `commands/<name>/`, not `commands/<name>.py` plus a
> `_<name>_*.py` sibling.

It binds **new work**, and **anything already being refactored**. Existing
flat-sibling commands are grandfathered: they convert when the size or the concern
count justifies the churn (Parts 2–3), or opportunistically when next touched
(Boy Scout) — not as a campaign. That is why `contribute` (787/3) and `sniff`
(399/2) are listed below as "leave": they satisfy the *shape* trigger but not the
*cost* threshold, and converting them buys nothing today.

Corollaries, both of which have bitten this tree already:

- **Package shape ≠ module size.** They are orthogonal. The single largest file in
  the repo is `commands/validate/pids.py` at **1441 lines** — *inside* a package.
  Packaging a command does not discharge the ~500-line obligation on its members.
- **Splitting a monolith and packaging it are one commit, not two.** A future
  `bix.py` (985) / `ecu.py` (919) / `pids.py` (841) / `hunt.py` (751) /
  `wican.py` (719) split lands as `bix/`, never as `bix.py` + `_bix_render.py`.

### Why not "all commands are packages"

Of the **38** commands registered in `commands/__init__.py::COMMAND_NAMES`, **26**
are a single module with one concern (largest: `scan` at 560), and **19** of those
are under 350 lines — `raw.py` is **35 lines**, `io.py` 53, `identity.py` 43.
Wrapping those in a directory plus an `__init__.py` buys uniformity and pays in
ceremony. The defect in the tree today is **not** that some commands are flat; it
is that **two idioms coexist for the identical situation**: `captures/` and
`validate/` are packages while six commands express the same "I need more than one
module" the old way.

### Measured: lines per command, private siblings folded in

| Command | Lines | Files | Shape today | Action |
|---|---:|---:|---|---|
| `captures` | 4427 | 19 | **pkg** | — (see Part B of the followups plan) |
| `decode` | 2968 | 5 | flat + `_decode_{calc,render,plot,plot_tui}` | **→ pkg** |
| `validate` | 2350 | 5 | **pkg** | — (Part A of the followups plan) |
| `correlate` | 1281 | 3 | flat + `_correlate_{calc,render}` | **→ pkg** |
| `investigate` | 1241 | 2 | flat + `_investigate_render` | **→ pkg** |
| `contribute` | 787 | 3 | flat + `_contribute_{gates,report}` | leave — under the bar |
| `sniff` | 399 | 2 | flat + `_sniff_tui` | leave — under the bar |
| `bix`/`ecu`/`pids`/`hunt`/`wican` | 985/919/841/751/719 | 1 each | flat monolith | out of scope; package **when** split |
| 26 others | ≤560 | 1 each | flat | leave |

Verified these siblings are genuinely command-private: `_decode_render`,
`_decode_calc`, `_correlate_calc`, `_correlate_render`, `_investigate_render`,
`_contribute_gates`, `_contribute_report`, `_sniff_tui` have **zero** importers
outside their own command + its test. They are package internals wearing a prefix.

Contrast the genuinely **shared** command-layer infra, which stays a flat `_x.py`
and is **not** touched: `_group` (7 commands), `_join` (6), `_categories`/`_domain`
(`cli.py`), `_hexarg`, `_hints`, `_promote`, `_can_args`. The one exception is
`_live` — shared by 10 commands *and* 1263 lines *and* the target of two layering
inversions, which is why it gets Part 4 rather than being left alone.

### On re-export façades

The captures split's rule was "**no re-export shims** — rewire every call site".
That applied to a *layering inversion* (`capture_store.py`), where the old import
path had to die. A package's own `__init__.py` re-exporting its public surface is
not a shim — it is the package API, exactly as `captures/__init__.py` already
re-exports `build_query`/`orphan_notice`. Both are honoured here: `_live/__init__.py`
keeps its surface (so 10 commands are untouched), while everything pushed **down**
to the library is rewired at every call site with no back-compat alias.

---

## Part 1 — write the rule down

One commit, docs only.

- **`.claude/skills/contributing-code/SKILL.md`** — add the trigger rule to
  *"Refactor proactively — no monoliths"*, next to the existing ~500-line
  guidance, with the two corollaries and the `commands/captures/` +
  `commands/validate/` precedents. State the flat-sibling `_<cmd>_*` convention is
  **retired for new work**.
- Same section: note the distinction between a **command-private** module (goes
  inside the package) and **shared command-layer infra** (stays a flat `_x.py`,
  or moves down to `canlib/`), with the verified importer counts as the test for
  which one you have.

Do this first so Parts 2–4 are the rule being applied, not the rule being invented.

---

## Part 2 — `commands/decode/`

The strongest case in the tree, stronger than captures was: 2968 lines / 5 files,
and the main module is **1048** with a **400-line `_decode_one`** and a 165-line
`add_parser`.

Measured spans in `decode.py`:

| Lines | Span | Concern |
|------:|---|---|
| 61 | 103–163 | `load_captures`, `scope_captures` — thin wrappers on the data layer |
| 89 | 164–252 | `payload_to_wican_bytes`, `decode_payload`, `parse_try_expr`, `build_try_params`, `resolve_ref` |
| 165 | 253–418 | `add_parser` |
| 73 | 419–491 | `_plot_pid_options`, `_build_plot_model` |
| 46 | 492–537 | `_resolve_targets` |
| 111 | 538–648 | `run` |
| 400 | 649–1048 | `_decode_one` — the per-PID mode dispatcher |

Target layout:

```
commands/decode/
  __init__.py     NAME/ALIASES + add_parser wiring + the re-exported surface
  parser.py       add_parser (165)
  run.py          run + _resolve_targets (~160)
  one.py          _decode_one, split by output section (400 → several)
  payload.py      payload_to_wican_bytes, decode_payload, parse_try_expr, …
  calc.py         ← _decode_calc.py       (223)
  render.py       ← _decode_render.py     (740 — still over; split in the same pass)
  plot.py         ← _decode_plot.py       (603 — ditto)
  plot_tui.py     ← _decode_plot_tui.py   (354)
```

`_decode_one` at 400 lines is the real work, not the rename — but **it is not a
selection table**, and treating it like one would be wrong. Read it: it is a
**pipeline** (resolve params → load/scope captures → then a *sequence* of
independent, non-exclusive output sections at `:834 --dump-bytes`, `:847 --plot`,
`:862 --json`, `:964 --compact`, `:975 --find-mirrors`, `:985 --stats`,
`:991 --discriminate`, `:1027 --corr`) closed by a `if not printed:` default value-range
view at `:1041`. Several can fire in one run. So: **one function per output
section**, each taking the prepared context, with `_decode_one` reduced to the
pipeline that calls them in order — ordering and the `printed` fall-through
preserved exactly.

The `captures/mode_select.py` precedent applies **one level up**, to `run`'s
flag-combination guards (`:567` `--changes-only` needs `--compact`, `:570`
`--group-by` needs `--stats`, `:573` `--corr-transform` needs `--corr`, `:596` the
single-PID requirement): those *are* a declarative, unit-testable table, and
`_SINGLE_PID_FLAGS` is already half of one — extend it rather than inventing a
second mechanism.

`render.py` (740) and `plot.py` (603) are also over the line — split them by view
in the same pass rather than moving a monolith (the mistake `validate/pids.py`
records).

### Two library-grade functions to push down first

`commands/pids.py:134` imports `decode_payload`, `load_captures`,
`payload_to_wican_bytes` **from `commands.decode`** — a command reaching into
another command for what is plainly library code (pure payload decoding plus a
thin `capture_store` wrapper). Push them to `canlib/` (`capture_decode.py`, or
fold into `capture_store.py`/`decoding.py` if they fit without bloat) **before**
the package move, so the packaging commit has no cross-command import to carry.
No alias; rewire `pids.py` and the tests.

---

## Part 3 — `commands/correlate/` and `commands/investigate/`

Same treatment, smaller. Both already name their seam in a filename
(`calc`/`render`), and both have an oversized `run`.

`correlate.py` (966): `add_parser` + 3 helpers = **277 lines of argparse**
(74–351); series/scope helpers (352–430); `_run_can_log` (136); **`run` (336)**;
promote/warn tail (62).

```
commands/correlate/
  __init__.py   NAME + add_parser wiring + surface
  parser.py     add_parser, _add_uds_parser, _add_can_parser, _add_shared_analysis_args (277)
  uds.py        run — the domain-A ranked/against/matrix modes (336 → split by mode)
  can.py        _run_can_log (136)
  series.py     _discover_specs, _gather_series, _scope_keep_flags, _fill_json
  calc.py       ← _correlate_calc.py   (64)
  render.py     ← _correlate_render.py (251)
  promote.py    _promote_top_byte, _warn_thin_reference_join
```

`investigate.py` (742): `add_parser` + 2 = **200 lines of argparse** (62–262);
`_ByteReport`/scoring (263–306); **`run` (246)**; `_run_can` (105); anchor helpers
(660–742).

```
commands/investigate/
  __init__.py
  parser.py     add_parser, _add_uds_parser, _add_can_parser (200)
  uds.py        run (246)
  can.py        _run_can (105)
  report.py     _ByteReport, AnchorHit, _best_anchor, _driver_r, _state_f, _independence_score, _word_expr
  render.py     ← _investigate_render.py (499)
```

### The cross-command private import to fix

`investigate.py:308` does `from canlib.commands.correlate import _discover_specs`
— one command importing another's **private** function. It is library-grade ("which
`(ECU, PID)` specs are in scope?", answered from the capture store), and being
reached across commands is the proof. Push it down to `canlib/` (next to
`load_signal_captures` in `align.py`, or `capture_store.py`) as a public name
**before** either package move. Five monkeypatch sites in `tests/test_investigate.py`
and three in `tests/test_correlate.py` retarget to the new home.

---

## Part 4 — split `_live.py` and kill the two inversions

`commands/_live.py` is **1263 lines** and is the shared runtime for 10 live
subcommands. It has five sections already marked with header comments, so the
seams are not in dispute:

| Lines | Span | Concern |
|------:|---|---|
| 112 | 68–179 | `CANAIR_DEFAULTS`, `parse_range`, `split_ecus_by_protocol` |
| 71 | 180–250 | argcomplete completers (5) |
| 32 | 251–282 | `STEP_VERBS`, `to_step`, `expand_step_groups` |
| 91 | 283–373 | `add_connection_args`, `finalize_live_parser` |
| 890 | 374–1263 | connection lifecycle + dispatcher |

The last section is the problem: `dispatch_mode` is **470 lines** (`:794`–`:1263`)
of `if/elif` over `args.<selector>`, and `async_main` is **176** (`:524`–`:699`).

### The inversions (measured)

| Consumer | Imports | Fix |
|---|---|---|
| `canlib/modes/raw_ops.py:58` | `commands._live.run_session_guarded` | move the callee down |
| `canlib/modes/raw_monitor.py:131` | `commands._live.wants_save` | move the callee down |
| `canlib/transport/protocol.py:7` | (docstring) `commands._live.dispatch_mode` | retarget the reference |
| `tests/test_kwp_routines_scan.py:8` | `commands._live.split_ecus_by_protocol` | move the callee down |

Library code reaching *up* into a command helper — the same class `capture_store.py`
fixed. Both `modes/` importers want functions that belong beside them.

### Target layout

```
commands/_live/                  (package: private to the command layer)
  __init__.py     re-exports the surface the 10 commands already import
  defaults.py     CANAIR_DEFAULTS (112)
  completers.py   ecu/pid/param/step completers (71)
  steps.py        STEP_VERBS, to_step, expand_step_groups (32)
  parser.py       add_connection_args, finalize_live_parser (91)
  connect.py      _print_sleep_banner, connect_elm_terminal, build_elm_reconnector (145)
  runtime.py      async_main, run_live, run (~220)
```

Pushed **down** to the library (no aliases — old paths fail loudly):

- **`canlib/modes/dispatch/`** — `dispatch_mode` + `run_session_guarded`. This is
  the honest home: it dispatches to `canlib/modes/*` and is already consumed by
  `modes/raw_ops.py`, so that import becomes a **sibling**, and
  `transport/protocol.py`'s docstring gets a reference that reads correctly
  ("dispatched through the shared modes dispatcher"). 470 lines → a small selector
  table plus per-family handlers (`reads`, `scans`, `actuators`, `diagnostics`,
  `multi`), each keeping its exact messages/`sys.exit` codes. `canlib/modes/__init__.py`
  must not import it (no cycle).
- **`wants_save` → `canlib/keepmode.py`**, beside `keep_mode_from_args` — the same
  kind of pure predicate over the args namespace.
- **`split_ecus_by_protocol` → `canlib/ecus.py`**. It is a pure ECU-registry query
  (`resolve_tx` + `ecu_id_protocol`) and a **safety boundary** (sending UDS `0x31`
  to a KWP2000 ECU means StartRoutine, which actuates) — it should not sit in a
  CLI helper. `parse_range` follows it down (used only by `dispatch_mode` today).

### The one design change: inject the reconnector

`dispatch_mode:831` hardcodes `reconnect=build_elm_reconnector(args, pids_data)` —
a **command-layer** call inside the function we are moving to `canlib/modes/`.
Moving it as-is would recreate the inversion in the opposite direction.

Fix: make `reconnect` a **parameter** of `dispatch_mode`. The ELM caller
(`_live/runtime.py::async_main`) builds it and passes it; the raw path already has
its own `build_raw_reconnector` in `modes/raw_monitor.py`. This is a small,
principled change that also removes a latent wrong-coupling — the "shared,
transport-agnostic" dispatcher currently hardcodes the *ELM* reconnector, which is
only harmless because raw monitoring bypasses `dispatch_mode` entirely.

**Rejected alternative:** move `connect_elm_terminal`/`build_elm_reconnector` down
to `canlib/transport/`. Defensible on paper (ELM construction + init is
transport-layer), but they read the argparse `args` namespace directly, so it
needs an options-object refactor of the connect path — real behavioural risk on
the live path, for no gain here. Flagged, not fixed.

---

## Safety nets

The refactor is mechanical; the gates are what make that claim checkable.

1. **`make gen-check`** — `scripts/gen_cli_reference.py --check` regenerates
   `docs/reference/cli/*.md` from live `--help`. Any parser drift in Parts 2–4
   fails here. This is the strongest existing gate and it is free.
2. **Goldens, extended first (commit 1 of each part).** `tests/_golden.py` +
   `tests/fixtures/golden/` already hold **34 golden files** across the two golden
   modules, but for the analysis commands they cover only the **byte-label** paths
   (`--dump-bytes`, `--discriminate`, `--find-mirrors`, `--bits`). The default
   value-range view, `--compact`, `--stats`, correlate's default ranked list and
   investigate's default table are **unpinned** — exactly what a mechanical move
   breaks silently. Add them **before** moving code.
   - They cannot go in `test_analysis_golden.py::CASES`: its
     `test_goldens_contain_byte_labels` gate (`:295`) would reject a
     `--stats`/`--compact` golden and its docstring scopes it to byte-label
     emission. New module, per the captures precedent.
   - **PII rule holds:** any case rendering free-text `label`/`notes` is pinned
     against `tests/fixtures/profiles/single-frame`, never `ioniq-2017`.
3. **`uv run ty check`** over `canlib/` catches a mis-rewired import or a lost
   annotation at the new module boundaries.
4. **Run serially as well as in parallel** (`pytest -n0`) for anything touching
   the Textual surfaces (`decode --plot`).
5. **Baseline: 5075 tests collected, `make check` green.** Record the number in
   each commit message; the count may only go up.

### Where the cost actually is

- **9 test modules** import `commands._live` (`test_dtc`, `test_ecu_groups`,
  `test_kwp_routines_scan`, `test_live_dispatch`, `test_monitor_command`,
  `test_raw_ops`, `test_read_command`, `test_skm_wakeup`, `test_suite_isolation`).
- **7 test modules** reach into the analysis commands' internals
  (`test_correlate`, `test_decode_plot`, `test_decode_query`, `test_hunt`,
  `test_investigate`, `test_tui_help`, `test_xanalysis`).
- Several use `monkeypatch.setattr("canlib.commands.correlate.load_signal_captures", …)`
  — a **module-attribute** patch that must retarget to where the name is *used*
  (e.g. `…correlate.series.load_signal_captures`). This fails loudly:
  `monkeypatch.setattr` raises `AttributeError` on a missing attribute unless
  `raising=False`, and **no affected test passes `raising=False`** (verified: zero
  occurrences in `test_correlate`/`test_investigate`/`test_hunt`/`test_decode_plot`/
  `test_decode_query`). So a missed retarget is a hard failure, not a silently
  un-patched test.

---

## Sequencing

Small, independently-green commits. Each part's goldens land **before** its moves.

| # | Commit | Risk |
|---|---|---|
| 1 | `docs:` adopt the command-package trigger rule (skill) | none |
| 2 | `test:` pin the decode/correlate/investigate default views with goldens | none |
| 3 | `refactor:` push `decode_payload`/`load_captures`/`payload_to_wican_bytes` down to the library | low |
| 4 | `refactor:` push `_discover_specs` down to the library | low |
| 5 | `refactor:` `decode` becomes a package (rename only) | low |
| 6 | `refactor:` split the decode package by concern (`_decode_one` → per-section functions, `run` guards → table, `render`/`plot`) | medium |
| 7 | `refactor:` `correlate` becomes a package + split | low-medium |
| 8 | `refactor:` `investigate` becomes a package + split | low-medium |
| 9 | `refactor:` move `dispatch_mode`/`run_session_guarded` to `canlib/modes/dispatch/`; inject `reconnect` | **medium-high** |
| 10 | `refactor:` `wants_save` → `keepmode`, `split_ecus_by_protocol`/`parse_range` → `ecus` | low |
| 11 | `refactor:` `_live` becomes a package (rename + section split) | medium |
| 12 | `docs:` retarget the module paths in `AGENTS.md` + skills | none |

Do **renames as pure renames** so git tracks them (the captures split's commit 2
produced 87/80 lines of import rewiring instead of a 3000-line add/delete — do
that again). Commit 9 is the one to be slow about: it is the live device path, and
`test_live_dispatch.py` has only 4 tests. Lean on `test_dtc.py::TestDispatchTransportAgnostic`,
`test_raw_ops.py` and `test_skm_wakeup.py`, and add a dispatch-table test with the
selector matrix as part of the same commit.

**Precondition:** start from a clean tree. It is currently dirty with unrelated
in-flight work (`commands/coverage.py`, `profiles/ioniq-2017/ecus/obc.yaml`, three
goldens, `tests/test_coverage.py`, plus an untracked bitfield-audit plan) — commit
or stash that first, or git's rename detection and the review both get muddier.

---

## Docs and skills to retarget (commit 12)

Verified references to paths this plan moves:

- `AGENTS.md:143` — `build_elm_reconnector` in `canlib.commands._live` →
  `commands/_live/connect.py`.
- `.claude/skills/contributing-code/SKILL.md:102` —
  `canlib/commands/_live.py::dispatch_mode` → `canlib/modes/dispatch/`.
- `.claude/skills/contributing-code/SKILL.md:205` — `_live.py` /
  `finalize_live_parser` → `commands/_live/parser.py`.
- `.claude/skills/decode-bitfields/SKILL.md:148` — `_investigate_render.py:193-201`
  → `investigate/render.py`.
- `canlib/inspect_bytes.py:13` — a **docstring** reference to `commands/_decode_plot`
  → `commands/decode/plot.py`. (Not an import; it is the only mention of a moving
  module outside `commands/`, found by grepping for the path rather than the symbol.)

`AGENTS.md:81` (`_join.py::add_join_args`) and `AGENTS.md:87` (the `contribute`
three-layer note) are **unaffected** — those modules stay put. No `docs/` page
references any private command module.

---

## Deliberately out of scope (flagged, not fixed)

- **`contribute` (787/3) and `sniff` (399/2)** — below the bar. Convert
  opportunistically when next touched, not as a campaign.
- **`bix.py` (985), `ecu.py` (919), `pids.py` (841), `hunt.py` (751),
  `wican.py` (719)** — single-file monoliths over the smell line. Each needs a
  by-concern split on its own merits; under the new rule that split lands as a
  package. Separate plans.
- **Part A of `plans/2026-08-05-layering-and-module-size-followups.md`** — the
  validators are library API in the command layer (`validate/pids.py` = 1441
  lines, `load_schema` calls `sys.exit(1)` from a path `ecus_edit.py` reaches).
  Genuinely bigger, needs its own golden-first sequence, and Part 4 here does not
  touch it.
- **Part B of the same plan** — `captures/step_model.py` (764) and `step_tui.py`
  (622). Note the tension: those live *inside* the package this plan holds up as
  the model. Landing Part B first is defensible; it was offered and not chosen, so
  it stays queued.
- **`connect_elm_terminal` down to `canlib/transport/`** — see Part 4's rejected
  alternative (needs an args→options refactor of the live connect path).
- **A third hex-range parser.** `commands/_live.py:146::parse_range` (raises
  `argparse.ArgumentTypeError`) and `canlib/modes/iocontrol_scan.py:148::_parse_range_str`
  (returns `None`) parse the same `START-END` hex form with different failure
  modes; `canlib/physical_bands.py:51::_parse_range` is a different thing (float
  bands). Worth unifying the first two when commit 10 moves `parse_range` — but
  their error contracts differ, so it is a behaviour change, not a move.
- **The mode-as-flag redesign** for `decode`/`correlate` (sub-subcommands instead
  of mutually-exclusive analysis flags) — user-facing, needs its own deprecation
  path, same argument as the one recorded for `captures uds`.
