# Cross-Signal Analysis for `decode` — Implementation Plan

Make **cross-ECU / cross-PID correlation, byte hunting, and unit/relationship
inference** first-class `canair` primitives instead of scratch scripts.

Motivated by the 2026-07-23 RE session: using ESC vehicle speed and MCU motor
RPM as anchors, a hand-written time-join + Pearson scan in `/tmp` cracked
**AAF 2181 B12/B13 = speed (MPH/km-h)**, **VCU 2101 speed** (promoted the
existing MPH formula, cross-verified r=0.999), and **MCU 2101 B22 = inverter
temp** (found by RPM correlation, *confirmed* by charging-vs-driving state
contrast). Every one of those wins required leaving the tool: manual timestamp
parsing, nearest-neighbour joins, linear fits, and unit guessing. This plan
turns those techniques into supported commands.

Approach: **build the alignment foundation first (Tranche 0), then the two
highest-value verbs (Tranche 1), then the inference/UX helpers (Tranche 2),
then hygiene (Tranche 3).** Each tranche lands with unit tests + `pytest`/`ruff`
green. All work is **read-only analysis over existing captures** — no device,
no transport, no schema-of-record change (decoded values stay regenerated, never
persisted).

## Decisions (locked)

- **Sequencing:** Tranche 0 → 1 → 2 → 3. Tranche 0 (alignment) is a hard
  prerequisite for everything else and ships standalone with tests.
- **Alignment key:** parse `(date, time)` into a real `datetime` once at load
  (new `capture_dates.entry_datetime`). Captures with no usable `time` (date-only
  rows) are **excluded from time-aligned analysis** but still counted/reported, so
  a sparse ECU never silently poisons a join.
- **Join semantics:** **nearest-neighbour within a tolerance** (default 2.5 s,
  `--join-tol` to override), asymmetric onto a chosen reference series. This
  matches the sequential single-connection polling model (per-cycle timestamps,
  ~0.3–3 s inter-ECU skew — see `MCU 2102` decode lead). Not interpolation: a
  reference point with no candidate inside tol is dropped, and the realised `n` is
  always reported so a thin overlap is visible, never hidden.
- **No numpy dependency (yet).** Reuse the hand-rolled `_pearson`/`_mean`/… ;
  they're correct at current scale (≤ a few thousand points). Revisit only if the
  full-session matrix (Tranche 1.2) is too slow — gate any numpy use behind an
  optional import with a pure-python fallback (repo has none today).
- **Cross-signal reference syntax:** `ECU:PID:PARAM` (param) or
  `ECU:PID:EXPR` where EXPR contains a byte token (raw expression, e.g.
  `MCU:2102:[S10:S11]`). Disambiguated by "does it match a defined param name for
  that PID?". Single-token `PARAM` (no colons) keeps the current same-PID
  behaviour, so nothing existing breaks.
- **Promotion is opt-in and explicit.** Analysis commands never auto-write
  `ecus/`. A `--promote NAME` flag hands off to the existing
  `pids upsert-param` path (enabled + `--unverified`, evidence auto-filled into
  `notes`). Default remains read-only.

## Confirmed facts (grounding, file:line)

- `decode` owns a **private, single-(ECU,PID) capture loader**
  `load_captures()` (`canlib/commands/decode.py:72`); the general flat loader is
  `load_all_captures()` (`canlib/commands/captures.py:170`). Two loaders =
  consolidation candidate.
- `--corr` is `print_correlations()` (`decode.py:592`) over `_pearson()`
  (`:446`) + `_paired()` (`:469`). `_paired` pairs values **by list position
  within `all_results`**, which is only valid because every candidate is decoded
  from the *same* payload (same ECU+PID). **No cross-PID/ECU correlation, no time
  alignment.**
- Timestamps are strings: `entry_date()` parses the session `date`
  (`capture_dates.py:39`); the per-capture `time` field is free text, sorted
  **lexicographically** (`captures.py:592`). There is **no datetime join key** —
  the primary thing to add.
- Scope filters (`--since/--until/--date/--state/--label/--first/--last`) are
  shared via `add_scope_args()` (`capture_dates.py`), reused by `decode` and
  `captures`. New commands must reuse them verbatim.
