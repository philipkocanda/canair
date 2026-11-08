---
# Paths are relative to the repository root. The firmware image is local-only (never committed):
# it holds the manufacturer's software. See "Provenance".
title: "VCU firmware findings: the KWP2000 diagnostic interface, its DIDs, and the charge path"
subject: >-
  The 2017 Ioniq Electric's VCU (Vehicle Control Unit, KWP2000 0x7E2), read from a byte-exact
  flash image with ReadMemoryByAddress and analysed statically: the customer diagnostic server and
  its service set, the service-0x21 data records and service-0x1A identification DIDs it serves,
  the writable DIDs, the CAN mailbox map, and the frames behind preconditioning and the charge
  timer
created: 2026-11-08
updated: 2026-11-08
status: active
verification: >-
  Static reverse-engineering of the VCU flash image (Ghidra, 32-bit PowerPC), cross-checked against
  this car's live captures. The service list, the 0x21 record table and the 0x1A read/write-flag
  split are confirmed against live reads on 2026-11-08 (2101/2102/21F2 answer, 2103-2105 and the
  read-flag-2 records give NRC 0x12, 1AFF reads 0x00, 0x23 memory reads work, 0x22 rejects). The
  four identity strings in the 0x1A table match the profile. The preconditioning control algorithm
  is NOT reversed; claims about it are tagged.
sections:
  - section: "3"
    status: open
    date: 2026-11-08
    note: >-
      The 0x1A DID map, confirmed live. 8C/8D/8E/91/90(VIN) match ecus/vcu.yaml; the other readable
      records (80, 8A, 8B, 92, 98, 99, FF) are not defined yet. The read-flag-2 slots (84/86/87/88/
      94) are not 0x1A-readable (NRC 0x12 live).
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "2"
    status: integrated
    date: 2026-11-08
    note: >-
      The service list confirms the pending service-ID-enumeration research entry in vcu.yaml:
      only session 0x81, and 0x21/0x23/0x1A/0x18/0x10/0x3E present. No 3B write path beyond VIN.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "5"
    status: open
    note: >-
      Preconditioning/charge-timer is a CAN path (0x587/0x567/0x55C in, 0x579-0x57B out), not a
      diagnostic service. The control algorithm is not reversed.
relations:
  expands_on:
    - path: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
      note: The identity block, the 2101/2102/21F2 records, and the pending service-ID entry.
    - path: plans/2026-11-05-read-memory-by-address.md
      note: The memory-read work that produced the image analysed here (KWP2000 results).
  related:
    - path: plans/2026-10-29-service-id-enumeration.md
      note: The service-support scan this firmware read answers from the NRC-free side.
    - path: profiles/hyundai-ioniq-ae-ev/research/IONIQ_AVN_FW_FINDINGS.md
      note: The head-unit/TMU frames (0x587 CF_TMU_IG3RLY/AIRCONCTR/CHARGECTR) the VCU consumes.
sources:
  - name: VCU flash image
    kind: >-
      byte-exact dump of the VCU (36601-0E250, sw EAEEO5L-NS7-D060) read on 2026-11-07 with
      ReadMemoryByAddress over KWP2000 while AC charging; holds the manufacturer's software
    location: references/firmware/vcu-36601-0E250-EAEEO5L-NS7-D060/ (local only, never committed)
  - name: Ghidra 12.2
    kind: static analysis (32-bit PowerPC BE); scripts kept beside the image
  - name: this car's captures
    kind: live 2101/2102/21F2/23 reads and the 2026-11-07 memory scan (captures/)
    location: profiles/hyundai-ioniq-ae-ev/captures/
---

# VCU firmware findings: diagnostic interface, DIDs, and the charge/climate path

This collects what a static read of the VCU's flash image shows about its diagnostic interface,
cross-checked against this car's captures. The image itself stays on the machine (it is the
manufacturer's software); only facts derived from it are here.

## 1. Provenance and how much to trust it

