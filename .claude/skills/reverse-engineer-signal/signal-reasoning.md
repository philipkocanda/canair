# Reference: reasoning about what a byte is

Sibling reference for the `reverse-engineer-signal` skill: the **hypothesize** step
(step 6) in full. Load this when you have captures of an unknown PID or frame and need
to work out what its bytes *mean*. The skill body carries the surrounding workflow
(orient → discover → capture → inspect → define → verify), the command cheat-sheet, and
the byte-index/expression reference.

Hypothesizing is not guessing a byte offset — it is reasoning from *domain knowledge*
about what a signal must physically be, then confirming it in the data. Everything here
is domain-agnostic: it applies to a diagnostic PID byte (WiCAN `Bnn`) and a broadcast
frame byte (`0xID:rN`) alike. Flags live in each command's `--help`;
`docs/concepts/analysis-commands.md` is the "which command when" map (and covers the
ranking traps: bimodal references, monotonic scopes, fill semantics).

## Let the ECU narrow the search space

**What an ECU is tells you what signals to expect** (EV modules below; "reason from the
role" applies to any powertrain):

- **BMS** — cell/pack voltages (tight clusters of 2-byte values), currents (signed,
  symmetric about zero: charge vs discharge), temperatures (slow, per-module), SOC/SOH
  (0–100 %), contactor/relay states (enum/bit), balancing flags.
- **MCU/inverter** — RPM and torque (signed, symmetric under regen), phase currents
  (large, load-tracking), DC-link voltage, *several* temperatures of different
  components (see thermal mass below).
- **VCU** — gear/drive-mode (enum), speed, pedal positions, ready/charging state
  machine. **OBC/LDC** — AC/DC input and output voltages/currents, converter
  temperatures, charge-state enums. **HVAC/AAF** — ambient/evaporator/heatsink
  temperatures, fan/compressor state, flap positions.
- **BCM/IGPM (body)** — mostly *discrete*: lights, locks, doors, switches ⇒ **bitfields
  and enums**, not analog. Decode with the bit-level, event-driven loop: capture a
  *narrated* event sequence (`monitor --save`, noting each physical action), then
  `investigate <ECU> <PID> --events --bits` for the edge timeline aligned to your notes,
  and `correlate --find-mirrors --bits` for the same bit on a second ECU (an IGPM door
  bit mirrored in BCM). This decoded DOOR_DRV_OPEN / HOOD_OPEN / the BC05 unlock+trunk
  bits. The **decode-bitfields** skill has the full loop (incl. `--dwell` and the
  partially-decoded-byte audit).

A byte's plausible identity is constrained by its ECU: a load-tracking current on the
BCM is unlikely; a door-ajar bit on the MCU is unlikely.

## Typed (multi-modal) signals

A byte that is a *mode/flag/schedule/date*, not a number on a line, gets a param
**`type:`** (`enum`/`bitmask`/`ascii`/`date`/`bcd`/`struct`) plus a `values:`/`bits:` map
(`pids upsert-param --type … --value RAW=LABEL --bit INDEX=LABEL`); the WiCAN
`expression` stays the pure float device value and the type is a parallel decoding.

Analyse them **categorically**, not with Pearson: `decode --discriminate state`
(Cramér's V), `correlate --method cramers_v` ("which byte is this mode?"),
`investigate --events --field NAME` (one transition per decoded-value change, e.g.
`fanMAX (45) → fan1 (40)`). For a setting the *head unit writes* (schedule/clock), use
toggle → re-read → `captures uds --diff` on the storing DID. See
`docs/concepts/typed-signals.md`.

## Reason from physics / electrical engineering / power electronics

A signal's *dynamics* reveal its nature before you know its scale:

- **Thermal mass (the most useful lever).** Temperatures move *slowly* and are decoupled
  from instantaneous load: high lag-1 autocorrelation, tiny per-sample step. And *how*
  slowly says *which* component — a small-die IGBT **junction** temp can swing within
  ~1 s yet keeps a coolant-ish baseline at idle, while a **heatsink/coolant/winding**
  temp drifts over minutes and lags load heavily. Which state it is *hottest* in
  disambiguates: hottest while **charging** (motor idle) ⇒ inverter/charger/power stage;
  warms only when **driving** ⇒ motor/coolant. Read per-state means with
  `--stats --group-by state`.
- **Signed vs unsigned & symmetry.** RPM, torque and pack current are signed and roughly
  symmetric about zero (regen ≈ −drive). A value that never goes negative but tracks
  |load| is a *magnitude* (RMS current, power), not signed torque.
- **Conservation you can check.** Power ≈ V·I; DC-link current ≈ motor power / DC-link
  voltage; pack current integrates toward SOC change. A candidate violating a physical
  identity is wrong even if its range looks right — check with `--corr`.
- **Rate limits.** A "temperature" that jumps 5 °C between two 5 s samples is a
  load/current metric (a real case from this project).

## Reason from computer science (state machines, counters, logic)

- **Enums / state machines** — few distinct integers (`--stats` shows low `distinct`),
  transitions only between adjacent states (park→ready→driving), correlation with a
  known mode. Label each value.
- **Bitfields** — bits toggle independently with discrete events; read bit-by-bit
  (`Bnn:k`). `coverage --bitfields` flags partly-decoded bytes; see **decode-bitfields**.
- **Counters** — a *fast* rolling counter or CRC is high-distinct noise with no physical
  correlation: don't give it a unit, mark it as such. A **slow accumulator** (odometer,
  operating hours, power-cycle count, cumulative Ah/Wh) is the opposite problem: it
  barely moves inside one session, so it reads as a *constant* byte to
  `--corr`/`--discriminate`/`hunt`/`investigate` and to triage's `counter` class
  (direction-blind, single-byte). Only its behaviour across the **whole history**
  identifies it — it never decreases. `investigate [<ECU> [<PID>]] --counters` sweeps
  1–4-byte windows × endianness and sorts hits into `accumulator`/`cycle`/`timer`, scored
  in **bits** (one clean rise with no fall = 1 bit: read 3 bits as a lead, 300 as a
  fact). Run it **unscoped** — the calendar horizon *is* the evidence, and a scope filter
  understates the score — and with no positionals to sweep the whole car, the natural
  discovery form since a counter is exactly what you have no decoded PID for.
- **Constants / calibration** — never change across states ⇒ cal/identity block, not live
  data (`--stats` distinct = 1–2).

## Reason from statistics & mathematics

The levers that count as *evidence*:

- **Distribution shape** (`--stats`: n/distinct/mean/median/stdev) — continuous vs enum
  vs constant.
- **Correlation** (`--corr`, Pearson) — the strongest single validation: test the
  candidate against a *known* signal it should relate to (a temp vs |torque| ≈ 0; a
  current vs |torque| high), and against a *derived* one (|Δbyte| vs |Δload|, which
  separates fast load-trackers from slow temps — on `decode --corr` that transform is
  `--corr-transform delta`). `--method spearman` catches monotone-but-nonlinear/quantized
  links; `cramers_v`/`mutual_info` are for enum/mode references.
- **Cross-ECU correlation is the fastest lever** — a signal on one ECU often mirrors a
  *verified* one on another (speed on ESC/EPS/VCU/AAF, RPM on MCU/VCU). Order of attack
  over a co-polled drive: `correlate --overlap` (which ECU:PID pairs actually share
  time-aligned samples — **first**, don't guess a reference) → `investigate <ECU> <PID>`
  (one-shot per-byte verdict: mapped?, state-separation F, best anchor + fit + unit
  guess, triage class, physical band; omit positionals to sweep an ECU or the profile) →
  `hunt --against ECU:PID:PARAM` ("which byte *is* this known signal?" — every byte ×
  interpretation ranked by |r|, with the linear fit and a unit guess like `raw−40 °C`;
  `--promote NAME` writes the winner into `ecus/`) → `correlate --state DRIVING` (rank
  *every* strong relationship at once; `--gate '> 0'` restricts to a regime such as
  while-moving, `--lag-scan N` reports the apparent lead/lag that maximises |r|). With no
  on-bus anchor: `hunt --physical` (named physical bands, no reference needed),
  `--against-file` (an external meter/GPS log on the captures' absolute clock),
  `--control` / `investigate --independent-of` (regress out a dominant driver so a signal
  hidden behind it appears). Widen `--join-tol` when a round-robin poll spreads the two
  signals further apart than the default join window.
- **Mirrors are the jackpot** (`decode --find-mirrors` intra-PID,
  `correlate --find-mirrors` cross-ECU, `--bits` for bit level) — a mirror of an
  already-**verified** signal decodes an unmapped byte at near-certainty with no new
  capture and no physical reasoning, so sweep for mirrors *before* correlation or physics
  when a PID is co-polled with well-mapped ECUs. Real mirrors are frequently not exact:
  pass **`--allow-offset`** (same quantity at a different zero/scale —
  `OBC:2101:B19 == AAF_LDC_TEMP + 100`, a raw 12 V byte at `× 12.8`) and leave
  **`--mirror-match`** at its 0.9 default, since round-robin polling reads a drifting
  signal on two ECUs seconds apart and they disagree by ±1 on a minority of rows
  (demanding every row alone hides most real mirrors). Beware a small `--min-n`: noisy
  low bits can match by chance. It cuts both ways — a "new" byte that merely mirrors a
  mapped one is recorded and left disabled, not shipped twice.
- **State discrimination** (`decode --discriminate state`, `--bytes`/`--bits`) — ranks by
  between-state vs within-state variance (F), surfacing thermal/mode/relay signals that
  shift by *power state* rather than by a driving anchor (how the MCU inverter-temp byte
  was confirmed: charging 22 °C vs driving 90 °C). `--discriminate ECU:PID:PARAM` groups
  by any cross-signal axis instead.
- **Sweeps, transforms and fits** — try `u8/i8/u16/i16/…` × endianness (the physically
  plausible, smooth, correctly-signed reading is usually right; beware one that only
  looks smooth because it crosses a PCI byte). Post-transforms
  (`delta/abs/cumsum/normalize/smooth`) identify a byte by *behaviour*: one whose cumsum
  tracks SOC is a current, one whose delta tracks acceleration is a torque/power proxy.
  Once a byte tracks a known engineering value, a linear fit (`value = a·byte + b`) gives
  scale and offset — sanity-check the intercept physically (a cold-park temperature ≈
  ambient).
- **Eyeball the raw series side by side** (`canair align A B C …`) — before trusting an r
  or a `--stats` min/max, print the candidate *next to* the reference and any mode/state
  column as one time-aligned table. **A single r or min/max flattens the *dynamics***: a
  signal with a non-zero **baseline** or **mode-dependent** behaviour can post a strong r
  (or a tidy range) yet mean something quite different up close. Real case: HVAC 2201A2
  B41 read as a clean "cooling power, 0 in heating" from `--stats` (min = 0) and was
  written as `HVAC_COOL_POWER`; an `align` beside `HVAC_HEAT_POWER`/`BATTERY_POWER`
  showed a **~20 idle baseline in *both* modes** (only cooling elevates it, heating never
  zeroes it) — correcting the mislabel to `HVAC_COOL_LOAD_B41` before it misled anyone.
  Make it routine on any candidate with an offset or a per-mode story.

## Expect thematic grouping (but don't rely on it)

Related signals are **often** contiguous — a run of cell voltages, a cluster of
temperatures, a phase-current pair beside the temperatures that track it. Finding one
member hints at its neighbours (the MCU B18–B21 thermal cluster): if `Bn` is a
temperature, probe `Bn±1`. **But it is a heuristic** — manufacturers interleave unrelated
bytes, pad with calibration constants, and reorder across DIDs. Confirm each byte on its
own evidence.

## Trust but verify existing PIDs

Existing definitions can be wrong — **even ones marked `verified: true`** (the flag
records that *someone* checked once, possibly against one short drive or a cross-vehicle
sheet). Watch for **offset-by-one / cross-vehicle drift** (Kia Soul/Niro sheets are 1
byte off from the Ioniq), a **misclassified signal** (a "temperature" that is really a
load/current metric — real examples here, later demoted to `enabled: false`), the **right
byte with wrong scale/sign/endianness**, and a definition **stale after re-analysis** (a
fuller corpus overturns a single-drive conclusion).

Use existing PIDs as priors and correlation references, but **re-validate** when they
contradict your data or physical reasoning. Finding a mistake in a `verified` param *is*
the finding: demote it (`--unverified` / `enabled: false`), record the corrected
reasoning in `notes:`, and open or adjust a `research:` lead rather than silently trusting
it.

---

Next: test the candidate expression without committing (`decode --try`/`--plot`), then
define it with `canair pids upsert-param` — steps 7–8 in the skill body. Remember the
PCI-boundary rule before you author a multi-byte range: check it with `canair bix`.
