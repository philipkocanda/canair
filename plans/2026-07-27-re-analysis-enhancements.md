# canair — Reverse-Engineering Analysis Enhancements

Status: **Phases 1–3 DONE** (commits: phase 1 byte-matrix export + external
reference files; phase 2 confounder control / physical bands / independence;
phase 3 byte-triage in `investigate`). **Post-review hardening done**: word
detection now feeds *all* data bytes (not the `min_distinct`-filtered report set)
and drops non-ISO-TP-adjacent pairs (no misleading `[Bhi:Blo]` spanning a
dropped byte); `physical_scan` moved below the `--events` short-circuit;
command-level `--control` tests added for `hunt`/`correlate`; `hunt --physical`
warns on ignored reference flags; `load_reference_file` warns on an
all-pre-2000 (relative/zero-based) series. Tier-2 items (domain-B
periodicity/counter detector, CUSUM segmentation) remain deferred; the
"considered & rejected" set (Transfer Entropy, HMM, BOCPD, Hamming clustering)
stands.

Gathered 2026-07-27 from (a) a proposed
list of statistical/RE techniques and (b) the concrete tooling gaps surfaced by
the [AC input voltage case study](../docs/case-studies/ac-input-voltage.md).
Implemented in **three phases as separate, individually-scoped PRs** (Phase 1 →
2 → 3), not one mega-change. Every item is **read-only analysis over existing
`captures/`** — no device, no transport change, no schema-of-record change
(decoded values stay regenerated, never persisted). Each item ships tests +
docs per the contributing skill.

## Why these, and not the rest — the critical evaluation

The proposed technique list (bit-flip/entropy per bit, XOR boundary detection,
Hamming clustering, transfer entropy, autocorrelation, cross-correlation with
lag, changepoint detection, HMMs) was weighed against canair's actual data model.
Three facts reorder it:

1. **Two domains, and most of the list is domain-B literature.** Entropy-per-bit,
   XOR-boundary detection, and Hamming clustering are the classic *raw-dump
   tokenization* pipeline (READ / LibreCAN / CAN-D) — for when field boundaries
   are unknown. That is canair's **domain B** (raw broadcast CAN,
   `frame_series.py`, `signals/`), which is deliberately under-developed.
   canair's mature surface is **domain A** (diagnostic UDS), where byte roles are
   semantically known and named reference signals exist — so several of these add
   little there.
2. **Sampling reality gates every time-series method.** Domain A is *irregularly
   sampled sequential round-robin polling* (~5 s interval, 0.3–3 s inter-ECU
   skew); the join is nearest-neighbour and **resampling was explicitly declined**
   (`plans/2026-07-23-cross-signal-analysis.md`). Autocorrelation lag structure,
   transfer-entropy conditionals, changepoint spacing, and HMM emission timing all
   assume a reliable time grid; in domain A they partly measure the poll cadence.
   Domain B (µs timestamps, 10–100 Hz per ID) is the only place they stand firm.
3. **Half the list already exists.** Cross-correlation with lag is
   `xanalysis.lag_scan` (`correlate --lag-scan`). Mutual information and Cramér's
   V are `stats.py` coefficients (`--method mutual_info|cramers_v`); the MI
   association graph is `correlate --bytes --matrix --method mutual_info`. F-ratio
   state-discrimination, linear fit, unit sniffing, `--gate`, `--find-mirrors`,
   and discrete-signal changepoints (`investigate --events`) are built.

The case study, by contrast, lists **seven evidence-grounded gaps** from a real
investigation that wasted hours — and the AC-voltage signal was *invisible to
every anchor-based tool* because it has **no correlate on the bus** (no MI / TE /
HMM finds it). What finds it: an **external reference**, **confounder removal**,
and **physical plausibility**. Those gaps are the higher-ROI work and drive
Phases 1–2; the one genuinely-missing rung of the proposed list — a cheap
**triage layer** (entropy / flip-rate / autocorrelation / word detection) — is
Phase 3, folded into `investigate` and built domain-agnostic so the raw-CAN path
adopts it next.

## Baseline pointers (verify before editing — these drift)

