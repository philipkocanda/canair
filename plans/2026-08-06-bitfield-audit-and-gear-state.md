# Bitfield audit blind spots, and the gear/vehicle-state bytes they hid

**Status:** in progress (2026-08-06)
**Scope:** `canair coverage --bitfields` (code) + the `ioniq-2017` VCU/BMS/IGPM
gear & vehicle-state bitfield definitions (data).

## Why

A body/status byte is 8 independent flags, and profiles accumulate them one bit
at a time — so the steady state is *partially-decoded bitfield bytes*. The
`decode-bitfields` skill's Step 1 tells an agent to find that work with
`canair coverage --bitfields`.

That audit was lying. It silently hides a byte's bit-gap whenever **any**
parameter also reads the byte whole — and the profile's own
`DEBUG_*_FLAGS` convention ("raw byte — individual bits below") does exactly
that. Measured on `ioniq-2017`: 14 bitfield bytes have gaps, **4 were hidden**,
including the two with the most captures in the whole profile.

| byte | captures | mapped bits | unknown **and varying** | was visible? |
|---|---|---|---|---|
| VCU 2101 **B10** (PRND) | 6365 | 0,1,2,3 | **5** | no |
| BMS 2101 **B14** | 5997 | 0,5,6,7 | **1** (a mirror) | no |
| VCU 2101 B26 (gear) | 6365 | 3,5 | — (rest constant) | no |
| IGPM 22BC06 B10 | 290 | 0,1,2 | 3,5 (co-driven) | no |
| VCU 2101 B23 | 6365 | 2,4,5 | — (rest constant) | yes |

Chasing the hidden bytes then surfaced four *wrong* shipped signals, so this
change is a tool fix plus a data correction.

> **Corpus caveat.** The first analysis pass ran before `52f1eb3` withdrew the
> hand-transcribed August-2025 captures. Every count in this document is from
> the cleaned corpus. The one conclusion that changed materially is BMS 2101
> B14 — see that section.

## Part 1 — the tool

`canlib/commands/coverage.py::analyze_pid` had three defects, all documented as
"blind spots" in the `decode-bitfields` skill and all live:

1. **A whole-byte read suppressed the finding entirely.** The old rule computed
   the gap and then dropped it if `references_full_byte()` matched any param.
2. **`Sn:k` bit reads were invisible** — a private `_BIT_RE` matched only `B`,
   while the shared `canlib/byteindex.extract_bit_indices` handles both.
3. **`type: bitmask` params contributed no bit coverage**, so converting a
   partially-labelled byte to a bitmask *hid* the remaining gap.

### The new rule

- **Bit coverage** = `Bn:k`/`Sn:k` references (via the shared
  `byteindex.extract_bit_indices` — the private regex is gone) **∪** the `bits:`
  keys of any `type: bitmask` param on that byte. A labelled bit *is* a decoded
  bit, whichever of the two models declares it.
- **A whole-byte read no longer suppresses the report.** The finding is
  annotated instead:
  ```
  BITS B10 have{0,1,2,3} missing{4,5,6,7}  (also read whole)
  ```
  Rationale: suppression trades a false positive for a **false negative**, and
  the false negative cost us a signal with 6365 captures. A few noisy lines on a
  legitimately-enumerated byte is the cheaper error — the reader can dismiss an
  annotated line, but cannot act on a line that was never printed.
- **Unchanged:** a byte with *only* a whole-byte read and no bit references is
  still not reported. It is not being treated as a bitfield, so it has no bit
  gap — that is the `unmapped`/`unverified` axis's job.

`BitfieldGap` gains `also_whole: bool`, surfaced in `--json`. Additive to the
existing bare-list payload, so nothing that reads it breaks (the envelope
question is `plans/2026-08-06-json-output-convention.md`, out of scope here).

### Deferred

Blind spot #1 as the skill describes it — a `(B09 & 0x04)` mask expression
yields no bit references *and* reads the byte whole. Under the new rule it no
longer suppresses anything, but it also contributes no bit coverage, so a byte
masked that way still reports no gap. Parsing masks into bit references would
make coverage guess at intent; the better fix is a `canair validate pids`
warning ("prefer the `Bn:k` accessor to an `&` mask"). Not in this change.
Zero `&` expressions exist in bundled profiles today.

## Part 2 — the data these blind spots hid

All findings are same-payload cross-tabs (bits in one PID response are perfectly
aligned, so there is zero join error — far stronger than a time-joined
correlation).

### `GEAR_DRIVE` (VCU 2101 B26:3) was misnamed and shipped wrong

B26 takes only two values across 6365 captures:

```
B10=0x21 P   B26=0x20   n=5983
B10=0x01 P   B26=0x20   n=9
B10=0x28 D   B26=0x08   n=318
B10=0x22 R   B26=0x08   n=54
B10=0x24 N   B26=0x08   n=3
```

