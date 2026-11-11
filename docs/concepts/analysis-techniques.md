# Analysis techniques in depth

This page goes deeper than [Which analysis command when](analysis-commands.md). It explains four
techniques: how to find counters, how to read support maps, how forward fill joins signals that
canair polled at different times, and how to find the same quantity in two places.

## Counters: the one thing correlation cannot find

An odometer, an operating-hours tally, a power-cycle count and an uptime timer are all invisible
to every tool above, for the same reason. Within any single session, a slow counter **does not
move**. It has no variance to correlate and reads as a constant byte. What identifies it is its
behaviour across the *whole* capture history: it never decreases.

`investigate --counters` sweeps every 1–4-byte window × endianness. It scores how much evidence
there is that the window only rises, in **bits**. Each clean up-step with no down-step is one bit.
So 8 bits means 8 rises and no falls (about a 1-in-256 coincidence). That single scale lets canair
rank a densely-polled cumulative-Ah counter and an odometer read six times in a year together.
The sparse counter is not lost under a step-count threshold.

canair groups the results by *where* the monotonicity lives, which is what names the signal:

| Fingerprint | Behaviour | Typically |
|---|---|---|
| **accumulator** | rises across the corpus **and** within sessions | odometer, operating-seconds, cumulative Ah/Wh |
| **cycle counter** | rises across the corpus but **flat inside every session** | ignition / power-cycle / trip count |
| **run timer** | ramps per session, **resets to ~0**, tracks wall-clock | uptime, seconds-since-key-on |

```bash
canair investigate CLU 22B002 --counters          # → [B12:B14] 70047 → 73048 (the odometer)
canair investigate BMS 2101 --counters            # → the five cumulative BMS registers
canair investigate BCM 22C011 --counters --unmapped-only   # only counters not settled yet
canair investigate --counters                     # sweep the WHOLE profile — every counter in the car
canair investigate BMS --counters                 # sweep every BMS PID
```

`investigate` sweeps when you omit the parameter identifier (PID). Give an electronic control unit
(ECU), or a QUERY like `"BMS:2101,2102"`, to sweep its captured PIDs. Give nothing to sweep the
whole profile.

A sweep prints a ranked **summary**. For `--counters`, it is one list of every counter found.
Otherwise it is one line per PID with the cheap local facts that mark a PID as worth a deep-dive.
These facts are the varying and unmapped byte counts, the best state-separation F, and the
physical-band hits. `--top N` caps the summary. The per-signal timelines
(`--events`/`--dwell`/`--field`) stay single-PID.

`--unmapped-only` hides a window only when **every** signal that covers it is `verified`. canair
keeps a window that an *unverified* guess maps, and tags it `[NAME?]`. Monotonicity is frequently
the evidence that **refutes** the guess. For example, a byte labelled as a user-set hour that in
fact only ever rises is a counter, not an hour. Hiding those windows would hide the finding.

canair forms multi-byte windows in **ISO-TP** space (the ISO 15765-2 transport protocol), where
payload bytes are contiguous. It then renders them as a WiCAN expression. A counter that
straddles a protocol control information (PCI) framing byte therefore comes out as an explicit
shift composition (`B14 | (B15 << 8) | (B17 << 16) | (B18 << 24)`), and not as a wrong `[Bn:Bm]`
range. See [Byte indexing](byte-indexing.md).

One physical counter makes every *prefix* window monotonic too (dropping the low byte divides by
256). canair therefore collapses nested hits to one representative: the widest window whose end
bytes are not padding. The width recovers the readable magnitude. `[B12:B14] = 72982` is instantly
an odometer. Its low byte alone (`B14 = 88`) says nothing.