The image was read on 2026-11-07 with `canair memory dump` over KWP2000 ReadMemoryByAddress
(`0x23`), `0x000000`-`0x3FFFFF`, while the car was AC charging. See the memory-read plan for the
read method and the "As built" results. The image is a 32-bit PowerPC (Book E) program; the CAN
layer drives an MPC5xx-family TouCAN controller (hardware registers at `0xFFFC0080`).

Evidence classes, strongest first:

- **This car's live captures.** Where they disagree with the firmware reading, they win.
- **The firmware image**, read statically in Ghidra. Function and data addresses below are image
  offsets. Values stored in RAM (`0x40000000`+) are not in the image, so a RAM-backed DID's
  current value is live-only.

The firmware image and the analysis scripts are kept locally under
`references/firmware/vcu-36601-0E250-EAEEO5L-NS7-D060/` (a gitignored path). They are not part of
this repository.

## 2. The customer KWP2000 server, its service set, and how each service is gated

The diagnostic server on `0x7E2` is a dispatcher (`FUN_00192788`) that switches on the request
service id, with one handler per service. A positive reply is built as `request SID + 0x40`; a
negative reply is sent by a shared builder as `7F <SID> <NRC>`. Before its handler, every service
is checked against a permission matrix (`FUN_00192E7C`, table at `DAT_00193C70`, stride 5) keyed
by the current session.

**Services implemented** (handler decompiled): `0x10` StartDiagnosticSession, `0x11` ECUReset,
`0x14` ClearDiagnosticInformation, `0x17` ReadStatusOfDTC, `0x18` ReadDTCByStatus, `0x1A`
ReadEcuIdentification, `0x20` StopDiagnosticSession, `0x21` ReadDataByLocalIdentifier, `0x23`
ReadMemoryByAddress, `0x27` SecurityAccess, `0x28` DisableNormalMessageTransmission, `0x29`
EnableNormalMessageTransmission, `0x30` InputOutputControlByLocalIdentifier, `0x31`
StartRoutineByLocalIdentifier, `0x3B` WriteDataByLocalIdentifier, `0x3E` TesterPresent. The
download/transfer cases `0x34`/`0x36`/`0x37` exist in the switch but are empty (they build no
response in this build), so they are reserved, not active.

**Services that always answer NRC `0x12`** (the handler is a one-line reject, so they are absent
in practice): `0x22` ReadDataByCommonIdentifier, `0x2C` DynamicallyDefineLocalIdentifier, `0x2E`
WriteDataByCommonIdentifier, `0x3D` WriteMemoryByAddress.

### 2.1 Session and security gating (the permission matrix)

The matrix gives, per service, what is allowed in each session. The default session is `0x81`;
the firmware also defines `0x85`, `0x90` and `0xF1` sessions, which require SecurityAccess
(`0x27`) to enter. This exactly matches the car: only `0x81` is reachable without a key, and
`0x82`/`0x83` answer NRC `0x12`.

- **Allowed in the default `0x81` session (no security):** `0x10`/`0x20` session control, `0x1A`
  identification, `0x21` ReadDataByLocalIdentifier, `0x23` ReadMemoryByAddress, `0x17`/`0x18`
  DTC read, `0x14` ClearDiagnosticInformation. Every live read this profile makes is in this set,
  and nothing here can change vehicle state. **`0x23` memory read is in the open set** — no
  SecurityAccess is needed, which is how the firmware image was dumped.
- **Gated behind a secured session (`0x85`/`0x90`/`0xF1`):** `0x11` ECUReset, `0x28`/`0x29` normal
  message control, `0x30` InputOutputControl, `0x31` routines, `0x3B` write, and the reserved
  `0x34`/`0x36`/`0x37` download/transfer cases. IOControl and routines open in session `0x90`;
  download opens in `0x85`. canair cannot reach these (it performs no SecurityAccess, by policy),
  so the VCU's actuators and routines are not usable through it.

SecurityAccess (`0x27`) itself is a seed/key exchange: sub-function `0x01` requests a 2-byte seed,
`0x02` sends the key, with higher levels `0xF1`/`0xF2`. The seed and key computation was not
analysed and must not be (project rule); only the presence and shape are recorded here.

