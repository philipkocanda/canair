---
name: reverse-engineer-signal
description: "Generic, vehicle-agnostic reverse-engineering workflow for canair — the whole flow from orient/discover through capture, analyze, define, verify, integrate, for ANY signal (a PID/DID parameter, a raw broadcast frame field, a routine, an IOControl actuator) on ANY car. Covers WiCAN Bnn / ISO-TP / PCI byte indexing, expression syntax, the analysis reasoning (signal types, physics/EE, statistics), and writing/validating definitions. Use when discovering, decoding, or verifying anything on a vehicle bus, writing or fixing an expression, working out a byte offset, or working a research: backlog — on the bundled Ioniq profile or a profile you built for another car. Examples use the Ioniq for concreteness; the method is generic."
---

# Reverse-engineering a vehicle signal (generic)

The **vehicle-agnostic, end-to-end** workflow for taking a signal from "unknown" to a
verified, decoded parameter in a profile's `ecus/`. Not PID-specific: the same loop
serves a service-`22`/`21` PID/DID, a passively-sniffed broadcast frame field, a
routine, or an IOControl actuator. Examples use the bundled 2017 Ioniq because it is
fully worked — the *method* transfers, not the byte offsets. This skill is the
*procedure*; the profile is the *data* (which ECU carries which signal, marque quirks
and addresses live in the active profile's `ecus/`).

Related skills: **`ioniq-reverse-engineering`** (bundled-car ECU table, device and
transport reference — load it too when working the Ioniq), **`decode-bitfields`** (the
bit-level loop for discrete body signals), **`contributing-profiles`** (sharing the
result upstream).

> **Target the right profile.** The repo ships several (`ioniq-2017`, `ioniq-5-2022`,
> …) and **none auto-selects**. **Pass `--profile NAME` on every mutative command** —
> `pids …`, `hunt --promote`, `import uds`, `signals upsert`, `--save` reads —
> otherwise they write to whatever `default_profile`/`CANAIR_PROFILE` resolves to,
> *not necessarily the car you mean* (exactly how signals once landed in the wrong
> profile). Prefer it on read-only commands too. Examples below omit it — add it.

## Safety first (non-negotiable)

Full policy: `docs/concepts/safety.md`. The essentials:

- **NEVER** open a programming session (`10 02`) or do any firmware write/upload —
  ECUs can be bricked. canair blocks these; never work around it.
- `0x22Fxxx` (flash/cal) is read-only. `2E` writes and `2F` actuation can brick or
  move hardware — out of scope for signal decoding.
- **One canair connection at a time, any transport** (a `flock` mutex enforces it); no
  concurrent requests to one ECU. Clear a stuck session with `--force` or
  `canair lock` — **never by rebooting the device**, and never reboot without asking
  (the WebSocket terminal overrides AutoPID; the reboot is what restores it).
- Be gentle: the first request after idle often fails — retry once before concluding a
  PID/ECU is dead.
- Disable device sleep for a long session, then re-enable. That is **`wican-cli`** (a
  *separate* package), NOT canair: `wican sleep --disable`. Status: `canair status`.

## Working principles

- **Put yourself in the shoes of the ECU's systems engineer.** Before looking at
  bytes, ask what this module must measure, report and control: a BMS engineer thinks
  cell voltages, pack current, temperatures, SOC, contactors, isolation resistance; an
  ESC engineer thinks wheel speeds, yaw rate, accelerations, brake pressure. Signals
  cluster by the ECU's job, come in physically sensible units and ranges, sit in
  orderly blocks (four wheel speeds in a row), and are scaled to fit their field
  width. A decode no real systems engineer would design is probably wrong.
- **Evidence, not vibes.** Never accept a byte because it "looks about right": confirm
  it (range, distribution, correlation, physical plausibility across states) and state
  your confidence honestly.
- **NEVER mark a parameter `verified: true` without proof.** `verified` claims the
  decode was checked against *real data from this vehicle* — a capture matching known
  physical state, a scan-tool cross-check, or a definitive constant. Not "looks
  plausible", not "copied from another car's sheet". **No capture ⇒ no proof ⇒ stays
  `--unverified`**, even when porting a structure verified on a *different* profile
  (here it is a hypothesis). A false `verified` poisons every downstream user and every
  correlation that trusts it; record the missing proof in `notes:`.
- **Notes are technical records, not prose**: byte offset, observed range, per-state
  values, the key evidence, the decision and why — then stop. Speculation gets one line
  at most. These files are read repeatedly and grow forever.

## The lifecycle

```
orient → prerequisites → discover → capture → inspect → hypothesize
       → define → decode/validate → verify → integrate
```

Progress is tracked per-ECU in the `research:` block of `ecus/<ecu>.yaml` (schema
`canlib/schema/pids_schema.yaml`): `pending → captured → done` (plus `nrc` for a dead
scan), ending in a real `parameters:` entry marked `verified: true`.

**Two domains.** The above is **domain A** (diagnostic request/response: `ecus/` PIDs,
freeform WiCAN `Bnn` expressions). **Domain B** is passively-broadcast CAN frames — no
request elicits them, and drive-mode/regen/thermal signals often live there:
`import can <log>` into `captures/can/` (list what's imported with `captures can`), find
fields with `correlate can` / `hunt can --id 0xID --against 0xREF:rN` / `investigate can`
(bytes are `0xID:rN` — raw CAN, no PCI), then define them in the DBC-compatible
**linear** `signals/<bus>.yaml` via `signals upsert` (or `import dbc`). The `uds`/`can`
kind selects the domain across ingest/list/analyze; bare = `uds`. Step 6's reasoning
applies to both; only the byte notation and definition model differ.
`docs/concepts/broadcast-frames.md` has the detail plus the log-storage/licensing policy
(own logs committed, Git LFS when large; third-party only if the license permits
redistribution; unlicensed corpora stay fetch-on-demand in gitignored `references/can/`).

### 1. Orient — pick a target

```bash
canair research --summary       # backlog counts; --priority P1 / --ecu MCU to narrow
canair coverage --no-capture    # params defined but never captured
canair coverage --unmapped      # captured PIDs with undecoded bytes
```

`research` surfaces *planned* work; `coverage` surfaces *undecoded bytes* in PIDs you
already capture. `canair ecu` shows which ECUs carry which kind of signal.

### 2. Prerequisites — power state & access

Decide the power state the PID needs (`vehicle_states`: UPPERCASE `SLEEP, PLUGGED,
ACC, ACC2, READY, CHARGING` + the `ALL` meta-token — the same field on PIDs/ECUs and in
`research:`, written as an inline flow list `[READY]`; see/edit the vocabulary with
`canair states`) and whether the ECU needs waking or an extended session. Body ECUs
(Ioniq IGPM 0x770, BCM 0x7A0) wake from CAN activity; powertrain ECUs (BMS/VCU/MCU)
generally need ACC/ignition or charging. `canair discover` shows what answers now.

### 3. Discover — which DIDs respond

```bash
canair scan MCU --service 21 --range 01-FF --save                        # KWP live data
canair scan IGPM --service 22 --range BC00-BCFF --session --wake --save  # UDS DIDs
```

A bare `canair scan <ECU>` is `scan range`. The other SAFE kinds cover the non-PID
signal types: `scan sessions` (which session types the ECU supports — informs step 2),
`scan routines` (`0x31`/KWP `0x33`), `scan iocontrol` (`0x2F`/KWP `0x30`); hits land in
the ECU's `routines:`/`iocontrol_discoveries:`.

**Always record the outcome — a discovered DID must never be lost:**

- **New responding DID → register it immediately** with `canair pids add-pid`
  (defaults to `status: draft`) as a parameter-less placeholder, raw payload in
  `--notes`, then add a `decode` lead. `draft` = tracked, queryable and captured but
  kept out of the generated WiCAN profile until decoded (`set-pid-status … active` to
  ship, `ignored` for a dead DID). Check first whether it is already registered — a
  re-scan refreshes payload/notes, it does not duplicate. (Ioniq placeholders:
  `ESC 22C102`, `EPS 220101/220102`, `CLU 22B001/B003`.)
- **NRC / no response → close the lead** (`pids set-status <ECU> "<target>" nrc --type
  scan`) so nobody re-probes it. `nrc` = "probed, ECU said no / silent"; `done` =
  "scan complete, responders registered".

```bash
canair pids add-research MCU --type decode --target 2102 \
    --status captured --priority P1 --prereq CHARGING --notes "62 bytes, undecoded"
```

Whether an ECU speaks UDS (`22`/`0x19`/`0x31`/`0x2F`) or KWP2000
(`21`/`0x18`/`0x33`/`0x30`) is recorded per-ECU as **`id_protocol`**, and
`scan`/`dtc`/`routines`/`iocontrol` auto-select the service from it — so a wrong value
makes an ECU blind-probe the wrong service and NRC everything. On a non-Hyundai car,
confirm each ECU's protocol with one successful read before bulk scanning
(`docs/concepts/ecu-protocols.md`). Ioniq example: the KWP2000 powertrain ECUs reliably
NRC every `22 xxxx` DID ported from the Ioniq 5 — confirm once, mark `nrc`, move on.
Expect the analogous mismatch on your own car.

### 4. Capture — record real payloads across states

```bash
canair read MCU:2102 --save --label "MCU 2102 driving" \
    --state "READY, DRIVING" --notes "hard launches + regen"  # bind PID with a colon
canair monitor MCU:2102 --interval 1 --keep-all --save        # values that move reveal meaning
```

Capture the SAME PID in DIFFERENT states (park vs drive, cold vs warm, charging vs
ready) — contrast separates signal bytes from constants. For step 6, **co-poll the
target together with an ECU carrying a known reference** (speed on ESC, RPM on MCU) in
one run so `hunt`/`correlate`/`align` can time-align them; save a recurring co-poll set
as a group and recall it with `@` (`canair monitor @driving CLU:220B`, managed by
`canair groups`).

**Never hand-edit `captures/`, and never read the raw capture JSON** — always go
through `canair captures`/`decode` (step 5), or you get undecoded payloads with no
byte-diffing, decoding or scoping. Saves are journaled and reconciled on exit, so a
killed session is recoverable with `captures uds --recover`. In `monitor`, `state` is
auto-suggested from decoded values; `s` edits the session's metadata, `n` starts a
fresh labelled segment, `● REC` blinks while recording. After saving, run
`captures uds --summary`.

**No device on hand? Steps 5–9 still work** — they run against the *existing* corpus;
a live car is needed only for *new* data. A reading pasted from a forum/issue is
onboarded device-free with `canair import uds`, filed through the same machinery as a
live `--save` (immediately queryable by `decode`/`captures`/`coverage`) — the
sanctioned alternative to hand-writing `captures/`:

```bash
canair import uds MCU:2102=6102... --label "forum: cold-soak" --state SLEEP \
    --notes "posted by <user>, 2017 Ioniq"   # ECU:PID=payload (SID-first, PCI stripped)
```

> **Keep modes change what "a capture" *means*.** `monitor --save` defaults to
> **`--keep-changes`** (run-length): a payload is stored only when it differs from the
> previous one for that PID. So **a stored-row count measures VOLATILITY, not
> sampling** — an ECU with 30 rows over 3 h was polled every cycle and merely didn't
> *change*, while another in the same run with one noisy ADC byte stores thousands.
> Never read a low count as "barely polled" or compare two ECUs' counts as sample
> sizes (that misreading produced a wrong conclusion about IGPM). Read the mode and span
> from `captures uds --sessions`.
>
> A run-length value stays valid until the next stored row, and the joins know it:
> `align`/`correlate`/`hunt`/`investigate`/`decode` **forward-fill** `keep:changes` rows
> (`--fill auto` default, `hold`/`none` to force/disable, `--max-hold` caps it), never
> across a session boundary, and always report what they carried. Choose deliberately:
> **`--keep-all`** for true timing/rate (dRPM, `--transform delta`, dwell);
> **`--keep-changes`** for narrated event captures (both edges + recoverable dwell);
> avoid **`--keep-unique`** whenever timing matters — global dedup discards
> return-to-previous transitions, so the run structure is unrecoverable and fill cannot
> reconstruct it.

### 5. Inspect — see the bytes

```bash
canair captures uds --sessions          # START HERE: TOC (date/span/state/label/notes/ECUs)
canair captures uds --summary           # captures per ECU / per date / totals
canair captures uds MCU 2102            # list + decoded (latest --limit; --limit 0 for all)
canair captures uds MCU:2102 --diff     # unique payloads, byte-diff (+ --all, --rulers)
canair captures uds MCU:2102 --diff --rulers --notation isotp   # re-label the byte ruler
canair captures uds MCU:2102 --step     # interactive stepper (e=note, d=delete, ?=keys)
canair captures uds "HVAC:220100,2201A2" --step   # several PIDs, time-joined in one frame
canair bix -1 --annotate 6101FFFF...    # map each byte → Bnn / ISO-TP / Torque / role
```

Full flag set: `canair captures uds --help`. The **QUERY mini-language** is shared with
`decode`: `MCU 2102`, `MCU:2102,2103`, `MCU` (all its PIDs), `"VCU:2101 BMS:2101"`
(cross-ECU — quote the space), `BCM:22` (prefix match) —
`docs/concepts/query-mini-language.md`.

Start with `--sessions` to see what data exists and pick a drive/state. Byte-diff
highlights which bytes moved between states — your candidate signal bytes. `captures`
and `decode` share the scoping flags (`--since`/`--until`/`--date`, `--state SUBSTR`/
`--label SUBSTR`, `--first`/`--last N`, `--last-session`), so isolate one drive
(`--state DRIVING`) before diffing. `--step` with a multi-PID QUERY stacks those PIDs
time-joined in one frame — how you cross-compare signals at the same instant.
`canair bix --annotate` gives each byte's WiCAN index and flags the PCI bytes you must
not read across (see Reference); `--ecu ECU --pid PID` overlays which parameter (and
bit) maps each byte and flags `unmapped` ones — the fastest way to catch a wrong offset.

!!! note "Scripting over captures — use `load_all_captures()`, mind the RX address"
    For a bulk edit/delete keyed on a predicate the QUERY can't express, go through
    `canlib.commands.captures.query.load_all_captures()`: flat entries with `ecu`
    already **resolved to the short name** (`OBC`), the raw `ecu_addr` (`0x7ED`), and
    `_session_idx`/`_capture_idx` locators for `canlib.captures.delete_capture` (delete
    in reverse `(file, session, capture)` order). **Do not re-derive the list with a raw
    `ecu == "OBC"` filter** — the stored `rx` field is the CAN **response address**, not
    the short name, so that scan silently matches nothing. Prefer
    `captures uds --delete` when the QUERY can express it.

### 6. Hypothesize — form an expression

Hypothesizing is not guessing a byte offset — it is reasoning from *domain knowledge*
about what a signal must physically be, then confirming it in the data. The full
reasoning toolkit is the sibling [`signal-reasoning.md`](signal-reasoning.md) — **load
it for this step**:

- **Let the ECU narrow the search space** — what a BMS/MCU/VCU/OBC/HVAC/body module must
  measure, and why a body PID is bitfields rather than analog.
- **Typed signals** — a mode/flag/date byte gets a param `type:` + `values:`/`bits:` map
  and *categorical* stats, never Pearson (`docs/concepts/typed-signals.md`).
- **Physics / EE** — thermal mass (the most useful lever), signed symmetry, conservation
  checks, rate limits.
- **Computer science** — enums, bitfields, fast counters vs slow accumulators, constants.
- **Statistics** — distribution shape, correlation (cross-ECU is the fastest lever),
  mirrors (the jackpot), state discrimination, sweeps/transforms/fits, and eyeballing the
  raw series before trusting an r.
- **Thematic grouping** as a lead only, and **trust-but-verify** on existing `verified`
  params — they can be wrong.

Cross-reference external signal maps for this car or a close relative (Ioniq: the Kia
Soul/Niro sheets in `profiles/ioniq-2017/references/`), and mind the PCI-boundary rule
when a value spans a frame (see Reference below).

### 7. Test the expression WITHOUT committing

`canair decode --try` evaluates a candidate against every capture with no YAML edit (a
bad expression shows `ERROR` rather than hiding):

```bash
canair decode MCU 2102 --try "MOTOR_RPM:RPM=[S10:S11]"           # value range
canair decode MCU 2102 --try "TORQUE:Nm=[S12:S13]/100" --stats    # distribution
canair decode MCU 2102 --try "T=[S17:S18]" --corr MCU_MOTOR_RPM   # validate by correlation
canair hunt AAF 2181 --against ESC:22C101:REAL_SPEED_KMH --state DRIVING
canair align HVAC:2201A2:HVAC_HEAT_POWER HVAC:2201A2:B41 BMS:2101:BATTERY_POWER --state READY
canair decode MCU 2102 --try "T=[S12:S13]/100" --state DRIVING --stats   # scope one drive
canair decode ESC 22C101 --param REAL_SPEED_KMH --state DRIVING --compact --changes-only
```

`hunt`/`correlate` give the *coefficient and fit*; `align` shows the *series* they were
computed from (its first selector sets the row cadence and the rest nearest-join onto
it, so a column reads `—` where a signal wasn't co-polled — a quick way to *see* which
window overlaps; `--csv`/`--json` export a slice). Scope with the step-5 flags so a
candidate is judged on the relevant drive, not the whole history; `--stats --group-by
state` contrasts segments and `--compact --changes-only` collapses stationary runs.
Iterate until the range is physical, the distribution makes sense (constant? enum?
continuous?), and — where a relationship should exist — the correlation confirms it.

**`canair decode <ECU> <PID> --plot`** is the fastest way to *find* a signal when you
have no candidate expression yet; it works even on a not-yet-defined PID (raw payloads
only). A Textual TUI that sweeps byte offsets × interpretation type (`u8 … f64`) ×
endianness with post-transforms, zooms/pans, lists the captures behind the view (`i`),
switches PID in place (`p`), annotates/renames via `canair pids` (`a`/`R`) and overlays
a `--corr` reference with live Pearson r — press **`?`** for the keymap. Crucially it
shows the **equivalent WiCAN expression** for the current interpretation (copy straight
into step 8), whether that byte is **already mapped**, and a warning when a multi-byte
read crosses a PCI byte.

### 8. Define — write it to ecus/

Use `canair pids` (surgical, comment-preserving, auto-validated and auto-reverted on
schema failure) rather than hand-editing:

```bash
canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" \
    --unit RPM --min -10500 --max 10500 --unverified \
    --source "Kia Soul VMCU CSV" --notes "signed 16-bit BE at B10:B11 (ISO-TP 0x07:0x08)"
```

Found the byte with `canair hunt`? Skip the manual step: `hunt … --promote NAME` writes
the top hit through the same validated path with the r/n, linear fit and unit guess
auto-filled into `notes`. After a write, `upsert-param` echoes the new expression's
decoded **range across existing captures** — a `constant` where you expected variation
usually means the offset landed on a PCI byte.

New params start **`--unverified` and enabled** (the default): enabled+unverified means
the candidate is generated into the WiCAN profile and streams live, so it is easy to
test against reality — the whole point of a candidate. Only use `--disabled` when a
byte is *proven* bogus or redundant and you're keeping it for the research trail
(padding, a counter, an exact mirror of a mapped param). **Keep `--notes` terse and
factual**: byte offset, observed range/per-state values, the one key piece of evidence.

### 9. Verify — confirm against reality

```bash
canair decode MCU 2102 --param MCU_MOTOR_RPM   # ranges for the one param you're verifying
canair decode MCU 2102 --stats --unverified    # distribution; validation focus
canair coverage MCU 2102                       # any bytes still unmapped?
canair validate pids                           # schema + PCI-boundary checks

canair pids upsert-param MCU 2102 MCU_MOTOR_RPM "[S10:S11]" --verified  # promote
canair pids set-status MCU 2102 done --type decode                      # close the lead
```

A parameter is `verified: true` only when validated against real data / known state
(physical correlation, a matching scan tool, or a definitive constant). **No capture ⇒
no proof ⇒ stays `--unverified`** — never promote on a plausible guess or an offset
ported from another car.

### Always mark off a worked lead (do not leave it open)

**Every time you touch a `research:` lead you MUST update its status before moving on**
— one left `pending`/`captured` is re-surfaced by `canair research` and re-worked from
scratch, wasting effort and risking re-probing the car.

```bash
canair pids set-status <ECU> "<target>" done     --type decode  # decoded, a param exists
canair pids set-status <ECU> "<target>" captured --type verify  # candidate awaiting live check
canair pids set-status <ECU> "<target>" nrc      --type scan    # probed, ECU said no / silent
```

Every outcome counts, not just success:

- **Decoded / verified → `done`** (a real `parameters:` entry exists, promoted to
  `verified` where possible).
- **Candidate still needs a live check → keep it `captured`** (a `verify`-type item)
  with the enabled+unverified param in place, noting exactly what to test.
- **"Nothing to decode here" is also a result → `done`.** If analysis proves the
  unmapped bytes are constants/padding, counters, checksums or exact mirrors of a mapped
  param, record that finding *with its evidence* in the lead's notes and close it — the
  negative result is the deliverable (e.g. MCU 2102 B52/B53 = counter/checksum, HVAC
  220100 FF-padding tail). Do NOT silently drop it.
- **Only part done → keep it open with a follow-up**: update `notes`/`what_to_test` so
  the next pass starts where you stopped instead of re-deriving it.

Rule of thumb: after any analysis or capture session, run `canair research --ecu <ECU>`
and confirm no lead you touched still shows its old status.

### 10. Integrate

```bash
canair wican autopid write   # regenerate out/autopid.json (--include-unverified for candidates)
canair wican autopid diff    # compare to device (optional)
uv run pytest -q             # keep the suite green (always `uv run` from the repo root)
```

Then consider contributing the profile upstream with **`canair contribute`** (alias
`share`) — it opens the PR via `gh` and runs a PII pre-flight first. Load the
**`contributing-profiles`** skill first: it covers the scrubbing that gates the PR and
the quality bar a shared profile must clear. For the bundled car, also consider an
upstream wican-fw PR — see `ioniq-reverse-engineering`.

## Tool cheat-sheet (this workflow)

Flags live in each command's `--help`; `docs/concepts/analysis-commands.md` is the
"which command when" map.

| Question | Tool |
|------|------|
| what to work on | `canair research`, `canair coverage` |
| what's captured | `canair captures uds --sessions` (TOC; `--json`) |
| talk to the car | `canair read`/`monitor`/`scan`/`discover` (`--save`) |
| onboard a reading (no device) | `canair import uds ECU:PID=PAYLOAD --label … --state …` |
| see captures | `canair captures uds` (`--diff`/`--step`/`--rulers`/`--latest`/`--summary`) |
| map bytes | `canair bix --annotate` (+ `--ecu`/`--pid`: which param maps each byte) |
| reason about a signal | [`signal-reasoning.md`](signal-reasoning.md) — ECU role, physics/EE, CS (enums/counters), statistics |
| explain an unknown PID | `canair investigate <ECU> <PID>` (mapped? / state F / best anchor + unit / triage / band); omit positionals to sweep an ECU or the profile |
| test expressions | `canair decode --try` / `--stats` / `--corr` / `--plot` |
| decode a body event capture | `canair investigate … --events --bits` + `canair correlate --find-mirrors --bits` |
| find an odometer / hour meter / cycle count | `canair investigate [<ECU> [<PID>]] --counters` (monotonic windows over the WHOLE corpus, scored in bits) — the one question correlation can't answer |
| what's co-polled here | `canair correlate --overlap` |
| cross-ECU correlate | `canair decode … --corr ECU:PID:PARAM`; `canair correlate [--against REF] [--bytes/--bits]` |
| which byte is signal Y | `canair hunt <ECU> <PID> --against ECU:PID:PARAM` (fit + unit guess; `--promote`) |
| eyeball signals side by side | `canair align ECU:PID:PARAM …` (time-aligned table; `--csv`/`--json`) |
| reference an external log | `canair hunt/correlate … --against-file series.csv` |
| a signal with no bus anchor | `canair hunt … --physical`; `canair investigate … --independent-of` |
| remove a confounder | `canair hunt/correlate … --control ECU:PID:PARAM` (partial correlation) |
| raw bytes for external analysis | `canair decode <ECU> <PID> --dump-bytes [--json]` |
| find state-dependent signals | `canair decode … --discriminate state [--bytes] [--bits]` |
| find redundant mirrors | `canair decode --find-mirrors` / `canair correlate --find-mirrors` (+ `--allow-offset`) |
| scope a drive | `--state DRIVING` / `--since`/`--until`/`--date` / `--last-session` |
| per-segment stats · evolution | `canair decode … --stats --group-by state` · `--compact --changes-only` |
| write definitions | `canair pids upsert-param`/`add-pid`/`rename-param`/`rm-param`/`add-research`/`set-status` |
| correct a stale PID note | `canair pids set-pid-notes <ECU> <PID> "…"` (omit text to clear) |
| validate · ship | `canair validate pids`, `canair coverage` · `canair wican autopid write` |

---

## Reference: WiCAN byte index notation

Full treatment: `docs/concepts/byte-indexing.md` (plus
`docs/concepts/wican-byte-index.md` for the firmware-grounded detail).

WiCAN expressions index the **raw CAN frame data including ISO-TP PCI bytes** — the
firmware's ELM327 parser copies all 8 CAN data bytes per frame sequentially into a flat
array. For a *multi-frame* response to `2101` on BMS (0x7E4):

```
Frame 0 (First Frame):  [10 3B] [61 01 FF FF FF FF]  → B00-B07
Frame 1 (Consecutive):  [21]    [d  d  d  d  d  d  d] → B08-B15
Frame 2 (Consecutive):  [22]    [d  d  d  d  d  d  d] → B16-B23
```

- `B00`/`B01` = First-Frame PCI, `B02` = response SID (`0x61`), `B03` = PID echo, `B04`
  = first data byte; PCI bytes recur at B08, B16, B24, …
- A **single-frame** (≤7-byte payload) response carries only **one** PCI byte, so its
  SID sits at `B01` — the WiCAN↔ISO-TP offset is length-dependent. `canair bix` resolves
  this from the payload; assuming multi-frame on a short response shifts every byte by
  one.
- The header after the SID is a property of the *service*, not a fixed width: `0x22` →
  2-byte DID, `0x21` → 1-byte LID, `0x01` → 1-byte OBD PID, `0x31` → SF *before* the
  RID, `0x2F` → CTRL *after* the DID, `0x7F` → rejected SID + NRC.

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` / `[Snn:Smm]` (multi-byte), `Bnn:k`
(bit k, 0 = LSB). Operators `+ - * / << >> & | ^`. Full reference:
`expression_parser.c` in `wican-fw/`.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes — it does NOT skip PCI bytes.** A
value spanning a frame boundary (B07-B08, B15-B16, …) swallows the PCI byte and
produces garbage; shift manually instead — `(B07 << 8) | B09`. Always check a range
with `canair bix`. (Exception: the byte-run types `ascii`/`date` take a plain range and
their decoder skips PCI, since a 17-char VIN cannot fit in one frame.)

```bash
canair bix                  # guided overview: legend + compact 2-frame table
canair bix w9               # WiCAN B09 → ISO-TP 0x06, Torque E, bix 32, CAN frame 1
canair bix --table          # full conversion table, grouped by CAN frame
canair bix -a 62BC03… --ecu IGPM --pid 22BC03   # annotate a payload; roles from the SID
canair bix -a 7F2231        # refused read: REJ SID + NRC, named
```

`--annotate` (`-a`) takes the **reassembled UDS payload** (SID-first, PCI stripped —
what the transport and `captures/` hold); `--raw` annotates an already-framed CAN
payload. It reconstructs the WiCAN frame, prints each byte's WiCAN Bnn / ISO-TP index /
Torque letter / bix / role — labelling every header byte from the payload's own response
SID — and warns when a `-1`/`-2` override or `--pid` contradicts it.

**Never convert a byte index by hand — run `canair bix`.** It is the only trustworthy
source, because the WiCAN ↔ ISO-TP ↔ Torque ↔ bix mapping is not a fixed table: it
depends on the response's *length* (one vs two PCI bytes) and its *service* (header
width and field order), both of which `bix` reads off the actual payload. A static
table is therefore wrong for a large share of real payloads and goes stale whenever the
notation model changes — a hand-maintained one used to live beside this skill and was
deleted for exactly that reason. Use `canair bix --table` when you want the table
itself, `canair bix w9` for one index, and `--annotate` for a real payload.

## Reference: UDS decoding conventions (Hyundai/Kia example)

Marque DID conventions for the bundled Ioniq — PID categories, DID paging vs indexing,
range semantics (`22Bxxx` cluster, `22Cxxx` body, `22Fxxx` flash), and the Hyundai/Kia
identity-DID `-1` offset — are in the sibling
[`hyundai-kia-uds-conventions.md`](hyundai-kia-uds-conventions.md). **These are
marque-specific, not universal**; for another car expect a different scheme and
re-derive it. The generic UDS-`22`-vs-KWP2000-`21` distinction (per each ECU's
`id_protocol`) is in the body above and `docs/concepts/ecu-protocols.md`.
