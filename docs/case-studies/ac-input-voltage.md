# Case study: finding the hidden AC input voltage

A worked account of decoding the OBC's **AC mains input voltage** on the bundled
Ioniq 2017 — a signal that had been written off as "not exposed" in the profile's
research notes, and took several wrong turns to find. It's here because the
*mistakes* are as instructive as the result, and because it exposed real gaps in
canair's tooling (see [What would have made this easy](#what-would-have-made-this-easy)).

**Result:** `AC_INPUT_V = [B14:B15]/100` on `OBC 2101` (~222 V while charging,
idles ~8.5 V in READY). It also solved a long-standing mystery byte
(`OBC_UNKNOWN_B15`) — that byte was simply the *low half* of this voltage word.

## The starting point: a confident "it's not there"

The profile's `OBC 2101` research lead carried a **negative result**:

> NEGATIVE RESULT: no ~230 V AC voltage, no clean AC current, no 50 Hz value is
> exposed. […] the fixed status bytes B14=~87-91, B28=16, B29=32, B31=200, B37=118
> stayed PUT — confirming they are NOT current/power signals.

That conclusion was wrong, but it was wrong for *understandable* reasons — and the
first fresh attempt repeated the same mistakes with more rigor, producing an even
*more* confident (and still wrong) negative. The lesson starts here: **a negative
result is only as strong as the hypothesis it tested.** This one had only ever
tested "a stable byte reading ~230".

## Why it was hard: four traps

**Trap 1 — wrong absolute level.** The search looked for a byte sitting at ~230.
The real byte reads ~222 V — *below* the nominal grid voltage. Any range filter
centered on 230 skips it.

**Trap 2 — the measurement point (the IR drop).** The OBC senses voltage at *its
own inlet*, not at your grid connection. Those differ by the voltage drop across
the house wiring + EVSE cable, which is **proportional to charge current**:

```
V_inlet(t) ≈ V_grid(t) − R_cable · I_ac(t)
```

So the byte is a few volts low *and* carries a current-dependent term. Correlating
it against a grid-voltage reference gives a mediocre `r`, and the drop partially
*anti-correlates* it with the charge current — the opposite of what you'd guess.

**Trap 3 — independence from the obvious anchor.** Every convenient charging
signal tracks charge current (DC current, DC power, the pilot). AC voltage is the
*one* charging signal that is **uncorrelated with charge current** — it's driven
by the grid (here, regional solar PV). So `hunt --against OBC_DC_A`,
`correlate`, and `investigate` — all of which rank by relationship to a
co-captured signal — sailed right past it. It has no anchor on the bus.

**Trap 4 — hiding as "known garbage" + "ignored constant".** The value is a 16-bit
word split across two bytes that had each been dismissed *separately*:

- `B14` — called a "fixed status byte" (it's the **integer** byte; it steps
  85→88 = 218→228 V, but over a narrow range it looks nearly constant).
