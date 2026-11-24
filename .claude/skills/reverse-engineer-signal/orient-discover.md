# Steps 1–3: Orient, prerequisites, discover

Scope: the two signal domains (diagnostic vs broadcast), choosing a target, power state and
access, and finding which DIDs respond. Byte-index and expression notation referenced here is
in `.claude/skills/reverse-engineer-signal/byte-indexing-expressions.md`.

**Two domains.** Everything below is **domain A** (diagnostic request/response: `ecus/` PIDs,
WiCAN `Bnn` expressions). **Domain B** is passively broadcast CAN frames: `import can`,
`correlate can`/`hunt can`/`investigate can`, `signals upsert`. Step 6's reasoning applies to
both. Only the byte notation and the definition model differ. See
`docs/concepts/broadcast-frames.md`.

### 1. Orient — pick a target

```bash
canair research --summary       # backlog counts; --priority P1 / --ecu MCU to narrow
canair research --gaps          # actuator DID families no 0x22 read has reached yet
canair coverage --no-capture    # signals defined but never captured
canair coverage --unmapped      # captured PIDs with undecoded bytes
```

`research` surfaces *planned* work, and `--gaps` the unplanned reads the profile's own
evidence points at; `coverage` surfaces *undecoded bytes* in PIDs you already capture.
`canair ecu` shows which ECUs carry which kind of signal.

**Once an ECU has captures, run `canair investigate <ECU> --support-map` before decoding
it.** Some records lead with a constant bitmap that says which data are real signals. Where one
fits, it lists the next targets (`open, marked`) and the bytes to skip (`unsupported`/`fill`).
Declare a fit with the `pids set-support-map` command the report prints. The verdicts are in
`docs/concepts/analysis-techniques.md` (Support maps). The Hyundai/Kia layouts are in
[`hyundai-kia-uds-conventions.md`](hyundai-kia-uds-conventions.md).

### 2. Prerequisites — power state & access

Decide the power state the PID needs (`vehicle_states`: UPPERCASE tokens from `canair states`,
plus the `ALL` meta-token; the same field on PIDs, ECUs and in `research:`). Write it as an inline
flow list (`[READY]`) and quote the YAML-boolean words `"OFF"` and `"ON"`. Decide also whether the
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
  (defaults to `status: draft`) as a signal-less placeholder, raw payload in
  `--notes`, then add a `decode` lead. `draft` = tracked, queryable and captured but
  kept out of the generated WiCAN profile until decoded (`set-pid-status … active` to
  ship, `ignored` for a dead DID). Check first whether it is already registered — a
  re-scan refreshes payload/notes, it does not duplicate.
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
(`docs/concepts/ecu-protocols.md`). Check once, mark `nrc`, move on.
