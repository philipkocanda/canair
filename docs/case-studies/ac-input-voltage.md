# Case study: finding the hidden AC input voltage

This page describes how we decoded the **AC mains input voltage** of the on-board charger (OBC) on
the bundled Ioniq 2017. The OBC is an electronic control unit (ECU) of the car. AC means
alternating current. The research notes in the profile had written this signal off as "not
exposed". It took several wrong turns to find it. The *mistakes* teach as much as the result. See
[the command trail](#the-hunt-step-by-step).

**Result:** `AC_INPUT_V = [B14:B15]/100` on `OBC 2101`. It reads about 222 V while charging and
idles at about 8.5 V in READY. It also solved a long-standing mystery byte (`OBC_UNKNOWN_B15`).
That byte was the *low half* of this voltage word.

## The starting point: a confident "it's not there"

The research lead for `OBC 2101` in the profile carried a **negative result**:

> NEGATIVE RESULT: no ~230 V AC voltage, no clean AC current, no 50 Hz value is
> exposed. […] the fixed status bytes B14=~87-91, B28=16, B29=32, B31=200, B37=118
> stayed PUT — confirming they are NOT current/power signals.

That conclusion was wrong, for *understandable* reasons. The first fresh attempt repeated the same
mistakes with more rigor. It produced an even *more* confident negative, which was also wrong.
The lesson starts here: **a negative result is only as strong as the hypothesis it tested.** This
one had tested only "a stable byte that reads ~230".

## Why it was hard: four traps

**Trap 1 — wrong absolute level.** The search looked for a byte near 230. The real byte reads
~222 V, *below* the nominal grid voltage. Any range filter centered on 230 skips it.

**Trap 2 — the measurement point (the IR drop).** The OBC senses voltage at *its own inlet*, not
at your grid connection. The two differ by the voltage drop across the house wiring and the
electric vehicle supply equipment (EVSE) cable. That drop is **proportional to the charge
current**:

```
V_inlet(t) ≈ V_grid(t) − R_cable · I_ac(t)
```

So the byte is a few volts low *and* carries a current-dependent term. A correlation against a
grid-voltage reference gives a mediocre `r`. The drop also partly *anti-correlates* the byte with
the charge current. You would guess the opposite.

**Trap 3 — independence from the obvious anchor.** Every convenient charging signal tracks the
charge current (direct current (DC) current, DC power, the pilot). AC voltage is the *one*
charging signal that is **uncorrelated with charge current**. The grid drives it (here, regional
solar photovoltaic (PV) power). So `hunt --against OBC_DC_A`, `correlate` and `investigate` all
missed it. Each of them ranks by the relationship to a co-captured signal. This signal has no
anchor on the bus.

**Trap 4 — hiding as "known garbage" + "ignored constant".** The value is a 16-bit word split
across two bytes. Someone had dismissed each byte *separately*:

- `B14` — called a "fixed status byte". It is the **integer** byte. It steps 85→88 = 218→228 V,
  but over a narrow range it looks nearly constant.
- `B15` — the infamous `OBC_UNKNOWN_B15`, written off as unexplained garbage that cycles 0–255. It
  is the **fractional low byte** of the voltage.

Neither byte is interesting alone. Together, `[B14:B15]/100` is a clean voltage.

## The hunt, step by step

This is the actual command trail. It has three dead ends before the pivot. It omits the boring
re-scoping and the scratch-script glue.

**Round 0 — orient.** What do the charging captures hold, and what is already decoded?

```bash
canair captures uds --sessions --date 2026-07-26
canair decode OBC 2101 --state charging --stats
```

**Round 1 — "find a constant ~230 V byte" (dead end).** AC current *should* track the charge
current. AC voltage *should* be a charging-only byte near 230:

```bash
canair hunt OBC 2101 --against OBC:2101:OBC_DC_A --state charging   # AC current?
#  → only the self-match; every other strong hit is NEGATIVE (control/duty bytes)
canair decode OBC 2101 --bytes --discriminate state                # AC volts ~230?
#  → no charging-active byte parks near 230
canair captures uds OBC:2102 --diff --state charging               # hidden on 2102?
#  → static (factory calibration)
```

Verdict: the round reproduced the old "not exposed" negative. It was more rigorous and still
wrong.

**Round 2 — correlate against the grid log (dead end).** The owner supplied a calibrated
grid-voltage log that *varied* over the session (225 → 231 V peak at 11:33 → 222 V). Now there was
a distinctive shape to match:

```bash
canair captures uds OBC:2101 --diff --all --date 2026-07-26 --state charging
#  → dumped the byte stream; correlated every byte/word (all scalings) against the
#    3-point grid profile in a scratch script
```

Verdict: no byte reproduced the 11:33 peak. No byte sat in the 222–231 V band.

**Round 3 — account for the inlet IR drop (dead end).** The owner noted that the OBC senses
voltage at its *inlet*, below the grid by `k·I_charge`. So we regressed out the current and swept
the cable resistance: `V_inlet = V_grid − k·I`.

Verdict: the best partial correlation was ≈ 0.22. The only "hits" were the current bytes
themselves, at an absurd cable resistance.

**Round 4 — widen to every co-polled ECU + physical value bands (the win).** We dropped the
correlation lens and asked a different question: *which byte, at any sane scaling, lands in a
mains-voltage band AND is active only while charging?* The search covered everything on the bus,
not just the OBC:

```bash
canair captures uds BMS --latest      # what else is co-polled during the charge?
canair captures uds OBC --latest
canair captures uds VCU --latest
#  → swept every byte/word × scaling (/1 /10 /100 ×2 ×√2, ~50 Hz) on BMS 2101–2105,
#    OBC 2101, VCU 2101/2102 for a value in a named physical band
#  → OBC [isotp11:12]/100 ≈ 222 V, sitting between OBC_OUTPUT_V and OBC_DC_A
```

Then a few quick confirmations turned the candidate into a fact:

```bash
canair bix -1 --annotate <charging-payload>        # isotp 11:12 → WiCAN [B14:B15]
canair decode OBC 2101 --try "V=[B14:B15]/100" --state charging --stats  # ~222 V
canair decode OBC 2101 --try "V=[B14:B15]/100" --state ready    --stats  # ~8.5 V idle
```

(B14 stepped 85→88 with the voltage. The mean sat ~3 V below the grid. This was the IR drop, which
now *confirmed* Trap 2 instead of hiding the signal.)

**Define & close.**

```bash
canair pids upsert-param OBC 2101 AC_INPUT_V "[B14:B15]/100" --unit V --unverified …
canair pids rm-param OBC 2101 OBC_UNKNOWN_B15      # it was just the low byte
canair validate pids && canair wican autopid write
```

### The same hunt today — no scratch scripts

Rounds 2–4 above relied on hand-written glue: parsing `captures --diff` text, regressing out the
current and sweeping scalings. These are now first-class flags. The whole detour collapses to
three commands:

```bash
# Round 2 — correlate every byte against the calibrated grid log directly
# (nearest-timestamp join on the captures' absolute clock; no scratch script)
canair correlate --against-file grid_voltage.csv --bytes --state charging

# Round 3 — regress out the charge current (the IR-drop confounder) and rank by
# the PARTIAL correlation, instead of hand-rolling V_inlet = V_grid − k·I
canair hunt OBC 2101 --against-file grid_voltage.csv --control OBC:2101:OBC_DC_A --state charging

# Round 4 — the winning lens needed NO reference at all: flag bytes whose scaled
# value lands in a named physical band, active only while charging
canair hunt OBC 2101 --physical --state charging
#  → [B14:B15]/100 ≈ 222 V  (mains RMS band)

# and the structured byte matrix that replaced the captures --diff regex:
canair decode OBC 2101 --dump-bytes --json --state charging
```

The `--independent-of` finder captures Trap 3 directly. It ranks the bytes that separate by state
yet *do not* track the obvious driver:

```bash
canair investigate OBC 2101 --independent-of OBC:2101:OBC_DC_A --state charging
```

## What finally cracked it

Three things cracked it, in this order:

### 1. A reference that *varies* distinctively

The breakthrough input was a calibrated grid-voltage log that **changed over the session**: 225 V
at charge start → 231 V peak at 11:33 → 222 V at the end. A *constant* reference makes a signal
nearly unfindable, because everything near-constant matches nothing. A reference with a
distinctive **shape** is a fingerprint. This changed the hunt from "find ~230" to "find the byte
with *this profile*".

> **Lesson:** When you supply an external anchor, prefer one that moves in a characteristic way
> over one that is flat. Variation is signal.

### 2. Widening every axis of the search

The first hunts were too narrow. The winning sweep widened every axis:

- **All co-polled ECUs and parameter identifiers (PIDs)**, not just the obvious one. AC voltage
  happened to be on the OBC, but the battery management system (BMS) pages 2101–2105 were
  co-captured and equally suspect.
- **Many encodings**, not just `/1` and `/100`. The sweep added `/10`, `×2` and `×0.5`. It also
  added the physically motivated ones: **peak voltage `×√2` (≈325 V)** and **line frequency
  (~50 Hz)** at several scalings.
- **The right lens.** Because of Trap 3, correlation alone could not find the byte. The lens that
  discriminated was **state contrast + plausible value**: *which bytes are active only while
  charging AND land in a mains-voltage range at some scaling?*

### 3. The decisive signatures

Once `[B14:B15]/100` surfaced as a ~222 V candidate, four independent checks turned a guess into a
fact:

| Check | Evidence |
| --- | --- |
| **Plausible value** | 218–228 V root mean square (RMS). This is mains voltage, not the ~370 V high-voltage (HV) rails or 12 V |
| **On/off by state** | ~222 V charging, **idles ~8.5 V in READY** (EVSE disconnected). This is exactly how the other OBC senses behave |
| **Integer byte steps** | `B14` marches 85→86→87→88 with voltage. It is not a stuck status byte |
| **IR-drop consistency** | mean 221.8 V vs calibrated grid 225.5 V ⇒ ~3 V drop at ~15 A ⇒ R≈0.2 Ω. This is physically sane, and it *explains Trap 2* |

The final confirmation ran `--try "[B14:B15]/100"` across *all* charging history (mean 221.8 V,
218–228 V active) and in READY (~9.4 V idle). So the finding was not overfit to a single drive.
(The exact commands are in the trail above.)

## Takeaways

- **Distrust a narrow negative.** "Not exposed" usually means "not exposed *to the one hypothesis
  I tried*." Re-open it with a different lens.
- **Model the sensor's location.** A real sensor reports a *local, load-dependent* value (inlet,
  not grid). Build the physics (IR drop, power factor correction (PFC) boost, thermal mass) into
  the reference that you correlate against.
