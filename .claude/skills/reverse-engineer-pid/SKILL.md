---
name: reverse-engineer-pid
description: Reverse-engineer / decode a NEW Ioniq PID or DID end to end — discover via scans, capture payloads, analyze bytes (WiCAN Bnn / ISO-TP / PCI boundaries, expression syntax, conversion table), hypothesize and test expressions, write & validate parameter definitions, verify, integrate. Use when adding a new PID or parameter, decoding an unknown/undecoded payload, writing or fixing a WiCAN expression, working out a byte offset, or working the research: backlog. For general device/tool/ECU-status reference use the ioniq-reverse-engineering skill.
---

# Reverse-engineering a new Ioniq PID

This is the end-to-end workflow for taking a PID/DID from "unknown" to a
verified, decoded parameter in the profile's `ecus/`.

**These two skills are complementary — load both.** They split the work, not the
subject matter:

- **`ioniq-reverse-engineering`** (the *parent*/context skill) — the vehicle
  facts, ECU status table, safety rules, device/transport/MQTT details, and the
  full `canair`/`wican-cli` command reference. It is the "what am I working on
  and with what tools" skill.
- **`reverse-engineer-pid`** (this skill) — the *decoding procedure* and
  reference: the discover→capture→analyze→define→verify lifecycle, byte-index /
  expression syntax, PCI boundaries, UDS conventions, and the analysis
  reasoning (signal types, physics/EE, statistics) below.

If you loaded **this** skill for an RE task, **also load `ioniq-reverse-engineering`**
— you will need the ECU status table (which ECU carries which kind of signal),
the safety rules, and the tool reference to do the workflow here. Conversely, the
parent skill points back here whenever you actually decode a PID. Treat "load the
reverse-engineering skill" as "load *both*."

## Safety first (non-negotiable)

- **NEVER** use UDS programming session (`10 02`) or any firmware write/upload.
  This is a real, un-brickable car.
- Be gentle: old, slow ECUs. **One `canair` connection at a time, any transport**
  — canair enforces a `flock` mutex (`/tmp/wican-connection.lock`); a second
  `slcan-tcp` client hangs unserved and a second `wican-ws` WebSocket can lock up
  the WiCAN (power-cycle to recover). No concurrent requests to the same ECU.
- **Never reboot the WiCAN without asking.** Using the WebSocket terminal
  overrides AutoPID; ask before rebooting to restore the MQTT feed.
- Treat `0x22Fxxx` (flash/cal) as read-only. `2E` writes and `2F` IOControl can
  brick or actuate hardware — out of scope for PID decoding.
- Disable device sleep during a session: `wican sleep --disable` (re-enable
  after).

## Working principles

- **Put yourself in the shoes of the ECU's automotive systems engineer.** Before
  guessing at bytes, ask: *if I designed this module, what would it need to
  measure, report, and control?* A BMS engineer thinks in cell voltages, pack
  current, temperatures, SOC, contactor and relay states, isolation resistance;
  an ESC engineer thinks in wheel speeds, yaw rate, lateral/longitudinal accel,
  brake pressure. Signals cluster by the ECU's job, come in physically sensible
  units and ranges, are laid out in orderly blocks (e.g. four wheel speeds in a
  row), and are scaled to fit their field width. Let that domain model generate
  your hypotheses and sanity-check your results — a decode that no real systems
  engineer would design is probably wrong.
- **Be rigorous.** Reverse engineering is evidence, not vibes. Don't accept a
  byte interpretation because it "looks about right" — confirm it with data
  (range, distribution, correlation, physical plausibility across states). State
  your confidence honestly: a hypothesis is a hypothesis until it's validated.
  Prefer "unverified until proven" over an optimistic guess promoted to fact.
- **Write notes to the point.** Notes on ECUs/PIDs/research are technical
  records, not prose. State the facts — byte offset, observed range, per-state
  values, correlation results, the decision and why — and stop. Cut filler,
  hedging, and narration. **Hold off on speculation**: record what the data shows
  and, at most, a one-line best interpretation; leave extended theorizing to the
  reader. These files are read repeatedly and grow forever — every excess word is
  a tax on everyone after you. Terse and factual beats thorough and unwieldy.

## The lifecycle

```
orient → prerequisites → discover → capture → inspect → hypothesize
       → define → decode/validate → verify → integrate
```

Progress is tracked per-ECU in the `research:` block of the profile's `ecus/<ecu>.yaml`
(schema in `canlib/schema/pids_schema.yaml`), graduating:
`pending → captured → (decoded) → verify → done`, at which point a real
`parameters:` entry exists and is marked `verified: true`.

### 1. Orient — pick a target

```bash
canair research --summary                 # backlog counts by status/type/priority
canair research --priority P1             # highest-value open items
canair research --ecu MCU                 # one ECU's backlog
canair coverage --no-capture              # params defined but never captured
canair coverage --unmapped                # captured PIDs with undecoded bytes
```

`canair research` surfaces *planned* work; `canair coverage` surfaces *undecoded
bytes* in PIDs you already capture. The ECU status table (parent skill) shows
which ECUs are worth probing.

