# Run-length (`keep:changes`) data is dropped by time-aligned joins

Status: **DONE** (2026-08-05) — see *Outcome* at the bottom · found while analysing IGPM on the 3h
AC-charge session.

**Scope of this change:** proposal items 1 and 3 (forward-fill + reporting) and
the mirror-tolerance gap at the bottom. **Item 2 (dwell-weighted statistics) is
deferred** to its own plan — weighted mean/stdev/Pearson/F/Cramér's V and a
`--resample` grid reach into `canlib/stats.py` and change the meaning of every
ranked report, which is a separate decision from "stop throwing rows away".

## The problem

`canair monitor --save` defaults to **`--keep-changes`** (run-length): a payload
is stored only when it *differs from the immediately preceding one* for that PID.
That is the right storage decision — a body ECU polled for three hours while the
car sits parked would otherwise write thousands of identical rows.

But the analysis commands treat stored rows as **samples**, not as **run-length
segments**. `align`/`correlate`/`hunt`/`investigate` nearest-join within
`--join-tol` (default 5 s), so a signal that legitimately did not change has
nothing to attach to and the row is silently dropped.

### Measured impact

IGPM `22BC07` was polled every cycle for the whole 2026-08-05 charge alongside
BMS/BCM/VCU/MCU/OBC/AAF. Its `CHARGE_PORT_LOCK` was **1 from 12:31 to 15:28**
(it changed exactly twice: locked at plug-in, unlocked at charge end), so its
value is known with certainty at every instant in that window.

```
canair align "BMS:2101:SOC_BMS" "IGPM:22BC07:CHARGE_PORT_LOCK" --date 2026-08-05 --join-tol 5

reference rows (BMS SOC):          2016
rows with an IGPM value joined:       5   (0.25 %)
rows LOST to the join:             2011   (99.75 %)
```