`GEAR_DRIVE` reads 1 in **Reverse (54) and Neutral (3)** — 57 false positives,
0 false negatives. It is not the drive gear; it is "gear is not Park", i.e.
`GEAR_PARK` exactly inverted (`B10:0 == B26:5` is unanimous over 6365).

The old note claimed `r=+0.984 vs DRIVE_MODE_D over 773 captures`. That was an
artifact of R and N being unsampled at the time; the corpus is now 8× larger.
`decode --find-mirrors --bits` already reported the pair as imperfect
(`B10:3 == B26:3 … κ=0.91`) — the evidence was sitting in the tool.

→ renamed `GEAR_PARK_INV` (house precedent: IGPM `CHARGE_PORT_LOCK_INV`) and
disabled, so it documents that bit 3 is accounted for without shipping a
duplicate device signal (precedent: `VCU_INVERTER_ARMED_MIRROR_B23_5`).

### `VEHICLE_STATE_POWER_ENABLE` (B11:6) is an exact mirror of EV_READY

Identical to `VEHICLE_STATE_EV_READY` (B11:3) in 6365/6365 captures. The old
note reasoned about the two "toggling together" but stopped short of the
conclusion. → renamed to name the mirror, disabled.

### Two bit params decoded a hard constant over 6365 captures

| was | read | now |
|---|---|---|
| `VEHICLE_STATE_MAIN_RELAY_ON` = `1-B11:5` | **constant 1** | `UNKNOWN_B11_5` = `B11:5` |
| `VEHICLE_STATE_START_KEY` = `B11:2` | **constant 0** | `UNKNOWN_B11_2` |

`MAIN_RELAY_ON` was the harmful one: it reported "HV relay closed" in `SLEEP`
and while charging, and its note described a `0` state never once observed.
Dropping the `1-` also removes an unproven inversion. Position-honest names
follow the existing `UNKNOWN_B10_6` convention; the semantic hypotheses moved to
`research:`.

### `VEHICLE_STATE_NOT_BRAKING` (B11:1) — note only

B11 bits 0/1 are a **redundant switch pair**, not complements: both read 0 in
4178 rows (almost all `VCU_POWER_STATE=7` charge standby) and both read 1 in 5.
The name is defensible while drive-ready; the note now states that `0` also
means "brake signal not available", so nobody reads it as "braking".

### New: `GEAR_SIGNAL_VALID` (VCU 2101 B10:5)

The unmapped varying bit *inside* the PRND byte — the byte the audit hid.

Set in 6356/6365 captures. Clear in exactly 9 rows, all on 2026-07-30, at four
VCU power-mode transitions:

```
09:52:00  B10=0x01 (bit5 clear)  B11=0x92  B25=1
09:52:28  B10=0x21 (bit5 set)    B11=0x92  B25=1
09:53:56  B10=0x01 (bit5 clear)  B11=0x5A  B25=4
09:54:23  B10=0x21 (bit5 set)    B11=0x5A  B25=4
```

Bit 5 is set in **all** of P/R/N/D, so it is not a gear bit — it is a
validity/plausibility flag for the gear field, dropping while the VCU
re-initialises. Left `unverified`: 4 episodes from a single session, all in Park.

### New: `VCU_POWER_STATE` (B25) value 1 was undeclared

The enum declared only 4/6/7, but value 1 occurs n=21 — all inside the same
2026-07-30 transition window. Added as `unknown_1`: the value is known, the
meaning is not, and inventing "accessory" would be a guess.

### Renamed: the gear signals no longer say "drive mode"

`DRIVE_MODE_*` was a misnomer. In this profile "drive mode" already means
**Eco/Normal/Sport** (see `gsa.yaml`, and the VCU regen research entry), whereas
B10 carries the *transmission range* P/R/N/D. The gear signals were renamed to
say so:

| was | now |
|---|---|
| `DEBUG_DRIVE_MODE_FLAGS` (B10, whole byte) | `GEAR` |
| `DRIVE_MODE_P` / `_R` / `_N` / `_D` (B10:0-3) | `GEAR_P` / `GEAR_R` / `GEAR_N` / `GEAR_D` |

`GEAR_SELECTOR` was *not* available: ESC 22C101 already ships it, and
`validate pids` enforces profile-wide uniqueness of shipped signal names
(`canlib/commands/validate/pids.py::_duplicate_param_errors`).

`GEAR` (B10) and `DEBUG_GEAR_STATE_FLAGS` (B26) also gained `type: enum` maps
over their observed values — the same parallel-decoding pattern
`VCU_POWER_STATE` already uses. The four booleans stay (Home Assistant wants
them); the enum adds one readable label.