### 2. Prerequisites — power state & access

Decide the car power state the PID needs (`vehicle_states`:
`sleep, plugged, acc, acc2, ready, charging` — the same field on PIDs/ECUs and
in `research:` entries) and whether the ECU needs waking /
an extended session. IGPM (0x770) and BCM (0x7A0) wake from CAN activity;
powertrain ECUs (BMS/VCU/MCU) generally need ACC/ignition or charging.

```bash
canair discover                  # which ECUs are answering right now
```

### 3. Discover — which DIDs respond

```bash
# service 21 (live data), service 22 (DID read; often needs session+wake)
canair scan MCU --service 21 --range 01-FF --save
canair scan IGPM --service 22 --range BC00-BCFF --session --wake --save
```

Record a research lead as you go:

```bash
canair pids add-research MCU --type decode --target 2102 \
    --status captured --priority P1 --prereq charging --notes "62 bytes, undecoded"
```

**Always record the scan outcome — a discovered DID must never be lost:**

- **New responding DID → register it immediately** as a `status: draft` placeholder
  PID in `ecus/<ecu>.yaml`, with the raw payload pasted into `notes` (and an empty
  `expression`), then add a `decode` research lead. This is the established project
  convention (e.g. `ESC 22C102`, `EPS 220101/220102`, `CLU 22B001/B003` are all such
  placeholders). `status: draft` means it is tracked, queryable and captured but stays
  out of the generated WiCAN profile until it's actually decoded (set `canair pids
  set-pid-status <ECU> <PID> active` to ship it, or `ignored` for a dead DID). Before
  adding, always check whether the DID is already registered — a re-scan of a known DID
  should just refresh the payload/notes, not create a duplicate.
- **Negative probe (NRC / no response) → close the scan lead** with
  `canair pids set-status <ECU> "<target>" nrc --type scan` so nobody re-probes it.
  Use `nrc` for "probed, ECU said no / silent" and `done` for "scan complete, responders
  found and registered". Powertrain ECUs (BMS/VCU/MCU/LDC) are KWP2000/service-21 and
  reliably NRC every `22 xxxx` (Ioniq-5-sourced) DID — confirm once, mark `nrc`, move on.

### 4. Capture — record real payloads across states

```bash
# Preferred: decoded, session-managed, saved
canair query "query MCU:2102" --save --label "MCU 2102 driving" \
    --state "ready, driving" --notes "hard launches + regen"