- **Some signals are defined by independence.** If a signal has no anchor on the bus, the
  anchor-based tools cannot find it. Switch to state contrast + physical range.
- **Values hide across byte boundaries.** A "garbage" byte and an "ignored constant" next to it
  can be one 16-bit number.
- **Confirm across all history, by state**, before you believe a decode. Leave it `unverified`
  until both the magnitude *and* the tracking are proven.

## Where it landed

- `profiles/hyundai-ioniq-ae-ev/ecus/obc.yaml` — We added `AC_INPUT_V` to `OBC 2101`, removed
  `OBC_UNKNOWN_B15` (it was the low byte), and flipped the `2101 AC-side` research lead from its
  negative to found.
- **Follow-on work built directly on this:** a later charge with a *stepped* EVSE current
  (10/13/16 A) decoded the **measured AC draw** (`AC_INPUT_A = [B33:B34]/100`) and the
  **pilot/setpoint** (`OBC_PILOT_AMPS`/`_DUTY` from `[B35:B36]`, which replaced a noisy earlier
  guess). It also surfaced the **charge time-to-full** of the vehicle control unit (VCU)
  (`VCU_CHARGE_TIME_REMAINING`). Finding the voltage was the thread that unravelled the rest of the
  AC side.

The [Analyze](../bring-your-own-car/06-analyze.md) workflow and the `reverse-engineer-signal`
agent skill describe the end-to-end method that this case study illustrates. This page shows what
that workflow looks like when the signal *fights back*.
