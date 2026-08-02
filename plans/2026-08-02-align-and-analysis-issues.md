# Analysis-tooling issues found during the 2026-08-02 charge deep-dive

Surfaced while stress-testing `canair align` (and `correlate`/`hunt`/`decode`)
on the 2026-08-02 READY→AC-charge→ACC2 capture (an 8-ECU `monitor --save`
`keep:unique` session), then cross-checking live against the car in ACC2.

Context: the capture is a large multi-ECU round-robin `monitor` session recorded
`keep_mode: unique` (only globally-distinct payloads kept per PID). Both facts
interact badly with the nearest-timestamp join that `align`/`correlate`/`hunt`
depend on.

## 1. Default join window too tight for multi-ECU round-robin — FIXED

**Symptom.** `align` on ~8 co-polled ECUs silently emitted columns with **zero
joined rows** for a "far" ECU (e.g. BCM against a BMS reference), even though
both were captured in the same session. Looked like a bug; it was the join
tolerance.

**Cause.** The sequential single-connection poller visits ECUs round-robin, so
two ECUs' samples are skewed by the time to poll everything in between. On an
8-ECU cycle, adjacent-in-cycle ECUs land **~3.4 s** apart — beyond the old
`DEFAULT_JOIN_TOL_S = 2.5`, so every pair fell outside the window and dropped.

**Fix (done).** Widened `DEFAULT_JOIN_TOL_S` 2.5 → **5.0** in `canlib/align.py`
(the single shared constant used by `align`/`correlate`/`hunt`/`investigate`/
`decode --discriminate`). 5 s covers the observed skew while still being
"nearest" (a closer sample always wins). Regenerated CLI reference docs, updated
AGENTS.md + CHANGELOG, added regression tests
(`tests/test_align.py::TestDefaultJoinTol`,
`TestJoinNearest::test_default_tol_covers_round_robin_skew`).

## 2. `align` silently emits zero-joined columns — TO FIX

**Symptom.** When a selector joins **0** (or very few) rows against the
reference, `align` prints the column header with all-empty cells and no warning —
indistinguishable from "the tool is broken". This is what made #1 look like a
bug rather than a tolerance issue.

**Proposed fix.** After the join, emit a stderr warning for any selector whose
joined-row count is 0 (or below a small floor, e.g. `< 5%` of reference rows),
naming the selector and suggesting `--join-tol`. Mirror the existing
`keep:unique` scope warning style. Consider the same for `correlate`/`hunt`
`--against` references. Keep it a warning (not an error) — a legitimately
disjoint pair (`series_time_ranges_disjoint`) should still produce output.

## 3. `keep:unique` + a bimodal signal breaks correlation ranking — DOCUMENT / GUARD

**Symptom.** Hunting for "which byte is the 12 V bus" on VCU 2102 returned many
bytes at r≈0.998 — none of them actually the 12 V. The 12 V here is effectively
**bimodal** (≈14.5 V while charging, ≈12.2 V in ACC2), so *any* byte that merely
differs between the charge and ACC2 regimes correlates near-perfectly with it
(two-cluster / point-biserial artifact). Combined with `keep:unique` (only
distinct values kept, so no within-regime variation to break ties), correlation
is nearly useless for *identifying* a signal on this scope.

**Proposed handling.**
- Document in `docs/concepts/analysis-commands.md`: correlation/hunt on a
  low-variance-within-regime + bimodal scope is unreliable; prefer a `keep-all`
  drive or a scope with continuous variation for byte identification.
- Consider a guard in `hunt`/`correlate`: when the reference (or candidates)
  collapse to ~2 clusters, warn that |r| is a cluster-separation score, not a
  signal match — and/or suggest `--method spearman` won't help here either.
- Related existing behaviour that's correct and worth keeping: the ACC2
  **cool-down** phase is what let temperature hunts avoid the analogous
  monotonic-charge trap (real temps cool, counters don't). Cool-downs /
  regime exits are valuable — don't scope them out.

## 4. VCU_AUX_BATTERY_VOLTAGE misidentified — FIXED (data, not code)

Not a tooling bug, recorded here for the trail. `VCU 2102 VCU_AUX_BATTERY_VOLTAGE`
(`B18/10`) read 14.70 V live in stable ACC2 while BMS/OBC/BCM agreed at
12.16–12.20 V (no lag possible). B18 tracks the bus **inversely**, so no rescale
fixes it. Demoted (`enabled: false`, unverified) with a research lead to find
VCU's real 12 V byte from `keep-all` data. See `ecus/vcu.yaml`.

## Notes / follow-ups

- The `align` cross-ECU **agreement** use (do N ECUs reporting the same physical
  quantity agree?) proved genuinely valuable — it caught #4. Worth a worked
  example in the docs.
- MCU `21F2` now has captures but no PID definition (see the separate
  registration + research lead).
</content>
</invoke>