- Expression eval: `evaluate_expression()` (`canlib/expression.py:9`); WiCAN
  frame (with PCI) reconstructed by `payload_to_wican_frame()`
  (`byteindex.py:221`) — used by both `decode.payload_to_wican_bytes()` (`:151`)
  and `bix --annotate`. `extract_byte_indices()` (`byteindex.py:170`) scrapes
  referenced indices and already knows PCI positions (powers the plot's
  "crosses PCI" and "already-mapped" flags).
- `--plot`'s interpretation sweep (`INSPECT_TYPES`, `interpret_bytes()`,
  `wican_expr()`, `decode.py:640/663/685`) already enumerates
  `u8/i16/f32/…×endian` and back-derives the WiCAN expression — **reusable as the
  candidate generator for byte hunting** (Tranche 1.3) instead of re-implementing.
- `decode.py` is ~1585 lines (loader + stats + corr + full TUI). Adding the
  matrix/hunt/align logic inline would worsen the largest module in the repo →
  new code lands in dedicated modules (`align.py`, `commands/correlate.py`).
- Existing tests to extend/mirror: `test_decode_try.py`, `test_decode_dates.py`,
  `test_decode_plot.py`, `test_byteindex.py`, `test_captures.py`.

---

## Tranche 0 — Alignment foundation (do first, ships standalone)

**0.1 Real datetime parsing.**
- Add `entry_datetime(entry) -> datetime | None` to `canlib/capture_dates.py`:
  combine `date` + `time` (`HH:MM:SS[.fff]`, tolerate missing ms / trailing
  timezone-free strings); return `None` when `time` is absent/unparseable.
