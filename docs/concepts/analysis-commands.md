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
    `align`/`decode`/`correlate`/`investigate` flag both in scope — a strong
    caveat for `keep:unique`, a milder one for `keep:changes` (stored rows are
    transitions, not fixed-rate samples). Rate/`delta` analysis is unreliable on
    either; use `--keep-all` when you need real sampling cadence.
