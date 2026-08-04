# Correlation ranking performance: share the join, hoist the Pearson

Status: **DONE** (2026-08-04). Landed with a **byte-identical hit set** and an
end-to-end **7.7x** on `correlate uds` / **15x** on `correlate uds --bytes` (which
previously did not finish inside 120s). Measured numbers in "Landed" at the bottom.
The one **correctness caveat** in the finiteness hoist (step 3) was honoured and has
its own regression test.

## Why (the mechanism)

`xanalysis.correlate_matrix` ranks **every pair of signals**, and for each pair
it runs a nearest-timestamp join (`align.join_prepared`) before computing the
coefficient. That is the wrong granularity: **a join depends only on the two
series' timestamps, not their values**, and nearly every signal drawn from the
same `(ECU, PID)` shares an *identical* timestamp vector — they are all decoded
from the same list of captures (`LoadedPid.timed_frames()`, read once and indexed
per byte offset in `build_byte_series`/`build_bit_series`).

So the same join is recomputed thousands of times. Measured on the bundled
`ioniq-2017` profile, `correlate uds --until 2026-08-02`:

| | count |
|---|---|
| signals | 329 |
| signal pairs (what we join today) | 53,956 (49,441 after the O(1) prunes) |
| distinct `(ECU, PID)` groups | 37 |
| **distinct timestamp vectors** | **741 bucket pairs** |

36 of the 37 PID groups have exactly **one** timestamp vector across all their
signals. (The one exception, `HVAC:220102`, has two — 632 and 633 samples —
because `build_byte_series` drops a byte offset on frames too short to contain
it: `if bn < len(fr)`. This is why the bucket key must be the **timestamp vector
itself**, not the PID group. Keying on the PID would silently mis-join that ECU.)

It gets worse with `--bytes`, which is where the command actually hurts:

| | `uds` | `uds --bytes` |
|---|---|---|
| signals | 329 | 908 |
| signal pairs | 53,956 | 411,778 |
| timestamp-vector buckets | 37* | 49 |
| bucket pairs | 741 | 1,225 |
| **join reduction** | **67x** | **336x** |

\* buckets, not groups; the two numbers coincide here.

`correlate uds --bytes --until 2026-08-02` **does not complete within 120s**
today. That is the user-visible symptom.

## Measured (fresh profile, 2026-08-04)

`correlate uds --until 2026-08-02`, 51.4s under cProfile / **23.6s** real:

| | tottime | calls |
|---|---|---|
| `align.join_prepared` | **18.62s** (29.53s cumulative) | 49,441 |
| `bisect.bisect_left` | 7.02s | **94,520,585** |
| `math.fsum` (Pearson) | 4.41s (10.62s cumulative) | 120,792 |
| `list.append` | 4.03s | 94,586,752 |
| `builtins.all` (finiteness) | 2.85s (9.85s cumulative) | 89,853 |
| `stats.py` genexprs | ~8.5s | ~172M |
| `math.isfinite` | 2.36s | 91,584,956 |
| `evaluate_expression` | — | (3.5%, already optimised) |

The join is **57% of cumulative runtime**; the Pearson accumulation is most of
the rest. These numbers reproduce the earlier measurement, so the previously
shipped `PreparedSeries` epoch-float/bisect optimisation did *not* address this —
it made each join cheaper, not fewer.

## Design — three changes, in dependency order

### 1. Join once per timestamp-vector pair, as an index mapping

Add to `canlib/align.py`:

```python
def join_indices(a_ts: list[float], b_ts: list[float],
                 tol_s: float = DEFAULT_JOIN_TOL_S) -> tuple[list[int], list[int]]:
    """Nearest-neighbour join as parallel INDEX lists, reusable across signals."""
```