There is a second, separate service table (an engineering/debug channel) whose `0x21` slot serves
only local id `0x10` and writes a fixed unlock value to a control register. It is not the
customer server and does not serve 2101/2102.

## 3. The complete DID / actuator / routine inventory

### 3.1 Service 0x21 ReadDataByLocalIdentifier — three records only

A table of three records (then the table ends). The length is the data length; the response adds
two bytes (the SID echo and the record id). `FUN_00204480` fills the record header before each
read (record `01` starts `FF E0 00 00 09`, which matches the live `2101` payload).

| DID | data bytes | source | profile |
|---|---|---|---|
| `2101` | 20 | RAM `0x4000A1EA` | the live-data record (22-byte payload matches a capture) |
| `2102` | 21 | RAM `0x4000A1FE` | the live-data record |
| `21F2` | 84 | RAM `0x4000005A` | the static / last-cycle record |

There is no other `21xx` record, which is why `2103`-`2105` answer NRC `0x12` on the car
(confirmed live 2026-11-08).

### 3.2 Service 0x30 InputOutputControlByLocalIdentifier — the actuator map

`FUN_0020451C` is a switch over the IOControl local id (the byte after the SID). It is reachable
only in a secured session (see 2.1). Control local ids with real effects:
`0x04`, `0x05`, `0x06`, `0x09`, `0x10`, `0x11`, `0x12`, `0x13`, `0x14`; status/read-only ids that
return the current value: `0x00`, `0x20`, `0x40`, `0x60`, `0x80`, `0xA0`, `0xC0`, `0xE0`. Each
writes a per-id state byte at `0x40009DEB + id` and some drive external hardware registers
(`0x09` toggles bits in the `0x90020000` register space via `FUN_0019CEE0`/`FUN_0019D578`). The
request mode byte is `0x07` (control) or `0x00` (return control to the ECU), as KWP2000 defines.
What each id physically switches was not mapped; it needs a secured session to probe, which is out
of scope.

### 3.3 Service 0x31 StartRoutineByLocalIdentifier — the routines

Routine ids accepted (`FUN_00204D5C`): `0x11`, `0x14`, `0x31`, `0x3B`, each gated to a secured
session. `0x11`/`0x31` also require an internal precondition flag. The start/stop path runs
`FUN_0018CB64`, which calls `WriteExternalEnableImmediate` and a low-vector routine — it looks
like a reflash/reset preparation, not a user feature. Two further manufacturer routines run only
in the programming session and only on an exact magic argument: routine `0xF1` with
`EE FF 00 11`, and routine `0xF3` with `CC DD 33 44`. Their effect was not traced; they are
flagged here as present and guarded, and must not be exercised.

### 3.4 Service 0x3B WriteDataByLocalIdentifier — the writable records

Only two records are writable, both gated to a secured session: `1A90` (VIN) and `1AFF` (one
status byte). Everything else in the `0x1A` table is read-only.

### 3.5 Service 0x1A ReadEcuIdentification — 17 DIDs

A table of 17 records: record id, data length, a source pointer, and read/write flags. The same
table backs InputOutputControl (`0x30`) and WriteDataByLocalIdentifier (`0x3B`), so not every
record is readable with `0x1A`: only the records whose read flag is `1` answer a `0x1A` request;
the read-flag-`2` records reject it with NRC `0x12` (they are slots for the other services). A
live sweep on 2026-11-08 (charging) confirmed this split exactly.

Readable with `0x1A` (read flag `1`), with the value seen live:

| DID | len | value seen live (2026-11-08) | state |
|---|---|---|---|
| `1A80` | 97 | masked (canair treats it as sensitive — may embed the VIN) | not in profile |
| `1A8A` | 10 | all `0x20` (blank system-supplier field) | not in profile |
| `1A8B` | 4 | `1E 09 0D 14` (ECU manufacturing date, packed) | not in profile |
| `1A8C` | 8 | `AEVLDC53` — ecu_id | in profile |
| `1A8D` | 16 | `EAEEO5L-NS7-D060` — sw_id | in profile |
| `1A8E` | 8 | `C5210617` — calibration | in profile |
| `1A90` | 17 | VIN — masked/withheld (sensitive); **writable** | in profile (vin) |
| `1A91` | 12 | `36601-0E250` — part number | in profile |
| `1A92` | 8 | empty | not in profile |
| `1A98` | 10 | all `0x20` (blank repair-shop/tester-serial field) | not in profile |
| `1A99` | 4 | `1E 09 0D 14` (programming date, packed) | not in profile |
| `1AFF` | 1 | `0x00` (status byte); **writable** | not in profile |

