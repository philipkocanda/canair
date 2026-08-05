# Run-length (`keep:changes`) data is dropped by time-aligned joins

Status: **proposed** · found 2026-08-05 while analysing IGPM on the 3h AC-charge
session.

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
