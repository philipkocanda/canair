# canair — Architecture & Organization Cleanup

Status: implementation plan (backlog). Findings of a 2026-07-27 architecture /
code-organization review, measured against the house standard in
`.claude/skills/contributing/SKILL.md` ("Refactor proactively — no monoliths":
file size is a smell approaching ~500 lines, *well* before 1000; prefer plain
functions over god objects; commands are argparse + orchestration, not a
computation/rendering layer).

This is the **successor to `plans/2026-07-25-architecture-audit-and-transport-fixes.md`**:
that audit fixed the confirmed dual-transport bugs and completed the first wave of
splits (`pids_edit/`, `validate/`, `multi.py`, stats consolidation, renderer
extraction from the two god objects, `wican_bytes.py`→`autopid_layout.py`). This
plan tracks the **residual structural debt** that audit recorded as backlog and
that the 2026-07-27 review re-confirmed against the current tree.

Each item is independently shippable — tackle as separate, individually-scoped,
test-backed changes, **not one mega-PR**. Every item ships with tests (regression
where behavior could shift) and, where a user-facing surface moves, a docs pass.
No user-facing behavior should change in items C1–C6; these are pure internal
moves/splits — the regression bar is "tests + `ruff` + `ty` stay green, `--help`
and command output byte-identical."

Verification gate for every item (contributing skill "Before you finish"):

```
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair <touched-cmd> --help    # parser sane, output unchanged
```

Baseline pointers (verify before editing — these drift):

- Layering: `cli.py` → `commands/` (argparse + orchestration) → `modes/` (async
  device I/O) → library (`xanalysis`/`align`/`stats`/`expression`/`decode_value`/
  `notation`/`byteindex`) → `transport/` + `schema/`.
- Command contract: `commands/__init__.py` (`NAME`/`add_parser`/`run`,
  `COMMAND_NAMES` = help order). Live commands delegate `run` to
  `commands/_live.py` via `finalize_live_parser`.
- Shared analysis core (leaf→engine): `stats.py` → `expression.py`/
  `decode_value.py` → `align.py` → `xanalysis.py`; frame domain via
  `frame_series.py` + `can_logs.py`.

---

## Confirmed findings (2026-07-27, verified against tree)

Line counts over the ~500 "smell" line (worst first):

| Lines | File | Concerns mixed |
|------:|------|----------------|
| 1628 | `commands/decode.py` | pure decode helpers + a local correlation/mirror/series stack + 7 `print_*` renderers + byte-matrix dump + argparse/orchestration |
| 1083 | `commands/correlate.py` | 264-line `run`, separate CAN path, cluster/gate math, 2 mirror renderers, promotion |
| 961  | `commands/investigate.py` | `_ByteReport` + two orchestrators + scoring math + ~230-line event-timeline renderer |
| 951  | `commands/_live.py` | connection lifecycle + `dispatch_mode` |
| 946  | `commands/captures.py` | query + rendering |
| 916  | `modes/iocontrol.py` | god object `_IOControlTUI` (>900 after renderer extraction) |
| 912  | `modes/monitor.py` | god object `MonitorController` (~30 methods) |
| 868  | `commands/wican.py` | **library-grade profile-transform logic** + HTTP I/O + diff + argparse |
| 767  | `xanalysis.py` | shared engine (acceptable size; see C1 layering wrinkle) |

Confirmed specifics:

1. **Layering inversion (verified).** The library engine imports byte-interpretation
   primitives *up* from a command helper: `xanalysis.py` imports from
   `canlib.commands._decode_plot` at lines 132, 476, 688; `frame_series.py:163`
   does too. The primitives — `INSPECT_TYPES`, `POST_TRANSFORMS`, `interpret_bytes`,
   `float_series_is_noise`, `wican_expr`, `apply_transform` — are pure and depend
   only on library modules (`byteindex`/`states`/`stats`). `_decode_plot.py:13-16,
   35-37` **documents this awkwardness itself** ("live in this leaf module and are
   re-exported by decode … so this leaf module has no import-time dependency on
   decode"). It is worked around with lazy in-function imports to dodge a cycle.

2. **`decode.py` still owns analysis math that belongs in the library (verified).**
   `find_mirrors` (`decode.py:764`), `_series`/`_paired`/`_paired_timed`/
   `_local_series` (487–534), `load_cross_ref_series` (555), `_transform_series`
   (921). Nothing outside `decode.py` imports these anymore (the old
   `investigate.py → decode.py` leak is fixed), so they are free to move. There are
   **three parallel "mirror" implementations**: `decode.find_mirrors` (single-PID,
   intra-capture, not time-aligned), `correlate._print_cross_mirrors` (cross-ECU,
   time-aligned), and the primitive `align.aligned_all_equal`.

3. **`wican.py` holds library-grade profile-transform logic (verified).**
   `generate_profile` (130), `to_device_format` (207), `normalize_device_profile`
   (268) are pure dict transforms with no argparse/device coupling; only
   referenced within `wican.py` (+ a doc-comment in `validate/pids.py:1006`). They
   belong beside `autopid_layout.py`, leaving `wican.py` as orchestration.

4. **Fake-terminal duplication in tests (verified).** 9 test files each hand-roll
   their own async fake terminal (`MockTerminal`/`FakeTerminal`/`FlakyTerminal`/
   ad-hoc `AsyncMock`): `test_dtc`, `test_identity`, `test_session_manager`,
   `test_multi_batching`, `test_discovery_scan`, `test_sessions_scan`,
   `test_discover_identify`, `test_kwp_routines_scan`, `test_kwp_iocontrol_scan`.
   `tests/conftest.py` only pins the profile; no shared fake fixture.

5. **Two-domain maturity asymmetry (already in the prior audit backlog).**
   Raw-CAN (domain B) has ingest + read-only analysis (`correlate can`/`hunt can`)
   but no `decode`/`investigate`/`coverage` `can` kind, no frame `--promote`, and
   `sniff --save` writes a file that never feeds the capture store/journal. Kept
   here as a pointer; these are *feature* work, tracked in
   `plans/2026-07-24-raw-can-analysis.md`, not pure organization — see Part D.

---

## Part C — Structural cleanup (pure internal moves/splits, no behavior change)

Ordered lowest-risk / highest-unblock first. C1 → C2 are the natural pair
(fix the inversion, then finish the migration); C3–C4 depend on nothing.

### C1. Fix the layering inversion — move byte-interpretation primitives to the library

**Move** `INSPECT_TYPES`, `POST_TRANSFORMS`, `interpret_bytes`,
`float_series_is_noise`, `wican_expr`, `apply_transform` out of
`commands/_decode_plot.py` into a neutral library module. Candidate home: a new
`canlib/inspect_bytes.py` (single-purpose: "read raw payload bytes at an offset as
a typed value; post-process a series") — cleaner than overloading `byteindex.py`
or `notation.py`, and mirrors the `uds_parse`/`frame_series` leaf style.

- New module depends only on `byteindex`/`states`/`stats` (already its current
  deps). No cycle.
- `_decode_plot.py`, `decode.py`, `xanalysis.py`, `frame_series.py` all import
  **down** from `canlib.inspect_bytes`. Delete the lazy in-function imports in
  `xanalysis.py` (132/476/688) and `frame_series.py:163` — they become top-level.
- Keep `decode.py`'s re-export shim (`from canlib.inspect_bytes import …`) if any
  test/consumer imports these names via `decode` or `_decode_plot`; grep first
  (`from canlib.commands._decode_plot import`, `from canlib.commands.decode import`)
  and update call sites rather than leaving dead re-exports.
- **Test:** `tests/test_inspect_bytes.py` — round-trip a few `interpret_bytes`
  specs (endianness, OOB→None, float NaN), `float_series_is_noise` bounds,
  `wican_expr` big-endian/little-endian/float cases. `test_xanalysis`/
  `test_frame_series` already exercise the callers; keep them green.
- **Risk:** low (pure move + import rewiring). Watch for a stray cycle via
  `_decode_plot`'s local color constants — leave those in `_decode_plot`.

### C2. Finish audit item #4 — relocate `decode.py`'s analysis math to the library

Depends on nothing but reads cleaner after C1.

- Move `find_mirrors`, `_series`, `_paired`, `_paired_timed`, `_local_series`,
  `load_cross_ref_series`, `_transform_series` from `decode.py` into `xanalysis.py`
  (series/mirror math) / `align.py` (the pairing helpers, next to
  `join_nearest*`). Keep public names stable where a name is meaningful; prefix
  internal-only movers.
- **Collapse the three mirror implementations** onto the `align.aligned_all_equal`
  primitive: have `decode`'s single-PID mirror finder and `correlate`'s cross-ECU
  mirror renderer both call one shared `xanalysis` function parameterized by the
  grouping (intra-capture vs time-aligned cross-signal), rather than three
  hand-rolled equality scans.
- Correct the **stale DONE mark** in
  `plans/2026-07-25-architecture-audit-and-transport-fixes.md:111-114` (item #4
  claims `find_mirrors` already moved — it had not; note it as completed *here*).
- **Test:** existing `test_decode_*`, `test_correlate`, `test_xanalysis` must stay
  green (behavior identical). Add a focused `test_xanalysis` case asserting the
  unified mirror function reproduces both the single-PID and cross-signal results.
- **Risk:** low–medium (the three mirror variants have subtly different grouping;
  unify carefully and lean on the existing command tests as the oracle).

### C3. Extract `wican.py` profile-transform logic to the library

- Move `generate_profile`/`to_device_format`/`normalize_device_profile` (+ their
  private helpers) into a library module beside `autopid_layout.py` — either fold
  into `autopid_layout.py` (it is already the generic AutoPID-layout home) or a new
  `canlib/autopid_profile.py` if `autopid_layout.py` grows past ~500 with it.
- `wican.py` imports them; the pure transforms gain independent unit tests
  (currently only exercised end-to-end via the command).
- Update the doc-comment reference in `validate/pids.py:1006` to the new location.
- **Test:** `tests/test_autopid_profile.py` — feed a small grouped-`ecus` dict
  through `generate_profile` → `to_device_format`, assert device-format shape;
  `normalize_device_profile` round-trip. Keep `test_wican*` (if present) green.
- **Risk:** low (self-contained pure functions).

### C4. Shared fake-terminal test fixture

- Add one async fake terminal exposing the documented surface
  (`set_header`/`send_uds`/`send_command`/`enter_extended_session`/`close`) to
  `tests/conftest.py` (or `tests/_fakes.py` imported by conftest), scriptable with
  a per-PID response map + failure/`NO DATA`/NRC injection to cover the 9 files'
  current needs (including `FlakyTerminal`'s retry behavior).
- Migrate the 9 files onto it incrementally; keep each file's specific scripting
  local, share only the surface + dispatch plumbing.
- Preserve the `mode=`-aware `enter_extended_session` surface the
  `test_discovery_scan` double encodes (the dual-transport contract regression the
  prior audit fixed) — the shared fake must accept `mode=`.
- **Test:** the migration *is* the test (existing suites stay green); add a tiny
  `test_fake_terminal.py` documenting the fixture's contract so it doesn't drift.
- **Risk:** low, but touches many files — do it as one mechanical PR after C1–C3.

### C5. Split the big analysis commands (renderer extraction)

Follow the existing precedent (`_decode_plot.py`, `modes/_monitor_render.py`,
`modes/_iocontrol_render.py`): peel the presentation layer into `_*_render.py`
siblings so each command drops back toward argparse + orchestration.

- **`decode.py` (1628 → 839). DONE (2026-07-27).** Extracted the presentation
  layer (`print_compact`/`print_value_ranges`/`print_stats_table`/
  `print_stats_grouped`/`print_discriminate`/`print_mirrors`/`print_correlations`,
  `format_value`/`check_range`/`scope_banner`/`_mark_for` + the byte-matrix dump
  `_dump_bytes`/`_dump_column_label`) into `commands/_decode_render.py`, and the
  decode-shaped analysis/series math (`_series`/`_paired`/`_paired_timed`/
  `_local_series`/`load_cross_ref_series`/`find_mirrors`/`_transform_series`) into
  `commands/_decode_calc.py`. decode.py is now core decode logic (`load_captures`/
  `scope_captures`/`decode_payload`/`parse_try_expr`/`build_try_params`/
  `resolve_ref`) + argparse + `run` orchestration. Behavior byte-identical
  (`--help` + all view modes verified); 2400 tests green, `ruff`/`ty`/format clean
  on touched files. **Deferred (still open under C2):** the calc helpers landed in
  a command-adjacent `_decode_calc.py` rather than being pushed down into
  `xanalysis.py`/`align.py`, and the three parallel mirror implementations
  (`_decode_calc.find_mirrors`, `correlate._print_cross_mirrors`,
  `align.aligned_all_equal`) are **not** yet collapsed. Doing that now would
  conflict with the in-flight uncommitted `xanalysis.py`/`align.py` work; revisit
  once that lands.
- **`correlate.py` (1083):** extract `_print_cross_mirrors`/`_print_can_mirrors`/
  overlap+matrix renderers into `commands/_correlate_render.py`; split the 264-line
  `run` — lift the cluster (`_colinear_clusters`) and gate (`_parse_gate`/
  `_apply_gate`) helpers to `xanalysis.py` if reusable, else a `_correlate_calc.py`.
- **`investigate.py` (961):** extract the ~230-line event/edge-timeline renderer
  (`_iter_edges`/`_iter_field_edges`/`_print_field_events`/`_print_events`/
  `_print_report`) into `commands/_investigate_render.py`; keep the two
  orchestrators + scoring in the command.
- Each extraction is behavior-preserving: **the command's stdout must be
  byte-identical.** Verify with a before/after capture of representative
  `--help` + real runs on the bundled `ioniq-2017` profile.
- **Test:** existing per-command tests are the oracle; add nothing new unless a
  renderer becomes independently unit-testable (then a small `test_*_render.py`).
- **Risk:** medium (large mechanical moves; the win is real but do one command per
  PR, smallest first — `investigate` renderer is the most self-contained).

### C6. God-object decomposition (higher-risk, later)

- `MonitorController` (`modes/monitor.py`, 912) and `_IOControlTUI`
  (`modes/iocontrol.py`, 916) remain >900 after the prior renderer/poller
  extraction. Decompose further into focused collaborators (recording/journaling
  vs display vs request-building vs state-suggestion for the monitor; probe/actuate
  vs TUI-state for iocontrol) behind the existing tested public surface.
- **Test:** `test_monitor_tui.py` (25 K) + monitor/iocontrol suites are the guard;
  keep the public method surface stable so tests don't churn.
- **Risk:** higher (TUI + async device state). Do **only after** C1–C5 land and
  with explicit sign-off — this is the "propose a redesign before adding a layer"
  case, not a quick move. Surface the decomposition sketch to the user first.

---

## Part D — Two-domain symmetry (feature work, cross-reference only)

Not organization cleanup — recorded so C-work doesn't re-discover it. Tracked in
`plans/2026-07-24-raw-can-analysis.md`; pull forward only if prioritized:

- `decode`/`investigate`/`coverage` gain a `can` kind (raw log analysis parity
  with `correlate can`/`hunt can`).
- Frame `--promote` (write a hunted frame byte into the `signals/` linear model).
- `sniff --save` feeds the shared capture store/journal (crash-recovery parity),
  not just an external file.
- `signals` authoring parity with `pids` (`rename-signal`, fit/overlap/collision
  validation in `check_signals_doc`).

---

## Suggested sequencing

1. **C1** (layering inversion) — smallest, safest, unblocks clean imports for C2/C5.
2. **C2** (finish math migration + collapse mirrors) — reads cleaner post-C1.
3. **C3** (wican transforms → library) — independent, quick.
4. **C4** (shared fake terminal) — one mechanical test PR.
5. **C5** (renderer extraction) — one command per PR, `investigate` → `correlate`
   → `decode`.
6. **C6** (god objects) — only with explicit sign-off after C1–C5.

C1–C4 are safe, high-confidence, no behavior change. C5 is the bulk of the
line-count win. C6 and Part D need a redesign conversation first.
