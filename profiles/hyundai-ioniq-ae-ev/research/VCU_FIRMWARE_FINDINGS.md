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
  this car's live captures. The service list and the 0x21 record table are confirmed against live
  reads (2101/2102/21F2 answer, 2103-2105 give NRC 0x12, 0x23 memory reads work). The four
  identity strings in the 0x1A table match the profile. The RAM-backed 0x1A values and the
  preconditioning control algorithm are NOT confirmed; each claim below is tagged.
sections:
  - section: "3"
    status: open
    note: >-
      The 0x1A identification DID map. 8C/8D/8E/91 already match ecus/vcu.yaml's identity block;
      the other 13 DIDs are not defined as signals or research entries yet, and their RAM-backed
      values were not read live.
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

## 2. The customer KWP2000 server and its service set

The diagnostic server on `0x7E2` is a dispatcher (`FUN_00192788`) that switches on the request
service id, with one handler per service. A positive reply is built as `request SID + 0x40`; a
negative reply is sent by a shared builder as `7F <SID> <NRC>`. Each service runs a
session/precondition check before its handler.

Present (handler decompiled): `0x10` StartDiagnosticSession, `0x11` ECUReset, `0x14`
ClearDiagnosticInformation, `0x17` ReadStatusOfDTC, `0x18` ReadDTCByStatus, `0x1A`
ReadEcuIdentification, `0x20` StopDiagnosticSession, `0x21` ReadDataByLocalIdentifier, `0x22`
ReadDataByCommonIdentifier, `0x27`, `0x28`, `0x29`, `0x2C`, `0x2E`, `0x30`
InputOutputControlByLocalIdentifier, `0x31`, `0x3B` WriteDataByLocalIdentifier, `0x3D`
WriteMemoryByAddress, `0x3E` TesterPresent.

Confirmed live (this session and earlier): `0x21`, `0x23`, `0x1A`, `0x18`, `0x10` (session `0x81`
only; `0x82`/`0x83` give NRC `0x12`), and `0x3E`.

There is a second, separate service table (an engineering/debug channel) whose `0x21` slot serves
only local id `0x10` and writes a fixed unlock value to a control register. It is not the
customer server and does not serve 2101/2102.

## 3. KWP identification and data DIDs (the main new finding)

### 3.1 Service 0x21 ReadDataByLocalIdentifier — three records only

A table of three records (then the table ends). The length is the data length; the response adds
two bytes (the SID echo and the record id).

| DID | data bytes | source | profile |
|---|---|---|---|
| `2101` | 20 | RAM | the live-data record (22-byte payload matches a capture) |
| `2102` | 21 | RAM | the live-data record |
| `21F2` | 84 | RAM | the static / last-cycle record |

There is no other `21xx` record, which is why `2103`-`2105` answer NRC `0x12` on the car.

### 3.2 Service 0x1A ReadEcuIdentification — 17 DIDs

A table of 17 records: record id, data length, a source pointer, and read/write flags. The same
table backs InputOutputControl (`0x30`) and WriteDataByLocalIdentifier (`0x3B`), gated by the
flags. Four records hold strings already in the profile's identity block, which confirms the
decode:

| DID | len | value / meaning | state |
|---|---|---|---|
| `1A8C` | 8 | `AEVLDC53` — ecu_id | in profile |
| `1A8D` | 16 | `EAEEO5L-NS7-D060` — sw_id | in profile |
| `1A8E` | 8 | `C5210617` — calibration | in profile |
| `1A91` | 12 | `36601-0E250` — part number | in profile |

The other 13 records are not in the profile yet:

| DID | len | source | note |
|---|---|---|---|
| `1A80` | 97 | RAM | a large status / identification block (live value) |
| `1A84` | 1 | RAM | live value |
| `1A86` | 1 | RAM | live value |
| `1A87` | 4 | RAM | live value |
| `1A88` | 2 | RAM | live value |
| `1A8A` | 10 | RAM | live value |
| `1A8B` | 4 | flash | version/date bytes |
| `1A90` | 17 | RAM, **writable** | VIN — sensitive; the only free-text writable record |
| `1A92` | 8 | RAM | live value |
| `1A94` | 10 | RAM | live value |
| `1A98` | 10 | RAM | live value |
| `1A99` | 4 | flash | version/date bytes |
| `1AFF` | 1 | RAM, **writable** | a status byte |

Only `1A90` (VIN) and `1AFF` are writable through WriteDataByLocalIdentifier (`0x3B`); the rest are
read-only. The RAM-backed values were not read live (the car stopped charging, so the VCU was
asleep, before the identity read). They need a session while the IG3 rail is on.

## 4. CAN mailbox map

The VCU registers 98 CAN mailboxes (a config array in RAM, programmed into the TouCAN controller).
The diagnostic ids are the physical request `0x7E2`, the response `0x7EA`, and the functional
`0x7DF`. The full list of ids, directions and periods is in the local analysis notes. The frames
that matter for section 5 are the remote-climate / charge-schedule receive frames `0x587` (the TMU
remote frame), `0x567` (clock), `0x55C`, `0x588`, `0x55F`, `0x561`, and the VCU's own status
transmit frames `0x579`, `0x57A`, `0x57B` (`0x57B` = `CF_Vcu_IG3RlyMd`).

## 5. Preconditioning and the charge timer

**Established (verified).** The VCU takes remote and scheduled climate and charge commands as
broadcast CAN frames, not as a diagnostic service. It receives them on `0x587` (the TMU remote
frame, carrying the air-conditioning and charge-control bits) together with the clock on `0x567`
and the companion frames `0x55C`/`0x588`/`0x55F`/`0x561`, and it reports its own charge and IG3
state on `0x579`/`0x57A`/`0x57B`. This matches the owner's manual ("remote control via
Center/Smartphone through the TMU") and the TMU frame definition in the head-unit findings. The
only writable diagnostic records are the VIN and one status byte, so there is no diagnostic
"start preconditioning" or "set charge timer" service.

**Not established.** The bit decode of the `0x587` command and the `0x567` clock, the arming and
countdown logic, and the frame the VCU uses to command the e-compressor and heater within its
allowed power were not reversed. That logic reads the TouCAN mailboxes for the schedule frames;
tracing it end-to-end is the next step.

## 6. What to fold into the profile next

- **Confirm the 13 new `1A` DIDs live** (needs the car charging or in IG3), then record them with
  `canair pids` — as research entries first, promoted to signals once their decode is known.
  Keep `1A90` (VIN) `sensitive: true`.
- **Record that service `0x21` has exactly three records**, so `2103`-`2105` are correctly known
  absent rather than untested, on the pending service-ID research entry in `ecus/vcu.yaml`.
- **The preconditioning path** belongs with the broadcast-frame work, not the diagnostic DIDs: it
  needs a P-CAN tap to see `0x587`/`0x567`/`0x57B`, which the OBD port does not carry.