**Knock-on:** with B10:0 named `GEAR_P`, the B26:5 param `GEAR_PARK` became a
second shipped signal for the same fact (`B10:0 == B26:5`, unanimous over 6365).
It gets the same mirror treatment as B26:3 — renamed to name the mirror and
disabled. B26 therefore ships nothing: it carries no information B10 does not.

**No enum on BMS 2101 B14** — its bits 5/6/7 are independently meaningful
charge-type flags, so it is a genuine bitfield, not an enum. This is the
enum-vs-bitfield distinction from the skill's Step 4.5, and B14 falls on the
other side of it from B10/B26.

### BMS 2101 B14 bit 1 is a perfect mirror; bit 2 has no evidence at all

**This finding was revised mid-change.** The first pass ran against a corpus that
still contained the hand-transcribed August-2025 captures, withdrawn in
`52f1eb3` because their provenance was untrustworthy (UTF-8-mangled `.bin`
payloads, a copy-pasted session). Those captures supplied the *only* `B14 = 0x45`
reading, and that single row carried the entire "DC charging" side of the byte.

On the cleaned corpus (5997 captures) B14 takes **four** values, not five:

```
0x03 [b0 b1      ] n=1065  ready, not charging
0x20 [      b5   ] n=55    plugged, not charging
0x23 [b0 b1 b5   ] n=12    plugged + ready
0xA3 [b0 b1 b5 b7] n=4865  AC charging
```

Revised conclusions:

- **bit 1 is an exact mirror of `BMS_MAIN_RELAY` (bit 0)** — identical in
  5997/5997 captures, **zero** divergence. The earlier reading ("tracks the main
  relay but diverges during DC charge, so it is the second contactor") rested
  entirely on the withdrawn `0x45` row. With that gone there is no evidence of a
  second, independent contactor; the honest model is a mirrored pair.
  → `BMS_MAIN_RELAY_MIRROR_B14_1`, disabled — the same treatment as the other
  proven mirrors, not a speculative new signal.
- **bit 2 is now constant 0** across the whole corpus. The `UNKNOWN_B14_2`
  candidate is **withdrawn**: its only sample was the corrupt one. Bits 2, 3, 4
  and 6 are all never set.
- **`CHARGING_DC` (B14:6) was already corrected** in the same cleanup — it is now
  `verified: false` with a note recording that the `0x45` evidence was withdrawn
  and that the bit-6 mapping is community lineage, not an observation on this
  car. It still ships (`enabled`) as a constant 0; flagged, not changed here,
  because the note is deliberate and the placeholder is useful.

Two stale cross-references remain from the withdrawn value and are corrected:
`CHARGER_CONNECTED`'s note still contrasts against "not DC (0x45)", and
`DEBUG_BMS_STATE_FLAGS`'s note still lists `0x45 (CCS/DC)` as an observed value.

**Method note worth keeping:** the corrupt row was a single sample that produced
a *plausible, physically-sensible* story (two contactors plus a rapid-charge
relay). The skill's "one episode is not evidence" bar is what stops that from
being written into the profile as a named signal — the discipline held, and the
naming would have been `UNKNOWN_B14_*` even before the data was withdrawn.

**No enum on B14** — bits 5 and 7 are independently meaningful (plug connected
vs charging active), so it is a genuine bitfield, not an enum. This is the
enum-vs-bitfield distinction from the skill's Step 4.5, and B14 falls on the
opposite side of it from B10/B26.

### IGPM 22BC06 turn signals

`LEFT_TURN_SIGNAL` (B10:0) shipped enabled while its own note said it *"falsely
shows 1 when neither is active"*. Data: bit 0 is set in 23 of its 24 set-samples
as part of the brake pattern `0x2D`. → unverified + disabled. `RIGHT_TURN_SIGNAL`
(B10:1) was `verified: true` on **n=1** → unverified. `BRAKE_LIGHT` reads the
whole byte (0/45) under a boolean name → note corrected; deliberately *not*
given a `type: enum`, so its genuine bit-3/bit-5 gap stays visible now that
Part 1 reports it.

## Part 3 — what the corpus cannot settle

Filed as prioritised `research:` leads with the exact capture recipe:

| P | target | recipe | closes |
|---|---|---|---|
| P1 | VCU 2101 + ESC 22C101 | shift P→R→N→D→R→P co-polled, `--keep-changes` | ESC `GEAR_SELECTOR`=3 (Neutral); real R/N samples for B26 |
| P1 | VCU 2101 | power-cycle READY→OFF→ACC1→ACC2→READY, **`--keep-all`** | `GEAR_SIGNAL_VALID` repeats; `VCU_POWER_STATE` value 1 |
| P2 | BMS 2101 | one DC fast-charge session | `CHARGING_DC` (B14:6) + bits 2/3/4 — never set in 5997 captures; corpus has **no** DC session |
| P2 | IGPM 22BC06 | brake with signals off, then each signal alone | untangle B10 b0 from the `0x2D` brake code |
| P3 | VCU 2101 | — | `VCU_STATE_FLAGS_RAW` (B12) is not a bitfield: 136 distinct values, all 8 bits varying |
| P3 | VCU 2101 | — | `B15 == B13 >> 1` in 6351/6365 — both unmapped, one quantity at two resolutions |

Note on the 2026-07-30 session: it was recorded `--keep-unique`, which is why
`GEAR_SIGNAL_VALID` and `VCU_POWER_STATE=1` have only 9 and 21 rows. The repeat
must be `--keep-all` to get the transition timing.

**IGPM 22BC03/BC04/BC05/BC07 and BCM 22B004/22B00E have nothing left to decode
from this corpus** — every remaining unknown bit is either constant in scope or
has n≤2 support, and the corpus is overwhelmingly charging/sleep sessions.
Existing leads already cover them (22BC03 P3, 22BC07 P2, BCM 22B004 P3); this
change cross-references rather than duplicating them.

## Verification

```
canair validate all                  # OK, 365 signals (229 verified)
canair coverage --bitfields          # 7 -> 9 PIDs; the 4 hidden bytes now appear
canair wican autopid stats
uv run pytest -q && uv run ruff check . && uv run ty check
```

Result: `coverage --bitfields` went from 7 reported PIDs to 9. The two target
bytes now read

```
BITS B10 have{0,1,2,3,5} missing{4,6,7} (also read whole)   # VCU 2101, bit5 newly claimed
BITS B14 have{0,1,5,6,7} missing{2,3,4} (also read whole)   # BMS 2101, bit1 newly claimed
```

`tests/test_coverage.py` had **no** `incomplete_bitfields` coverage before this
change; it now covers each of the three fixed defects plus the unchanged
whole-byte-only case.

### Regenerated artifacts

- `tests/fixtures/golden/coverage-bitfields.txt`, `coverage-multi-frame.txt`,
  `captures-diff-multiframe.txt` — the first two also had **pre-existing** drift
  from the August-2025 capture removal (`coverage-multi-frame` was already
  failing before this change).
- `docs/screenshots/coverage.svg` (`shots.yaml` shoots `coverage BMS`, which
  gains the B14 line).
- `docs/reference/cli/coverage.md` via `scripts/gen_cli_reference.py`.
- `profiles/ioniq-2017/out/autopid.json` — **deliberately NOT regenerated here.**
  It does need it (shipped signal names changed: `DRIVE_MODE_*` -> `GEAR_*`, and
  the newly disabled mirrors drop out), but regenerating mixes in two unrelated
  sources: pre-existing drift (BMS 2102/2103/2104 cell-voltage groups added to
  `ecus/` since the last generation, ~120 lines) and whatever uncommitted
  `ecus/` edits happen to be in the tree. Run `canair wican autopid write` as
  its own commit once the tree is otherwise clean, before the next device
  upload.

### Two gaps this exposed

1. **Nothing cross-validated `vehicle_states.yaml` predicates against signal
   names.** Renaming `GEAR_PARK` silently broke the `PARKED` predicate
   (`VCU.GEAR_PARK == 1`); `canair validate all` stayed green and only
   `tests/test_captures.py::TestCmdBackfillStates` caught it.

   **Fixed.** `canair validate states` now resolves each `when:` predicate's
   `ECU.PARAM` references against the `ecus/` registry and errors on one that
   cannot resolve — the only place this is catchable, since Kleene evaluation
   makes a missing signal indistinguishable from a not-polled one. Resolution
   mirrors how the decoded value map is keyed at evaluation time, so each way of
   missing is reported distinctly (unknown ECU, an ECU alias, a lower-case ECU, a
   case-mismatched signal name, a signal defined on another ECU, a signal under a
   `status: ignored` PID, and a signal that doesn't decode to a number). The same
   check runs as a non-blocking warning where a reference can break — `states
   add --when`/`set-predicate`, `pids rename-param`/`rm-param` — and `canair
   states` marks a dead predicate `✗` instead of `●`. New module
   `canlib/state_refs.py`; `states.predicate_references` extracts the references;
   `decoding.decodes_to_number` owns the "only numerics reach a predicate"
   invariant. Regression guard: `tests/test_state_refs.py` asserts every
   predicate in every bundled profile resolves.
2. **No surgical editor reaches a `research:` item's `notes`/`what_to_test` or a
   `scan_log` note.** Correcting the stale VCU research lines and the GSA
   scan_log cross-reference required hand-editing `ecus/`, against the
   edit-via-tool rule. Per the contributing-code skill the fix is to add the
   editor (`pids set-research-notes` / an equivalent), not to normalise
   hand-editing.