**99.75 % of the window is unusable** for cross-signal analysis, even though the
signal was never unknown. The same mechanism makes IGPM look "barely polled" in
`captures --summary` (30 stored rows vs BCM's 4697) when in fact both were polled
identically — the count measures **volatility, not sampling**. That is an easy
and load-bearing misreading: it caused a wrong conclusion about IGPM in this
session's first analysis pass.

## Proposal

1. **Forward-fill (last-observation-carried-forward) for run-length signals.**
   For a session tagged `keep_mode: changes`, a stored value is valid *until the
   next stored row for that PID*. Joins should carry it forward rather than
   require a row inside `--join-tol`. Suggested surface: `--fill hold|none`
   (default `hold` for `keep:changes` scopes, `none` for others), on `align`,
   `correlate`, `hunt`, `investigate`, and `decode --dump-bytes`.

2. **Dwell-weighting for statistics.** Once filled, a value held for 30 minutes
   must not carry the same weight as one held for 5 s. `decode --stats`,
   `correlate`, and `discriminate` should weight by segment duration (or
   resample to a uniform grid, e.g. `--resample 5s`) so means/variances/F/r are
   not dominated by whichever signal happens to be noisiest.

3. Report it. Emit how many rows were forward-filled vs directly joined, so a
   reader can tell reconstructed coverage from measured coverage.

### Constraints / edge cases

- **Never carry across a session boundary** or across a gap where the device was
  disconnected — the ECU may have changed unobserved. Cap with a `--max-hold`
  (and treat a reconnect as a boundary).
- **`keep:unique` cannot be safely forward-filled.** Global dedup discards
  return-to-previous transitions, so the run structure is genuinely lost. Keep
  refusing (or loudly warning) there — this is exactly why `keep:changes` is the
  default.
- **The final run's duration is unknown** (already warned about today): the last
  stored value has no closing timestamp, so hold it only to the session end.
- Preserve the existing `keep:changes` warning, but it should stop implying the
  data is unusable once filling exists — today it is the only signal a user gets,
  and it under-sells what run-length data can support.

## Design

**A run-length sample is a segment, not a point.** Value `v` stored at `t_i` is
valid from `t_i` until the next stored capture for that PID. So the whole feature
reduces to one derived quantity per sample — **`hold_until`** — computed *once*,
at series-build time, where the capture rows are still adjacent and still carry
`keep_mode` and the session identity `(file, _session_idx)`. Every joiner then
consults it as a **fallback**: nothing within `--join-tol` → take the last sample
at or before the reference instant and accept it if `hold_until` covers that
instant.

That placement is the load-bearing decision. All keep-mode and
session-boundary policy lives in one module; the join primitives never learn what
a keep mode is, and the five separate nearest-join bodies do not each grow a
policy branch.

```
capture rows (keep_mode ✅, session ✅)
   └─ hold_until_vector()        ← the ONLY place policy is decided
        └─ TimePoint.hold_until  → PreparedSeries.hold_ts
             └─ join_indices(..., cand_hold_ts=)   ← pure mechanism
```

- **New `canlib/fill.py`** — `FillMode = auto|hold|none`, `FillPolicy(mode,
  max_hold_s)`, `hold_until_vector()`, `session_end_times()`. No I/O, no joins.
- **`TimePoint.hold_until` / `PreparedSeries.hold_ts`**, both defaulted, so no
  existing constructor or call site changes.
- **`join_indices(..., cand_hold_ts=None)`** keeps its return shape: filling only
  *adds* pairs, so a caller that passes no hold vector is bit-identical to today.
- **Validity window:** `min(next capture of that PID in the same session,
  session end, t_i + --max-hold)`. Never across `(file, _session_idx)`.
- **`--fill auto` is the default** and fills only entries whose session recorded
  `keep_mode: changes` — per *entry*, so a scope spanning a run-length and a
  `keep:unique` session fills only the part it may. `--fill hold` forces it
  (legacy / `keep:all` data) and warns loudly on a `keep:unique` scope; `--fill
  none` restores point semantics for comparison.

### A disconnect gap needs no special handling

The plan's "never carry across a device disconnect" constraint mostly
**self-heals**, and building a gap heuristic for it would be wasted complexity: a
join only emits rows where the *reference* has samples, and if the device was
down there are no reference samples in the gap either. The one case that can put
rows inside a bus gap is an external `--against-file` reference, which
`--max-hold` already covers. Recorded here so it isn't "fixed" later by adding a
threshold nobody needs.

(A mid-session reconnect keeps the same journal, so it is *not* a session
boundary — see `plans/2026-08-03-monitor-reconnect-and-wait.md`. That is exactly
why the reasoning above, rather than session identity, is what covers gaps.)

## Build stages

Each stage is independently green.

0. **Consolidate the joins first (Boy Scout).** There are five nearest-join
   bodies: `align.join_indices` (canonical), `align.align_many` (a separate
   `datetime`-based copy), `align.mirror_aligned_count`,
   `_decode_calc.axis_group_keys::_nearest`, and `captures/join.py::_nearest_within`.
   Collapse what can be collapsed onto `join_indices` *before* adding fill, so
   the new rule lands in one body instead of three. Same pass: hoist the seven
   copy-pasted `--join-tol` declarations into one shared `add_join_args` (the
   `notation.add_notation_arg` precedent). No behaviour change; goldens must not
   move.
1. **`canlib/fill.py` + the hold plumbing** — through `load_signal_captures`,
   `LoadedPid`, `extract_series`, and the `xanalysis` byte/bit series builders.
2. **Hold-aware join + reporting** — the `cand_hold_ts` fallback, and
   filled-vs-directly-joined counts (plus the longest hold, so a 3 h carry is
   visible) in the human and `--json` output.
3. **CLI surface** — `--fill`/`--max-hold` on `align`, `correlate`, `hunt`,
   `investigate`, `decode`; soften `keepmode.CHANGES_BANNER` so it stops implying
   the data is unusable.
4. **Mirror tolerance** — the "Related gap" below.

**Oracle:** `tests/test_align.py`, `tests/test_corrmatrix.py` (the bucketed
sweep buckets on the timestamp vector — re-check it against a filled series),
and the ~11 join-sensitive cases in `tests/test_analysis_golden.py`, whose diff
is the readable proof of what filling changed.

## Related gap found the same day: mirror detection requires *exact* equality

`correlate --find-mirrors` reports byte positions "exactly equal across all
captures". Two limitations made it miss every mirror actually present in the
co-polled charge session:

- **No offset/scale tolerance.** Real mirrors are frequently the same physical
  quantity at a different offset or scale: `AAF:2181:B19 − 100 == OBC LDC_TEMP`,
  `VCU:2102:B18 − 100 == AAF coolant temp`, `BCM:22C011:B11 == 12.8 × BCM_12V_BATTERY`.
  All were found by hand with an absolute-difference test; `--find-mirrors`
  reported none of them.
- **All-rows equality is too strict.** Round-robin poll skew means a *drifting*
  signal read by two ECUs seconds apart differs by ±1, which disqualifies the
  pair outright. The AAF/OBC LDC-temp mirror is exact in 94.9 % of 1729 rows and
  ±1 in the rest — obviously the same signal, reported as no mirror.

Suggested: `--find-mirrors --allow-offset` (search a constant integer offset
and/or simple scale) with a **match-fraction** threshold (e.g. report pairs
matching in ≥90 % of rows) instead of demanding unanimity.

---

## Outcome (built 2026-08-05)

Both parts landed as designed. Measured against the motivating case:

```
canair align "BMS:2101:SOC_BMS" "IGPM:22BC07:CHARGE_PORT_LOCK" --date 2026-08-05

before:  2016 reference rows,    5 joined  (99.75 % lost)
after:   2016 reference rows, 2016 joined  (5 measured + 2011 held, up to 1h55m)
```

and against the mirror gap, on `correlate uds "AAF OBC" --find-mirrors
--allow-offset` — previously **no mirrors reported at all**:

```
OBC:2101:B19  ==  AAF:2181:AAF_LDC_TEMP + 100      n=1124, 1070 agree, κ=0.95
AAF:2180:B21  ==  AAF:2181:COMPRESSOR_TEMP + 40    n=673,   619 agree, κ=0.91
AAF:2181:B25  ==  AAF:2180:AAF_AUX_BATTERY_VOLTAGE × 10
```

### Three things the build changed about the design

1. **A chance-corrected floor is mandatory, not optional.** Thresholding raw
   agreement at 0.9 reported **73** mirrors on IGPM `22BC03 --bits` where 3 were
   real: two flags that are both zero in 99 % of rows agree 99 % of the time *by
   construction*. `MIN_KAPPA` (Cohen's κ ≥ 0.8) cuts it back to 5 — the 3 exact
   plus 2 genuine 309/310 pairs. The original plan's match-fraction proposal alone
   would have shipped an unusable amount of noise. Related: a pair whose sides are
   both *constant* in scope is never a mirror (`build_param_series` does not filter
   constants the way `build_byte_series` does, so every constant param mirrored
   every other one until this guard).
2. **Reporting must be filtered by the join tolerance.** A hold shorter than
   `--join-tol` cannot contribute a row — the strict join already reached those
   instants — so counting it produced a "values carried forward" note on nearly
   every report describing 2-second holds that changed nothing. Exact, not a
   heuristic.
3. **Percentages lie at this precision.** 309 of 310 rows renders as "100 %",
   which reads as unanimous and hides exactly the disagreement the reader must
   weigh. Reports show the count and κ instead.

### Latent bug found and fixed on the way

`corrmatrix`'s bucket prune dropped every series shorter than `--min-n` from the
ranked sweep, commented as a mechanical impossibility. It is not one: a join emits
one row per *reference* sample, and several reference rows may map to the same
candidate sample, so a short series can supply a full overlap **as the candidate**.
Combined with run-length recording it discarded precisely the sparse signals this
plan is about. The bound now applies to the reference side of each ordered pair.
The same reasoning shaped `mirrors.find_series_mirrors`, which uses the *denser*
clock of a bucket pair as the reference (mirroring is symmetric, so fixing the
direction by sort order would hide sparse-vs-dense mirrors).

### Unexpected win: the mirror sweep got faster, not slower

A match-fraction test cannot bail on the first disagreement, so this looked like a
straight performance cost. It was the opposite: the old sweep's time went almost
entirely into re-deriving the *same join* for every signal pair, which the
early-bail hid. Bucketing signals by clock (the `corrmatrix` trick) and joining
once per bucket pair took the whole-corpus sweep from **24 s → 4 s** while
searching strictly more; `--allow-offset` costs ~33 s, and is opt-in.

## Follow-ups

- **Dwell-weighted statistics** — plan item 2, deliberately deferred. A value held
  for 30 minutes currently carries the same weight as one held for 5 s, so
  `decode --stats`, `correlate` and `--discriminate` are dominated by whichever
  signal is noisiest. Needs weighted mean/stdev/Pearson/F/Cramér's V in
  `canlib/stats.py` and/or a `--resample 5s` grid, and it changes the meaning of
  every ranked report — its own change, with its own goldens.
- **Scope filters run before validity windows.** `--since 13:00 --until 13:01`
  drops the earlier row that established the value held through that minute, so a
  run-length signal looks absent. Documented as a caveat (prefer
  `--date`/`--state`/`--last-session`); the fix is for the loader to also admit the
  last row at-or-before `since` per PID, which needs care because that row's
  session may itself be excluded by `--state`/`--label`.
- **`captures uds --step` keeps its own join** and does not fill. It is a viewer
  with different semantics (union anchor, wider tolerance, live-adjustable), but a
  run-length PID still renders "no capture within Ns" where the value is in fact
  known — worth revisiting.
- **`canlib/align.py` is ~900 lines** after this. The seams are clear (SignalRef +
  loading | series building | join primitives | reference-file loading); splitting
  it is a mechanical follow-up, not part of this change.
