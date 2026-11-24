# Reference: reasoning about what a byte is

Sibling reference for the `reverse-engineer-signal` skill: the **hypothesize** step in
full. Load this when you have captures of an unknown PID or frame and need to work out what
its bytes *mean*. `SKILL.md` routes to the other steps (orient → discover → capture →
inspect → define → verify) and the byte-index/expression reference.

Hypothesizing is not guessing a byte offset — it is reasoning from *domain knowledge*
about what a signal must physically be, then confirming it in the data. Everything here
is domain-agnostic: it applies to a diagnostic PID byte (WiCAN `Bnn`) and a broadcast
frame byte (`0xID:rN`) alike. Flags live in each command's `--help`;
`docs/concepts/analysis-commands.md` is the "which command when" map.
`docs/concepts/analysis-techniques.md` covers counters, support maps, forward-fill
semantics and mirrors, including the ranking traps (bimodal references, monotonic scopes).

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
  and enums**, not analog. Use the **decode-bitfields** skill's event-driven loop.

A byte's plausible identity is constrained by its ECU: a load-tracking current on the
BCM is unlikely; a door-ajar bit on the MCU is unlikely.

## Typed (multi-modal) signals

A byte that is a *mode/flag/schedule/date*, not a number on a line, gets a signal
**`type:`** (`enum`/`bitmask`/`ascii`/`date`/`bcd`/`struct`) plus a `values:`/`bits:` map
(`pids upsert-param --type … --value RAW=LABEL --bit INDEX=LABEL`); the WiCAN
`expression` stays the pure float device value and the type is a parallel decoding.

Analyse them **categorically**, not with Pearson: `decode --discriminate state`
(Cramér's V), `correlate --method cramers_v` ("which byte is this mode?"),
`investigate --events --field NAME` (one transition per decoded-value change, e.g.
`park (33) → drive (40)`). For a setting the *head unit writes* (schedule/clock), use
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
  load/current metric.

## Reason from computer science (state machines, counters, logic)

- **Enums / state machines** — few distinct integers (`--stats` shows low `distinct`),
  transitions only between adjacent states (park→ready→driving), correlation with a
  known mode. Label each value.
- **Bitfields** — bits toggle independently with discrete events; read bit-by-bit
  (`Bnn:k`). `coverage --bitfields` flags partly-decoded bytes; see **decode-bitfields**.
- **Counters** — a *fast* rolling counter or CRC is high-distinct noise with no physical
  correlation: don't give it a unit, mark it as such. A **slow accumulator** (odometer,
  operating hours, power-cycle count, cumulative Ah/Wh) reads as a *constant* byte inside one
  session, so correlation tools miss it. Find it with `investigate [<ECU> [<PID>]] --counters`,
  **unscoped** (the whole history is the evidence). Read 3 bits as a lead, 300 as a fact. See
  `docs/concepts/analysis-techniques.md`.
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
  *verified* one on another (speed on ESC/EPS/VCU/AAF, RPM on MCU/VCU). Over a co-polled
  drive, run `correlate --overlap` **first** (which ECU:PID pairs share time-aligned
  samples). Do not guess a reference. Then `investigate <ECU> <PID>`, then
  `hunt --against ECU:PID:PARAM` (`--promote NAME` writes the winner into `ecus/`), then
  `correlate --state DRIVING`. With no on-bus anchor, use `hunt --physical`, `--against-file`,
  or `--control` / `investigate --independent-of` to regress out a dominant driver. Widen
  `--join-tol` when a round-robin poll spreads two signals further apart than the join window.
  Order and flags: `docs/concepts/analysis-commands.md`.
- **Mirrors are the jackpot** (`decode --find-mirrors`, `correlate --find-mirrors`, `--bits`
  for bits) — a mirror of an already-**verified** signal decodes an unmapped byte at
  near-certainty with no new capture and no physical reasoning. Sweep for mirrors *before*
  correlation or physics when a PID is co-polled with well-mapped ECUs. Pass
  **`--allow-offset`** (real mirrors often differ in zero or scale) and leave
  **`--mirror-match`** at 0.9 (`docs/concepts/analysis-techniques.md`). Beware a small
  `--min-n`: noisy low bits match by chance. A "new" byte that merely mirrors a mapped one
  is recorded with `ship: false`, not shipped twice.
- **State discrimination** (`decode --discriminate state`, `--bytes`/`--bits`) — ranks by
  between-state vs within-state variance (F), surfacing thermal/mode/relay signals that
  shift by *power state* rather than by a driving anchor (an inverter temperature: charging
  22 °C vs driving 90 °C). `--discriminate ECU:PID:PARAM` groups
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
  (or a tidy range) yet mean something quite different up close. A byte that reads "0 in
  heating" from `--stats` may sit at a ~20 idle baseline in *both* modes, so it is a load
  metric, not a mode-gated power. Make `align` routine on any candidate with an offset or a
  per-mode story.

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
load/current metric), the **right
byte with wrong scale/sign/endianness**, and a definition **stale after re-analysis** (a
fuller corpus overturns a single-drive conclusion).

Use existing PIDs as priors and correlation references, but **re-validate** when they
contradict your data or physical reasoning. Finding a mistake in a `verified` signal *is*
the finding: demote it (`--unverified` / `ship: false`), record the corrected
reasoning in `notes:`, and open or adjust a `research:` lead rather than silently trusting
it.

### Right in one regime, wrong in another — suspect a byte boundary

A decode that is correct in one operating band and off elsewhere is a **bit-width or sign
error**, not a calibration error: a wrong scale is off proportionally everywhere, a wrong
offset by a constant. Example: a pack current built as `((S15 << 8) | S17)/10` reads the
*low* byte signed, so every value whose low byte is ≥ 0x80 comes out 25.6 A low. Only
−25.6..0 A decodes right (AC charging), so it "verifies" against charging captures while
driving power is far out. Tells, each usable on its own:

- **The error is quantized at 2ⁿ × scale.** Residuals against a reference (or jumps
  between consecutive samples) clustering at ±256·scale or ±65536·scale mean a byte
  boundary, not noise.
- **The correct band lines up with 0x80/0x100 of a byte.** Ask which values decode right
  and whether that range is one half of a byte's span.
- **A low byte that never uses half its range.** A continuous signal's low byte covers
  0x00–0xFF about evenly. `decode --dump-bytes` shows it.
- **A small enum next to a byte that wraps.** A clean 0/1/2 "mode" byte whose neighbour
  falls 2 → 254 in the same poll is the high byte of a 16-bit word. Before you name an
  enum, look at its neighbour for a wrap at the same instant.
- **An integral that misses its counter.** Integrate an instantaneous power/current over a
  drive and compare with the ECU's cumulative energy/charge counter (`investigate
  --counters` finds those).

Hence: **verify a signed signal on both sides of zero**. In any hand-built word only the
top byte is `Sn`, and every lower byte is `Bn`. `validate pids` rejects a signed lower byte,
so `pids upsert-param` reverts one.

---

Next: test the candidate expression without committing (`decode --try`/`--plot`), then
define it with `canair pids upsert-param` (see `hypothesize-define.md`). Check a multi-byte
range against the PCI-boundary rule with `canair bix`.
