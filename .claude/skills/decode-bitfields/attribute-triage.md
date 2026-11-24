# Steps 4 and 4.5 — attribute the bits and triage the byte

Scope: `investigate --events/--dwell/--bits`, discriminate/mirror sweeps, and enum/analog checks.

## Step 4 — attribute the bits

Four levers, in the order they usually pay off:

**1. Edge timeline — the primary tool.**
```
uv run canair --profile hyundai-ioniq-ae-ev investigate IGPM 22BC03 --events --bits
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
suppressed (`cli/commands/investigate/render.py`).

**2. Dwell classification — separates a pulse from a state.**
```
uv run canair --profile hyundai-ioniq-ae-ev investigate IGPM 22BC03 --dwell --bits
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
uv run canair --profile hyundai-ioniq-ae-ev decode IGPM 22BC03 --discriminate state --bits
```
Ranks every toggling bit by how cleanly it separates vehicle power states (F =
between/within variance). Use when you have broad state coverage but no narrated
events — an ignition/relay bit separates states enormously.

**4. Cross-ECU mirrors — free identification.**
```
uv run canair --profile hyundai-ioniq-ae-ev correlate IGPM --find-mirrors --bits
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

**Enumerate the byte's distinct values.** A real bitfield's bits combine freely, so an exercised
byte shows many values. A byte with a handful of values whose bits never move independently is
an **enum**, not a bitfield. List the values with `decode <ECU> <PID> --dump-bytes` (add
`--json` to script it: `{ecu, pid, columns, offsets, rows}`, each row `{time, date,
vehicle_states, bytes}`).

If bits only ever appear together, they are one state code, not separate flags. Defining them
per bit would misrepresent it. Model the byte as `type: enum` instead. Run this check **before**
you hunt for a session where the bits diverge: a byte with three distinct values has none.

**Sanity-check the episode count.** `--dwell`'s `episodes` column separates flags
from bits of an *analog* byte. A flag toggles with discrete events (single or
double-digit episodes). A bit toggling hundreds of times is the low bit of a
continuously-varying value or a counter.

An analog byte's low bit shows as `B14:3 sustained 1143 episodes`. A real flag shows as
`B23:4 sustained 4 episodes`.

Discard the high-count rows before ranking; a multi-byte analog value's bits will
otherwise dominate every `--bits` sweep on a powertrain PID. Cross-check by
looking for a range expression (`[B22:B23]`) over the byte — note that a range
read does **not** suppress the bitfield-gap report, because
`references_full_byte` deliberately excludes `[...]` ranges.