Not readable with `0x1A` (read flag `2`) — these answer NRC `0x12` to a `0x1A` request, confirmed
live for `84`/`86`/`87`/`88`; they are shared-table slots for IOControl/write, not identification
reads: `1A84` (1 byte), `1A86` (1), `1A87` (4), `1A88` (2), `1A94` (10).

Only `1A90` (VIN) and `1AFF` are writable through WriteDataByLocalIdentifier (`0x3B`), both gated
to a secured session; everything else is read-only. The two writable records are the only way to
change VCU state over diagnostics, and neither is a schedule.

## 4. CAN mailbox map

The VCU registers 98 CAN mailboxes (a config array in RAM, programmed into the TouCAN controller).
The diagnostic ids are the physical request `0x7E2`, the response `0x7EA`, and the functional
`0x7DF`. The full list of ids, directions and periods is in the local analysis notes. The frames
that matter for section 5 are the remote-climate / charge-schedule receive frames `0x587` (the TMU
remote frame), `0x567` (clock), `0x55C`, `0x588`, `0x55F`, `0x561`, and the VCU's own status
transmit frames `0x579`, `0x57A`, `0x57B` (`0x57B` = `CF_Vcu_IG3RlyMd`).

## 5. Preconditioning and the charge timer — is the schedule on the diagnostic bus?

**No. The schedule is not exposed over the diagnostic interface.** The service set was enumerated
exhaustively (section 2 and 3): the only readable items are the three `0x21` records (live
powertrain data in `2101`/`2102`, the static `21F2`), the `0x1A` identification records, memory
(`0x23`), and DTCs. None carries a departure time, a charge window or a preconditioning timer, and
there is no writable record for one (`0x22`/`0x2C`/`0x2E` are rejected, and `0x3B` writes only the
VIN and one status byte). So a tester cannot read or set the schedule through KWP2000.

**Where it lives instead (verified).** The VCU takes remote and scheduled climate and charge
commands as broadcast CAN frames. It receives them on `0x587` (the TMU remote frame, carrying the
air-conditioning and charge-control bits) together with the clock on `0x567` and the companion
frames `0x55C`/`0x588`/`0x55F`/`0x561`, and it reports its own charge and IG3 state on
`0x579`/`0x57A`/`0x57B`. This matches the owner's manual ("remote control via Center/Smartphone
through the TMU") and the TMU frame definition in the head-unit findings.

**Not established.** The bit decode of the `0x587` command and the `0x567` clock, the arming and
countdown logic, and the frame the VCU uses to command the e-compressor and heater within its
allowed power were not reversed. That logic reads the TouCAN mailboxes for the schedule frames;
tracing it end-to-end is the next step.

## 6. What to fold into the profile next

- **The 7 new readable `1A` DIDs are confirmed live** (80, 8A, 8B, 92, 98, 99, FF). Record them
  in `ecus/vcu.yaml` with `canair pids`/`ecu`. `1A8B`/`1A99` are packed date fields; `1A8A`/`1A92`/
  `1A98` are blank on this unit; `1AFF` is a `0x00` status byte; `1A80` stays masked (`sensitive`),
  as does the `1A90` VIN.
- **Service `0x21` has exactly three records** (2103-2105 NRC `0x12` confirmed live), so they are
  correctly known absent rather than untested.
- **The preconditioning path** belongs with the broadcast-frame work, not the diagnostic DIDs: it
  needs a P-CAN tap to see `0x587`/`0x567`/`0x57B`, which the OBD port does not carry.

## 7. Record internals — what fills each byte of 2101/2102/21F2

