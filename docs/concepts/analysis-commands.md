# Which analysis command when

canair's offline analysis family looks large, but it's organized by two
questions. Every command answers one of:

- **"What are the values?"** — read the decoded/raw data, or
- **"How do signals relate?"** — find correlations, separations, mirrors

…at one of two grains: **one PID** or **many signals / a whole drive**. That's
four quadrants, and it's the fastest way to pick the right tool.

## The map

```
                    READ THE VALUES  ─────────────►  FIND RELATIONSHIPS
                    (what does it say?)              (how do things move together?)

   ┌─────────────────────────────────────┬──────────────────────────────────────┐
 S │  captures       raw payloads / diff  │  decode --corr    param vs 1 reference │
 I │  decode         one PID's params     │  decode --try     test a hypothesis    │
 N │  decode         --dump-bytes matrix  │  decode --plot    sweep interps (TUI)  │
 G │                                      │  hunt             which byte IS ref Y? │
 L │                                      │  investigate      one PID, all angles  │
 E │                                      │  decode --discriminate <axis>          │
 ─ ├─────────────────────────────────────┼──────────────────────────────────────┤
 M │                                      │  correlate        rank all pair rels    │
 A │   align                              │  correlate --against   vs 1 reference   │
 N │   several signals, time-aligned      │  correlate --overlap   what's co-polled │
 Y │   rows (CSV / JSON / table)          │  correlate --find-mirrors  dup signals  │
   └─────────────────────────────────────┴──────────────────────────────────────┘
     cross-ECU / whole-drive grain ▲
```

`captures`/`decode` are the "look at it" tools; `investigate`/`hunt`/`correlate`
are the "reason about it" tools; **`align`** reads *several* signals side by side;
`decode --try` → `pids upsert-param` → `decode --stats`/`--corr` is the
define→verify loop.

## "I have X, I want Y → use Z"

| I have… | I want to… | Command |
|---|---|---|
| an unknown PID, no idea | everything about it in one shot | `investigate <ECU> <PID>` |
| a **known reference** signal | which *byte* on a target PID **is** it | `hunt <ECU> <PID> --against REF` |
| a known reference signal | *everything* that tracks it across a drive | `correlate --against REF` |
| nothing specific | the strongest relationships in a whole drive | `correlate` (bare) |
| a candidate expression | to test it without editing YAML | `decode --try "N=EXPR"` |
| a defined PID | its value ranges / stats / distribution | `decode [--stats]` |
| a grouping signal (state, mode, on/off) | which bytes *separate* the groups | `decode --discriminate <axis>` |
| several cross-ECU signals | them **side-by-side, time-aligned** | `align "A:B:C D:E:F"` |
| several PIDs' **raw captures** | to read them at the same instant, frame by frame | `captures "A:P1,P2" --step` |
| raw bytes of one PID | the timestamp×byte matrix | `decode --dump-bytes` |
| raw payloads | the hex / byte-diff / sessions index | `captures` |
| a finished-ish profile | what bytes are still undecoded | `coverage` |
| a session's end | what to work on next | `research` |
| a confounder to remove | correlation with a nuisance regressed out | `… --control REF` (hunt/correlate) |
| no on-bus anchor | a byte that lands in a physical band | `hunt --physical` |

## Where each sits in the RE lifecycle

```
orient ──► discover ──► capture ──► INSPECT ──► HYPOTHESIZE ──► DEFINE ──► VERIFY
research    scan/       query/      captures     investigate     pids       decode
coverage    discover    monitor     decode        hunt           upsert     (--stats,
                        (--save)    align         correlate                  --corr)
                                    decode        decode --try
                                    --dump-bytes  --discriminate
                                                  --plot
            └─────────────── correlate --overlap (what's even co-polled?) ──┘
```

## `align` vs the correlation tools

They share the *same* nearest-timestamp join, but differ in what they emit:

- **`align`** emits the **data** — a wide `time × signals` table you read,
  regime-split, or export (`--csv` joins with a `decode --dump-bytes` CSV or an
  external meter log via `--against-file`). Reach for it to *see* several signals
  together, or when you'd otherwise write a one-off join script.