!!! note "This is the one analysis you should *not* scope"
    Unlike everything else on this page, `--counters` wants the **whole history**. A
    `--state` or `--since` filter shortens the horizon, and the horizon *is* the evidence.
    canair therefore **warns** on stderr about a scope filter (and flags `scoped: true` in
    `--json`), because the filter understates the bits ranking. Narrow the scope only to exclude
    known-bad data. If the report is empty, it names the best sub-threshold window and the
    `--min-bits` value that would show it. A dead end then still points somewhere.

`--counters` currently works on `uds` (diagnostic PIDs read through Unified Diagnostic Services)
only. The raw broadcast Controller Area Network (CAN) counterpart is not implemented.

## Support maps: which bits are signals at all

Some body and climate data identifiers (DIDs) start their data with a few constant bytes. These
bytes say which of the bits (or bytes) that follow are real signals. On the bundled Ioniq, the
`22BC03`–`22BC07` DIDs of the IGPM read `62 BC03 FD EE 3C 73 …`. The four bytes never change.
They map bit 7 of the first byte to data bit 0 of the first covered byte, bit 6 to bit 1, and so
on.

Every bit that ever moves in about 1,750 captures per DID falls on a marked position. A bit
outside the map never moves. HVAC `220100` uses the coarser form: one map bit per data byte.

```bash
canair investigate IGPM --support-map        # every IGPM DID: map, open signals, verdict
canair investigate BCM 22B005 --support-map  # one DID, including why a fit is refused
canair pids set-support-map IGPM 22BC03 --at 0 --length 4 --unit bit   # declare it
```

For each DID, canair reports the verdict. For the signals that the map marks, it reports which
are defined and which are **open**. Open signals split into *moving*, *always on* and *never set*.
A marked bit that nobody has exercised yet is where the next body signal is hiding. canair also
lists defined signals that the map does **not** mark, which deserve a second look.

How far to trust a fit:

| Verdict | Meaning |
|---|---|
| `STRONG` | consistent with the map and very unlikely to be chance on its own |
| `CORROBORATED` | consistent, but convincing only together with sibling DIDs of the same ECU that share the placement |
| `WEAK` | consistent, but too little moving data to say more |
| `REJECTED` | set bits (or live bytes) outside the map; canair shows the closest fit but does not propose it |

The score is the probability that an unrelated map would fit this well. It is `C(m, s) / C(8, s)`
per byte for *m* marked bits of which *s* ever move. canair multiplies it across the map and
charges it for every placement tried. A single DID with a handful of moving bits can never be
significant alone, which is why siblings corroborate each other. A DID that breaks the layout
counts against them, not for them.

A marked bit may be the *start* of a multi-bit field. canair therefore reports an unmarked bit
directly above a mark as a **field continuation**, not a violation (the left/right turn signal of
the IGPM is one 2-bit field).

### Field maps: one bit per parameter

The service-21 pages (BMS, VCU, MCU, OBC) lead with constant bytes too. Each bit marks one *field*
of the page, in order, whatever its width. That is how Hyundai's own diagnostics spec defines the
bitmap. The per-bit and per-byte layouts above are its special cases, where every field is one bit
or one byte. A record with more than 32 fields carries a further bitmap as field 32, 64, and so
on.

The map gives the number of fields, not their widths. `--support-map` therefore splits the bytes
using the defined signals:

- A defined signal is never cut.
- The bytes of an unmarked field must never change.
- Every marked field must exist.

| Verdict | Meaning |
|---|---|
| `EXACT` | the bytes split into fields exactly one way |
| `CONSISTENT` | several splits fit (the bytes that no signal reads group more than one way) |
| `BROKEN` | no split fits; shown as a `per-field reading` note under the other fits |

What this gives you on a new car:

- **open, marked**: a marked field that no signal reads yet. On the Ioniq, BMS `2101` (32 fields)
  has exactly one, and VCU `2101` (11) has one.
- **unsupported / fill**: the bytes of an unmarked field are fill on this unit, not a signal that
  never moved. On the Kona, the fill of BMS `220101` is a fifth module temperature and the
  battery-fan fields, which a liquid-cooled pack lacks.