The records are built in RAM each cycle by producer functions (`2101` by `FUN_001F46C0`, `2102`
by `FUN_0021AD44`, `21F2` by `FUN_00204028`); `FUN_00204480` overwrites only the header at read
time. Decompiling the producers gives the width, byte order, scale and source of every byte, which
resolves most of the coverage gaps in `canair coverage VCU`.

**Byte-index note.** WiCAN `Bnn` counts the ISO-TP framing, so `B0`/`B1` are the first-frame PCI,
`B2`/`B3` are `61 <id>`, and `B8`/`B16`/`B24` are consecutive-frame PCI bytes, not data. Data byte
`Dn` therefore maps to `B4,B5,B6,B7, B9,B10,…,B15, B17,…,B23, B25,…` (skipping the PCI bytes).

### 7.1 Record 2101 (`FUN_001F46C0`)

- **`B9` (D4) is a constant `0x09`** — a record-format byte, not a signal. This is the lone
  "unmapped" byte in the coverage report; it can be marked as a constant.
- `B10` GEAR (D5): bits 0-3 are one-hot from an internal gear state (`1`=P, `2`=R, `3`=N, `4`=D),
  **bit 4 is set when that state reads `7`** (a fifth state, not yet observed), and bit 5 is the
  valid flag set separately by `FUN_00204480`.
- `B11` vehicle-state flags (D6): bit0 brake-lamp, bit1 not-braking, **bit2** = an internal flag
  (source `r13-0x17FA`, meaning not yet named), bit3 EV-ready, bit4 IG3, bit6 EV-ready mirror,
  bit7 LDC-enabled. **Bit5 is never written (always 0, unused).**
- `B12/B13` (D7/D8) APS1, `B14/B15` (D9/D10) APS2, `B17/B18` (D11/D12) accelerator pedal,
  `B19/B20` (D13/D14) speed, `B21/B22` (D15/D16) 12 V supply — all 16-bit little-endian, matching
  the profile's (previously unverified) signals. The firmware confirms the width and byte order.
- `B23` (D17): bit2 inverter-enabled, bit4 armed, bit5 armed-mirror (profile), plus bits 0/1/3/6
  from internal inverter flags (sources identified, not yet named); bit7 unused.
- `B25` (D18) is a whole byte = `VCU_POWER_STATE`. `B26` (D19) is a gear-state debug byte.

### 7.2 Record 2102 (`FUN_0021AD44`)

- `B4-B7` (D0-D3) are a constant header (`F8 F8 00 00`), not signals.
- `B9/B10/B11/B12/B13` (D4-D8) are single status bits unpacked from two internal words
  (`r13+0xBAC`, `r13+0xBAB`); the profile has `B9/B11/B12/B13` unmapped — they are bit flags, not
  bytes.
- `B17` (D11) inverter input voltage (×2) and `B18` (D12) 12 V aux (÷10) match the profile.
- **Four 16-bit little-endian scaled values** the profile lists as unverified, now with their
  firmware scale: `B19/B20` = internal float ×1000 (so decode `((B20<<8)|B19)/1000`), `B21/B22`
  = float ×100, `B23/B25` = float ×100 (spanning the frame boundary), `B26/B27` = float ×1000.
  The physical quantity each float is (a current, a voltage, a temperature) still needs
  correlating against captures; the scale and layout are now known.

### 7.3 Record 21F2 (`FUN_00204028`) — the statistics record

The 84-byte record is a last-cycle statistics block: a header, several 16- and 32-bit counters
(e.g. a 32-bit value at `D9-D12`), and a block of about a dozen 16-bit values each computed as
`internal_int / (sum_of_three_ints × 0.01)` — i.e. normalised ratios (percent-like) over a common
denominator. `B21/B22` (D15/D16) reuse the same 12 V-supply source as 2101. The record is a
distribution/counter dump, not live analogue signals, which is why most of it reads as a static
byte field. Naming each counter and ratio needs correlation; the structure (counters + a
normalised ratio block) is established.

### 7.4 Honest limit

