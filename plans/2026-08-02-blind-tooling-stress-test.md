# Blind rediscovery stress-test of the analysis toolset

**Date:** 2026-08-02
**Profile under test:** `profiles/ioniq-2017`
**Purpose:** Validate that canair's *analysis* tooling (`decode`, `hunt`,
`correlate`, `investigate`'s underlying primitives, `--discriminate`,
`--physical`, `captures --diff`, `bix`) can independently rediscover already-solved
signals from captured data alone — i.e. how well the tools perform "blindfolded".

## Method

- Picked **15 verified signals** (across 12 PIDs) that were *non-trivial* to reverse
  engineer originally — chosen to span every hard signal class the tools claim to
  handle: signed multi-byte, ISO-TP PCI-skip, offset-binary, physical-band-only
  (anchorless voltages), thermal-mass discrimination, unusual scale/byte-order,
  state-machine enums, single event bits, and monotonic counters.
- For each, launched a **blind `general` sub-agent** given *only* the ECU+PID and the
  physical quantity to locate (which follows from the ECU's role — not a leak). Each
  agent was:
  - **forbidden** to read `ecus/*.yaml`, notes, research, docs, skills, plans (the
    answer key), and forbidden to run `investigate` / `coverage` / `ecu … pids` /
    `bix --annotate --ecu/--pid` (which echo stored parameter names);
  - instructed to **ignore any leaked parameter name** a tool happened to print and
    to justify identity purely from statistical/physical evidence;
  - allowed to use **verified signals on *other* ECUs** as correlation references
    (legitimate cross-signal bootstrapping — never the target itself).
- Agents reported a WiCAN expression + byte interpretation + confidence + the concrete
  tool evidence. The main agent held ground truth and graded; the three divergences
  were re-checked directly.

**Blindness caveats (honest):** `hunt`/`correlate` output does *not* leak names, but
`decode`/`captures` can print an existing param name for a byte; agents were told to
disregard it and did. Session **labels** (visible via `captures --sessions`) sometimes
narrate the maneuver (e.g. "clockwise 90°", fan sweep, hood open) — this is legitimate
capture metadata, not a definition, but it did assist confirmation for steering, fan,
and hood. Where a label helped, it is noted below.

## Signals selected

| # | Signal | ECU:PID | Verified expr | Hard part being tested |
|---|--------|---------|---------------|------------------------|
| 1 | Motor RPM | MCU:2102 | `[S10:S11]` | signed 16-bit, cross-signal anchor |
| 2 | Vehicle speed | VCU:2101 | `((S20*256)+B19)*1.609344/100` | MPH-stored, LE byte order, odd scale |
| 3 | Power-mode state | VCU:2101 | `B25` | enum among several state bytes |
| 4 | Steering angle | ESC:22C101 | `([B35:B36]-32768)/10` | offset-binary 16-bit, no cross-ref |
| 5 | Steering angle | EPS:220101 | `0-(S9*256+B10)/10` | signed BE, angle vs rate/torque |
| 6 | HV DC-link voltage | VCU:2102 | `B17*2` | physical-band anchor, ×2 scale |
| 7 | 12V aux voltage | VCU:2102 | `B14*0.0974` | empirical fractional scale |
| 8 | Pack current | BMS:2101 | `((S15<<8)|S17)/10` | **signed + ISO-TP PCI-skip** |
| 9 | AC mains input V | OBC:2101 | `[B14:B15]/100` | present-only-while-charging, mains band |
| 10 | LDC converter temp | OBC:2101 | `B19-100` | thermal mass, non-standard −100 offset |
| 11 | Heat-pump temp | HVAC:220100 | `(B13/2)-40` | pick refrigerant temp among 3+ temps |
| 12 | Ambient temp | AAF:2180 | `(B18-80)/2` | ambient vs decoy temps, slow dynamics |
| 13 | Hood ajar bit | IGPM:22BC03 | `B11:0` | single event bit, once-ever in history |
| 14 | Odometer | CLU:22B002 | `[B12:B14]` | 24-bit BE monotonic counter |
| 15 | Blower fan speed | HVAC:2201A0 | `B18` | fan-level indicator vs motor duty |

## Results

**Score: 13/15 solid hits (11 exact, 2 with a minor transform caveat), 2 partials, 0 misses.**

| # | Signal | Blind result | Grade | Note |
|---|--------|--------------|-------|------|
| 1 | Motor RPM | `B9+S10*256` (signed, high byte B10) | **HIT** | Correct physical ID + signed high byte B10; low-byte LE/BE choice unresolved (r 0.994 vs 0.992 — within join jitter) |
| 2 | Vehicle speed | `(B19+S20*256)/65` | **HIT\*** | Byte pair, LE order, sign all correct (r=0.998); scale ÷65 vs true ÷62.14 (~4.5%), missed the MPH×1.609/100 structure |
| 3 | Power state | ranked `B11` #1, listed `B25` #2 | **PARTIAL** | Surfaced `B25` with correct per-state semantics (4=ready/driving, 7=charging) but ranked it below `B11`; VCU:2101 genuinely has ≥4 state bytes |
| 4 | ESC steering | `([B35:B36]-32768)/10` | **HIT** | Exact; positively ID'd offset-binary center 32768 |
| 5 | EPS steering | `-[S9:S10]/10` | **HIT** | Exact; correctly separated angle from rate (`[S11:S12]`) and torque (`[S12:S13]`) |
| 6 | HV DC-link V | `B17*2` | **HIT** | Exact; `--physical` + charging anchor to BMS pack voltage |
| 7 | 12V aux V | `B18/10` | **PARTIAL** | Right domain (12V), *different byte* than verified `B14*0.0974`; both read plausible 12V (see findings) |
| 8 | Pack current | `((S15<<8)\|B17)/10` | **HIT** | Exact; **nailed the PCI-skip**, proved sign flip charging↔driving, rejected PCI-straddle garbage |
| 9 | AC mains V | `[B14:B15]/100` | **HIT** | Exact; `--physical` mains band + charging-only presence |
| 10 | LDC temp | `B19-100` | **HIT** | Exact; *derived the non-standard −100 offset* from cold-baseline plausibility, rejected 3 decoy temp bytes |
| 11 | Heat-pump temp | `(B13/2)-40` | **HIT** | Exact; distinguished refrigerant (drops below cabin/ambient at compressor-ON) from cabin/ambient/duct temps |
| 12 | Ambient temp | `(B18-80)/2` | **HIT** | Exact; seasonal + slow dynamics + speed-independence isolated it from decoy `B25` |
| 13 | Hood bit | `B11:0` | **HIT** | Exact; only bit set *on top of* an open-door bit, fires once in 310 captures |
| 14 | Odometer | `[B12:B14]` | **HIT** | Exact; strictly non-decreasing over 12 months, plausible km + ~8.4k km/yr |
| 15 | Blower fan | `B18` | **HIT** | Exact raw byte; monotonic through fan sweep, state-invariant vs motor-duty bytes |

\* HIT with a scale/interpretation nuance.

## What the tooling did well

- **`hunt --physical` (named physical bands) is the standout.** For absolute voltages
  (HV pack, 12V rail, AC mains) it resolved the signal *with no reference* where
  Pearson correlation was actively misleading. Three independent agents reported the
  same thing: `hunt --against`/`correlate --bytes` gave spurious rankings for absolute
  levels because **cross-session DC offsets dominate**; the physical-band scan +
  per-state absolute comparison to a known reading is what actually worked.
- **`--discriminate state --bytes/--bits`** was decisive for state machines, the
  current sign-flip, and thermal signals that shift by power state.
- **`hunt --against` + linear fit + unit guess** cleanly rediscovered RPM and speed
  from a cross-ECU speed reference, including proving signedness (unsigned collapses r
  to ~0).
- **PCI-skip trap handled correctly.** The BMS current agent used `bix --table` to
  locate the CF PCI byte at B16 and built `(S15<<8)|B17`, explicitly showing the
  adjacent read is garbage — the hardest byte-layout case, solved blind.
- **Thermal-mass / dynamics reasoning** (via `--compact --changes-only` and `align`
  within a clean window) separated same-range temperatures by *behaviour* (refrigerant
  vs cabin vs ambient vs duct; LDC converter vs inlet).
- **Event/counter fingerprints** (once-ever bit; strictly-monotonic 24-bit) were
  unambiguous.

## What was misleading or missing (tooling findings)

1. **Correlation ranking is unreliable for absolute levels.** `hunt --against` /
   `correlate --bytes` rank by Pearson r, which is corrupted by cross-session offsets
   and flat-reference windows for voltages/temps. Every voltage agent had to fall back
   to `--physical` + per-state anchoring. *Recommendation:* when the reference/target
   is a slowly-varying absolute level, the tools could default toward
   band/anchor-based ranking, or at least warn (as `correlate` already does for a
   bimodal reference — see next).
2. **Bimodal-reference warning works, but ranking still misleads.** `correlate
   --against HVAC:220102:B17` (a 0/1 compressor flag) ranked a *duct* temp above the
   true refrigerant temp; the tool *did* warn the reference is bimodal, and the agent
   correctly overrode it with an `align` time-series. Good warning; the ranking itself
   is still a foot-gun for enum references — `--method cramers_v`/`mutual_info` should
   be nudged harder here.
3. **`hunt` can rank an interpretation it cannot express (`<no-expr>`).** For MCU RPM
   the top-ranked `i16 LE` printed `<no-expr>` even though it is expressible as
   `B9+S10*256`. `hunt`'s expression synthesizer should emit the explicit shift form
   for LE / PCI-skip interpretations instead of `<no-expr>`, so the winning candidate
   is directly promotable.
4. **`hunt`'s physical-unit guesser emits spurious labels.** It tagged the RPM
   slope ≈64 as "raw×0.02 (cell V)". Two agents flagged this. It's cosmetic but can
   mislead — the guess should be gated on the target ECU's plausible quantity set.
5. **Sign vs `--dump-bytes` column confusion.** The speed agent saw `S20` correlate
   r=0.996 while the `--dump-bytes` `B20` column gave r=0.06 and suspected an index
   mismatch. It is **not** an index bug — `B20` (unsigned) vs `S20` (signed) differ
   because the high byte is `0xFF` near standstill. Still, this tripped up a careful
   analyst; `--dump-bytes` could optionally emit signed columns or a note.
6. **Exact scale/offset often can't be nailed blind.** Speed (÷65 vs ÷62.14), LDC
   temp offset (−100 inferred), current (÷10 by convention), ambient (0.5/−40
   inferred) — the *byte and structure* were found, but the final calibration
   constant repeatedly rested on physical plausibility, not a bus-internal anchor.
   This is inherent (no ground-truth meter on the bus), not a tool defect — but it
   caps blind confidence at ~85–90% for calibrated analog signals.

## Potential profile issues surfaced (worth follow-up — not yet changed)

- **MCU:2102 motor RPM low byte (LE vs BE).** Blind fit marginally prefers
  `B9+S10*256` (LE, r=0.994, resid 72) over verified `[S10:S11]` (BE, r=0.992, resid
  89). Both share high byte B10; they disagree only on the low byte (B9 vs B11). B9 and
  B11 are *both* full-range rolling bytes (distinct 187 / 166), so the data can't break
  the tie — the difference is within nearest-join jitter. **Not an error**, but the
  verified BE choice of B11 as the low byte deserves one confirming high-resolution
  drive (or a reverse-gear check) since B11 may be an independent rotor-angle byte.
- **VCU:2102 has ≥2 plausible 12V-range bytes.** Verified `VCU_AUX_BATTERY_VOLTAGE =
  B14*0.0974` (median 14.51V) and the blind pick `B18/10` (median 13.40V) are *both*
  clean 12V readings — likely two different nodes (LDC output vs battery terminal). Only
  `B14` is defined; `B18` looks like a real, cleaner-quantized (0.1V) second 12V signal
  worth adding/labelling. The empirical `0.0974` scale on B14 vs the clean `/10` on B18
  is itself a hint one of them is the direct battery-terminal reading.
- **VCU:2101 power-state is distributed across bytes.** No single byte enumerates
  charging/READY/driving/off; the verified `VCU_POWER_STATE=B25` is the best single
  choice but needs `B26`/`B23` to fully reconstruct the 4-way state — the current
  `--discriminate` output makes this clear and could be cited in the notes.

## Conclusion

The analysis toolset is **strong at blind rediscovery**: 13/15 non-trivial verified
signals were re-identified to the correct byte and interpretation with no access to the
answer, including the two hardest layout cases (ISO-TP PCI-skip current, offset-binary
steering) and several calibration derivations (LDC −100 offset, ambient 0.5/−40). The
two non-exact outcomes were a genuine multi-byte-state ambiguity and a plausible
*alternate* 12V node — both arguably tool *successes* (they surfaced real structure the
profile under-documents) rather than failures.

The main systematic weakness is **correlation ranking on absolute levels**: for
slowly-varying voltages/temps, `hunt --against`/`correlate` mislead, and analysts must
reach for `--physical` + per-state anchoring. The tools already partially compensate
(physical bands, bimodal warnings); the recommendations above (band-first ranking for
level signals, `hunt` emitting shift-form expressions, gated unit guesses) would close
most of the remaining gap.

### Suggested follow-ups

1. `hunt`: synthesize explicit shift-form expressions for LE / PCI-skip winners
   (kill `<no-expr>`).
2. `hunt`/`correlate`: prefer band/anchor ranking (or warn) when target/reference is a
   slowly-varying absolute level; strengthen the enum-reference nudge toward
   `cramers_v`/`mutual_info`.
3. `hunt`: gate the physical-unit guess to the ECU's plausible quantities.
4. Re-validate MCU:2102 RPM low byte; investigate/label VCU:2102 `B18` as a second 12V
   node; add a note to `VCU_POWER_STATE` about the distributed state bytes.
