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
 S │  captures       raw payloads / diff  │  decode --corr    signal vs 1 reference │
 I │  decode         one PID's signals    │  decode --try     test a hypothesis    │
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

**`search`** answers a different question — *where is this text?* — and so needs no
target at all: it scans every payload for a part number, version string, VIN or byte
pattern, or lists every plausible string when you give it none.

Two tools sit outside the map because they need **no reference signal at all** —
they judge a byte by its own shape instead of by what it tracks:
`hunt --physical` (does its scaled value land in a named physical band?) and
`investigate --counters` (does it only ever go *up*?). Reach for these when a PID
has nothing co-polled to correlate against.

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
| a part number, version or VIN | which ECU/PID holds it | `search <TEXT>` |
| nothing, just the captures | every part number / version string in them | `search` |
| a finished-ish profile | what bytes are still undecoded | `coverage` |
| a session's end | what to work on next | `research` |
| an ECU known only by its actuators | which of its DID families were never read | `research --gaps` |
| a confounder to remove | correlation with a nuisance regressed out | `… --control REF` (hunt/correlate) |
| no on-bus anchor | a byte that lands in a physical band | `hunt --physical` |
| a suspected odometer / hour meter / cycle count | the bytes that **only ever rise** | `investigate <ECU> <PID> --counters` |
| no idea which PID hides a counter | every counter in the car, ranked | `investigate --counters` (bare) |
| a body/climate ECU with many bit-flag DIDs | which of their bits are **real signals** at all | `investigate <ECU> --support-map` |

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
one block per PID in a time-joined frame (decoded signals + byte-diff hex). Reach for
it when you want to *look at the bytes* of several PIDs at one instant — typically
walking the frames across the moment a known signal switched. The PID set, the join
tolerance and the rendering are all editable inside the TUI; `--json` emits the same
frames as data. See [Analyze](../bring-your-own-car/06-analyze.md#reading-several-pids-at-the-same-instant).

## `--discriminate` by any axis

`decode --discriminate` ranks which signals/bytes/bits most cleanly separate across
groups. The axis is either:

- **`state`** — the vehicle power state (charging / ready / driving), or
- **a cross-signal `ECU:PID:PARAM`** — grouped by that signal's discretized value.

```bash
# which byte separates heat-pump heating being on from off?
canair decode HVAC 2201A2 --discriminate HVAC:220102:HVAC_HEAT_PUMP_ON --bytes
```

The axis signal is nearest-joined onto each capture and discretized into
low-cardinality groups (enum/flag/mode); a too-continuous axis is rejected. Numeric
signals are scored with an F ratio, typed enum/bitmask signals with Cramér's V.

## Scope every analysis to the right drive

All of these share the scope flags (`--since`/`--until`/`--date`, `--state`,
`--label`, `--today`, `--last-session`) — see
[Vehicle states](vehicle-states.md) and [Captures & states](captures-and-states.md). The natural unit is usually a
`--state` (e.g. `--state charging`) or a single session.

!!! note "keep-mode caveats"
    The monitor records **every poll** by default (`--keep-all`), so a stored row is a real
    sample. Repeated payloads are folded on disk, so this costs little space (see
    [Capture file format](capture-format.md)). Two dedup modes appear in older captures:
    `keep:changes` (`--keep-changes`, still available) is **run-length**: it stores every genuine
    value transition (so `A→B→A` survives and dwell durations are recoverable from timestamps)
    and collapses only immediate repeats. The retired `keep:unique` was **global** dedup, so
    return-to-previous transitions and durations are absent.

    `align`/`decode`/`correlate`/`investigate` print a banner when `keep:changes` sessions are in
    scope, because a **stored-row count then measures volatility, not sampling** — an ECU polled
    every cycle that rarely changes looks "barely polled". `keep:unique` gets no blanket banner
    (many historical captures use it); it is flagged only where it changes a reading: the
    `investigate --events` dwell classes, the `--transform`/`--lag-scan` time-gap warnings and a
    forced `--fill hold`. Rate/`delta` analysis is unreliable on either; record with the default
    keep-all when you need real sampling cadence.

