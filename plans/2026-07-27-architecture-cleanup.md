# canair — Architecture & Organization Cleanup

Status: **Part C DONE** (C1–C6 all landed 2026-07-27, C6 with explicit sign-off).
**Part D remains** — but it is feature work, not organization cleanup, and is
owned by `plans/2026-07-24-raw-can-analysis.md`; pull it forward only if
prioritized, and only after the redesign conversation that plan's deferred
Stage-4 items need. Findings of a 2026-07-27 architecture /
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

**DONE (2026-07-27).** Created `canlib/inspect_bytes.py` holding `INSPECT_TYPES`,
`POST_TRANSFORMS`, `interpret_bytes`, `float_series_is_noise`, `wican_expr`,
`apply_transform`, and `norm01` (the former `_decode_plot._norm01`). `_decode_plot`,
`decode`, `_decode_calc`, `_decode_render`, `xanalysis`, and `frame_series` all now
import **down** from `canlib.inspect_bytes`; the lazy in-function imports in
`xanalysis` (3×) and `frame_series` (1×) that dodged the old cycle are gone (top-level
now). New `tests/test_inspect_bytes.py` covers all primitives; `test_decode_plot`,
`test_notation`, and `test_frame_series`/`test_xanalysis` stay green (2433). No dead
re-export shims left — call sites were rewired. `--help` + real analysis runs verified
unchanged.

<details><summary>Original plan text</summary>

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

</details>

### C2. Finish audit item #4 — relocate `decode.py`'s analysis math to the library

**DONE (2026-07-27).** Refined against the post-C5 tree (the analysis math already
lived in the command-adjacent `_decode_calc.py`, not `decode.py`):

- **Generic positional mirror core pushed down.** `xanalysis.find_frame_mirrors(frames,
  *, bits)` now holds the intra-frame (single-PID, positionally-aligned) mirror
  algorithm; `_decode_calc.find_mirrors` is a thin adapter that extracts each
  capture's WiCAN frame from decode's `all_results` and delegates.
- **The two duplicate time-aligned mirror primitives collapsed.**
  `align.aligned_all_equal` (sorted-`TimePoint`-list, datetime arithmetic) was a
  near-duplicate of `align.mirror_aligned_count` (the faster `PreparedSeries`
  epoch-float path). `aligned_all_equal` was **deleted**; `correlate`'s cross-ECU
  (`_print_cross_mirrors`) *and* cross-ID (`_print_can_mirrors`) renderers both now
  route through the single `mirror_aligned_count` primitive (same `prepare_series` +
  `n >= min_n` pattern). Three parallel scans → one generic positional finder + one
  generic time-aligned primitive.