- **Coefficients (numpy-free leaf):** `canlib/stats.py` — `pearson`, `spearman`,
  `correlation` (dispatcher), `cramers_v`, `mutual_information`, `compute_stats`,
  `fmt_num`. Add new coefficients here (partial correlation) + wire into
  `correlation()`'s `--method` where applicable.
- **Time alignment:** `canlib/align.py` — `TimePoint`, `load_signal_captures`,
  `extract_series`, `join_nearest`/`join_nearest_presorted`, `align_many`,
  `DEFAULT_JOIN_TOL_S = 2.5`. Join key is `capture_dates.entry_datetime` (ms).
- **Analysis engine:** `canlib/xanalysis.py` — `linear_fit`, `sniff_unit`
  (+ `_UNIT_CANDIDATES`), `discriminability` (F-ratio), `byte_state_buckets`,
  `lag_scan`, `correlate_matrix`, `hunt_byte`, `load_ref`.
- **Byte-interpretation sweep:** `canlib/commands/_decode_plot.py` —
  `INSPECT_TYPES`, `interpret_bytes`, `wican_expr`, `apply_transform`,
  `POST_TRANSFORMS`.
- **Byte indexing / PCI:** `canlib/byteindex.py` — `payload_to_wican_bytes`,
  `wican_to_isotp` (PCI detector), `mapped_offsets`/`mapped_bits`,
  `extract_byte_indices`.
- **Domain B:** `canlib/frame_series.py` — `build_frame_series`,
  `build_frame_bit_series`, `hunt_frame`; `canlib/can_logs.py` (`iter_frames`).
- **Commands:** `commands/decode.py` (`add_parser` ~L934, `run` ~L1064,
  `add_scope_args`), `commands/hunt.py`, `commands/correlate.py`,
  `commands/investigate.py`, `commands/bix.py`.
- **Scope flags:** `canlib/capture_dates.py` (`add_scope_args`,
  `filter_by_date_range`, `filter_by_text`). **`keep:unique` awareness:**
  `canlib/keepmode.py` (`scope_is_keep_unique`, `BANNER`).

## Decisions (locked, from review)

- **Domain A first.** The case-study gaps (external ref, confounder control,
  physical bands, byte-matrix export, bix fix) are diagnostic-UDS wins and lead.
  The triage primitive is built domain-agnostic; `investigate can` (domain B)
  adopts it as the tail of Phase 3.
- **Triage folds into `investigate`.** No standalone `canair triage` command; a
  new leaf `canlib/triage.py` backs new `investigate` columns/sections.
- **Rejected techniques are documented, not built** (see below) so the decision
  is durable.
- **One combined plan doc, phased PRs.**

## Non-negotiable constraints (from the contributing skill)

- **numpy-free leaf stats.** `stats.py`/new `triage.py` import only stdlib
  `math`. Partial correlation uses the closed form (three pairwise Pearsons);
  entropy/flip-rate are pure counting. Gate any future numpy behind an optional
  import with a pure-python fallback.
- **No resampling.** Time joins stay nearest-neighbour within `--join-tol`; the
  realised `n` is always reported.
- **Two domains stay symmetric.** New primitives are neutral helpers consumed by
  both the diagnostic (`ecus/`) and broadcast (`signals/`/`frame_series`) paths.
- **`--json` on every programmatic surface** (agent-drivable) — `--dump-bytes`,
  `investigate --json` columns, `hunt --physical --json`.
- Every behavioral change ships tests; `pytest`/`ruff`/`ty`/`validate all` green.
- User-facing changes update `docs/` + `README` + `AGENTS.md` + skills in the
  same change.

## Phase 1 — Foundations & the biggest lever (case-study gaps 1, 6, 7)

### 1.1 Byte-matrix export — `decode --dump-bytes --json|--csv` (gap 6)

Emit a `timestamp × byte-offset` matrix per capture: WiCAN `Bnn` columns with PCI
framing bytes skipped (honours the shared `--notation` flag; `--include-pci`
escape hatch to dump everything). Reuses `payload_to_wican_bytes` +
`entry_datetime` + the existing decode scope flags. *Foundational*: it is both
the substrate the Phase 3 triage stats consume and the safe, first-class
replacement for the `captures --diff` regex-scraping the case study resorted to.