Identical sweep to `join_prepared` (same bisect, same "smaller absolute delta
wins", same drop-if-out-of-tolerance rule) but emitting `(ia, ib)` index pairs
instead of value pairs. `join_prepared` then becomes a thin wrapper over it, so
there is **one** join implementation and the tie-breaking semantics cannot drift
between the fast and slow paths.

In `correlate_matrix`: bucket signals by `tuple(prepared[name].ts)`, iterate
**bucket pairs**, compute `join_indices` once per bucket pair, and reuse the
mapping for every signal pair spanning those buckets. Keep the existing O(1)
prunes (`series_time_ranges_disjoint`, `min_n`) at the bucket level.

### 2. Hoist sub-vector extraction + per-signal statistics out of the pair loop

For a given bucket pair the joined sub-vector of each signal is fixed. So per
bucket pair, compute **once per signal** (O(signals), not O(signals²)):

- the joined sub-vector's deviations from its mean (`dev = [x - mean]`),
- its sum of squared deviations (`ss`),
- **its finiteness** (see the caveat below).

Then the per-pair work collapses from three `fsum` passes plus two `all()` scans
to a **single** `fsum` for the covariance:

```python
r = math.fsum(x * y for x, y in zip(da, db, strict=True)) / (ssa**0.5 * ssb**0.5)
```

### 3. Correctness caveat — do NOT hoist finiteness to the whole series

`stats.pearson` currently tests `all(math.isfinite(v) for v in xs)` where `xs` is
the **joined subset**. The tempting hoist — "compute finiteness once per whole
series" — is **not equivalent**: a series carrying one `inf` *outside* every join
window is currently usable, but a whole-series flag would discard it.

This did not show up in the prototype (the bundled corpus has no non-finite
series, so hit sets matched exactly), which is precisely why it must be written
down — it is a latent behaviour change that the current test corpus cannot catch.
`f64`/`f32` byte reinterpretations in the hunters *do* produce `inf`/`nan`, so
the risk is real, not theoretical.

**Required form:** compute finiteness on the **joined sub-vector**, in the same
pass that computes `dev`/`ss` (step 2). Still O(signals) per bucket pair instead
of O(signals²), so the win is preserved with no semantic change.

Also preserve from `stats.pearson`: the `try/except (OverflowError, ValueError)`
guard, the `sx == 0 or sy == 0 → None` zero-variance rule, and the final
`isfinite(r)` check. A byte-sweep must never abort mid-run on a pathological
series.

### Method scope

The hoist applies cleanly to:

- **pearson** — as above.
- **spearman** — Pearson of ranks; ranks are a property of the sub-vector, so
  `rank()` is hoisted per signal per bucket pair too (a strictly bigger win, as
  `rank()` sorts).

It does **not** apply to **`cramers_v` / `mutual_info`**, which are contingency
-table statistics over the *pair*. Those keep the shared join (step 1 — still a
67-336x join reduction) but fall back to the current per-pair coefficient path.
Do not contort the design to fit them.

## Prototype result

Pure stdlib, no new dependency, on `correlate uds --until 2026-08-02`:

| | time | joins | hits |
|---|---|---|---|
| current `correlate_matrix` | 22.27s | 49,441 | 10,207 |
| prototype | **1.50s** | **741** | 10,207 |

**14.9x**, and the hit sets are identical as sets of `(a, b, round(r, 9))`.

## Explicitly out of scope

- **numpy.** It would turn the per-bucket-pair covariance into one matmul and go
  faster still, but it is **not currently a dependency** (confirmed: not even
  transitively via cantools/python-can) and the stdlib version already gets
  14.9x. Adding a compiled numerical dependency to a CLI that ships to
  hobbyists needs its own justification. Revisit only if profiling after this
  lands still shows the covariance dominating.
- Changing any coefficient's **semantics**, tie-breaking, or the
  `min_r`/`min_n`/`--join-tol` defaults.
- The `--against` single-reference paths (`correlate.py:458`, `:742`) and
  `investigate.py`'s anchor joins. They join one reference against N signals —
  O(N), not O(N²) — so they are not the bottleneck. They *do* get the
  `join_indices` refactor for free; leave their loop structure alone.
- `mirror_aligned_count` (`--find-mirrors`), already a fused fast path.

## Tests

- **Equivalence (the important one):** `correlate_matrix` old-vs-new over a
  fixture profile — assert identical hit lists including `r` to full precision
  and identical ordering. Parametrise over `pearson`/`spearman` and
  `include_intra` both ways.
- `join_indices` ↔ `join_prepared` agreement, including the tie case (a
  reference point exactly `tol` from two candidates) and the empty/one-sided
  cases.
- **The finiteness caveat, explicitly:** a series with `inf`/`nan` *outside* the
  join window must still produce a hit (this is the regression test for the trap
  in step 3), and one with a non-finite value *inside* the window must not.
- Buckets: a PID whose signals have two distinct timestamp vectors (the
  `HVAC:220102` shape) must join each vector separately — a fixture with a short
  final frame.
- Zero-variance and overflow series still return no hit rather than raising.
- Golden-output gate: `correlate` stdout byte-identical on the bundled profile.

## Docs

- `CHANGELOG.md` `[Unreleased]` — performance entry with the measured numbers and
  an explicit "results unchanged" note.
- `docs/concepts/analysis-commands.md` — if it documents `correlate` as slow on
  wide sweeps, update it; `--bytes` becomes usable.
- No CLI-surface change, so no `docs/reference/cli/` regeneration and no
  `AGENTS.md` command-description change.

## Risks

- **Silent result drift** is the only real risk, and it has exactly two sources:
  the finiteness hoist (step 3) and join tie-breaking (step 1). Both are pinned
  by the equivalence + golden tests above. Land nothing without them green.
- Memory: bucketing holds one `dev` list per signal per bucket pair. Bounded by
  the largest bucket pair, freed as the loop advances — but if `--bytes --bits`
  on a large corpus regresses memory, extract sub-vectors lazily per signal-row
  instead of per bucket pair (costs some of the win, keeps the join reduction).

## Status

- [x] `join_indices` in `align.py`; `join_prepared` reimplemented over it
- [x] `correlate_matrix` buckets by timestamp vector; join per bucket pair
- [x] per-signal `dev`/`ss`/finiteness hoisted per bucket pair (sub-vector form)
- [x] spearman hoist (`rank()` per sub-vector)
- [x] categorical methods fall back to the per-pair path
- [x] equivalence + finiteness + bucket + tie tests; golden gate
- [x] CHANGELOG; re-profile and record the end-to-end number

## Landed

Engine extracted from `xanalysis.py` (already a 1100-line grab-bag) into a new
single-purpose **`canlib/corrmatrix.py`** — `CorrHit`, `correlate_matrix`,
`signal_group_key`, `colinear_clusters`, and the bucketing internals.
`xanalysis` re-exports all four, so every existing call site
(`commands/correlate.py`, `commands/investigate.py`, muscle memory, tests) is
unchanged; a test pins the re-exports. `_CLUSTER_THRESHOLD` became the public
`CLUSTER_THRESHOLD` (it was being imported cross-module under an `as` alias).

`align.py` gained `join_indices` (the single join implementation) and
`timestamps_disjoint` (the timestamp-only core of `series_time_ranges_disjoint`,
so the bucketed sweep can prune on bare timestamp vectors); `join_prepared` is
now a thin wrapper over `join_indices`.

**End-to-end, byte-identical stdout** (bundled `ioniq-2017`, `--until 2026-08-02`):

| command | before | after |
|---|---|---|
| `correlate uds` | 23.9s | **3.1s** |
| `correlate uds --bytes` | 145s | **9.7s** |

**Ranking core alone** vs a naive pair-at-a-time oracle, hit lists identical to
full `repr(float)` precision (all four methods × `include_intra` both ways):

| signals | method | naive | bucketed |
|---|---|---|---|
| 329 (params) | pearson | 23.7s | **1.7s** |
| 329 | spearman | 35.0s | **2.2s** |
| 329 | cramers_v | 143.5s | **26.6s** |
| 329 | mutual_info | 33.9s | **8.6s** |
| 843 (`--bytes`) | pearson | 423.5s | **7.2s** |
| 843 | spearman | 507.6s | **37.1s** |

The categorical methods gain only the join reduction, as designed — their
coefficient is a contingency table over the pair and stays on the per-pair path.

### Notes for the next reader

- **`d ** 2` is not `d * d`.** The hoisted sum of squared deviations deliberately
  keeps the `d ** 2` form: on this platform's libm, `x**2 != x*x` for ~1 in 700
  random floats (2756 of 2M measured), so switching to a multiply would drift the
  last bits of every `r` and break the full-precision equivalence gate.
- **The sub-vector cache is per *side*, not per signal.** A self-bucket join is
  not the identity when timestamps repeat (`bisect_left` lands on the first of a
  duplicated stamp), so one signal can need two different sub-vectors within one
  bucket pair. Pinned by `test_duplicate_timestamps_join_like_the_naive_sweep`.
- **Pair position is carried through the sort.** Buckets visit pairs in a
  different order than `i < j`, and equal-|r| ties are common, so each hit keeps
  its naive-enumeration position as the tie-break. Pinned by
  `test_ties_keep_the_naive_orderings` (interleaved clocks — a same-clock corpus
  does *not* discriminate, which the first draft of that test got wrong).
- All three of those invariants were mutation-tested: dropping the tie-break,
  sharing one cache across sides, and bucketing by `(ECU, PID)` each fail the
  suite (1, 10 and 30 tests respectively).
- **Docs:** no CLI surface changed, so no `docs/reference/cli/` regeneration and
  no `AGENTS.md` edit. `docs/concepts/analysis-commands.md` was checked and
  carries no performance claim to update.

