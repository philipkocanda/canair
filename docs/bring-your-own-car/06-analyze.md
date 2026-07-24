# 6. Analyze

You have captures; now work out **which byte is which signal**. This is the core
of reverse-engineering, and it's a *reasoning* process, not a single command:

1. **Inspect** — see which bytes actually move.
2. **Reason** — from what the ECU is, and how the byte behaves, form a hypothesis
   about what it could be.
3. **Test** — check that hypothesis against the data (a known reference, a
   plausible range, correlation) *without* committing anything.
4. **Confirm or reject**, then move to the next byte.

canair's analysis tools support each of those steps, and all work over your saved
captures — no live car needed. Below is the workflow, worked through on a real
example: finding vehicle speed.

## Step 1 — Inspect: which bytes move?

A byte that never changes across your captures can't be a live signal. Start by
diffing captures of the same PID taken in different conditions:

```bash
canair captures MyECU:2101 --diff --state driving   # byte-level diff across a drive
canair captures MyECU:2101 --diff --rulers          # overlay the byte-index ruler
```

The bytes that **change** are your candidates. The ones highlighted as you go
from parked to driving are the ones carrying motion-related information. Map the
raw payload to byte indices so you know exactly what you're looking at:

```bash
canair bix --annotate 6101FFFF… --ecu MyECU --pid 2101
```

> Byte indexing is the classic trap — WiCAN, ISO-TP, and Torque all count bytes
> differently, and there are transport (PCI) bytes you must not read across. See
> [Byte indexing](../concepts/byte-indexing.md).

## Step 2 — Reason: what could this byte be?

Before guessing offsets, ask *what would this ECU need to report?* An ECU's job
constrains what its bytes can plausibly be:

- A **BMS** reports cell voltages, pack current (signed, symmetric about zero),
  temperatures (slow-moving), state-of-charge (0–100%).
- An **MCU/inverter** reports motor RPM and torque (signed, symmetric under
  regen), and several component temperatures.
- A **body module** (BCM/IGPM) reports mostly *discrete* things — lights, locks,
  doors — as **bitfields**, not continuous values.

The byte's *behaviour* narrows it further:

- **Range & distribution** — a value bounded 0–100 is likely a percentage; one
  symmetric about zero is likely signed (torque, current).
- **Speed of change** — temperatures drift slowly; loads/currents jump fast.
- **Distinct values** — a handful of discrete integers is an enum/state machine,
  not an analog reading.

For our example: on a drive, a byte that sits at 0 while parked and climbs
smoothly to ~100 as you accelerate, staying non-negative, looks a lot like
**speed in km/h**.

`canair decode --stats` gives you the distribution to reason from:

```bash
canair decode MyECU 2101 --stats                  # n / distinct / mean / stdev per byte
canair decode MyECU 2101 --stats --group-by state # per-state means (parked vs driving)
```

## Step 3 — Test the hypothesis (without committing)

Now confirm it with evidence. The single strongest lever is **correlation against
a signal you already trust** — often a known signal on *another* ECU captured
during the same drive.

**If you have a reference signal**, let `canair hunt` do the search for you: it
sweeps every byte offset and interpretation on the PID, correlates each against
the reference, and reports the best fit with a physical-unit guess:

```bash
canair hunt MyECU:2101 --against ESC:22C101:REAL_SPEED_KMH
# → B12  r=0.99  y = 1.00·x + 0.3   (looks like km/h)
```

> `ESC:22C101:REAL_SPEED_KMH` here is a *known* speed signal from the bundled
> Ioniq profile, used purely to illustrate. On your car the reference is
> whatever signal *you've* already verified (or an external anchor like
> GPS-logged speed) — the technique is the same.

An `r` near 1.0 on byte 12 with a ~1:1 linear fit is strong evidence that **byte
12 is speed in km/h**. That's our hypothesis, confirmed by data.

**If you're not sure what relates to what**, let `canair correlate` rank *every*
strong cross-signal relationship in the drive, then focus:

```bash
canair correlate --overlap --state driving   # which PIDs share a timeline?
canair correlate --state driving              # rank the strongest pairs
```

**Test an exact expression** against all captures without editing any YAML:

```bash
canair decode MyECU 2101 --try "SPEED_KMH=[B12]" --stats
canair decode MyECU 2101 --try "SPEED_KMH=[B12]" --corr ESC:22C101:REAL_SPEED_KMH
canair decode MyECU 2101 --plot               # interactive: sweep interpretations visually
```

## The one-shot shortcut: `investigate`

`canair investigate` bundles inspect + reason + correlate into a single report:
for every varying byte it tells you whether a parameter already maps it, how well
it separates across vehicle states, and its strongest relationship to a
co-captured signal — with a unit guess. Point it at an unknown PID first:

```bash
canair investigate MyECU 2101 --state driving
canair investigate MyECU 2101 --bits     # rank toggling bits too (body/discrete signals)
canair investigate MyECU 2101 --events   # edge timeline for narrated door/lock/hood captures
```

It's the fastest way to get oriented; the individual tools above are how you
follow up on what it surfaces.

## Be rigorous

A hypothesis is a hypothesis until the data confirms it. Don't accept a byte
because it "looks about right" — check the range, the distribution, correlation
against a known reference, and physical plausibility across states. State your
confidence honestly. This is exactly why the next step starts every new parameter
as **unverified**.

> The full reasoning toolkit — thermal-mass tricks, signed/symmetry tests,
> conservation laws (P ≈ V·I), enum/counter fingerprints, endianness sweeps — is
> documented in depth in the `reverse-engineer-signal` agent skill
> (`.claude/skills/reverse-engineer-signal/`). This page is the human-facing tour of
> the same workflow.

---

You now have a confident hypothesis (*"byte 12 of `MyECU:2101` is speed in
km/h"*). Next: **[7. Define & verify →](07-define-and-verify.md)** turns it into a
stored parameter.
