# Steps 1–3: Orient, prerequisites, discover

Scope: the two signal domains (diagnostic vs broadcast), choosing a target, power state and
access, and finding which DIDs respond. Byte-index and expression notation referenced here is
in `.claude/skills/reverse-engineer-signal/byte-indexing-expressions.md`.

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
canair research --gaps          # actuator DID families no 0x22 read has reached yet
canair coverage --no-capture    # params defined but never captured
canair coverage --unmapped      # captured PIDs with undecoded bytes
```

`research` surfaces *planned* work, and `--gaps` the unplanned reads the profile's own
evidence points at; `coverage` surfaces *undecoded bytes* in PIDs you already capture.
`canair ecu` shows which ECUs carry which kind of signal.

**Once an ECU has captures, run `canair investigate <ECU> --support-map` before decoding
it.** Some records lead with a constant bitmap saying which of the data that follows are
real signals, one bit per data bit, per data byte or per parameter of any width. Where one
fits, it does part of the work for you:

- **`open, marked`** (and `open, moving` on a per-bit map) lists marked positions no signal
  reads yet: the next targets.
- **`unsupported` / `fill`** are bytes the map leaves unmarked. They are filler on this unit,
  so do not spend time decoding them.
- On a per-field map, *N* marks over *W* bytes means *W − N* bytes belong to multi-byte
  parameters. That fixes how the record splits before any correlation.
- **`no map: leading bytes vary`** is an answer too: the data starts right after the DID echo.

Declare a fit with the `pids set-support-map` command it prints, and `coverage` stops listing
non-signals as gaps. The method and verdicts are in `docs/concepts/analysis-commands.md`
(Support maps); the Hyundai/Kia layouts are in
[`hyundai-kia-uds-conventions.md`](hyundai-kia-uds-conventions.md).

### 2. Prerequisites — power state & access

Decide the power state the PID needs (`vehicle_states`: UPPERCASE tokens from the profile's
vocabulary, on the Ioniq `SLEEP, PLUGGED, "OFF", ACC, "ON", READY, CHARGING, IG3` + the `ALL`
meta-token — the same field on PIDs/ECUs and in `research:`, written as an inline flow list
`[READY]`, with a YAML-boolean word like OFF or ON quoted; see/edit the vocabulary with `canair
states`) and whether the
ECU needs waking or an extended session. Body ECUs (Ioniq IGPM 0x770, BCM 0x7A0) wake from CAN
activity; powertrain ECUs (BMS/VCU/MCU) generally need the ignition on or a charge. `canair
discover` shows what answers now (it talks to the car: approval first).

### 3. Discover — which DIDs respond

Steps 3 and 4 send requests to the car. Propose the commands, and run them only once the user
has said "yes" in this session (`AGENTS.md` → The car).

```bash
canair scan MCU --service 21 --range 01-FF --save                        # KWP live data
canair scan IGPM --service 22 --range BC00-BCFF --session --wake --save  # UDS DIDs
```

A bare `canair scan <ECU>` is `scan range`. `--range` takes a comma list
(`B000-B01F,B100-B11F`); without it a UDS ECU gets its **DID families**, the high bytes the
profile already shows it using in `22`/`2F` PIDs and IOControl hits, each swept whole
(`--slots N` for the first N). The other SAFE kinds cover the non-PID
signal types: `scan sessions` (which session types the ECU supports — informs step 2),
`scan routines` (`0x31`/KWP `0x33`), `scan iocontrol` (`0x2F`/KWP `0x30`); hits land in
the ECU's `routines:`/`iocontrol_discoveries:`. **After an IOControl scan, run
`canair research --gaps`:** ECUs often keep their live-data records in their actuators'
family, so an ECU that "has no live data" frequently just has never been read there. A saved
sweep the ECU slept through does not count as a read, so save sweeps (`--save`) and propose a
re-run of asleep ones with `--wake`.

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
propose one read per ECU to confirm its protocol before any bulk scan
(`docs/concepts/ecu-protocols.md`). Ioniq example: the KWP2000 powertrain ECUs reliably
NRC every `22 xxxx` DID ported from the Ioniq 5 — check once, mark `nrc`, move on.
Expect the analogous mismatch on your own car.