- **Deliberately NOT moved:** the decode-shaped `all_results` glue (`_series`,
  `_paired`, `_paired_timed`, `_local_series`, `load_cross_ref_series`,
  `_transform_series`). The plan (pre-C5) targeted these for `xanalysis`/`align`, but
  pushing decode's `{capture, decoded, error}` shape into the generic engine would
  invert the layering the contributing skill mandates ("generalize the shared layer
  rather than special-casing"). They stay as thin command-adjacent adapters in
  `_decode_calc.py` (their C5 home), over the generic `TimePoint`/`PreparedSeries`
  primitives. This supersedes the plan's original "move everything down" wording.
- Corrected the stale DONE mark in
  `plans/2026-07-25-architecture-audit-and-transport-fixes.md` item #4.
- **Tests:** `tests/test_xanalysis.py::TestFindFrameMirrors` (generic core), the
  existing `_decode_calc.find_mirrors` delegation tests, and `test_align`'s new
  `TestMirrorAlignedCount` (retitled from the deleted `aligned_all_equal`). 2438 green;
  `decode --find-mirrors` / `correlate --find-mirrors` output verified unchanged.

<details><summary>Original plan text (superseded)</summary>

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

</details>


### C3. Extract `wican.py` profile-transform logic to the library

**DONE (2026-07-27).** Created `canlib/autopid_profile.py` (238 lines) holding the
pure transforms `generate_profile`/`to_device_format`/`normalize_device_profile` +
their helpers `make_pid_init` and `DuplicateParameterError`. A **new** module (not
folded into `autopid_layout.py`) so byte-layout reconstruction and profile-dict
transforms stay single-purpose. `wican.py` imports them (868 → 653 lines) and keeps
the HTTP upload/download, diff rendering, stats table, and argparse. Updated the
doc-comment in `validate/pids.py` to the new location and re-pointed
`test_status_vocab.py`'s import. New `tests/test_autopid_profile.py` covers the
device-format conversion + normalize round-trip (`test_status_vocab` still covers the
shipping gate / dup-name rejection). 2446 green; `wican autopid write`/`stats` output
verified unchanged.

<details><summary>Original plan text</summary>

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

</details>

### C4. Shared fake-terminal test fixture

### C4. Shared fake-terminal test fixture

**DONE (2026-07-27).** Added `tests/_fakes.py` with a single `FakeTerminal`
exposing the faithful async surface (`set_header`/`send_uds`/`send_command`/
`enter_extended_session(wake, mode)`/`close`), a scriptable per-request response
map (req-keyed or `(header, req)`-keyed via `key_by_header`), a configurable
`default` (NO DATA / NRC), `flaky_recover` (the old `FlakyTerminal` retry
behaviour), `session_result`, and uniform recorders (`sent`/`headers`/`sessions`/
`calls`/`uds_kwargs`), plus `ok()`/`nrc()`/`NO_DATA` builders. The `mode=`
dual-transport contract is first-class (recorded in `sessions`). All 9 files
migrated: `test_dtc`, `test_identity`, `test_session_manager` (factory with
`key_by_header`+`send_command_reply`), `test_discovery_scan`, `test_sessions_scan`
(`.reqs`→`.sent`), `test_discover_identify` (thin subclass keeping
`session_result=(None, None)` + its `SweepTerminal` override), `test_kwp_routines_scan`
& `test_kwp_iocontrol_scan` (inline per-test fakes → response map + `.uds_kwargs`
assertions), and `test_multi_batching` (static closures → `FakeTerminal.send_uds`;
the one kwargs-computed parse test stays a local closure). Added
`tests/test_fake_terminal.py` (11 contract tests). 2457 green.

<details><summary>Original plan text</summary>

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

</details>

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
- **`correlate.py` (1099 → 818). DONE (2026-07-27).** Extracted the self-contained
  sub-report renderers (`_color_r`/`_print_overlap`/`_print_cross_mirrors`/
  `_print_can_mirrors`) into `commands/_correlate_render.py`; moved the gate helpers
  (`_parse_gate`/`_apply_gate`/`_GATE_OPS`) into `commands/_correlate_calc.py`; and
  lifted the cluster helper to the library as `xanalysis.colinear_clusters` (it
  operates on `CorrHit`, which lives there — reusable). `correlate.py` keeps the
  ranked-pair / `--against` / matrix orchestration in `run`/`_run_can_log` and
  imports the moved helpers back. Behavior byte-identical (`correlate`, `--overlap`,
  `--find-mirrors`, `correlate can` verified). Tests updated to the new homes
  (`test_xanalysis` → `xanalysis.colinear_clusters` + `_correlate_calc._parse_gate/
  _apply_gate`); 2457 green.
- **`investigate.py` (1012 → 697). DONE (2026-07-27).** Extracted the event/edge
  timeline + per-byte report renderers (`print_report`/`print_events`/
  `print_field_events`/`iter_edges`/`iter_field_edges`/`print_keep_banner`/`cap_note`)
  into `commands/_investigate_render.py`; kept the two orchestrators (`run`/`_run_can`)
  + scoring (`_best_anchor`/`_driver_r`/`_independence_score`/`_word_expr`) and the
  `_ByteReport` dataclass in the command. Behavior byte-identical (`investigate`,
  `--events`, `--events --field` verified); `test_investigate` re-pointed at the
  render module. 2457 green.

<details><summary>Original plan text</summary>

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

</details>

### C6. God-object decomposition (higher-risk, later)

**DONE (2026-07-27, with explicit sign-off).** Both remaining >900-line god
objects shed a focused collaborator via the delegate-to-collaborator pattern the
`modes/` files already use (`MonitorRawPoller`/`MonitorEditor`):

- **`MonitorController` (929 → 759).** Recording/journaling extracted to
  `modes/_monitor_record.py::MonitorRecorder` (frame counters, display/`--save`
  history, write-ahead journal, on-demand save, segment rotation, segment
  metadata + the `_merge_history`/`_write_merged`/`_open_journal` helpers). The
  controller keeps poll/display state (`prev_hex`/`decoded_values`) the recorder
  reads via its back-ref, and exposes the tested surface (`total_frames`/
  `unique_frames`/`journal`/`session_*`/`save_now`/`new_segment`/`has_captures`/
  `_record`) via thin delegating properties/methods, so the 160 monitor tests +
  TUI/renderer are unchanged.
- **`_IOControlTUI` (916 → 685).** CAN-facing session + actuation extracted to
  `modes/_iocontrol_actuate.py::IOControlActuator` (`ensure_session`/`send_on`/
  `send_off`/`toggle`/`send_adjust`/`poll_status_once`/`status_poll_loop`/
  `release_all`/`cleanup`/`extract_status_bytes`). Pure behaviour move — all
  state stays on the TUI, the actuator reads/updates it through a **typed**
  back-ref (`self.t: _IOControlTUI`). The TUI has no automated tests, so the
  typed back-ref is the structural guard: `ty` verifies every state access. Both
  extractions verified with `ruff`/`ty`/full suite (2461 green) + smoke.

<details><summary>Original plan text</summary>

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

</details>

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