- Leave existing string sorts intact (don't churn `captures.py` display paths).
- **Test:** `HH:MM:SS.fff`, `HH:MM:SS`, date-only → None, garbage → None.

**0.2 Multi-(ECU,PID) capture loader.**
- Add `load_signal_captures(specs, scope) -> dict[(ecu,pid)] -> list[record]`
  in **`canlib/align.py`** (new), delegating to the *general*
  `load_all_captures()` (retire `decode`'s private loader in Tranche 3.1). Each
  record carries the parsed `datetime`, `payload`, and scope metadata.
- Reuse `filter_by_date_range` / `filter_by_text` / first/last slicing so scope
  flags behave identically to `decode`.
- **Test:** multi-ECU fixture → correct grouping, scope filters applied,
  date-only rows retained but flagged `dt=None`.

**0.3 Nearest-neighbour join primitive.**
- `join_nearest(ref: list[(dt,val)], cand: list[(dt,val)], tol) -> (xs, ys, n)`
  in `align.py` (bisect over sorted candidate datetimes; drop ref points with no
  candidate within `tol`). `n` = realised overlap.
- `align_many(reference, others, tol)` → an aligned table (reference row + one
  column per other signal) for the matrix/`--compact` cross views.
- **Test:** skewed series (A@0.8s, B@0.9s, tol=1.0) → both join; tol=0.05 → n
  drops; identical timestamps → exact pairing.

**Rationale:** this is the single missing primitive. `--corr`, the session
matrix, and byte-hunt all reduce to "align these series, then Pearson." It also
removes the latent correctness assumption in `_paired` (positional pairing) for
the cross-signal case.

---

## Tranche 1 — The two highest-value verbs

**1.1 Cross-signal `--corr` reference.**
- Extend `resolve_ref()` (`decode.py:584`) + `print_correlations()` to accept
  `ECU:PID:PARAM` / `ECU:PID:EXPR`. When the ref is cross-PID/ECU, load that
  series via `align.load_signal_captures` and **time-align** (0.3) instead of
  positional pairing. Same-PID single-token refs keep the fast positional path.
- Report the realised `n` and `--join-tol` in the header so a thin join is
  obvious.
- Example (would have one-shotted the AAF find):
  `canair decode AAF 2181 --corr ESC:22C101:REAL_SPEED_KMH`
- **Test:** cross-ECU ref aligns + correlates on a two-ECU fixture; same-PID ref
  unchanged (regression); bad ref → clear error, not a crash.

**1.2 `canair correlate` — session correlation matrix (the ">2 PID" ask).**
- New command `canlib/commands/correlate.py`. Given a scope (`--session` /
  `--date` / `--state` / ECU-PID selectors via the existing `query.Query`
  mini-language), build **every decoded param + every non-constant raw byte**
  across all co-polled ECU/PIDs, time-align them all onto a common grid
  (`align_many`), and emit a ranked cross-signal correlation table.
- Modes: default = ranked `|r|` pair list (drop within-same-PID pairs unless
  `--include-intra`); `--matrix` = labelled r-matrix; `--against REF` = one
  column vs a reference (the anchor-hunt shortcut); `--json`; `--min-r`
  (default 0.6) and `--min-n` (default 15) thresholds; `--top N`.
- Skip constant / near-constant series (distinct ≤ 3) and PCI-crossing byte
  reads automatically (reuse `extract_byte_indices` / plot's PCI logic).
- Example:
  `canair correlate --state driving --date 2026-07-22 --min-r 0.7`
- **Test:** synthetic 3-ECU session where B_x = k·speed → matrix surfaces the
  pair at top; constants excluded; `--against` matches the pairwise result.

**1.3 `canair hunt ECU PID --against REF` — "which byte *is* this signal?".**
- New verb (its own module or a `correlate` subcommand). Sweeps every byte offset
  × interpretation (`u8/i8/i16/u16/f32/…×endian`) using the **existing
  `INSPECT_TYPES`/`interpret_bytes`/`wican_expr` machinery** (`decode.py:640+`),
  time-aligns each against REF, ranks by `|r|`, and for the top hits reports the
  **best linear fit** `value = m·ref + c` + residual + the ready-to-paste WiCAN
  expression. Skips PCI-crossing and constant candidates.
- This is the automation of the manual regression I ran 3× (AAF slope 0.624 ≈
  MPH; VCU B20 ≈ 0.25·km/h; MCU B22 vs RPM).
- Example: `canair hunt MCU 2101 --against ESC:22C101:REAL_SPEED_KMH`
- **Test:** planted byte = 0.6214·speed → top hit is that offset/interp with
  slope ≈ 0.6214, residual ≈ 0, and `wican_expr` = `Bn`.

---

## Tranche 2 — Inference & confirmation helpers

**2.1 State-discriminability ranking (`--discriminate state`).**
- Add to `decode` (and `correlate`): rank each byte/param by between-state vs
  within-state variance (F-like score) using the existing `_join_states` buckets
  (`print_stats_grouped`, `decode.py:554`). Surfaces signals that shift by *power
  state* rather than by a driving anchor — exactly how `MCU 2101 B22` was
  confirmed (charging 16–26 °C vs driving 7–104 °C).
- **Test:** byte constant-within-state but different-across-states ranks top;
  noise ranks bottom.

**2.2 Transform-aware correlation (`--corr-transform`).**
- Let the correlation reference apply a transform (`delta`, `abs`, `cumsum`,
  `normalize`) before pairing — reuse `POST_TRANSFORMS`/`apply_transform`
  (`decode.py:656/706`). Answers "is this the signal or its **rate**?" in one
  flag (the manual angle-vs-angle-rate check on EPS `[B12:S13]`).
- **Test:** ramp signal correlates with `delta` of its integral; position vs rate
  disambiguated.

**2.3 Mirror / redundancy detector (`--find-mirrors`).**
- Report byte/bit pairs that are **exactly equal (distance 0) across all
  captures**, optionally cross-ECU. Auto-surfaces redundant status bits and
  unit-variants — the hand-found HVAC `B12==B13==B15==B18`, IGPM
  `B11:6==ACC2_IGN_ON`, and the km/h-vs-MPH speed pair.
- **Test:** fixture with a duplicated byte + a bit mirror → both reported; a
  near-but-not-equal pair excluded.

**2.4 Unit sniffer (attached to `hunt`/`correlate` top hits).**
- Given a correlated pair, try common scalings (`×1, /2, /10, /100, ×0.02,
  ×1.609344, −40`, and best-fit `m/c`) and report the closest physical
  interpretation ("raw ≈ °C, no −40" / "≈ km/h ÷ 1.609 ⇒ MPH"). Pure heuristic
  ranking by residual; advisory only.
- **Test:** planted `−40` temp and `×1.609` speed each identified as the
  best-fit scaling.

**2.5 `--promote NAME` (discovery → candidate param).**
- On a `hunt`/`correlate` hit, `--promote NAME` writes the winning expression to
  `ecus/` via the existing `pids upsert-param` code path: **enabled +
  `--unverified`**, with the correlation `r`/`n`, slope/offset, and reference
  auto-filled into `notes` and `source`. Closes discovery→shipped without a
  hand-copied expression. Respects the reverse-engineer-pid skill's
  "enabled+unverified is the default candidate state" rule.
- **Test:** promote writes a schema-valid enabled-unverified param;
  `validate pids` stays green; PCI-crossing expression is refused (mirrors the
  existing PCI test).

---

## Tranche 3 — Consolidation & hygiene (small, independent)

**3.1 One canonical capture loader.** Retire `decode.load_captures()`
(`decode.py:72`) in favour of `align.load_signal_captures` (single-spec case).
Keep the public `decode` behaviour identical (regression tests).

**3.2 One PCI-reconstruction path.** `byteindex.payload_to_wican_frame` and
`wican_bytes.uds_hex_to_wican_bytes` do the same job; route the new code through
`payload_to_wican_frame` (already returns payload-index mapping) and note the
duplication for a later merge — do **not** refactor the monitor/decoding path in
this plan (out of scope, higher risk).

**3.3 Split the plot TUI out of `decode.py`.** Move `cmd_plot` + `_Braille` +
interpretation/transform helpers into `canlib/commands/_decode_plot.py` (or
`canlib/plotting.py`) so the new analysis code doesn't pile onto a 1585-line
module. Pure move + re-import; per the contributing skill's large-file guidance.
Behaviour-preserving; guarded by `test_decode_plot.py`.

---

## Cross-cutting: verification & rollout

- Per-tranche: `uv run pytest -q` + `uv run ruff …` green; add the unit tests
  above. `uv run canair validate all` stays green after any `--promote` path work.
- **Re-run the 2026-07-23 session as an acceptance test** (no device needed —
  pure history): the tool alone should now reproduce AAF speed (`hunt AAF 2181
  --against ESC:…`), VCU speed cross-verify, and MCU B22 temp (via
  `--discriminate state`). If a one-liner can't rediscover those, the ergonomics
  aren't there yet.
- Docs: add the new verbs to `AGENTS.md`'s tool list and the
  **reverse-engineer-pid** skill's tool cheat-sheet (cross-ECU corr, `correlate`,
  `hunt`, `--discriminate`, `--find-mirrors`). Fold the "cross-ECU correlation"
  technique into the skill's step 7 (Test the expression).
- Both transports untouched (analysis is offline over `captures/`) — no transport
  matrix needed.

## Non-goals (this plan)

- No interpolation/resampling join (nearest-neighbour only; revisit if a
  high-rate synced capture ever lands).
- No persisted/precomputed decode cache on disk (values stay regenerated).
- No numpy hard dependency.
- No changes to capture on-disk schema, the writer, or the monitor render path.
- Not solving the *acquisition* skew (sequential polling) — that's the companion
  pipeline/timeout work; here we just align what's already recorded and always
  surface the realised `n`/tolerance so skew is visible.

## Open questions

- **Join tolerance default:** 2.5 s chosen from observed 0.3–3 s inter-ECU skew;
  confirm against `--timings` once available, and consider per-pair adaptive tol
  (median inter-sample interval).
- **`correlate` scope by "session":** is a session the `sessions:` YAML unit, or a
  time-contiguous run? Start with the YAML session (simplest, matches
  `captures --sessions`), add a `--gap` time-split later if needed.
- **Matrix size:** a full driving session can be ~30–50 series → ~1–2k pairs.
  Pure-python Pearson is fine; if not, gate an optional numpy fast-path (3.x).

## Status

- [ ] Tranche 0 — datetime parsing (0.1), multi-signal loader (0.2), nearest-join
      primitive (0.3) in `canlib/align.py` + tests.
- [ ] Tranche 1 — cross-signal `--corr` ref (1.1); `canair correlate` matrix
      (1.2); `canair hunt --against` with linear fit (1.3) + tests.
- [ ] Tranche 2 — `--discriminate state` (2.1); transform-aware corr (2.2);
      `--find-mirrors` (2.3); unit sniffer (2.4); `--promote` (2.5) + tests.
- [ ] Tranche 3 — one capture loader (3.1); note/route PCI path (3.2); split plot
      TUI out of `decode.py` (3.3) + regression tests.
- [ ] Docs — `AGENTS.md` tool list + reverse-engineer-pid skill cheat-sheet/step 7.
- [ ] Acceptance — 2026-07-23 finds (AAF/VCU speed, MCU B22 temp) reproducible from
      single commands over existing captures.
