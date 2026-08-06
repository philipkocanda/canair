---
name: decode-bitfields
description: Decode the UNKNOWN bits of a partially-decoded bitflag byte in a canair profile — finding bytes where only one or two bits are mapped, capturing narrated event data, and attributing the remaining bits with investigate --events/--dwell/--bits, discriminate --bits and --find-mirrors --bits. Load this when auditing bitfield coverage, working a "byte X has 3 of 8 bits decoded" gap, or decoding discrete body/status signals (doors, locks, lights, relays, latches). NOT for continuous analog bytes (use reverse-engineer-signal), pruning/renaming already-decoded params (use pid-cleanup), or submitting a profile upstream (use contributing-profiles).
---

# Decoding the unknown bits of a bitflag byte

A body/status byte is rarely a number — it is 8 independent flags. Profiles
accumulate them **one bit at a time** (a door bit here, a light bit there), so
the steady state of a mature profile is *partially-decoded bitfield bytes*: 3 of
8 bits named, 5 unknown but almost certainly meaningful. Those 5 are the
cheapest remaining signals in the whole profile — the byte is already being
polled, already captured, and its neighbours are already proven.

This skill is the **bit-level** loop. For the general orient → capture →
analyze → define → verify method see `reverse-engineer-signal`; that skill's
byte-level statistics (Pearson r, unit guessing, physical bands) mostly do
**not** apply to a 0/1 series. Bits are decoded by **event attribution**, not
correlation.

Always `uv run canair …` from the repo root. Edit definitions only via
`canair pids`. Pass `--profile NAME` on every mutation.

## Know which of the two models you are looking at

canair can represent a bitfield **two** ways, and they are handled by
completely separate code paths:

| | Model A — one param per bit | Model B — one `type: bitmask` param |
|---|---|---|
| Expression | `expression: B10:5` (×8 params) | `expression: B10` + `bits: {5: door_drv}` |
| Analysis tooling sees it | **yes** — all of `--bits`, `coverage --bitfields`, `bix` overlay | partly — `coverage --bitfields` counts its `bits:` map, but `--bits` sweeps do not split it |
| Best for | signals decoded incrementally, mixed verified/unverified, per-bit notes/`ha_class` | a byte that is *wholly* a known flag set (e.g. a weekday schedule mask) |

**Model A is the working default.** The bundled `ioniq-2017` profile uses it
exclusively — verified: zero `type: bitmask` params exist in any bundled
profile. Prefer A while bits are still being discovered; it is the only model
the per-bit ranking sweeps (`--discriminate --bits`, `--find-mirrors --bits`,
`investigate --bits`) can attribute individually.