The producers store values that were computed elsewhere in the firmware, so the decompile gives
each byte's **width, byte order, scale, and the internal source variable** — not a human name for
every quantity. Constants (`2101 B9`, the `2102` header), unused bits (`2101 B11:5`), bit-field
sources, and the 16-bit scales above are now firm. The remaining flag bits and the scaled floats
need a correlation pass against captures (or further tracing of the source variables) to attach
physical names and units.

## 8. Second pass: resolving the small-data base, and what it proved

### 8.1 r13 = 0x400063E4

The producers read their inputs as `r13 + offset`: in the PowerPC EABI, r13 is the small-data-area
base. Scanning every instruction that writes r13 found the startup pair
`lis r13,0x4000; ori r13,r13,0x63e4` at image offset `0x145104` — so **r13 = `0x400063E4`**, and
the `0x140000`-`0x1451FF` area is startup/init code, not only calibration data as first assumed.
Cross-checks hold: the diagnostic request buffer is then `r13+0xDC` = `0x400064C0` with the service
id at +0, the record id at +1 and the length at +6, exactly as the handlers use it.

With the base known, every anonymous producer input resolves to an address, e.g. `2101`'s speed
word at `0x40004E46`, its 12 V supply word at `0x4000507E`, the unnamed `B11:2` flag at
`0x40004BEA`, and the four `2102` floats at `0x4000A52C`, `0x40006DCC`, `0x4000A514`,
`0x4000A550`.

### 8.2 What the resolution did *not* give

Two attempts did not produce usable names, and are recorded so they are not repeated:

- **Cross-referencing the sources to CAN transmit builders.** The `2101` inputs are reached only
  by r13-relative addressing, which carries no Ghidra reference, so the xref came back empty. The
  `2102` floats do have referencing functions, but the "CAN id" constants in them are dense
  (dozens per function) and are struct offsets, not frame ids. No name can be claimed from this.
- **Naming the internal floats from the firmware alone.** The producers only pack values computed
  elsewhere, so the decompile gives scale and layout, not physics.

### 8.3 What the firmware scales proved against the captures

Checking the firmware-derived expressions against the stored captures with
`canair decode VCU:2102 --try … --stats` (14,657 captures) **confirms all four 16-bit pairs that
the profile already carries as unverified**, independently of the original correlations:

| bytes | firmware | profile signal | verdict |
|---|---|---|---|
| `B19`/`B20` | float ×1000 | `VCU_LDC_OUTPUT_VOLTAGE` (`/1000`) | scale and byte order match |
| `B21`/`B22` | float ×100 | `VCU_AUX_POWER` (`/10`, W) | consistent: the internal float is in units of 10 W |
| `B23`/`B25` | float ×100 | `VCU_DCLINK_VOLTAGE` (`/100`) | match; a `--try` of the firmware expression reproduced it exactly (4915 distinct values) |
| `B26`/`B27` | float ×1000 | `VCU_AUX_BATTERY_VOLTAGE_FINE` (`/1000`) | scale and byte order match |

`VCU_DCLINK_VOLTAGE_COARSE` is explained rather than merely correlated: it reads the **high byte**
of the `B23`/`B25` pair, and `256/100 = 2.56` is exactly its empirical scale factor.

### 8.4 New signal and newly explained bytes in 2102

The firmware says `2102 B9`-`B13` are single status bits from an internal word, not bytes. Testing
each against the captures:

- **`B9` is a new signal, now defined as `VCU_HV_AVAILABLE`.** It is strictly broader than
  `VCU_HV_MAIN_RELAY_ON` (`B10`): a cross-tab over 14,657 captures gives B9=1/B10=1 in 14,198 rows,
  B9=1/B10=0 in 450, B9=0/B10=0 in 9, and **B9=0/B10=1 in none**. So B9 never clears while the main
  relay is closed, and clears only in a deeper HV-off state.
- **`B11`, `B12` and `B13` read 0 in all 14,657 captures.** They are real status bits that are
  inactive on this car in the recorded states, not undecoded bytes, so they should not be counted
  as coverage gaps.
- `B4`-`B7` are a constant header (`F8 F8 00 00`).

Both PIDs' notes now carry these facts, so `canair coverage VCU` reports only genuinely open bytes.
