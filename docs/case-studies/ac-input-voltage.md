# Case study: finding the hidden AC input voltage

A worked account of decoding the OBC's **AC mains input voltage** on the bundled
Ioniq 2017 — a signal that had been written off as "not exposed" in the profile's
research notes, and took several wrong turns to find. It's here because the
*mistakes* are as instructive as the result — see [the command trail](#the-hunt-step-by-step).

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

## The hunt, step by step

The actual command trail — three dead ends before the pivot (boring re-scoping and
scratch-script glue omitted).

**Round 0 — orient.** What's in the charging captures, and what's already decoded?

```bash
canair captures uds --sessions --date 2026-07-26
canair decode OBC 2101 --state charging --stats
```

**Round 1 — "find a constant ~230 V byte" (dead end).** AC current *should* track
the charge current; AC voltage *should* be a charging-only byte near 230:

```bash
canair hunt OBC 2101 --against OBC:2101:OBC_DC_A --state charging   # AC current?
#  → only the self-match; every other strong hit is NEGATIVE (control/duty bytes)
canair decode OBC 2101 --bytes --discriminate state                # AC volts ~230?
#  → no charging-active byte parks near 230
canair captures uds OBC:2102 --diff --state charging               # hidden on 2102?
#  → static (factory calibration)
```

Verdict: reproduced the old "not exposed" negative — more rigorously, still wrong.

**Round 2 — correlate against the grid log (dead end).** The owner supplied a
calibrated grid-voltage log that *varied* over the session (225 → 231 V peak at
11:33 → 222 V) — now there was a distinctive shape to match:

```bash
canair captures uds OBC:2101 --diff --all --date 2026-07-26 --state charging
#  → dumped the byte stream; correlated every byte/word (all scalings) against the
#    3-point grid profile in a scratch script
```

Verdict: nothing reproduced the 11:33 peak; no byte sat in the 222–231 band.

**Round 3 — account for the inlet IR drop (dead end).** The owner noted the OBC
senses voltage at its *inlet*, below the grid by `k·I_charge`. So regress out the
current and sweep the cable resistance: `V_inlet = V_grid − k·I`.

Verdict: best partial correlation ≈ 0.22 — the only "hits" were the current bytes
themselves at an absurd cable resistance.

**Round 4 — widen to every co-polled ECU + physical value bands (the win).** Drop
the correlation lens; ask instead: *which byte, at any sane scaling, lands in a
mains-voltage band AND is active only while charging?* — across everything on the
bus, not just OBC:

```bash
canair captures uds --latest BMS      # what else is co-polled during the charge?
canair captures uds --latest OBC
canair captures uds --latest VCU
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

(B14 stepped 85→88 with the voltage; the mean sat ~3 V below the grid — the IR
drop, now *confirming* Trap 2 rather than hiding the signal.)

**Define & close.**

```bash
canair pids upsert-param OBC 2101 AC_INPUT_V "[B14:B15]/100" --unit V --unverified …
canair pids rm-param OBC 2101 OBC_UNKNOWN_B15      # it was just the low byte
canair validate pids && canair wican autopid write
```

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

Final confirmation ran `--try "[B14:B15]/100"` across *all* charging history (mean
221.8 V, 218–228 V active) and in READY (~9.4 V idle) — so the finding wasn't
overfit to a single drive. (Exact commands in the trail above.)

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

- `profiles/ioniq-2017/ecus/obc.yaml` — `AC_INPUT_V` added to `OBC 2101`,
  `OBC_UNKNOWN_B15` removed (it was the low byte), and the `2101 AC-side` research
  lead flipped from its negative to found.
- **Follow-on work built directly on this:** a later charge with a *stepped* EVSE
  current (10/13/16 A) decoded the **measured AC draw** (`AC_INPUT_A = [B33:B34]/100`)
  and the **pilot/setpoint** (`OBC_PILOT_AMPS`/`_DUTY` from `[B35:B36]`, replacing a
  noisy earlier guess), and surfaced the VCU's **charge time-to-full**
  (`VCU_CHARGE_TIME_REMAINING`). Finding the voltage was the thread that unravelled
  the rest of the AC side.

The end-to-end method this case study illustrates is the
[Analyze](../bring-your-own-car/06-analyze.md) workflow and the
`reverse-engineer-signal` agent skill; this page is what that workflow looks like
when the signal *fights back*.