Model B's payoff is elsewhere: a single logical signal for
`investigate --events --field NAME` and categorical stats (Cramér's V). Reach
for it only once a byte is essentially fully understood.

## Step 1 — find the partially-decoded bytes

```
uv run canair --profile ioniq-2017 coverage --bitfields
```

```
  IGPM 22BC03 (18p, 15 verified)  11 data bytes, 2026-04-15
      UNMAPPED B5,B6,B7,B13,B14,B15,B17
      BITS B11 have{0,1,2,5,6} missing{3,4,7}
      BITS B12 have{2,3,4,5} missing{0,1,6,7}
  VCU 2101 (…)
      BITS B10 have{0,1,2,3} missing{4,5,6,7} (also read whole)
```

A `BITS` line is exactly this skill's work item: `have{}` = bit indices some
param reads (0 = LSB), `missing{}` = the unknowns. A byte is listed when (a) ≥1
param reads it bit-wise (`Bn:k`/`Sn:k`) **or** a `type: bitmask` param labels
some of its bits, (b) it is a real data byte (PCI/SID/DID echo excluded), and
(c) fewer than 8 bits are covered (`canlib/commands/coverage.py::analyze_pid`).

**`(also read whole)`** means some param *additionally* reads the byte as a
whole (`Bn`), the `DEBUG_*_FLAGS` convention. That is not a decoding of the
individual bits, so the gap still counts — the flag just tells you the raw byte
is already exposed, and warns that on a byte which is really a discrete *code*
rather than independent flags the "gap" may be intentional (run Step 4.5 to
tell which).

Scope with `coverage IGPM 22BC03 --bitfields`; `--json` gives
`incomplete_bitfields: [{byte, have, missing, also_whole}]` for scripting. Note
`--bitfields` filters which **PIDs** print, not which lines — `UNMAPPED`/
`UNVERIFIED` still appear on a selected PID.

### Coverage's remaining blind spot

`coverage --bitfields` used to under-report badly; two of the three historical
gaps are now fixed (a whole-byte read no longer suppresses the finding, and
`Sn:k` + `type: bitmask` maps are counted). One remains — cross-check before
concluding a byte is done:

**Mask-style expressions are invisible to it.** `(B09 & 0x04)` yields no bit
references at all, so a byte read only that way reports no gap even though seven
bits are undecoded. Always author bits as the `Bn:k` accessor, never an `&`
mask. (Zero `&` expressions exist in bundled profiles today; keep it that way.)

> Historical note, in case you meet an old profile or doc: before 2026-08-06 a
> byte vanished from this report entirely if *any* param read it whole. That hid
> the two most-captured bitfields in the bundled profile — VCU 2101 B10 (the
> PRND byte, 6365 captures) and BMS 2101 B14 (5997) — because both follow the
> `DEBUG_*_FLAGS` "raw byte + individual bits below" convention. See
> `plans/2026-08-06-bitfield-audit-and-gear-state.md`.

## Step 2 — orient on the byte

```
uv run canair --profile ioniq-2017 bix -a 62BC030AFF00000000 --ecu IGPM --pid 22BC03
```

```
  subfunction: 2-byte DID — derived from --pid 22BC03 (override with -1/-2)
    B10 | 0x00 |   0x07 |        | [DOOR_RL_OPEN:0] [DOOR_LOCK_RL:1] … [TRUNK_OPEN:7]
    B11 | 0x00 |   0x08 |        | [HOOD_OPEN:0] [SEATBELT_FL:1] [SEATBELT_FR?:2] [ACC2_IGN_ON:5] [IGN_STATUS_MIRROR?:6]
```

The fastest per-bit "what's already claimed here" view (`?` suffix =
unverified). B11's gaps at bits 3, 4, 7 are visible at a glance.

> **Subfunction width.** `--pid` derives it (`22xxxx` → 2 bytes, else 1) and says
> so in the header, so the overlay form above needs no `-2`. An explicit `-1`/`-2`
> still wins and is warned about when it contradicts the PID. **Without `--pid`**
> the 1-byte default applies — annotating a `22xxxx` payload bare still needs
> `-2`, or the second DID-echo byte is mislabelled as a data byte and every role
> after it shifts (verified: B03 reads `PID`, B04 becomes a data byte).

## Step 3 — capture data that can attribute a bit

Correlation cannot decode a door bit; **a narrated event log can.** Bits need
captures where you *caused* the transitions and wrote down what you did.

```
uv run canair --profile ioniq-2017 monitor IGPM:22BC03 --save \
    --label "IGPM bitfield events" --state ACC \
    --notes "fob unlock, open/close trunk, open/close pax door, open drv door, open/close hood, fob lock"
```

- Keep the default **`--keep-changes`** — it records both edges. `--keep-unique`
  **drops falling edges**, which makes dwell durations unrecoverable (`--dwell`
  warns and reports `unknown` classes).
- Exercise **one actuation at a time**, pausing between, and press **`s`** in the
  monitor to note each action. A per-capture note is what `--events` aligns to.
- Cover states: a bit gated by ignition only moves in `ACC`/`RUN`.
- Poll the **suspected mirror ECUs too** (e.g. `"IGPM:22BC03 BCM:22B004"`) so
  cross-ECU mirror detection has co-polled data.

## Step 4 — attribute the bits

Four levers, in the order they usually pay off:

**1. Edge timeline — the primary tool.**
```
uv run canair --profile ioniq-2017 investigate IGPM 22BC03 --events --bits
```
```
    22:07:14  B12:2 0→1  [DRL]
    22:07:14  B12:3 0→1  [TAIL_LIGHTS]
    22:07:14  B12:5 0→1  [LOW_BEAM]
    12:41:25  B10:5 0→1  [DOOR_DRV_OPEN]  ~ note: parked
```
Every rising/falling edge with its timestamp and the nearest capture note. An
**unlabelled bit that toggles at the same instant as your noted action is the
finding.** When bit edges exist for a byte the redundant whole-byte edge is
suppressed (`_investigate_render.py:193-201`).

**2. Dwell classification — separates a pulse from a state.**
```
uv run canair --profile ioniq-2017 investigate IGPM 22BC03 --dwell --bits
```
```
    B12:2   sustained   4 episodes   8 trans    55.5s  [DRL]
    B13:1   sustained   2 episodes   4 trans   233.7s
```
`momentary` (briefly pulsed — a button, a request, a wake latch) vs `sustained`
(a held state — a door, a lamp). This distinguishes "door open" from "door-open
*event*" and is how a self-clearing wake latch is recognised.

**3. State discriminability — for bits with no event log.**
```
uv run canair --profile ioniq-2017 decode IGPM 22BC03 --discriminate state --bits
```
Ranks every toggling bit by how cleanly it separates vehicle power states (F =
between/within variance). Use when you have broad state coverage but no narrated
events — an ignition/relay bit separates states enormously.

**4. Cross-ECU mirrors — free identification.**
```
uv run canair --profile ioniq-2017 correlate IGPM --find-mirrors --bits
```
If an unknown bit mirrors a **named** bit on another ECU, it inherits that
meaning with no reasoning required. Time-aligned, so it needs co-polled
captures. `decode … --find-mirrors --bits` is the intra-PID variant (rows
aligned by capture index, no join).

`investigate … --bits` (no `--events`) also emits a ranked table per bit
(mapped-by / stateF / best anchor). Note it **hides verified-mapped positions by
default** — pass `--all` to see them, which is how you confirm an existing claim.

## Step 4.5 — triage the byte before you judge the bits

Two cheap checks that decide whether the "bits" are flags **at all**. Do these
*before* reasoning about meaning — each one can invalidate a whole byte's worth
of candidates.

**Enumerate the byte's distinct values.** A real bitfield's bits combine freely,
so an exercised byte shows many values. A byte with a handful of values whose
bits never move independently is an **enum**, not a bitfield:

```
uv run canair --profile ioniq-2017 decode BCM 22B004 --dump-bytes --json \
  | uv run python -c "import json,sys,collections; \
rows=json.load(sys.stdin)['rows']; \
c=collections.Counter(int(r['bytes']['B10']) for r in rows if 'B10' in r['bytes']); \
print({f'{k} (0b{k:08b})': n for k,n in sorted(c.items())})"
```

```
{'0 (0b00000000)': 861, '14 (0b00001110)': 30, '192 (0b11000000)': 2}
```

(`--dump-bytes --json` emits `{ecu, pid, columns, offsets, rows}`; each row is
`{time, date, vehicle_states, bytes}` — a CSV is the default without `--json`.)

`BCM 22B004 B10` returns only `{0, 14, 192}` over 893 captures — bits 1/2/3 only
ever appear together (as `14`), and 6/7 together (as `192`). Those are **not five
flags**; they are one state code, and defining them per-bit would misrepresent
it. Model the byte as `type: enum` instead.

This settles enum-vs-bitfield outright, so run it **before** the
"find a session where they diverge" hunt below — a byte with 3 distinct values
has no diverging session to find.

**Sanity-check the episode count.** `--dwell`'s `episodes` column separates flags
from bits of an *analog* byte. A flag toggles with discrete events (single or
double-digit episodes). A bit toggling hundreds of times is the low bit of a
continuously-varying value or a counter:

```
    B14:3   sustained   1143 episodes   2288 trans   10.4s    <- analog byte's low bit
    B23:4   sustained      4 episodes      9 trans  245603s   <- a real flag
```

Discard the high-count rows before ranking; a multi-byte analog value's bits will
otherwise dominate every `--bits` sweep on a powertrain PID. Cross-check by
looking for a range expression (`[B22:B23]`) over the byte — note that a range
read does **not** suppress the bitfield-gap report, because
`references_full_byte` deliberately excludes `[...]` ranges.

## Step 5 — judge the evidence

- **A bit and the param that maps it appear as duplicate adjacent rows with
  identical F** in `--discriminate`. That is expected and is the *confirmation*
  that a param really is that bit. Two *different* bits with identical F are
  either mirrors or co-driven — not independent findings.
- **Prefer a same-payload cross-tab to a time join.** Bits in the *same* PID
  response are perfectly aligned — cross-tabulating them has zero join error and
  is far stronger evidence than a correlation. This is how `VCU 2101 B23`'s
  nesting (`EV_READY ⊆ INVERTER_ENABLED ⊆ B23:4`) was proven over 6367 captures
  with no violations. Reach for `align`/`correlate` only across ECUs/PIDs.
- **Simultaneous edges are not one signal.** DRL, tail lights and low beam all
  switch on one stalk movement; three bits legitimately move together. Once
  Step 4.5 has ruled out an enum, separate them by finding a session where they
  *diverge* (a lighting mode that raises only some of them). Until then say so,
  or don't name them separately. Bits that never diverge across a large corpus
  are a **mirrored pair** — define one and record the other as a disabled
  mirror rather than inventing two meanings.

- **Mirror agreement must beat coincidence.** Two mostly-zero flags "agree" 99%
  of the time by construction; the matcher guards this with Cohen's κ
  (`canlib/mirrors.py:196`). Trust the reported κ, not the raw agreement.
- **A high correlation with a named signal may mean SUPERSET, not equality.**
  This is the single most productive check in the skill, because it catches
  *already-verified* mistakes. `VCU 2101 B26:3` shipped as `GEAR_DRIVE` on the
  strength of `r=+0.984` vs the drive-gear bit — but it is really "gear is not
  Park": it also reads 1 in Reverse and Neutral, which simply had not been
  sampled when the correlation was computed. 57 false positives, 0 false
  negatives, and 99.1% agreement, which is exactly what a superset looks like.
  A correlation cannot distinguish `A == B` from `A ⊇ B`; a cross-tab against
  *every* value of the reference can. Always tabulate the reference's full value
  set (`P/R/N/D`, not `D`/not-`D`) and read the **asymmetry** of the errors —
  all-false-positives-no-false-negatives means superset, and the honest name is
  the superset (`GEAR_PARK_INV`), not the special case.
- **A near-perfect correlation computed on a thin corpus expires.** Re-run the
  old evidence before trusting a note: the `r=+0.984` above was true over 773
  captures and false over 6365. When a note cites an `n`, check today's `n`.
- **One episode is not evidence — and a single sample can tell a coherent
  story.** Prefer ≥2–3 clean, independent actuation→edge pairs before naming a
  bit (`--dwell`'s `episodes`/`trans` columns are the count). The failure mode is
  not obvious noise: one `BMS 2101 B14 = 0x45` row produced a *physically
  sensible* three-part reading (two HV contactors plus a rapid-charge relay) that
  survived scrutiny until the capture itself was withdrawn as untrustworthy.
  Naming it `UNKNOWN_B14_*` per the rule below is what kept the profile honest.
- **Never-toggling bits are unknowable, not absent.** Constant bits are dropped
  from every `--bits` series by design (`canlib/xanalysis.py:334-361`). A bit
  in `missing{}` that never appears in `--events` simply wasn't exercised — file
  a `research:` lead naming the state/action to try, don't conclude "unused".
- **A constant-decoding param is a defect, whatever its `verified` flag.** Sweep
  for them (decode every bit param and check `len(set(values)) == 1`): the
  bundled profile had `1-B11:5` shipping a constant 1 under the name
  `VEHICLE_STATE_MAIN_RELAY_ON`, i.e. asserting "HV relay closed" in `SLEEP` and
  while charging. An inverted expression (`1-Bn:k`) is especially dangerous —
  it turns "bit never set" into a confident `true`. Distinguish *unexercised*
  (siblings vary; keep the claim, add a caveat + research lead) from *wrong*
  (the expression cannot be what the name says; rename to the position and
  disable).

## Step 6 — define the bits

One param per bit (Model A):

```
uv run canair --profile ioniq-2017 pids upsert-param IGPM 22BC03 HOOD_OPEN B11:0 \
    --ha-class door --verified \
    --source "timed event capture 2026-07-24" \
    --notes "Bit flag — 1=open. B11:0 went 0->1 only when the hood was opened during a narrated event sequence."
```

- **Watch the decoded-range echo** `upsert-param` prints — a bit param that
  reads `constant 0` across captures means the offset or bit index is wrong.
- **Naming honesty** (same bar as `pid-cleanup` rule 4): known meaning → name
  it; known bit, unknown meaning → put the position in the name
  (`UNKNOWN_B10_6`, matching the file's existing convention); a guess → suffix
  `?` in the name or leave it `--unverified`. A confirmed-but-unexplained bit
  is still worth defining as a placeholder — it stops the next person
  re-deriving it and makes the gap visible.
- Use `--unverified` until you have the repeated evidence from Step 5;
  `--disabled` for a bit you want tracked but not shipped to the device.
- Speculation about untested states goes in `canair pids add-research`, not the
  note.

## Step 7 — verify

```
uv run canair --profile ioniq-2017 validate pids
uv run canair --profile ioniq-2017 coverage IGPM --bitfields
uv run canair --profile ioniq-2017 bix -a <payload> --ecu IGPM --pid 22BC03
```

`have{}` must have grown by exactly the bits you defined and `missing{}` shrunk
to match. If the byte *disappeared* from the report, you wrote an `&`-mask
expression and silently claimed the whole byte — go back to Step 1's blind spot.
(A plain whole-byte `Bn` no longer hides the gap; it just adds
`(also read whole)`.)

## Pitfalls

| Symptom | Cause |
|---|---|
| Byte vanished from `coverage --bitfields` after an edit | An `&`-mask expression now claims it (a plain `Bn` only adds `(also read whole)`) |
| New bit param reads `constant` in the upsert echo | Wrong byte offset (WiCAN `Bnn` includes ISO-TP PCI) or wrong bit index |
| `bix -a` roles shifted by one | Annotating a `22xxxx` payload with no `--pid` and no `-2` |
| `--dwell` classes all `unknown` | Session recorded `--keep-unique`; falling edges dropped |
| Bit never appears in `--events` | Constant in scope — never exercised, not "unused" |
| Several bits look like one signal | Enum code (check distinct byte values, Step 4.5) or co-driven by one action |
| A bit toggles hundreds of times | Low bit of an analog/counter byte, not a flag (Step 4.5) |

## Definition of done

- Every bit you claim has ≥2 independent actuation→edge pairs, or is explicitly
  `--unverified`/named for its position.
- The byte's distinct values were enumerated — an enum was not filed as flags,
  and analog-byte bits were not filed as flags.
- `coverage --bitfields` shows the byte's `have{}` grown and `missing{}`
  shrunk — the byte did not disappear.
- No `&`-mask expression was introduced on a partially-decoded byte.
- Every bit claimed against a *named* reference was cross-tabbed over the
  reference's full value set, not just correlated — supersets were ruled out.
- No param decodes a constant under a name that asserts a state.
- Unexercised bits have a `research:` lead naming the state/action needed.
- `canair validate pids` passes; notes are bare facts.