# Capture change across time (values that move reveal what a byte means)
canair query "query MCU 2102" --monitor 1 --keep-all --save
```

Capture the SAME PID in DIFFERENT states (park vs drive, cold vs warm, charging
vs ready) — contrast is what lets you separate signal bytes from constants. For
**cross-signal analysis** (step 7), co-poll the target PID *together with* an ECU
carrying a known reference (speed on ESC, RPM on MCU) in one `canair query` /
`--monitor` run — they'll share a drive so `hunt`/`correlate`/cross-ECU `--corr`
can time-align them. Every payload capture is now timestamped automatically, so
any co-polled drive is joinable; only one-shot scans/identity reads stay untimed.
**Never hand-edit `captures/` — and never read the raw `captures/*.yaml` files
directly.** Always inspect captures through `canair captures`/`canair decode`
(next step): reading the YAML by hand gives you undecoded raw payloads and skips
byte-diffing, decoding, and state/date scoping. Saves are journaled to `captures/.journal/` and
reconciled on exit (a killed/disconnected `--monitor` session is recoverable with
`canair captures --recover`); in `--monitor` the `state` is auto-suggested from
decoded values (press `s` to edit metadata live). After saving, run
`canair captures --summary`.

### 5. Inspect — see the bytes

```bash
canair captures --sessions                    # what's in the captures? (TOC: date/state/label/notes/ECUs)
canair captures --sessions --state driving    # index of every drive
canair captures --sessions --json             # machine-readable TOC
canair captures --summary                     # overview: captures per ECU / per date / totals
canair captures --latest MCU                  # most recent payload per PID (optionally per-ECU)
canair captures MCU 2102                      # list captures + decoded
canair captures MCU:2102 --diff               # unique payloads, byte-diff
canair captures MCU:2102 --diff --all         # every payload, not just unique ones
canair captures MCU:2102 --diff --rulers      # add the idx/wican byte-index ruler above the hex
canair captures MCU:2102 --diff --since 2026-07-19   # scope by date
canair captures MCU:2102 --diff --state driving       # scope to one drive/state
canair captures MCU:2102 --step               # interactive step-through (e=note, d=delete)
canair captures --recover                     # reconcile orphaned journals (--discard to drop)
canair bix -1 --annotate 6101FFFF...          # map each byte -> Bnn/ISO-TP/Torque/role
```

The QUERY mini-language is shared with `canair decode`: `MCU 2102` (one PID),
`MCU:2102,2103` (several PIDs), `MCU` (all PIDs for an ECU), `"VCU:2101 BMS:2101"`
(cross-ECU — quote the space), and `BCM:22` (substring PID match — all `22xxxx`
DIDs on an ECU).

Start with `canair captures --sessions` to see what data exists (labels, states,
notes per session — no payloads) and pick a drive/state to analyze; `--json`
gives a machine-readable index.
Byte-diff highlights which bytes moved between states — your candidate signal
bytes; add `--rulers` to overlay the byte-index ruler and `--all` to include
duplicate payloads. Both `captures` and `decode` share the same scoping flags —
`--since`/`--until`/`--date`, `--state SUBSTR`/`--label SUBSTR`, `--first`/
`--last N` — so you can isolate a single drive (`--state driving`) before
diffing/decoding. `canair bix --annotate` tells you each byte's WiCAN index and
flags the PCI bytes you must not read across (see Reference below).

### 6. Hypothesize — form an expression

Cross-reference the Kia Soul/Niro sheets and the Obsidian vault; watch the
PCI-boundary caution for `[Bnn:Bmm]`. See the Byte Index & Expression reference
at the bottom.

Hypothesizing is not just guessing a byte offset — it's reasoning from *domain
knowledge* about what a signal must physically be, then confirming it in the
data. Use every discipline you have.

#### Let the ECU narrow the search space

**What an ECU is tells you what signals to expect.** Consult the ECU status
table in the parent skill and reason about the component's job before you look at
bytes:

- **BMS (battery)** — cell/pack voltages (tight clusters of similar 2-byte
  values), currents (signed, symmetric about zero, charge vs discharge),
  temperatures (slow, per-module), state-of-charge/health (bounded 0–100%),
  contactor/relay states (enum/bit), cell-balancing flags (bitfields).
- **MCU/inverter** — motor RPM (signed, ±, symmetric under regen), torque
  (signed), phase currents (large, load-tracking), DC-link voltage, and *several
  temperatures of different components* (see thermal-mass reasoning below).
- **VCU** — gear/drive-mode (enum), vehicle speed, pedal positions (0–100%),
  ready/charging state machine (enum/bits).
- **OBC/LDC** — AC/DC input & output voltages/currents, charger/converter
  temperatures, charge-state enums.
- **BCM/IGPM (body)** — mostly *discrete* signals: lights, locks, doors,
  switches → **bitfields and enums**, not continuous analog.
- **HVAC/AAF** — temperatures (ambient/evaporator/heatsink), fan/compressor
  states, flap positions.

A byte's plausible identity is constrained by its ECU: a load-tracking current
on the BCM is unlikely; a door-ajar bit on the MCU is unlikely.

#### Reason from physics / electrical engineering / power electronics

The *dynamics* of a signal reveal its nature even before you know its scale:

- **Thermal mass (the most useful physics lever).** Temperatures change *slowly*
  and are *decoupled from instantaneous load*: high lag-1 autocorrelation, tiny
  per-sample step. Crucially, **thermal mass varies by component**, so *how
  slowly* a temperature moves tells you *which* component it measures:
  - A small-die IGBT **junction** temperature can rise/fall within ~1 s (low
    thermal mass) yet still sit near coolant temp at idle — it looks *fast* but
    keeps a temperature-like baseline.
  - A **heatsink / coolant / motor-winding** temperature drifts over minutes
    (large thermal mass) and lags load heavily.
  - Which state a temperature is *hottest* in disambiguates the component: a byte
    hottest while **charging** (motor idle) is inverter/charger/power-stage; one
    that warms only with **driving** is motor/coolant. Use `--stats
    --group-by state` to read these per-state means straight out.
- **Signed vs unsigned & symmetry.** Motor RPM, torque, and battery current are
  **signed** and roughly **symmetric about zero** (regen ≈ –drive). A value that
  never goes negative and tracks |load| is a *magnitude* (current RMS, power),
  not a signed torque.
- **Conservation / relationships you can check.** Power ≈ V·I; DC-link current ≈
  motor power / DC-link voltage; pack current integrates toward SOC change. A
  candidate that violates a physical identity is wrong even if its range "looks
  right." Validate these with `--corr` against an already-known signal.
- **Rate limits.** Real physical quantities can't step arbitrarily fast — a
  "temperature" that jumps 5 °C between two 5 s samples is a load/current metric,
  not a temperature (a real example from this project).

#### Reason from computer science (state machines, counters, logic)

Not every byte is analog. Discrete/logic signals have distinct fingerprints:

- **Enums / state machines** — a small set of distinct integer values (`--stats`
  shows low `distinct`), transitions only between "adjacent" states (park→ready→
  driving), and correlation with a known mode. Map with per-bit or whole-byte
  reads and label each value.
- **Bitfields** — individual bits toggle independently with discrete events
  (a light, a door, a relay). Read bit-by-bit (`Bnn:k`); `canair coverage
  --bitfields` flags bytes only partly decoded.
- **Counters / alive / checksum** — monotonic wrap-around, or high-distinct noise
  with *no* physical correlation to anything (a rolling counter or CRC). Don't
  try to give these a physical unit; mark them as such.
- **Constants / calibration** — never (or rarely) change across all states →
  cal/identity block, not live data. Confirm with `--stats` (distinct = 1–2).

#### Reason from statistics & mathematics

The tooling exposes real statistical levers — use them as evidence, not decoration:

- **Distribution shape** (`--stats`: n / distinct / mean / median / stdev) —
  continuous vs enum vs constant; a bimodal/low-distinct byte is likely discrete.
- **Correlation** (`--corr PARAM`, Pearson r) — the single strongest validation:
  test a candidate against a *known* signal it should relate to (a temp vs
  |torque| ≈ 0; a current vs |torque| ≈ high). Correlate against a *derived*
  reference too (e.g. |Δbyte| vs |Δload|) to separate fast load-trackers from
  slow temps.
- **Cross-ECU correlation** — a signal on one ECU often mirrors a *verified*
  signal on another (speed appears on ESC/EPS/VCU/AAF; RPM on MCU/VCU). Three
  ways to exploit it, all time-aligned by nearest timestamp over a co-polled
  drive:
  - `canair decode <ECU> <PID> --corr ESC:22C101:REAL_SPEED_KMH` — correlate a
    PID's params against a *cross-ECU* reference (`ECU:PID:PARAM` or
    `ECU:PID:EXPR`). Add `--corr-transform delta` to test level-vs-rate.
  - `canair hunt <ECU> <PID> --against ESC:22C101:REAL_SPEED_KMH` — "which byte
    *is* this known signal?": sweeps every byte×interpretation, ranks by |r|,
    and prints the linear fit + a unit guess (`×1.609 mph→km/h`, `raw−40 °C`).
    `--promote NAME` writes the winner into `ecus/` as an enabled-unverified
    candidate. This is the fastest path from "unknown byte" to "candidate param".
  - `canair correlate --state driving` — rank *every* strong cross-signal
    relationship in a drive at once (`--against REF` to focus one, `--bytes` to
    include raw bytes). The "show me everything that moves together" entry point.
- **State discrimination** (`canair decode … --discriminate state`) — ranks
  bytes/params by between-state vs within-state variance (F). Surfaces
  thermal/mode/relay signals that shift by *power state* (charging vs ready vs
  driving) rather than by a driving anchor — how the MCU inverter-temp byte was
  confirmed (charging 22 °C vs driving 90 °C). Complements correlation.
- **Mirror detection** (`canair decode … --find-mirrors [--bits]`) — reports
  byte/bit positions exactly equal across all captures: redundant status mirrors
  and unit-variants (the km/h-vs-MPH speed pair, an ignition bit echoed in two
  places). A fast way to prune "new" bytes that are just copies.
- **Autocorrelation / step size** — lag-1 autocorrelation and mean |Δ| per sample
  separate slow (thermal, integrating) from fast (load, switching) signals.
- **Differencing / transforms** (`--plot` `delta/abs/cumsum/normalize/smooth`) —
  a byte whose *cumulative sum* tracks SOC is a current; a byte whose *delta*
  correlates with load acceleration is a torque/power proxy.
- **Endianness & word width sweeps** (`--plot` `t`/`e`) — try `u8/i8/u16/i16/…`
  ×endianness; the physically-plausible, smooth, correctly-signed interpretation
  is usually the right one. Beware readings that only look smooth because they
  cross a PCI byte (garbage).
- **Linear fit for scale/offset** — once a byte tracks a known engineering value,
  a straight-line fit (value = a·byte + b) gives the scale and offset; sanity
  check the intercept physically (a temperature's cold-park reading ≈ ambient).

#### Expect thematic grouping (but don't rely on it)

Related signals are **often** laid out contiguously — a run of cell voltages, a
cluster of temperatures, a phase-current pair next to the temperatures that track
it. Finding one member of a group is a strong hint the neighbors are related
(e.g. the MCU B18–B21 "thermal cluster"). Use adjacency as a *lead*: if `Bn` is a
temperature, probe `Bn±1` for more temperatures. **But grouping is a heuristic,
not a rule** — manufacturers interleave unrelated bytes, pad with calibration
constants, and reorder across DIDs. Always confirm each byte on its own evidence;
never assume a neighbor's identity.

#### Trust but verify existing PIDs

Cross-referencing the profile's existing PIDs is essential, but **existing
definitions can be wrong — even ones marked `verified: true`.** A "verified"
flag records that *someone* checked it once, possibly against a single short
drive, a cross-vehicle sheet with a byte offset, or a plausible-but-untested
hypothesis. Sources of error to watch for:

- **Offset-by-one / cross-vehicle drift** — Kia Soul/Niro sheets are offset by 1
  byte from the Ioniq; a definition ported from them may read the wrong byte.
- **Misclassified signal** — a byte labeled a temperature that is actually a
  load/current metric (this project has real examples that were later demoted to
  `enabled: false`).
- **Right byte, wrong scale/sign/endianness** — plausible range but subtly wrong
  transform.
- **Stale after re-analysis** — a fuller capture corpus can overturn an earlier
  single-drive conclusion.

So: **use existing PIDs as priors and correlation references, but re-validate**
when they contradict your data or physical reasoning. If you find a mistake in a
`verified` param, that *is* the finding — demote it (`enabled: false` /
`--unverified`), record the corrected reasoning in `notes:`, and open/adjust a
`research:` lead rather than silently trusting it. Trust, but verify.

### 7. Test the expression WITHOUT committing

`canair decode --try` evaluates a candidate against every capture — no YAML edit:

```bash
canair decode MCU 2102 --try "MOTOR_RPM:RPM=[S10:S11]"        # value range across captures
canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100" --stats  # mean/median/stdev/distinct
# Validate by correlation against a known signal (the key RE lever):
canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM
# Or hunt visually: interactive plot, sweep byte interpretations + transforms.
canair decode MCU 2102 --plot                      # sweep interpretations, find the signal
canair decode MCU 2102 --plot --corr MCU_MOTOR_RPM # overlay a known signal + live r
```

**Cross-ECU / cross-PID** — the fastest lever when a *known* signal on another
ECU (speed on ESC, RPM on MCU) should relate to your unknown byte. All three
time-align by nearest timestamp over a co-polled drive (see the "Cross-ECU
correlation" bullet in step 6 for the full detail):

```bash
# "Which byte on this PID IS the known signal?" — sweeps every byte×interp,
# ranks by |r|, prints the linear fit + a physical-unit guess.
canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --state driving
canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --promote AAF_SPEED  # → candidate param
# Correlate this PID's params against a cross-ECU reference (level or rate):
canair decode MCU 2101 --corr MCU:2102:[S10:S11] --corr-transform delta
# Rank EVERY strong relationship in a drive at once:
canair correlate --state driving --against ESC:22C101:REAL_SPEED_KMH
```

**Scope the captures** so a candidate is judged on the relevant drive/state, not
the whole history (shared with `canair captures`): `--since`/`--until`/`--date`,
`--state SUBSTR`/`--label SUBSTR` (case-insensitive; the natural unit of drive
analysis, e.g. `--state driving`), and `--first N`/`--last N`. Combine with
`--stats --group-by state` to contrast a candidate across drive segments, or
`--compact --changes-only` to watch it evolve with stationary runs collapsed:

```bash
canair decode MCU 2102 --try "T=[S12:S13]/100" --state driving --stats  # one drive
canair decode MCU 2102 --stats --group-by state --state driving          # per-segment
canair decode ESC 22C101 --param REAL_SPEED_KMH --state driving --compact --changes-only
```

Iterate until the range is physical, the distribution makes sense (constant?
enum? continuous?), and — where a relationship should exist — the correlation
confirms it. A bad expression shows `ERROR` rather than hiding.

**`--plot` (interactive signal explorer)** is the fastest way to *find* a signal
when you don't yet have a candidate expression. It works even on a not-yet-defined
PID (raw payloads only). Keys:
- `←`/`→` move the byte offset (byte mode) or switch parameter (param mode)
- `t`/`T` cycle the interpretation type (`u8 i8 u16 i16 u24 i24 u32 i32 u64 i64 f16 f32 f64`)
- `e` toggle endianness · `f` cycle post-transform (`raw delta abs cumsum normalize smooth`)
- `+`/`-` zoom the x-axis · `,`/`.` pan · `0` reset x-range
- `i` toggle a modal listing the captures behind the current view (date/time, state, label, notes, file)
- `m` toggle byte↔param source · `o` overlay the `--corr` reference (with live Pearson r) · `q` quit

The caption under the chart shows the visible capture index range **and its
date/time span**, both tracking zoom/pan, so you always know which captures —
and when — the plotted segment came from. Byte mode shows the **equivalent
WiCAN expression** for the current interpretation (e.g. `[S10:S11]`) — copy it
straight into step 8 — and whether that byte is **already mapped** by a defined
parameter (`= mapped: NAME`, or `~ reads Bn: …` for a partial overlap, or
`unmapped`), so you don't re-decode known bytes. It also warns when a multi-byte
read crosses a PCI byte (garbage); endianness and float types with no direct
WiCAN expression are flagged as such. Zoom/pan (`+`/`-`/`,`/`.`) narrows the
x-axis to inspect a segment (e.g. a single launch or regen event); `i` lists the
exact captures in that segment.

### 8. Define — write it to ecus/

Use `canair pids` (surgical, comment-preserving, auto-validated + auto-reverted
on schema failure) rather than hand-editing:

```bash
canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" \
    --unit RPM --min -10500 --max 10500 --unverified \
    --source "Kia Soul VMCU CSV" --notes "signed 16-bit BE at B10:B11 (ISO-TP 0x07:0x08)"
```

If you found the byte with `canair hunt`, skip the manual step: `hunt … --promote
NAME` writes the top hit straight to `ecus/` as an enabled, unverified candidate
(same validated path) with the correlation r/n, linear fit, and unit guess
auto-filled into `notes`.

New params start **`--unverified` and `--enabled`** — this is the default.
Enabled+unverified means the candidate is generated into the WiCAN profile and
streams live, so it's easy to test against reality (that's the whole point of a
candidate). Do **not** add candidates as `--disabled`; only reach for
`--disabled` (or `enabled: false`) when a byte is *proven* bogus/redundant and
you're keeping it solely for the research trail (e.g. a constant/FF-padding byte,
or an exact mirror of an already-mapped param). A plausible-but-unconfirmed
hypothesis belongs enabled so you can watch it move. (Hand-editing `ecus/` is
allowed, but the tool keeps field order/quoting correct and runs
`canair validate pids` for you.)

**Keep `--notes` terse and factual** (see Working principles). State the byte
offset, observed range/per-state values, and the one key piece of evidence
(e.g. correlation) — not a narrative. Record what the data shows; leave
speculation to the reader.

### 9. Verify — confirm against reality

Confirm the decoded values across the full history and against known physical
state, then flip to verified:

```bash
canair decode MCU 2102                      # ranges (default) — sanity across captures
canair decode MCU 2102 --stats              # distribution / enum detection
canair decode MCU 2102 --param MCU_MOTOR_RPM  # isolate the one param you're verifying
canair decode MCU 2102 --unverified         # validation focus: only not-yet-verified params
canair coverage MCU 2102                    # any bytes still unmapped?
canair validate pids                        # schema + PCI-boundary checks

canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" --verified   # promote
canair pids set-status MCU 2102 done --type decode                       # close the lead
```

A parameter is `verified: true` only when validated against real data / known
state (physical correlation, matching a scan tool, or a definitive constant).

### Always mark off a worked lead (do not leave it open)

**Every time you touch a `research:` lead you MUST update its status before moving
on** — a lead you investigated but left `pending`/`captured` will be re-surfaced by
`canair research` and re-worked from scratch, wasting effort (and risking
re-probing the car). Close it to match reality. Valid statuses are
`pending → captured → done` (plus `nrc` for a dead scan); a lead awaiting live
confirmation is a `verify`-**type** item that stays `captured` until confirmed,
then goes `done`:

```bash
canair pids set-status <ECU> "<target>" done      --type decode   # decoded + a param now exists
canair pids set-status <ECU> "<target>" captured  --type verify   # candidate defined, awaiting live check
canair pids set-status <ECU> "<target>" nrc       --type scan     # probed, ECU said no / silent
```

This applies to **every** outcome, not just success:

- **Fully decoded / verified → `done`** (a real `parameters:` entry exists,
  promoted to `verified` where possible; for a `verify`-type lead, `done` once
  confirmed against reality).
- **Decoded a candidate but it still needs a live/physical check → keep it
  `captured`** (as a `decode`- or `verify`-type item) with the enabled+unverified
  param in place, and note exactly what to test.
- **"Nothing to decode here" is also a result → mark it `done`.** If analysis proves
  the unmapped bytes are constants/padding, message counters, checksums, or exact
  mirrors of an already-mapped param, record that finding in the lead's `notes`
  (with the evidence) and set it `done` — the negative result is the deliverable
  (e.g. MCU 2102 B52/B53 = counter/checksum, HVAC 220100 FF-padding tail). Do NOT
  silently drop it.
- **Only part done → keep it open, but add a follow-up.** If you did part of the
  work (e.g. registered candidates but they need a drive to verify), update the
  `notes`/`what_to_test` to reflect exactly what's left so the next pass starts where
  you stopped, rather than re-deriving it.

Rule of thumb: after any analysis or capture session, run `canair research --ecu
<ECU>` and confirm no lead you touched is still showing its old status. Prefer
`canair pids set-status` (surgical, validated) over hand-editing the `research:`
block.

### 10. Integrate

```bash
canair wican autopid write               # regenerate the bundle's out/autopid.json
canair wican autopid diff --wican home   # compare to device (optional)
python3 -m pytest -q             # keep the suite green
```

Then consider an upstream wican-fw PR (see parent skill goals).

## Tool cheat-sheet (this workflow)

| Step | Tool |
|------|------|
| what to work on | `canair research`, `canair coverage` |
| what's captured | `canair captures --sessions` (TOC: date/state/label/notes/ECUs; `--json`) |
| talk to the car | `canair query`/`scan`/`discover` (`--monitor`, `--save`) |
| see captures | `canair captures` (`--diff`/`--step`/`--rulers`/`--all`/`--latest`/`--summary`/`--since`/`--until`/`--state`/`--label`) |
| map bytes | `canair bix --annotate` |
| reason about a signal | step 6 Hypothesize — ECU context, physics/EE (thermal mass), CS (enums/counters), statistics (`--corr`/`--stats`/autocorr) |
| test expressions | `canair decode --try` / `--stats` / `--corr` / `--plot` |
| cross-ECU correlate | `canair decode … --corr ECU:PID:PARAM` (+ `--corr-transform`); `canair correlate [--against REF] [--bytes]` |
| which byte is signal Y | `canair hunt <ECU> <PID> --against ECU:PID:PARAM` (linear fit + unit guess; `--promote NAME`) |
| find state-dependent signals | `canair decode … --discriminate state` |
| find redundant mirrors | `canair decode … --find-mirrors [--bits]` |
| scope a drive | `--state driving` / `--since`/`--until`/`--date` / `--first`/`--last N` (both `captures` + `decode`) |
| per-segment stats | `canair decode … --stats --group-by state` |
| watch evolution | `canair decode … --compact --changes-only` |
| write definitions | `canair pids upsert-param` / `add-research` / `set-status` |
| validate | `canair validate pids`, `canair coverage` |
| ship | `canair wican autopid write` |

---

## Reference: WiCAN byte index notation

WiCAN expressions index into the **raw CAN frame data including PCI bytes**. The
firmware's ELM327 parser (`parse_elm327_response()` in `autopid.c`) runs headers
ON and copies ALL 8 CAN data bytes per frame (including ISO-TP PCI bytes)
sequentially into a flat byte array.

### Byte layout (AutoPID internal format)

For a multi-frame response to `2101` on BMS (0x7E4):

```
Frame 0 (First Frame):  [10 3B] [61 01 FF FF FF FF]  → B00-B07
Frame 1 (Consecutive):  [21]    [d  d  d  d  d  d  d] → B08-B15
Frame 2 (Consecutive):  [22]    [d  d  d  d  d  d  d] → B16-B23
...
```

- `B00` = PCI high byte (0x10), `B01` = PCI low byte (length)
- `B02` = SID response (0x61), `B03` = PID echo (0x01)
- `B08` = PCI consecutive (0x21), `B09` = first actual data byte of frame 1
- PCI bytes occupy indices 0, 8, 16, 24, 32, 40, 48, 56, ...

### Byte indexing examples

For a `0x21` service request (PID `01`), the response starts `61 01 <data...>`:
- `B0` = `0x61` (service response ID)
- `B1` = `0x01` (PID echo)
- `B2` = first data byte

For a `0x22` service request (DID `C00B`), the response starts `62 C0 0B <data...>`:
- `B0` = `0x62` (service response ID)
- `B1` = `0xC0` (DID high byte)
- `B2` = `0x0B` (DID low byte)
- `B3` = first data byte

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` (multi-byte unsigned),
`[Snn:Smm]` (multi-byte signed), `Bnn:k` (bit k, 0=LSB). Operators:
`+ - * / << >> & | ^`. See `expression_parser.c` for the full reference.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes — it does NOT skip PCI
bytes.** If a multi-byte value spans a CAN frame boundary (B07-B08, B15-B16,
etc.), the PCI byte at B08/B16/... is included, producing garbage. Use manual
bit-shifting instead: `(B07 << 8) | B09` to skip the PCI byte at B08. Always use
`canair bix` to check whether a byte range crosses a PCI boundary.

```bash
canair bix w9        # WiCAN B09 → ISO-TP 0x06, Torque E, bix 32
canair bix E         # Torque letter → all notations
canair bix -2 w5     # 2-byte subfunction mode (22xxxx DIDs)
canair bix --table   # Full conversion table
canair bix -2 --annotate 62B0047402990C0040A000AAAA   # annotate a real payload
canair bix --annotate 6101FFFF...                     # service 21 (1-byte PID)
```

`--annotate` (`-a`) reconstructs the WiCAN frame with PCI bytes inserted and
prints each byte's WiCAN Bnn, ISO-TP index, Torque letter, bix, and role. Use
`-1` (default) for service 21, `-2` for service 22 DIDs.

### Conversion table (WiCAN ↔ ISO-TP ↔ Torque ↔ bix)

Each CAN frame has 8 data bytes. PCI bytes (WiCAN indices 0, 8, 16, 24, ...) are
consumed by ISO-TP framing and have no ISO-TP/Torque/bix equivalent. Torque
1/bix 1 are for 1-byte subfunctions (service `21xx`), Torque 2/bix 2 for 2-byte
subfunctions (service `22xxxx`).

| WiCAN | ISO-TP | Torque 1 | bix 1 | Torque 2 | bix 2 |
| ----- | ------ | -------- | ----- | -------- | ----- |
| 0     |        |          |       |          |       |
| 1     |        |          |       |          |       |
| 2     | 0x00   |          |       |          |       |
| 3     | 0x01   |          |       |          |       |
| 4     | 0x02   | A        | 0     |          |       |
| 5     | 0x03   | B        | 8     | A        | 0     |
| 6     | 0x04   | C        | 16    | B        | 8     |
| 7     | 0x05   | D        | 24    | C        | 16    |
| 8     |        |          |       |          |       |
| 9     | 0x06   | E        | 32    | D        | 24    |
| 10    | 0x07   | F        | 40    | E        | 32    |
| 11    | 0x08   | G        | 48    | F        | 40    |
| 12    | 0x09   | H        | 56    | G        | 48    |
| 13    | 0x0A   | I        | 64    | H        | 56    |
| 14    | 0x0B   | J        | 72    | I        | 64    |
| 15    | 0x0C   | K        | 80    | J        | 72    |
| 16    |        |          |       |          |       |
| 17    | 0x0D   | L        | 88    | K        | 80    |
| 18    | 0x0E   | M        | 96    | L        | 88    |
| 19    | 0x0F   | N        | 104   | M        | 96    |
| 20    | 0x10   | O        | 112   | N        | 104   |
| 21    | 0x11   | P        | 120   | O        | 112   |
| 22    | 0x12   | Q        | 128   | P        | 120   |
| 23    | 0x13   | R        | 136   | Q        | 128   |
| 24    |        |          |       |          |       |
| 25    | 0x14   | S        | 144   | R        | 136   |
| 26    | 0x15   | T        | 152   | S        | 144   |
| 27    | 0x16   | U        | 160   | T        | 152   |
| 28    | 0x17   | V        | 168   | U        | 160   |
| 29    | 0x18   | W        | 176   | V        | 168   |
| 30    | 0x19   | X        | 184   | W        | 176   |
| 31    | 0x1A   | Y        | 192   | X        | 184   |
| 32    |        |          |       |          |       |
| 33    | 0x1B   | Z        | 200   | Y        | 192   |
| 34    | 0x1C   | AA       | 208   | Z        | 200   |
| 35    | 0x1D   | AB       | 216   | AA       | 208   |
| 36    | 0x1E   | AC       | 224   | AB       | 216   |
| 37    | 0x1F   | AD       | 232   | AC       | 224   |
| 38    | 0x20   | AE       | 240   | AD       | 232   |
| 39    | 0x21   | AF       | 248   | AE       | 240   |
| 40    |        |          |       |          |       |
| 41    | 0x22   | AG       | 256   | AF       | 248   |
| 42    | 0x23   | AH       | 264   | AG       | 256   |
| 43    | 0x24   | AI       | 272   | AH       | 264   |
| 44    | 0x25   | AJ       | 280   | AI       | 272   |
| 45    | 0x26   | AK       | 288   | AJ       | 280   |
| 46    | 0x27   | AL       | 296   | AK       | 288   |
| 47    | 0x28   | AM       | 304   | AL       | 296   |
| 48    |        |          |       |          |       |
| 49    | 0x29   | AN       | 312   | AM       | 304   |
| 50    | 0x2A   | AO       | 320   | AN       | 312   |
| 51    | 0x2B   | AP       | 328   | AO       | 320   |
| 52    | 0x2C   | AQ       | 336   | AP       | 328   |
| 53    | 0x2D   | AR       | 344   | AQ       | 336   |
| 54    | 0x2E   | AS       | 352   | AR       | 344   |
| 55    | 0x2F   | AT       | 360   | AS       | 352   |
| 56    |        |          |       |          |       |
| 57    | 0x30   | AU       | 368   | AT       | 360   |
| 58    | 0x31   | AV       | 376   | AU       | 368   |
| 59    | 0x32   | AW       | 384   | AV       | 376   |
| 60    | 0x33   | AX       | 392   | AW       | 384   |
| 61    | 0x34   | AY       | 400   | AX       | 392   |
| 62    | 0x35   | AZ       | 408   | AY       | 400   |
| 63    | 0x36   | BA       | 416   | AZ       | 408   |
| 64    |        |          |       |          |       |
| 65    | 0x37   | BB       | 424   | BA       | 416   |
| 66    | 0x38   | BC       | 432   | BB       | 424   |
| 67    | 0x39   | BD       | 440   | BC       | 432   |
| 68    | 0x3A   | BE       | 448   | BD       | 440   |
| 69    | 0x3B   | BF       | 456   | BE       | 448   |
| 70    | 0x3C   | BG       | 464   | BF       | 456   |
| 71    | 0x3D   | BH       | 472   | BG       | 464   |

## Reference: UDS decoding conventions (Hyundai/Kia)

Source: `KB/EV/Hyundai Ioniq/Reverse engineering/Hyundai Kia UDS DID Conventions.md`

### PID categories

- **`0x21xx`** — fast live-data snapshots; no session/security; multiple params
  per response; manufacturer function byte `0x21`.
- **`0x22xx`** — structured; may need extended session (`10 03`); standard UDS
  ReadDataByIdentifier (`22`); some DIDs writable via `2E` — handle with care.

### DID paging vs indexing

- **Paging** (e.g. BMS): `2101`, `2102`, `2103`, `2104` each return a different
  block (the `xx` is a page number, not a DID).
- **Indexing**: `2101`, `2102` are sub-functions/pages within one dataset.

### DID range semantics

- `0x21xx` — live data, manufacturer-specific
- `0x22Bxxx` — cluster/display data
- `0x22Cxxx` — body/comfort (BCM, TPMS)
- `0x22Exxx` — powertrain (BMS, MCU, VCU, HVAC)
- `0x22Fxxx` — often flash/calibration — **do not write**

### Hyundai/Kia DID -1 offset (F1xx identity DIDs)

HK ECUs shift identity DIDs by **-1** from the UDS spec. When reading identity
DIDs, use the HK DID:

| Standard UDS DID | HK DID | Field            |
|------------------|--------|------------------|
| F188             | F187   | ECU Part Number  |
| F18C             | F18B   | Manufacture Date |
| F192             | F191   | Supplier HW No   |

`canair identity` queries both. The ECU answers the HK DID (e.g. `22F187` →
`62F187 <part number>`) while the standard DID may NRC. If a scan finds data
echoing a DID one less than requested, try the -1 DID directly.