- Files: `commands/decode.py` (new flags + emitter); tests in `tests/test_decode.py`.
- Design note: default column notation = WiCAN; CSV header is `time,ecu,pid,B3,B4,…`
  (union of offsets across the scoped captures, blank where a capture is shorter).

### 1.2 External reference series — `--against-file series.csv` (gap 1)

The single biggest gap: `--against` only accepts an in-capture `ECU:PID:PARAM`.
Add `--against-file PATH` to `hunt` and `correlate` (mutually exclusive with
`--against`), reading a two-column `timestamp,value` CSV into `list[TimePoint]`
fed through the *existing* join path. Unlocks calibrated meter logs, GPS speed
tracks, grid-voltage exports.

- New loader in `canlib/align.py` (`load_reference_file` → `list[TimePoint]`);
  accepts ISO-8601, `YYYY-MM-DD HH:MM:SS[.fff]`, and epoch-seconds timestamps.
- Wiring: `hunt.py`, `correlate.py` (reference-building path shared with
  `xanalysis.load_ref`).
- **Caveat documented:** the CSV must be on the same *absolute* clock as the
  captures (session `date` + `time`); a relative/zero-based log won't align. Error
  clearly when the joined `n` is 0.
- Tests: `tests/test_align.py` (parsing/formats), `tests/test_hunt.py`.

### 1.3 `bix --annotate` truncated-payload fix (gap 7)

A truncated/partial payload currently produces *empty output* instead of an
error — a silent failure that cost a round-trip. Detect short/odd-length /
PCI-length-mismatched input and emit a clear message with a nonzero exit.

- Files: `commands/bix.py`; regression test in `tests/test_bix.py`.

## Phase 2 — Confounder-aware & plausibility analysis (case-study gaps 2, 3, 4)

### 2.1 Partial correlation / confounder control — `--control` (gaps 2 & 4)