- **the count itself**: *N* marks over *W* bytes means *W − N* bytes belong to multi-byte fields,
  before any correlation.
- **more bitmaps**: further bitmaps in a long record (Ioniq BMS `21F2` has one at data+48).

The spec counts from the most significant bit. Where that reading is broken and the least
significant bit first fits (the Ioniq MCU's `00 00 03 FF`), the report says `LSB first`.

When the captures carry several maps for one PID (two cars, two software versions), canair fits
each on its own rows and reports them as `VARIANTS`, with the map bits that differ.

### Trust, absence and declaring

This is **not make-wide**. BCM `22B005` is a hard exception: its light-stalk bits move outside its
map. That is why you *declare* a map per PID after you read the report, and canair does not apply
it automatically. No map has turned up on a Nissan or XPeng profile.

A PID with no fit says why, and that is information too. **Leading bytes vary** means the data
starts right after the DID echo, so the first bytes are signals, not a map. The other reasons mean
that the captures cannot tell: constant `00`, constant `FF`, nothing moving, too few captures, or
a few fixed values that differ between cars or sessions. A sweep prints the count of each on its
header line.

Once you declare a map (`canair pids set-support-map`, with `--unit field` and, where reported,
`--order lsb` or `--also-at`), `canair coverage` reads the map off each payload:

- The bytes of the map itself, and the bytes it marks as carrying nothing, stop being reported as
  `UNMAPPED`.
- `BITS` counts only marked bits.
- Signals that read unmarked positions are reported as `OUTSIDE MAP`.
- A field map adds a `FIELDS` line (marked fields against defined ones) and `OPEN FIELDS`.

`canair bix --annotate --ecu --pid` labels the map bytes in a payload. `canair monitor`,
`canair read` and `canair captures uds --step`/`--diff` draw them in a muted grey-blue. See
[`plans/2026-10-29-support-bitmap-coverage.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-10-29-support-bitmap-coverage.md)
and [`plans/2026-11-03-support-map-field-maps.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-11-03-support-map-field-maps.md)
for the design and the evidence.

## Forward fill: a run-length row is a segment, not a sample

A `keep:changes` row means *"the value changed to this, and stays this until the next row"*. The
time-aligned joins used to treat it as a point measurement. A signal that legitimately did not
change then had nothing to attach to, and canair silently dropped the row.

Measured on the bundled profile: aligning the charge-port lock of the IGPM against BMS SOC joined
**5 of 2016 rows**. The lock was known with certainty for a whole 3 h charge, in which it changed
exactly twice. That is a 99.75 % loss of a window that was never unknown.

`align`, `correlate`, `hunt`, `investigate` and `decode` therefore **carry a run-length value
forward** to reference instants where it has no sample:

| Flag | Effect |
|---|---|
| `--fill auto` | **Default.** Fills only rows from `keep:changes` sessions. It decides per *row*, so a scope that spans a run-length session and a `keep:unique` session fills only the part it may |
| `--fill hold` | Forces the fill everywhere (legacy or `keep:all` data, whose provenance is unrecorded). Warns loudly on `keep:unique`, where global dedup genuinely destroyed the run structure |
| `--fill none` | Strict point semantics: the behaviour before the fill existed, for comparison |
| `--max-hold SECONDS` | Caps the carry (default: until the next row or the end of its recording session) |

**canair never carries a value across a session boundary**, because the ECU may have changed
unobserved between two recordings. The final run of a session closes when that *session* stopped
recording, not at the last capture of the PID.

**canair always reports filling**, because a filled row is reconstructed rather than measured:

- `align` shows a per-column `[N joined + M held, up to 2h58m]` and marks `filled` per row in
  `--json`.
- `correlate`/`hunt`/`investigate` name the run-length signals they carried forward, in text and
  in a `--json` `fill` block.
- `--csv` stays a pure numeric table and reports the carry on stderr instead.

!!! warning "A narrow time window can hide a run-length signal entirely"
    Scope filters run *before* canair computes the validity windows. `--since 13:00 --until 13:01`
    therefore excludes the earlier row that established the value held through that minute, and
    the signal then appears absent. Scope by `--date`, `--state` or `--last-session` (which keep
    whole sessions) when a run-length signal is involved.

!!! note "Correlation magnitudes change under fill"
    A filled candidate contributes many repeated values, so `|r|` reflects the step-wise
    reconstruction. The strict join kept only the handful of transition instants. The filled
    number is more honest, because the strict join silently restricted the comparison to
    transitions, which *inflates* `|r|`. But it is a different number. Weighting statistics by
    segment duration is deliberately a follow-up (see
    `plans/2026-08-05-run-length-forward-fill-joins.md`).

## Mirrors: the same quantity reachable two ways

A mirror is one physical value that two signals expose. Examples are a status bit that one ECU
publishes and another repeats, a temperature that a second module reports at a different offset,
and a raw byte with the signal derived from it. A mirror is the fastest possible identification of
an unknown byte, because it needs no correlation reasoning: the byte *is* the known signal.

- `decode --find-mirrors` — within one PID (rows aligned by capture, no time join)
- `correlate --find-mirrors` — across co-polled ECU/PIDs, time-aligned
- `correlate can --find-mirrors` — across arbitration IDs in a frame log

Two knobs are shared by all three:

| Flag | Why |
|---|---|
| `--mirror-match FRACTION` (default `0.9`) | Round-robin polling reads a *drifting* signal on two ECUs seconds apart, so they disagree by ±1 on a minority of rows. Demanding every row (`--mirror-match 1`) hides most real mirrors on its own |
| `--allow-offset` | Real mirrors are frequently the same quantity at a different zero or in different units. `AAF:2181:AAF_OBC_TEMP + 100` is the raw temperature byte of the OBC. A raw 12 V byte is `× 12.8` the decoded rail |

Raw agreement alone is not evidence. A pair must *also* agree better than coincidence (Cohen's κ,
reported whenever agreement is not unanimous). Without that floor, two flags that are both zero in
99 % of rows "agree" 99 % of the time by construction. On the bundled profile, that turned 3 real
mirrors into 73 reported pairs.

!!! warning "Bimodal references defeat correlation ranking"
    When a reference signal collapses into **two flat, well-separated clusters**, correlation
    stops being useful for *identifying* a signal. An example is a 12 V bus that sits at ~14.5 V
    while charging and ~12.2 V otherwise, with little variation *within* each level.

    **Any** candidate byte that merely differs between the two regimes then correlates
    near-perfectly (`|r|≈1`). All the apparent "signal" is the single between-cluster jump that
    every regime-discriminating byte shares (a two-cluster / point-biserial artifact). So
    `hunt --against` and `correlate --against` rank *cluster separation*, not a real match, and
    the top hits are meaningless.

    `hunt` and `correlate` **warn** when the reference is bimodal. When you see the warning (or
    suspect the problem), do not trust the ranking. Instead, do one or both of these:

    - Scope to data with **continuous variation** in the reference (a `keep-all` drive where the
      12 V or temperature actually sweeps a range), not a two-state regime flip.
    - **Anchor on absolute value** rather than correlation. A real signal must match the *known*
      physical value at each regime (for example, read ≈12.2 in ON **and** ≈14.5 while
      charging). A spurious regime-discriminator will not.

    A continuously-varying reference is **not** flagged. Vehicle speed is an example: it is flat
    at 0 while parked, then a *wide* moving cluster. The gap there does not dominate the spread
    of the moving cluster, so correlation against it stays meaningful.

    Related: the reverse trap on a **monotonic** scope (a single long charge where everything
    slowly rises). Every rising byte correlates too. A regime *exit* (the post-charge cool-down)
    breaks it: real temperatures fall, counters do not.