- `B15` — the infamous `OBC_UNKNOWN_B15`, written off as unexplained garbage
  cycling 0–255 (it's the **fractional low byte** of the voltage).

Neither byte is interesting alone. Together, `[B14:B15]/100` is a clean voltage.

## What finally cracked it

Three things, in order:

### 1. A reference that *varies* distinctively

The breakthrough input was a calibrated grid-voltage log that **changed over the
session**: 225 V at charge start → 231 V peak at 11:33 → 222 V at the end. A
*constant* reference is nearly unfindable (everything constant-ish matches
nothing); a reference with a distinctive **shape** is a fingerprint. This reframed
the hunt from "find ~230" to "find the byte with *this profile*".

> **Lesson:** when you supply an external anchor, prefer one that moves in a
> characteristic way over one that's flat. Variation is signal.

### 2. Widening every axis of the search

The first hunts were too narrow. The winning sweep widened all of them:

- **All co-polled ECUs/PIDs**, not just the obvious one. (AC voltage happened to
  be on OBC, but BMS 2101–2105 were co-captured and equally suspect.)
- **Many encodings**, not just `/1` and `/100`: `/10`, `×2`, `×0.5`, and the
  physically-motivated ones — **peak voltage `×√2` (≈325 V)** and **line frequency
  (~50 Hz)** at several scalings.
- **The right lens.** Because of Trap 3, correlation alone couldn't find it. The
  discriminating lens was **state contrast + plausible value**: *which bytes are
  active only while charging AND land in a mains-voltage range at some scaling?*

### 3. The decisive signatures

Once `[B14:B15]/100` surfaced as a ~222 V candidate, four independent checks
turned a guess into a fact:

| Check | Evidence |
| --- | --- |
| **Plausible value** | 218–228 V RMS — mains, not the ~370 V HV rails or 12 V |
| **On/off by state** | ~222 V charging, **idles ~8.5 V in READY** (EVSE disconnected) — exactly how the other OBC senses behave |
| **Integer byte steps** | `B14` marches 85→86→87→88 with voltage — not a stuck status byte |
| **IR-drop consistency** | mean 221.8 V vs calibrated grid 225.5 V ⇒ ~3 V drop at ~15 A ⇒ R≈0.2 Ω — physically sane, and it *explains Trap 2* |

Final confirmation used canair's own evaluator across *all* charging history — not
just the one session — so the finding wasn't overfit to a single drive:

```bash
canair decode OBC 2101 --try "AC_INPUT_V:V=[B14:B15]/100" --state charging --stats
#  → mean 221.8 V, median 222.1 V   (218–228 V active)
canair decode OBC 2101 --try "AC_INPUT_V:V=[B14:B15]/100" --state ready --stats
#  → mean 9.4 V   (idle — AC absent)
```

## What would have made this easy

This took a detour into hand-written Python to parse `captures --diff` text and
correlate bytes against an external series. **None of that should have been
necessary.** Concrete tooling gaps this surfaced, roughly in priority order:

1. **External reference series for `hunt`/`correlate`.** ✅ **Shipped** —
   `hunt`/`correlate` now take `--against-file series.csv` (columns
   `timestamp,value`), joined by nearest timestamp like the cross-ECU join. A
   calibrated grid-voltage export can now be the reference directly. *(This was
   the single biggest gap.)*

2. **Confounder control / partial correlation.** `hunt`/`correlate` rank by raw
   correlation, which the IR-drop term (Trap 2) sabotages. A
   `--control ECU:PID:PARAM` flag that regresses out a nuisance signal (here,
   `OBC_DC_A`) and correlates the *residual* would expose signals that are only
   visible once the dominant driver is removed. (`--gate` restricts to a regime;
   this is the complementary "subtract a regime" operation.)

3. **A "physical-value" hunt.** `hunt --physical` (or an `investigate` column)
   that sweeps common scalings (`/1 /10 /100 ×2 ×√2`) and flags bytes whose value
   lands in a **named physical band** — mains RMS (200–250 V), mains peak
   (300–340 V), line frequency (49–51 Hz), 12 V rail, HV pack (300–450 V). This
   alone would have flagged `[B14:B15]/100 ≈ 222 V` on the first pass, no external
   reference needed.

4. **An "active-but-independent" finder.** The exact fingerprint of AC voltage was
   *"varies within a state, but is uncorrelated with the state's obvious driver."*
   A mode like `discriminate state --independent-of OBC:2101:OBC_DC_A` — rank
   bytes that separate by state yet *don't* track a named reference — would
   directly surface signals that current-anchored tools miss.

5. **Multi-byte candidate detection.** The value hid as *constant-ish hi byte +
   full-range lo byte* on two bytes dismissed separately. `investigate`/`coverage`
   could heuristically flag adjacent `[Bn:Bn+1]` where `Bn` is near-constant and
   `Bn+1` spans 0–255 as a probable scaled word, and suggest testing the pair.

6. **A structured byte-matrix export.** ✅ **Shipped** — `canair decode
   --dump-bytes` emits a `timestamp × byte-offset` matrix (CSV, or `--json`),
   PCI bytes skipped by default, so ad-hoc analysis no longer needs to regex the
   `captures --diff` text.

7. **Minor:** ✅ **Fixed** — `bix --annotate` now errors on a truncated/partial
   payload instead of producing empty output.

## Takeaways

- **Distrust a narrow negative.** "Not exposed" usually means "not exposed *to the
  one hypothesis I tried*." Re-open it with a different lens.
- **Model the sensor's location.** A real sensor reports a *local, load-dependent*
  value (inlet, not grid). Bake the physics (IR drop, PFC boost, thermal mass)
  into the reference you correlate against.
- **Some signals are defined by independence.** If a signal has no anchor on the
  bus, the anchor-based tools can't find it — switch to state-contrast + physical
  range.
- **Values hide across byte boundaries.** A "garbage" byte and an "ignored
  constant" next to it can be one 16-bit number.
- **Confirm across all history, by state**, before believing a decode — and leave
  it `unverified` until the magnitude *and* the tracking are both proven.

## Where it landed

- `profiles/ioniq-2017/ecus/obc.yaml` — `AC_INPUT_V` and `AC_CURRENT_SETPOINT`
  params on `OBC 2101`; the `2101 AC-side` research lead updated (negative
  overturned); a new `2101 AC input current` lead opened (a *measured* AC current
  still needs a charge where the current actually **varies** — a steady charge
  can't separate it from the DC current).

The end-to-end method this case study illustrates is the
[Analyze](../bring-your-own-car/06-analyze.md) workflow and the
`reverse-engineer-signal` agent skill; this page is what that workflow looks like
when the signal *fights back*.