`hunt`/`correlate` rank by raw correlation, which a nuisance term (the case
study's IR-drop, proportional to charge current) sabotages. Add
`--control ECU:PID:PARAM` (and `--control-file`) that removes a nuisance signal
and ranks the *residual* relationship:

- New `partial_correlation(xy, xz, yz)` in `stats.py` — closed form
  `r_xy·z = (r_xy − r_xz·r_yz) / √((1−r_xz²)(1−r_yz²))`. Exact, cheap, leaf.
- `--independent-of ECU:PID:PARAM` on `investigate` / `decode --discriminate` is
  the same machinery inverted: rank bytes that separate by `state` yet *don't*
  track the named driver (the "active-but-independent" finder — exactly the AC-
  voltage fingerprint).
- Wiring: `xanalysis.py` (thread a control series through `correlate_matrix`/
  `hunt_byte`/`load_ref`), `hunt.py`, `correlate.py`, `investigate.py`,
  `decode.py`.
- Tests: `tests/test_stats.py` (closed-form vs a known residual case),
  `tests/test_xanalysis.py`.

### 2.2 Physical-value band hunt — `hunt --physical` + investigate column (gap 3)

Find anchorless signals by plausibility instead of correlation. Sweep common
scalings (`/1 /10 /100 ×2 ×√2`) and flag bytes/words whose value lands in a
**named physical band** for a share of samples: mains RMS (200–250 V), mains peak
(300–340 V), line frequency (49–51 Hz), 12 V rail, HV pack (300–450 V). Would have
flagged `[B14:B15]/100 ≈ 222 V` on the first pass with no external reference.

- Band table + scorer in `xanalysis.py` (built-in, extensible; profile-override
  is future work). Report band name + scaling + in-band fraction; `--json`.
- Wiring: `hunt.py` (`--physical`, no `--against` required), an `investigate`
  column.
- Tests: `tests/test_xanalysis.py`, `tests/test_hunt.py`.

## Phase 3 — Byte-triage primitive folded into `investigate` (list items 1, 2, 5 + gap 5)

New leaf **`canlib/triage.py`** (stdlib `math` only), consumed by `investigate`:

- Per-byte Shannon **entropy**; per-bit **flip rate** (XOR of time-sorted
  consecutive frames); **lag-1 autocorrelation**; **step-size** (mean |Δ|);
  distinct-count.
- `classify_byte(...)` heuristic → `constant | counter | checksum | enum |
  continuous` from the above.
- **Word/boundary detection** (gap 5 + XOR-boundary idea): flag adjacent
  `[Bn:Bn+1]` where `Bn` is near-constant and `Bn+1` spans ~0–255 as a probable
  scaled 16-bit word, and suggest testing the pair (this is how the AC voltage hid
  — const-ish hi byte + full-range lo byte, each dismissed separately).
- Surface: new columns + a "suggested words" section in `canair investigate`
  (domain A first). Built domain-agnostic so `investigate can` / `frame_series`
  (domain B — where entropy/boundary segmentation pays off most) adopts the same
  primitive as the tail of this phase.
- **Caveats in output:** lag-1 autocorrelation is labelled *sample-lag* (not a
  time-domain ACF) on irregular domain A; reuse `keepmode.BANNER` to warn when
  flip-rate is computed over `keep:unique` (rising-edge-only) scope.
- Files: `triage.py`, `investigate.py`, `frame_series.py`; tests in new
  `tests/test_triage.py` + `tests/test_investigate.py`.

## Considered & rejected (with rationale — do not build)

- **Transfer Entropy.** Needs many samples on a reliable time grid to estimate
  conditional distributions; domain A's sequential poll makes the estimate track
  the *acquisition offset*, not causality (the exact confound `lag_scan` already
  warns about). Fragile even in domain B, hard numpy-free at scale, speculative
  payoff. Not worth it.
- **Hidden Markov Models.** Latent-state inference is largely already served by
  `states.yaml` predicates + typed/enum decode + Cramér's V / MI. HMM adds a real
  dependency (Baum-Welch wants numpy/scipy → breaks the leaf/no-numpy rule),
  training instability on sparse/irregular data, and interpretability cost.
- **Bayesian Online Changepoint Detection.** Over-engineered vs CUSUM for
  marginal gain; `investigate --events` + `--group-by state` already cover the
  practical need. (If any changepoint work happens, CUSUM only — see Deferred.)
- **Hamming-distance clustering.** canair already has the message key (ECU:PID /
  arbitration ID), so the raw-dump use case is moot; as a payload-mode clusterer
  it is redundant with distinct-count + Cramér's V + `--discriminate`. Revisit
  only if a concrete multiplexed-DID need appears.
- **Already implemented (not rebuilt):** cross-correlation with lag
  (`xanalysis.lag_scan` / `correlate --lag-scan`); MI / Cramér's V association
  graph (`correlate --bytes --matrix --method …`).

## Deferred (Tier 2 — flagged, unscheduled unless prioritized)

- Domain-B **periodicity / counter detector** on the uniform grid (autocorrelation
  beyond the Phase 3 lag-1 column) — natural once raw-CAN analysis matures.
- Opt-in **CUSUM** regime segmentation for *continuous* signals without state
  labels (complements `--group-by state`).

## Risks

- **Word-detection false positives:** a full-range low byte next to any stable
  byte isn't always a word. Rank/threshold on the low byte's span + the pair's
  physical plausibility; keep it a *suggestion*, never an auto-promote.
- **`--against-file` clock mismatch:** the commonest failure will be a
  relative-timestamp CSV joining to nothing. Report `n=0` loudly with the clock
  caveat, not a silent empty result.
- **Physical-band tuning:** bands are car-class-specific (mains/HV are EV/region
  assumptions). Ship a conservative built-in table; make override a documented
  follow-up, not a launch blocker.
- **`--control` degeneracy:** when the control is itself near-collinear with the
  reference, the denominator → 0. Guard and report "undefined (control collinear
  with reference)" rather than emitting a spurious ±1.
- **Close the loop:** update `docs/case-studies/ac-input-voltage.md`
  "What would have made this easy" to point at the shipped features as each phase
  lands, so the case study stops advertising gaps that no longer exist.