- **`correlate` / `hunt`** emit **relationships** — ranked correlation summaries
  (r, linear fit, unit guess). Reach for them to *quantify* how signals relate.

A third shape of the same join is **`captures --step`** with a multi-PID QUERY: it
emits neither a table nor a coefficient but the **raw captures themselves**, stacked
one block per PID in a time-joined frame (decoded params + byte-diff hex). Reach for
it when you want to *look at the bytes* of several PIDs at one instant — typically
walking the frames across the moment a known signal switched. The PID set, the join
tolerance and the rendering are all editable inside the TUI; `--json` emits the same
frames as data. See [Analyze](../bring-your-own-car/06-analyze.md#reading-several-pids-at-the-same-instant).

## `--discriminate` by any axis

`decode --discriminate` ranks which params/bytes/bits most cleanly separate across
groups. The axis is either:

- **`state`** — the vehicle power state (charging / ready / driving), or
- **a cross-signal `ECU:PID:PARAM`** — grouped by that signal's discretized value.

```bash
# which byte separates the AC compressor being on from off?
canair decode HVAC 2201A2 --discriminate HVAC:220102:HVAC_COMPRESSOR_ON --bytes
```

The axis signal is nearest-joined onto each capture and discretized into
low-cardinality groups (enum/flag/mode); a too-continuous axis is rejected. Numeric
signals are scored with an F ratio, typed enum/bitmask params with Cramér's V.

## Scope every analysis to the right drive

All of these share the scope flags (`--since`/`--until`/`--date`, `--state`,
`--label`, `--today`, `--last-session`) — see
[Captures & states](captures-and-states.md). The natural unit is usually a
`--state` (e.g. `--state charging`) or a single session.

!!! note "keep-mode caveats"
    The monitor deduplicates recorded payloads per PID. The default,
    `canair monitor --keep-changes`, is **run-length**: it stores every genuine
    value-transition (so `A→B→A` is preserved and dwell durations are recoverable
    from the timestamps), collapsing only immediate repeats. The legacy
    `--keep-unique` is **global** dedup: it keeps only globally-distinct values,
    so return-to-previous transitions and durations are absent.
    `align`/`decode`/`correlate`/`investigate` print a banner when
    `keep:changes` sessions are in scope, because a **stored-row count then
    measures volatility, not sampling** — an ECU polled every cycle that rarely
    changes looks "barely polled". `keep:unique` gets **no blanket banner** — most
    historical captures were recorded that way, so it was noise on nearly every
    report; it is called out only where it actually changes a reading (the
    `investigate --events` dwell classes, the `--transform`/`--lag-scan`
    time-gap warnings, and a forced `--fill hold`). Rate/`delta` analysis is
    unreliable on either; use `--keep-all` when you need real sampling cadence.

## Forward fill: a run-length row is a segment, not a sample

A `keep:changes` row means *"the value changed to this, and stays this until the
next row"*. The time-aligned joins used to treat it as a point measurement, so a
signal that legitimately did not change had nothing to attach to and the row was
silently dropped. Measured on the bundled profile: aligning IGPM's charge-port
lock (known with certainty for a whole 3 h charge, in which it changed exactly
twice) against BMS SOC joined **5 of 2016 rows** — a 99.75 % loss of a window that
was never unknown.

`align`, `correlate`, `hunt`, `investigate` and `decode` therefore **carry a
run-length value forward** to reference instants it has no sample at:

| Flag | Effect |
|---|---|
| `--fill auto` | **Default.** Fills only rows from `keep:changes` sessions — per *row*, so a scope spanning a run-length and a `keep:unique` session fills only the part it may |
| `--fill hold` | Force it everywhere (legacy or `keep:all` data, whose provenance is unrecorded). Warns loudly on `keep:unique`, where global dedup genuinely destroyed the run structure |
| `--fill none` | Strict point semantics — the pre-fill behaviour, for comparison |
| `--max-hold SECONDS` | Cap the carry (default: until the next row or the end of its recording session) |

**A value is never carried across a session boundary** — the ECU may have changed
unobserved between two recordings — and the final run of a session closes when that
*session* stopped recording, not at the PID's own last capture.

**Filling is always reported**, because a filled row is reconstructed rather than
measured: `align` shows a per-column `[N joined + M held, up to 2h58m]` and marks
`filled` per row in `--json`; `correlate`/`hunt`/`investigate` name the run-length
signals they carried forward, in text and in a `--json` `fill` block. `--csv` stays
a pure numeric table and reports the carry on stderr instead.

!!! warning "A narrow time window can hide a run-length signal entirely"
    Scope filters run *before* the validity windows are computed, so
    `--since 13:00 --until 13:01` excludes the earlier row that established the
    value held through that minute — and the signal then appears absent. Prefer
    scoping by `--date`, `--state` or `--last-session` (which keep whole sessions)
    when a run-length signal is involved.

!!! note "Correlation magnitudes change under fill"
    A filled candidate contributes many repeated values, so `|r|` reflects the
    step-wise reconstruction rather than the handful of transition instants the
    strict join happened to keep. That is more honest — the strict join silently
    restricted the comparison to transitions, which *inflates* `|r|` — but it is a
    different number. Weighting statistics by segment duration is deliberately a
    follow-up (see `plans/2026-08-05-run-length-forward-fill-joins.md`).

## Mirrors: the same quantity reachable two ways

A mirror is one physical value exposed by two signals — a status bit an ECU
publishes that another repeats, a temperature a second module reports at a
different offset, a raw byte and the parameter derived from it. It is the fastest
possible identification of an unknown byte, because it needs no correlation
reasoning: the byte simply *is* the known signal.

- `decode --find-mirrors` — within one PID (rows aligned by capture, no time join)
- `correlate --find-mirrors` — across co-polled ECU/PIDs, time-aligned
- `correlate can --find-mirrors` — across arbitration IDs in a frame log

Two knobs, shared by all three:

| Flag | Why |
|---|---|
| `--mirror-match FRACTION` (default `0.9`) | Round-robin polling reads a *drifting* signal on two ECUs seconds apart, so they disagree by ±1 on a minority of rows. Demanding every row (`--mirror-match 1`) is enough on its own to hide most real mirrors |
| `--allow-offset` | Real mirrors are frequently the same quantity at a different zero or in different units — `AAF:2181:AAF_LDC_TEMP + 100` is the OBC's raw LDC temperature byte; a raw 12 V byte is `× 12.8` the decoded rail |

Raw agreement alone is not evidence, so a pair must *also* agree better than
coincidence (Cohen's κ, reported whenever agreement isn't unanimous). Without that
floor, two flags that are both zero in 99 % of rows "agree" 99 % of the time by
construction — on the bundled profile that turned 3 real mirrors into 73 reported
pairs.

!!! warning "Bimodal references defeat correlation ranking"
    When a reference signal collapses into **two flat, well-separated clusters** —
    e.g. a 12 V bus that sits at ~14.5 V while charging and ~12.2 V otherwise,
    with little variation *within* each level — correlation stops being useful for
    *identifying* a signal. **Any** candidate byte that merely differs between the
    two regimes then correlates near-perfectly (`|r|≈1`), because all the apparent
    "signal" is the single between-cluster jump every regime-discriminating byte
    shares (a two-cluster / point-biserial artifact). So `hunt --against` and
    `correlate --against` rank *cluster separation*, not a real match, and the top
    hits are meaningless.

    `hunt`/`correlate` now **warn** when the reference is bimodal. When you see it
    (or suspect it): don't trust the ranking — instead

    - scope to data with **continuous variation** in the reference (a `keep-all`
      drive where the 12 V/temperature actually sweeps a range), not a two-state
      regime flip, and/or
    - **anchor on absolute value** rather than correlation: a real signal must
      match the *known* physical value at each regime (e.g. read ≈12.2 in ACC2
      **and** ≈14.5 while charging), which a spurious regime-discriminator won't.

    A continuously-varying reference (vehicle speed: flat at 0 parked, then a
    *wide* moving cluster) is **not** flagged — the gap there doesn't dominate the
    moving cluster's own spread, so correlation against it stays meaningful.

    Related: the reverse trap on a **monotonic** scope (a single long charge where
    everything slowly rises) — every rising byte correlates too. A regime *exit*
    (the post-charge cool-down) breaks it: real temperatures fall, counters don't.
