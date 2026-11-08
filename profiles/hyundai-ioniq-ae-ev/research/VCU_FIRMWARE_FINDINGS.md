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
  four identity strings in the 0x1A table match the profile. The CAN frame layouts (§9) come from
  emulating each unpack and pack function bit by bit, and are checked against opendbc (0x587),
  the profile's verified clock frame (0x567) and uhi22's P-CAN trace. The scheduling state
  machines are NOT named; claims about them are tagged.
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
  - section: "4"
    status: open
    date: 2026-11-08
    note: >-
      The CAN layer: three FlexCAN controllers, two used (P-CAN and the EPCU's CCP-CAN), 98
      mailbox slots, the RAM buffer and unpack/pack function of every frame. Bus assignment matches
      uhi22's EPCU transceiver test exactly. can_buses.yaml has no CCP-CAN entry yet.
    target: profiles/hyundai-ioniq-ae-ev/can_buses.yaml
  - section: "5"
    status: open
    date: 2026-11-08
    note: >-
      Preconditioning/charge-timer is a CAN path (0x587/0x567/0x55C/0x588 in, 0x579-0x57B out) run
      by a scheduling model, with the VCU's own SPI real-time clock as time base and wake alarm. The
      state machines are not named.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "9"
    status: open
    date: 2026-11-08
    note: >-
      Frame layouts as the VCU reads and writes them. 0x567, 0x549 and most received frames match
      signals/. 0x55C/0x57A (bits 16-49) and 0x588/0x579 (all fields) do not: the profile's
      layouts are wrong and need replacing.
    target: profiles/hyundai-ioniq-ae-ev/signals/
  - section: "10"
    status: integrated
    date: 2026-11-08
    note: >-
      The VCU's own real-time clock and wake alarm. Folded into the vcu.yaml research entry "who
      wakes the car for a scheduled start" (result). Whether the alarm powers the EPCU is still
      open there.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "11"
    status: integrated
    date: 2026-11-08
    note: >-
      The parked 12 V top-up is the VCU's: 24 h check, thresholds, 10/20 min timer, inhibits.
      Folded into the igpm.yaml top-up research entry (result), the TOPUP state description, and
      signals/ (0x590 VCU_590 bits 1, 2, 6, 7; 0x51B AUX_BATTERY_SAVER; 0x57A
      VCU_AUX_BATTERY_SAVER).
    target: profiles/hyundai-ioniq-ae-ev/ecus/igpm.yaml
  - section: "12"
    status: open
    date: 2026-11-08
    note: >-
      The schedules are in SRAM (standby copy at 0x40000A07-0x40000A2F). The 0x23 server reaches
      only 0x000000-0xFFFFFF, so it cannot read them. The 0x18CAC8 table is a CCP 2.1 slave with no
      seed/key on CCP-CAN (0x6C0/0x6C2) that can read SRAM, but the OBD port does not reach it.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
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
      note: >-
        The head-unit/TMU frames (0x587 CF_TMU_IG3RLY/AIRCONCTR/CHARGECTR) the VCU consumes.
        Section 9 here corrects its P-CAN layouts for 0x55C and 0x588.
    - path: profiles/hyundai-ioniq-ae-ev/signals/body.yaml
      note: GW_DEPART_SCHEDULE (0x55C), GW_ECON_WINDOW (0x588), CGW_CLOCK (0x567), checked in §9.
    - path: profiles/hyundai-ioniq-ae-ev/signals/powertrain.yaml
      note: VCU_DEPART_SCHEDULE (0x57A), VCU_ECON_WINDOW (0x579), BAT11 (0x549), checked in §9.
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
  - name: uhi22 Ioniq28Investigations
    kind: >-
      P-CAN logs of a 2018 EU Ioniq 28 kWh (scripts/fetch_can_corpus.py) and the EPCU bench readme
      (which transceiver sends which frame)
    location: https://github.com/uhi22/Ioniq28Investigations
  - name: opendbc hyundai_can.dbc
    kind: TMU11 (0x587) definition, used to check the 0x587 unpack (commaai, MIT)
---

# VCU firmware findings: diagnostic interface, DIDs, and the charge/climate path

This collects what a static read of the VCU's flash image shows about its diagnostic interface,
cross-checked against this car's captures. The image itself stays on the machine (it is the
manufacturer's software); only facts derived from it are here.

## 1. Provenance and how much to trust it

The image was read on 2026-11-07 with `canair memory dump` over KWP2000 ReadMemoryByAddress
(`0x23`), `0x000000`-`0x3FFFFF`, while the car was AC charging. See the memory-read plan for the
read method and the "As built" results. The image is a 32-bit PowerPC program for an MPC55xx-family
microcontroller: an e200 (Book E) core, SRAM at `0x40000000`, and the MPC55xx peripheral map. The
CAN layer drives FlexCAN controllers at `0xFFFC0000`, `0xFFFC4000` and `0xFFFC8000` (message
buffers from offset `0x80`, 16 bytes each), and the real-time clock sits on the DSPI at
`0xFFF98000` (section 10). The exact part number was not determined.

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

There is a second, separate command table at `0x18CAC8`. It is a CCP (CAN Calibration Protocol
2.1) slave on CCP-CAN, not a KWP2000 server: its slot numbers are CCP command codes, not service
ids. Section 12.3 has the details. It does not serve 2101/2102.

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
| `1AFF` | 1 | `0x00` (mode byte, see section 4.4); **writable** | not in profile |

Not readable with `0x1A` (read flag `2`) — these answer NRC `0x12` to a `0x1A` request, confirmed
live for `84`/`86`/`87`/`88`; they are shared-table slots for IOControl/write, not identification
reads: `1A84` (1 byte), `1A86` (1), `1A87` (4), `1A88` (2), `1A94` (10).

Only `1A90` (VIN) and `1AFF` are writable through WriteDataByLocalIdentifier (`0x3B`), both gated
to a secured session; everything else is read-only. The two writable records are the only way to
change VCU state over diagnostics, and neither is a schedule.

## 4. The CAN layer: controllers, buses and the mailbox map

### 4.1 Controllers and buses

The VCU has three FlexCAN controllers and uses two of them:

| controller | base | global mailbox index | bus | evidence |
|---|---|---|---|---|
| FlexCAN A | `0xFFFC0000` | `0x00`-`0x3F` | **P-CAN** | carries the BMS, MCU, OBC, gateway and ESC frames and the diagnostic ids |
| FlexCAN C | `0xFFFC8000` | `0x40`-`0x7F` | **CCP-CAN**, the EPCU's second external bus | `0x2C1` in, `0x232`/`0x58F` out |
| FlexCAN B | `0xFFFC4000` | `0x80`-`0xBF` | none | initialised, but no slot uses it |

The bus assignment matches uhi22's EPCU bench test exactly. uhi22 found two external CAN buses on
the EPCU, each wired to both the MCU and the VCU. On the "left CAN, CCP-CAN" the VCU's
transceiver sends `0x232` and `0x58F` and the MCU's sends `0x2C1`. On P-CAN the VCU sends `0x109`,
`0x200`-`0x202`, `0x549`, `0x579`-`0x57B`, `0x590` and `0x592`. These are exactly the firmware's
FlexCAN C and FlexCAN A transmit slots. None of the CCP-CAN ids appears in either uhi22 P-CAN log.
`can_buses.yaml` has no entry for this bus yet.

### 4.2 The slot table

`FUN_001885D4(id, slot, dir, controller, class)` fills a 98-entry config array at `0x40007120`
(6 bytes per slot), called once per slot from `FUN_00186F90`:

- byte 0-1: `id << 4 | controller << 2 | dir << 1` (dir 1 = transmit; controller 0 = A, 2 = C).
- byte 2: bits 0-2 the timing class, bits 5-7 the transmit state.
- byte 3: the global mailbox index, assigned in slot order per controller by `FUN_00188620`.

The timing class matches the period of the transmit frames in uhi22's logs (class 0 = 10 ms,
class 3 = 100 ms). For receive frames it does not match the observed rate, so it is probably a
timeout class. All mailboxes are standard 11-bit ids with 8 data bytes.

Each received frame is copied into its own 8-byte RAM buffer every cycle (`FUN_001A04DC`, plus
`FUN_001A6F30` for `0x153`/`0x386`/`0x2C1`/`0x331`), and a "new frame" bit is set in a bitmap at
`0x4000736A`. One generated function per frame then unpacks the buffer into signal variables
(section 9). Every unpacked signal has a calibration override, and a default value that replaces
it while the frame's status flag (probably its timeout) is set.

In the table, bus P is P-CAN and CCP is CCP-CAN. "unpack / pack" is the function that reads the
received buffer or fills the transmit buffer; `signals/` names the profile's definition, if any.

| slot | id | dir | bus | class | RAM buffer | unpack / pack | `signals/` |
|---|---|---|---|---|---|---|---|
| 0x00 | `7DF` | RX | P | 0 | - | - | - |
| 0x01 | `7E2` | RX | P | 0 | - | - | - |
| 0x02 | `7EA` | TX | P | 0 | - | - | - |
| 0x03 | `6C0` | RX | CCP | 0 | - | - | - |
| 0x04-0x13 | `6C2` | TX | CCP | 0 | - | - | - |
| 0x14 | `594` | RX | P | 3 | `400065B8` | `1B5FA4` | - |
| 0x15 | `595` | RX | P | 3 | `400065C0` | `1B5D34` | BMS_595 |
| 0x16 | `596` | RX | P | 3 | `400065C8` | `1B5C70` | BMS_596 |
| 0x17 | `597` | RX | P | 3 | `40006803` | `1B5BB0` | BMS_597 |
| 0x18 | `598` | RX | P | 3 | `400065D0` | `1B5AB0` | - |
| 0x19 | `542` | RX | P | 3 | `400065D8` | `1B59A4` | BMS_542 |
| 0x1A | `541` | RX | P | 0 | `400065E0` | `1ACF5C`, `1C2A14` | CGW1 |
| 0x1B | `553` | RX | P | 1 | `400065E8` | `1C28CC` | - |
| 0x1C | `490` | RX | P | 0 | `400065F0` | `1CC744` | - |
| 0x1D | `5D0` | RX | P | 6 | `400065F8` | `1AD254` | EV_CP1 |
| 0x1E | `450` | RX | P | 0 | `40006600` | `1AD254` | EV_CP2 |
| 0x1F | `578` | RX | P | 1 | `40006608` | `1ACF5C`, `1CA0C0` | EV_CP5 |
| 0x20 | `462` | RX | P | 0 | `40006610` | `1D7E7C` | - |
| 0x21 | `5B3` | RX | P | 2 | `40006618` | `1C2390` | - |
| 0x22 | `4FE` | RX | P | 3 | `40006620` | - | - |
| 0x23 | `418` | RX | P | 7 | `40006628` | - | - |
| 0x24 | `419` | RX | P | 7 | `40006630` | `1D3E84` | - |
| 0x25 | `41A` | RX | P | 7 | `40006638` | `1D4850` | - |
| 0x26 | `41B` | RX | P | 7 | `40006640` | `1D4774` | - |
| 0x27 | `41C` | RX | P | 7 | `40006648` | `1D4464` | - |
| 0x28 | `587` | RX | P | 0 | `40006650` | `1C5354` | - |
| 0x29 | `588` | RX | P | 0 | `40006658` | `1C5000` | GW_ECON_WINDOW |
| 0x2A | `55C` | RX | P | 0 | `40006660` | `1C47F4` | GW_DEPART_SCHEDULE |
| 0x2B | `55F` | RX | P | 0 | `40006668` | `1C4414` | - |
| 0x2C | `561` | RX | P | 0 | `40006670` | `1C4034` | - |
| 0x2D | `567` | RX | P | 0 | `40006678` | `1C3D44` | CGW_CLOCK |
| 0x2E | `5DF` | RX | P | 5 | `4000680B` | `1D4400` | - |
| 0x2F | `5F5` | RX | P | 3 | `40006680` | `1B8BF4` | - |
| 0x30 | `5F6` | RX | P | 3 | `40006688` | `1B88DC` | - |
| 0x31 | `5F7` | RX | P | 3 | `40006690` | `1B86A0` | - |
| 0x32 | `5F8` | RX | P | 3 | `40006698` | `1B83A8` | - |
| 0x33 | `5F9` | RX | P | 3 | `400066A0` | `1B8194` | - |
| 0x34 | `570` | RX | P | 3 | `400066A8` | `1D342C` | - |
| 0x35 | `590` | TX | P | 3 | `400066B0` | `219F0C` | - |
| 0x36 | `58F` | TX | CCP | 3 | `400066B8` | `219F0C` | - |
| 0x37 | `354` | RX | P | 0 | `400066C0` | `1D345C` | GEAR_354 |
| 0x38 | `291` | RX | P | 0 | `400066C8` | `1B03B4` | - |
| 0x39 | `523` | RX | P | 0 | `400066D0` | `1AFD20` | - |
| 0x3A | `524` | RX | P | 0 | `400066D8` | `1AFAF4` | EPCU_524 |
| 0x3B | `57F` | RX | P | 3 | `400066E0` | `1C2A70` | OBC1 |
| 0x3C | `10C` | RX | P | 0 | `400066E8` | `1B0828` | - |
| 0x3D | `200` | TX | P | 0 | `400066F0` | `1F3F18` | EMS20 |
| 0x3E | `201` | TX | P | 0 | `400066F8` | `1F3544` | - |
| 0x3F | `202` | TX | P | 0 | `40006700` | `1F2788` | - |
| 0x40 | `592` | TX | P | 3 | `40006708` | `1F2340` | - |
| 0x41 | `579` | TX | P | 3 | `40006710` | `1F2054` | VCU_ECON_WINDOW |
| 0x42 | `57A` | TX | P | 3 | `40006718` | `1F1D38` | VCU_DEPART_SCHEDULE |
| 0x43 | `57B` | TX | P | 3 | `40006720` | `1F3A44` | - |
| 0x44 | `109` | TX | P | 0 | `40006728` | `1F395C` | - |
| 0x45 | `2A2` | RX | P | 0 | `40006730` | `1CA114` | - |
| 0x46 | `51B` | RX | P | 7 | `40006740` | `1C22D4` | - |
| 0x47 | `220` | RX | P | 0 | `40006748` | `1D32B4` | ESP12 |
| 0x48 | `153` | RX | P | 0 | `40006750` | `1D801C` | - |
| 0x49 | `394` | RX | P | 0 | `40006758` | `1BC398` | TCS13 |
| 0x4A | `507` | RX | P | 0 | `40006760` | `1BC320` | - |
| 0x4B | `386` | RX | P | 0 | `40006768` | `1D81B0` | WHL_SPD11 |
| 0x4C | `010` | RX | P | 7 | `40006770` | `1B5958` | - |
| 0x4D | `549` | TX | P | 3 | `40006778` | `219F0C` | BAT11 |
| 0x4E | `011` | RX | CCP | 0 | - | - | - |
| 0x4F | `001` | TX | CCP | 0 | - | - | - |
| 0x50 | `012` | RX | CCP | 0 | `4000656C` | `19C78C` (and 3 others) | - |
| 0x51 | `002` | TX | CCP | 0 | `40006574` | `19C4E0` | - |
| 0x52 | `013` | RX | CCP | 0 | - | - | - |
| 0x53 | `003` | TX | CCP | 0 | - | - | - |
| 0x54 | `050` | RX | CCP | 3 | `40006780` | relayed, see §4.4 | - |
| 0x55 | `051` | RX | CCP | 3 | `40006788` | relayed, see §4.4 | - |
| 0x56 | `055` | RX | CCP | 0 | `40006790` | - | - |
| 0x57 | `056` | RX | CCP | 0 | `40006798` | - | - |
| 0x58 | `057` | RX | CCP | 0 | `400067A0` | - | - |
| 0x59 | `058` | RX | CCP | 0 | `400067A8` | - | - |
| 0x5A | `059` | RX | CCP | 0 | `400067B0` | - | - |
| 0x5B | `060` | TX | CCP | 0 | - | - | - |
| 0x5C | `061` | TX | CCP | 0 | - | - | - |
| 0x5D | `6FC` | TX | CCP | 3 | `400067C8` | - | - |
| 0x5E | `232` | TX | CCP | 0 | `400067D8` | `1F44C0` | - |
| 0x5F | `2C1` | RX | CCP | 0 | `400067E0` | `1D3928` | - |
| 0x60 | `331` | RX | P | 0 | `40006738` | `1D30BC` | TCS_331 |
| 0x61 | `300` | RX | CCP | 0 | `400067E8` | `1CA5D0` | - |

`0x587`'s buffer is also read by `FUN_0019E8D0`, and `0x567`'s by `FUN_0019E25C`/`FUN_0019E8D0`:
these set the real-time clock (section 10). `0x4FE` and `0x418` are received but no function reads
their buffers.

### 4.3 Transmission

Two scheduler tasks send the periodic frames:

- **10 ms (`FUN_001A5D68`):** `0x200`, `0x201`, `0x202` and `0x109`. Each carries a 2-bit rolling
  counter. `0x201` and `0x202` end in an XOR checksum of bytes 0-6. `0x109` byte 7 holds the counter
  in bits 4-5 and a 4-bit nibble-sum checksum in bits 0-3 (uhi22's log shows byte 7 cycling `0A`,
  `2C`, `1B`, `3D`). The same task would send `0x6FC` on CCP-CAN, but a calibration flag
  (`0x2D612` = 0) disables it in this build.
- **100 ms (`FUN_001A652C`):** `0x590`, `0x58F` (CCP-CAN), `0x592`, `0x579`, `0x57A`, `0x57B` and
  `0x549`.

Both tasks send nothing while the KWP2000 DisableNormalMessageTransmission (`0x28`) flag is set
(`0x400064E4`, set by the `0x28` handler, cleared by `0x29`, `0x20` and two other paths).

### 4.4 The CCP-CAN side

CCP-CAN carries the CCP slave (command `0x6C0`, 16 response slots on `0x6C2`, the command table
at `0x18CAC8`, section 12.3), three request/response pairs (`0x011`/`0x001`,
`0x012`/`0x002`, `0x013`/`0x003`), the MCU's `0x2C1`, and a test-mode set of frames:

- `0x590`, `0x58F` and `0x549` are normally built from VCU data (`FUN_00219F0C`; `0x549` is the
  LIN battery sensor's data, section 9.5).
- When DID `1AFF` (RAM `0x40006115`) is `1`, the VCU instead copies CCP-CAN `0x050` into `0x590`
  and `0x051` into `0x58F` (`FUN_00181404`/`FUN_001A6E30`), and reads CCP-CAN `0x300`. `1AFF`
  survives a warm reset (it is mirrored at `0x40000059`), and about 20 other functions branch on
  it. It reads `0x00` on this car. Its purpose (an end-of-line or bench mode) is inferred, not
  confirmed.
- A latched flag at `0x400067F0` stops all periodic transmission. A test sequence run from the
  diagnostic task (`FUN_0019BFF0`) sets it when it completes. What starts that sequence was not
  traced.

## 5. Preconditioning and the charge timer — is the schedule on the diagnostic bus?

**No. The schedule is not exposed over the diagnostic interface.** The service set was enumerated
exhaustively (section 2 and 3): the only readable items are the three `0x21` records (live
powertrain data in `2101`/`2102`, the static `21F2`), the `0x1A` identification records, memory
(`0x23`), and DTCs. None carries a departure time, a charge window or a preconditioning timer, and
there is no writable record for one (`0x22`/`0x2C`/`0x2E` are rejected, and `0x3B` writes only the
VIN and one status byte). So a tester cannot read or set the schedule through KWP2000. Section 12
shows why `0x23` cannot read it from memory either.

**Where it lives instead (verified).** The VCU takes remote and scheduled climate and charge
commands as broadcast CAN frames on P-CAN, and keeps the schedules itself:

- **In:** `0x55C` (departure schedule, two slots), `0x588` (charge mode and off-peak windows),
  `0x567` (the gateway clock), `0x587` (the telematics unit's remote command, `TMU11`), and
  `0x55F`/`0x561` (five 4-bit/8-bit pairs each, probably navigation road segments, not traced).
- **Out:** `0x57A` (the stored departure schedule), `0x579` (the stored charge mode and windows)
  and `0x57B` (scheduler and remote-climate status).

The bit layouts are in section 9. A Simulink-generated scheduling model, step function
`FUN_0020ED4C` (37 subsystems), connects them. Section 10 has what is established about it and
about the clock and wake alarm.

This matches the owner's manual ("remote control via Center/Smartphone through the TMU"). The EU
car has no telematics unit, so `0x587` is absent from uhi22's P-CAN logs.

## 6. What to fold into the profile next

- **The 7 new readable `1A` DIDs are confirmed live** (80, 8A, 8B, 92, 98, 99, FF). Record them
  in `ecus/vcu.yaml` with `canair pids`/`ecu`. `1A8B`/`1A99` are packed date fields; `1A8A`/`1A92`/
  `1A98` are blank on this unit; `1AFF` is a `0x00` status byte; `1A80` stays masked (`sensitive`),
  as does the `1A90` VIN.
- **Service `0x21` has exactly three records** (2103-2105 NRC `0x12` confirmed live), so they are
  correctly known absent rather than untested.
- **The preconditioning path** belongs with the broadcast-frame work, not the diagnostic DIDs: it
  needs a P-CAN tap to see `0x587`/`0x567`/`0x57B`, which the OBD port does not carry.
- **Replace the `0x55C`/`0x57A` and `0x588`/`0x579` layouts in `signals/`** with the VCU's
  (section 9.2 and 9.3), using `canair signals`. The first 16 bits of `0x55C`/`0x57A` are already
  right.
- **Add the CCP-CAN bus** to `can_buses.yaml` (section 4.1), as a private EPCU segment.
- **Done 2026-11-08:** the scheduled-wake entry in `ecus/vcu.yaml` and the top-up entry in
  `ecus/igpm.yaml` carry sections 10.2 and 11 as their results; both stay pending until the car
  shows that the clock's alarm powers the EPCU. `signals/` has the `0x590` top-up bits, the
  `0x51B` Aux. Battery Saver+ setting and its `0x57A` echo, and the `TOPUP` state description
  names the 24 h check.
- **The `0x57B` IG3-relay-mode entry** in `ecus/vcu.yaml`: on this VCU, byte 2 bits 4-7 are four
  separate flags from two subsystems (section 9.4), not one 4-bit mode.

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

## 9. Broadcast-frame layouts as the VCU reads and writes them

### 9.1 Method, and the checks that it works

Each unpack function (section 4.2) was run in Ghidra's p-code emulator once per frame bit, with only
that bit set in the frame buffer, and the changed RAM variables were recorded. That gives each
field's start bit, width and scale, including offsets and calibration defaults. Each pack function
was run once per bit of each input variable. Bit numbers below are DBC little-endian start bits
(byte × 8 + bit), as in `signals/`.

Two independent checks show that the method reproduces known layouts:

- **`0x567` (clock):** the VCU reads the weekday mask at bits 1-7 (bit 7 = Sunday), hour 8|5,
  minute 16|8, second 24|8 and the valid flag 32|1. This is exactly `CGW_CLOCK` in
  `signals/body.yaml`.
- **`0x587` (`TMU11`):** every field that opendbc defines is where opendbc puts it (section 9.5).

All decodes of real traffic below use uhi22's P-CAN logs of a 2018 EU Ioniq (not this car), where
the frames are constant: `0x55C` = `8D C0 90 9E FE 1D 01 00`, `0x57A` = `8D C0 90 9E FE 1D 01 80`,
`0x588` = `A4 49 A4 49 00 00 00 00`, `0x579` = `A4 49 A4 49 44 00 00 FE`,
`0x57B` = `E3 01 00 00 00 50 64 A0`.

### 9.2 `0x55C` departure schedule (gateway) and `0x57A` (the VCU's stored copy)

Two 25-bit slots, back to back. Unpacked by `FUN_001C47F4`, packed by `FUN_001F1D38`. Both frames
use the same bits 0-49.

| bits | field | VCU variable (in / out) | `signals/` today |
|---|---|---|---|
| 0\|5 | slot 1 hour | `554A` / `6128` | same |
| 5\|3 | slot 1 minute, ×10 | `554B` / `6129` | same |
| 8\|7 | slot 1 weekday mask, bit 0 Monday … bit 6 Sunday | `5519` / `612A` | same |
| 15\|1 | slot 1 enabled | `551E` / `6137` | same |
| 16\|6 | slot 1 cabin temperature: °C = raw × 0.5 + 15 | `5556` / `6138` | wrong (`SET2_HOUR`/`SET2_MIN`) |
| 22\|1 | slot 1 temperature unit (inferred) | `5521` / `6139` | wrong |
| 23\|1 | slot 1 defrost | `551A` / `613A` | wrong |
| 24\|1 | slot 1 precondition (inferred) | `551B` / `6127` | wrong |
| 25\|5 | slot 2 hour | `554C` / `612C` | wrong |
| 30\|3 | slot 2 minute, ×10 | `554D` / `612D` | wrong |
| 33\|7 | slot 2 weekday mask | `5525` / `612E` | wrong |
| 40\|1 | slot 2 enabled | `552A` / `613B` | wrong (`SET1_DEFROST`) |
| 41\|6 | slot 2 cabin temperature | `5557` / `613C` | wrong |
| 47\|1 | slot 2 temperature unit (inferred) | `552D` / `613D` | wrong |
| 48\|1 | slot 2 defrost | `5526` / `613E` | wrong |
| 49\|1 | slot 2 precondition (inferred) | `5527` / `612B` | wrong |
| 50\|1 | `0x57A` only: scheduler output | — / `6188` | — |
| 62\|2 | `0x57A` only: scheduler status (reads 2 in the trace) | — / `618C` | — |

Variables are `0x4000xxxx`; the leading `4000` is dropped.

The temperature is half-degrees with an offset: the VCU adds 30 and rejects values above 64, so the
range is 15-32 °C. The flag names come from two sources. The scheduling model pairs bit 22/47 with
`0x587`'s `CF_Tmu_TempMd` and bit 23/48 with `CF_Tmu_DefrostCtr` (subsystem `0x20B9D4`), and it
reads bit 24/49 together with the slot's time and weekdays. The head unit's slot has a
precondition flag, a temperature, a temperature unit and a defrost flag.

Decoded with this layout, uhi22's frame reads: slot 1 at 13:40 on Sunday, enabled, 23.0 °C,
defrost on, precondition off; slot 2 at 15:20 every day, enabled, 22.0 °C, defrost on, precondition
off. The IONIQ_AVN_FW_FINDINGS notes say preconditioning was off in that car. The layout in
`signals/` reads the same frame as slot 2 at 16:40 Tuesday-Friday with temperatures 63 and 7,
which those notes could not explain.

### 9.3 `0x588` charge mode and off-peak windows (gateway) and `0x579` (the VCU's copy)

Unpacked by `FUN_001C5000`, packed by `FUN_001F2054`. Both frames use the same bits 0-33. The VCU
puts the charge mode **first** and every window 2 bits later than `signals/` does.

| bits | field | VCU variable (in / out) | `signals/` today |
|---|---|---|---|
| 0\|2 | charge mode (0 = Charge Now) | `550F` / `6126` | wrong (at 32\|2) |
| 2\|5 | weekday window start hour | `5552` / `612F` | wrong (at 0\|5) |
| 7\|3 | weekday window start minute, ×10 | `5554` / `6130` | wrong |
| 10\|5 | weekday window end hour | `554E` / `6131` | wrong |
| 15\|3 | weekday window end minute, ×10 | `5550` / `6132` | wrong |
| 18\|5 | weekend window start hour | `5553` / `6133` | wrong |
| 23\|3 | weekend window start minute, ×10 | `5555` / `6134` | wrong |
| 26\|5 | weekend window end hour | `554F` / `6135` | wrong |
| 31\|3 | weekend window end minute, ×10 | `5551` / `6136` | wrong |
| 34\|2, 36\|2, 38\|2 | `0x579` only: other VCU state | — / `53AD`, `53B1`, `53B8` | — |
| 53\|2, 55\|1 | `0x579` only | — / `4F99`, `4F9A` | — |
| 56\|8 | `0x579` only (reads `FE` in the trace) | — / `53AF` | — |

The order of the four windows is confirmed by the off-peak subsystem (`0x207000`). It converts each
pair to minutes (hour × 60 + minute), repeats the first window for five weekdays and the second for
the weekend, and handles windows that cross midnight. `0x57B` bits 1-2 also follow the mode: they
read 1 only while the effective mode is 0 (a table at `0x1A8822`).

Decoded with this layout, uhi22's frame reads: Charge Now, weekday window 09:30-18:00, weekend
window 09:30-18:00. The layout in `signals/` reads 04:50-09:20 for both.

### 9.4 `0x57B` scheduler and remote-climate status (VCU, 100 ms)

Packed by `FUN_001F3A44`. Most inputs are outputs of the scheduling model (section 10.1); the
subsystem that sets each is given where it is known.

| bits | source | note |
|---|---|---|
| 0\|1 | `6156`, charge subsystem `0x208F10` | |
| 1\|2 | `6161`, charge subsystem | 1 in charge mode 0 (Charge Now) |
| 3\|6 | `6160` − 40 | a calibration constant (100) in this build: the frame reads 60 |
| 9\|1 | `5325` | |
| 10\|1, 11\|1 | `616F` (climate `0x20B2D8`), `618B` (`0x20D2DC`) | |
| 12\|4 | `616A`, subsystem `0x20A444` | |
| 16\|1 | `5531` | a copy of `0x587` bit 37 |
| 17\|2 | `6170`, climate | |
| 19\|1, 20\|1, 21\|1 | `615C`, `6159`, `6157`, charge | |
| 22\|1, 23\|1 | `616D`, `6178`, climate | |
| 24\|6 | `6177` − 30 | climate set temperature, same encoding as section 9.2 |
| 30\|1, 31\|1 | `618A` (`0x20D164`), `4BDC` | |
| 32\|1, 33\|1 | `615A` (charge), `617A` (climate) | |
| 34\|3, 37\|1 | `4CF7`, `4CF8` | |
| 40\|8, 48\|8, 56\|8 | `600A`, `6009`, `6008`, each × 2 (255 above 127) | read 80, 100, 160 in the trace |

The US head unit reads `CF_Vcu_IG3RlyMd` as a 4-bit field at bits 20-23 (IONIQ_AVN_FW_FINDINGS
section 6). This VCU writes four separate flags there, two from the charge subsystem and two from
the climate subsystem. The values 2 and 10 seen in the US are consistent with flags (bit 21, and
bits 21 + 23).

### 9.5 `0x587` `TMU11` (the telematics unit's remote command) as the VCU reads it

Unpacked by `FUN_001C5354`. opendbc names where it has them; head-unit names from
IONIQ_AVN_FW_FINDINGS section 6.

| bits | field | VCU variable |
|---|---|---|
| 0\|1 | `CF_Tmu_VehSld` | `5545` |
| 1\|1 | `CF_Tmu_VehImmo` | `5544` |
| 2\|2 | `CF_Tmu_ReqRepCnd` | `5537` |
| 4\|1 | `CF_Tmu_AirconCtr` (remote climate on) | `550A` |
| 5\|1 | `CF_Tmu_TempMd` | `5543` |
| 6\|16 | `CF_Tmu_TempSet`: VCU adds 30 (half-degrees, as in section 9.2); values above 34 are rejected | `5558` |
| 22\|1 | `CF_Tmu_DefrostCtr` | `5518` |
| 23\|1 | used by the charge subsystem; probably `CHARGECTR` | `550E` |
| 24\|2 | `IMMECHAR_MODE` | `5534` |
| 26\|6 | VCU adds 40; 61 is rejected | `5546` |
| 32\|2 | `CF_TMU_IG3RLY` (0 none, 1 immediate, 2 monitoring, 3 reservation) | `5532` |
| 34\|1 | `TMU_RDY` | `5536` |
| 35\|1 | | `550D` |
| 36\|1 | `CHAR_CANCEL` | `5533` |
| 37\|1 | echoed in `0x57B` bit 16 | `5531` |
| 38\|1 | `AIRCONCTR_CANCEL` | `550B` |
| 40\|1 | | `5542` |
| 56\|4 | `CF_Tmu_AliveCnt1` | `550C` |

The head-unit findings put `CHARGECTR`, `DEFROSTCTR` and `TEMPSET` in bytes 0-2 at other bits.
Their byte-4 fields (`IG3RLY`, `TMU_RDY`, `CHAR_CANCEL`, `AIRCONCTR_CANCEL`) agree with the VCU.
The VCU does not read bit 39 (`SEPZONEDISP`).

### 9.6 Other frames against `signals/`

A bit-overlap check of every `signals/` definition on a frame the VCU receives or sends:

- **Consistent:** `0x541` `CGW1`, `0x542`, `0x567`, `0x57F` `OBC1` (all ten signals), `0x595`
  (all four), `0x596` temperatures, `0x5D0` odometer, `0x450` speed, `0x331`. Every field the VCU
  reads on these frames sits at the bits `signals/` gives. The VCU does not read some of the
  `signals/` fields (for example `CGW1`'s `Ign1`/`Ign2` and `0x596`'s 12 V voltage).
- **`0x549` `BAT11` (sent by the VCU, built by `FUN_0021A330` from the 12 V battery sensor's raw
  values):** current 0\|16, SoC 16\|8, voltage 24\|14, temperature 38\|9 and SoH 48\|7 match. The
  VCU decodes the temperature as unsigned (raw × 0.5 − 40), and `signals/` marks it signed. Bits
  56-62 (× 0.1) and bit 55 (it selects the temperature source) are not in `signals/`.
- **`0x200` `GEAR_SHIFTER`:** the VCU writes 4 bits (11-14); `signals/` has 3.
- **`0x524` `EPCU_DC_VOLTAGE`:** the VCU reads bytes 0-1 as a 16-bit value (`FFFF` = invalid);
  `signals/` has 9 bits. The values agree below 512 V.
- **`0x354` `GEAR_LEVER`:** the VCU reads the whole of byte 5; `signals/` has its low nibble.
- `0x220` `LONG_ACCEL`, `0x386` wheel speeds: the VCU combines the field with its status bits or
  with the other wheel, so the overlap check flags them, but the field positions agree.

Not in `signals/` at all: `0x109`, `0x201`, `0x202`, `0x57B`, `0x587`, `0x590`, `0x592` and the
CCP-CAN frames. Their layouts can be read off the probe output in the local analysis directory.

## 10. The scheduling model, the real-time clock and the wake alarm

### 10.1 The scheduling model (roles inferred from inputs and outputs)

`FUN_0020ED4C` runs 37 generated subsystems. Each reads its inputs through one-line getter
functions (`0x20E26C`-`0x20E8C4`) and writes its outputs through setters (`0x20E6BC`-`0x20ED3C`),
so the inputs and outputs of each are exact. What happens inside the state machines is not
reversed. Roles:

- **Clock (`0x2057D8`, `0x205758`, `0x2058CC`):** weekday, hour and minute from `0x567` when its
  valid bit is set, else from the VCU's own real-time clock (section 10.2). Time of week in minutes
  (weekday × 1440 + hour × 60 + minute) at `0x4000611C`.
- **Schedule input (`0x205AAC`):** takes the departure slots and windows from CAN only when the
  gateway clock is valid (`0x567` bit 32) or the telematics unit is ready (`0x587` bit 34).
  Otherwise it keeps a stored copy (`0x400054D6`-`0x400054FF`; where that copy is persisted was
  not traced). A cleared slot reads hour 31 and minute 7 (all ones). It flags every change and
  writes the active schedule to `0x40006126`-`0x4000613E`, which `0x579` and `0x57A` send.
- **Departure (`0x206A54`, `0x207FD0`):** the next departure time from both slots (enable,
  weekdays, precondition, time). **Climate settings (`0x2085C0`):** the temperature, unit and
  defrost of the slot that matches the next departure.
- **Off-peak (`0x207000`):** the windows, as section 9.3 describes.
- **Climate request (`0x20B9D4` → `0x20B2D8`):** merges the remote request (`0x587` air
  conditioning on/cancel, set temperature, temperature mode, defrost, `TMU_RDY`) with the departure
  climate settings and BMS, OBC and gateway inputs. Several outputs go to `0x57B`.
- **Charge control (`0x2095D4` → `0x208F10`):** `0x587` `IG3RLY`, charge cancel, bits 23 and 37,
  with OBC and BMS state, drive a state machine (state byte `0x40006D2A`, states `0x08`-`0x1E`, not
  named). Outputs go to `0x57B` bits 0-2, 19-21 and 32.
- **Wake time (`0x2088E4`):** inputs include both clocks, the departure and off-peak results and
  the charge state. Outputs `0x4000617E`-`0x40006180` are the next wake time (section 10.2).

Two scheduler outputs (`0x4000617B` from the climate request, `0x4000618E` from `0x20D92C`) are also
inputs to the LDC-enable decision (`FUN_001C1800`, which sets the source of `2101` `B11:7`). How
they combine there was not worked out.

### 10.2 The real-time clock and the wake alarm

The VCU drives a real-time clock over SPI (the DSPI at `0xFFF98000`, chip select 1). Its command
byte is `0x80` (read) or `0x00` (write), plus `0x10`, plus a register number. Registers
`0x02`-`0x08` are seconds to years in BCD, `0x09`-`0x0C` are alarm registers, `0x00`/`0x01` are
control and `0x0E`/`0x0F` are a timer. That is the register map and command format of the NXP
PCF2123. The chip itself was not identified on the board.

- **At start-up** (`FUN_0019D154`) the VCU reads the control registers and re-initialises the clock
  when the oscillator-stop bit (seconds bit 7) is set.
- **While awake** (`FUN_0019E8D0`) it copies the gateway time into the clock whenever `0x567` is
  valid or `0x587` reports `TMU_RDY`. The scheduler uses the clock's own time otherwise.
- **While awake** (`FUN_0019E858`) it enables the clock's alarm interrupt (`FUN_0019CA84`) when the
  scheduler has a wake time (`0x4000617E`-`0x40006180`), and disables it (`FUN_0019D454`) when the
  scheduler reports none (`0xFF`).
- **At shutdown** (`FUN_0019EDD4`) it builds the `21F2` record (which is why `21F2` holds the
  previous cycle). Then `FUN_0019EACC` clears the clock's flags and writes the current time and the
  alarm (the scheduler's wake time) into the clock, and the VCU waits for power-off.

So the VCU keeps its own time while parked and sets a wake alarm for the next scheduled event. It
does not depend on the gateway's frozen `0x567` copy. This answers most of the research entry "who
wakes the car for a scheduled start" in `ecus/vcu.yaml`. **Not established:** that the clock's
interrupt output switches the EPCU on. uhi22's readme shows the regulator's enable fed through a
diode from connector pin CN1000.P3. A bench test with a departure time set, or the capture protocol
of that research entry, would settle it.

### 10.3 Preconditions for remote climate (`0x587`) versus scheduled climate

The climate request is a chart (`FUN_0020C66C`, its output state at `0x40006D5D`), fed by input
conditioning in `FUN_0020B9D4` and by two triggers: the departure window (`0x40006148`, from
`0x2079B8`) and a remote-climate timer (`0x4000614C`, from `0x207AD0`). The timer starts on a 0→1
edge of `0x587` `CF_Tmu_AirconCtr`, runs for a calibrated time (`0x2D5A8` = 15, probably minutes),
restarts on a new edge, and stops on an `AIRCONCTR_CANCEL` edge. The model step is 10 ms.

To leave idle and start, the chart needs all of:

- a trigger: the remote timer running, or the departure window open with more than 6 minutes
  (`0x2D5E9`) left;
- the VCU's wake supply sensed (an analog input with hysteresis, `0x400065B6`), and the chart idle
  for 1 s;
- the hard-wired input that `2101` `B23:2` reports (`VCU_INVERTER_ENABLED`, on in READY) off;
- not READY, and, after a READY cycle, re-armed by a new `AirconCtr` edge or by the plug bit
  below;
- no latched `0x587` `CF_TMU_IG3RLY` = 1 (it blocks a start until a new trigger edge arrives while
  `IG3RLY` is not 1);
- no lost OBC, BMS or climate-unit frames, and no fault flag from the climate unit (`0x5F5` bits 36,
  37).

Then, within 20 s (`0x1A8874` = 2000 steps), the car must be in **P** and one of these must hold:

| trigger | allowed when |
|---|---|
| departure window | plugged: `0x57F` bit 33 and `0x595` bit 9 (both debounced), **and** `0x57F` bit 4 `OBC_EVSE_READY` |
| remote (`0x587`) | the same plugged condition, **or** `0x553` bits 2-5 = 2 |

After that, the BMS flag `0x594` bit 34 and the VCU's own `0x590` byte 0 bit 2 must be set before
it reaches the running state. While running, a remote session continues only while the plugged
condition or `0x553` bits 2-5 = 2 still holds. A scheduled session needs the plug throughout.

So the firmware has an unplugged path, but only for the remote trigger, and only when the gateway
reports 2 in `0x553` bits 2-5. That field reads 1 in every P-CAN log available: uhi22's two Ioniq
logs and about 60 Kona logs (driving, READY, charging, scheduled charge, no key). Its meaning on
P-CAN is not known. On C-CAN, opendbc defines those bits as four single-bit diagnostic states, and
the VCU reads them as one 4-bit value that holds its last value on timeout.

A climate energy budget also applies. The VCU integrates the climate unit's power (`0x5F5`/`0x5F6`)
and compares it with a table indexed by BMS SoC (`0x594` byte 5, × 0.5 %). The table's breakpoints
are 3, 10, 12, 14, 15, 23, 50 and 70 %. The budget is 0 at the bottom, small up to about 15 %, and
effectively unlimited above about 50 %. What exceeding it does is not traced.

Not traced: which frame or signal closes the HV contactors and powers the compressor and heater for
a remote session. `0x57B` carries the climate state and set temperature to the climate unit.

## 11. The parked 12 V top-up: the VCU decides, schedules and runs it

Three parts of the VCU firmware work together. The IGPM's IG3 relay supplies the rail during a
top-up (`ecus/igpm.yaml`), but the schedule, the decision and the timing are the VCU's.

### 11.1 The schedule: a 24-hour check, kept in the VCU's own clock

Subsystem `0x20D92C` of the scheduling model keeps the next check time as minutes of the week
(`0x40006190`). When a check finishes, charged or not, it sets the next one to the current time
plus a calibrated interval: `0x2D5EE` = **1440 minutes (24 h)**. The value survives sleep in
standby RAM (`0x4000097A`, written by `FUN_001D5AF8`). The wake-time subsystem (`0x2088E4`)
programs the earliest of three times into the real-time-clock alarm (section 10.2): this check,
the charge-schedule wake (`0x40006142`) and the climate-departure wake (`0x40006150`).

On waking, `0x2088E4` compares the clock's time with each stored time. A match with the check time
sets "woken for the 12 V check" (`0x40006183`). A check therefore runs only when the VCU is awake at
its own stored minute, which points to the VCU's own alarm as the wake source. The owner's manual
says 72 hours; this calibration says 24 hours, as uhi22 reports. Because the next time counts from
the end of the previous check, the time of day moves later by each check's length.

### 11.2 The decision: battery low? (`FUN_00218B90`, every 100 ms)

This runs in the 100 ms task (`FUN_001815A0`, skipped when DID `1AFF` = 1), only while not READY,
and only in one of two situations:

- **Woken for the check while parked** (`0x40006183`, and `0x57B` bit 31: the wake supply is on
  without the ignition input, `FUN_001AEC14`). Raised thresholds apply.
- **IG3 on but not READY**, at most 9 completed top-ups per power-up (`0x30158`). Normal
  thresholds apply.

It raises a charge request (`0x590` byte 0 bit 6) when one of these conditions holds for **30 s**
(`0x30248`/`0x30260` = 300 × 100 ms) with the HV main relay open:

| battery data | normal | woken for the check |
|---|---|---|
| 12 V sensor (`0x549`) valid | voltage ≤ 12.0 V or SoC ≤ 70 % | voltage ≤ 12.7 V or SoC ≤ 85 % |
| sensor not valid | VCU's own 12 V reading ≤ 12.4 V | ≤ 12.7 V |

Once the main relay closes, the request runs for a fixed relay-closed time. That time comes from a
map of 12 V SoC by temperature (`0x30160`/`0x30178`/`0x30194`): **20 min** (12000 × 100 ms) up to
80 % SoC, **10 min** from 81 %, and the same at every temperature. Without valid sensor data it is
20 min (`0x3015C`). When the time is up, the VCU clears bit 6 and sets bit 7 (done). If the relay
opens early, it drops the request. A second rule also sets "done" after 20 min (`0x30150`) of relay
time in READY or with the BMS flag `0x594` bit 63.

### 11.3 Running it: the top-up chart (`0x20D92C`, 10 ms model step)

A second chart in `0x20D92C` handles the woken check:

1. 1 s after start-up, if woken for the check, it waits up to **60 s** (`0x1A8898`) for the request
   bit from 11.2. With no request it ends, and the next check is set 24 h later.
2. With a request it sets a "prepare" output (`0x4000618D`). That output feeds `0x57B` bit 30 after
   a 0.5 s debounce (`0x20D164`). It waits up to **5.2 s** for the BMS's HV-ready flag (`0x594` bit
   34).
3. Then it requests the LDC (`0x4000618E`, an input to the LDC decision `FUN_001C1800`, which sets
   `2101` `B11:7`). Once the VCU sees the main relay closed (`0x590` byte 0 bit 2 = `2102`
   `VCU_HV_MAIN_RELAY_ON`), it enters the running state.
4. It runs until the request ends (the 20/10 min of 11.2), HV-ready drops, an inhibit appears, or
   **21 min** pass (`0x1A88A0` = 126000 × 10 ms). Then it drops the LDC request, and the HV side
   opens the relay.

The recorded top-ups fit this: 16-19.5 min long, the LDC stops while the BMS is still awake, and
the main relay opens afterwards.

A sibling chart in the same subsystem runs a top-up while the ignition input is on but the car is
not READY, only with the charge cable in. It uses the normal thresholds.

### 11.4 What blocks a parked top-up (`0x20D368`)

- the ignition input on (the input `2101` `B23:2` reports);
- READY, the gear not in P, or a charge or climate session running;
- **HV SoC below 20 %**, with the block held until it rises above 25 % (`0x2D5EA`/`0x2D5EC`, from
  `0x594` byte 5);
- lost OBC or BMS frames, or fault flags (`0x594` bit 36, `0x590` bit 9, OBC fault, `0x523` bit
  0, `0x598` bit 19);
- with no charge cable: any door, the hood or the tailgate open (`0x541` `DrvDrSw`, `AstDrSw`,
  `HoodSw`, `TrunkTgSw`; `0x553` rear doors);
- the Aux. Battery Saver+ setting off. The cluster sends it in `0x51B` bits 46-47 (1 = off,
  2 = on, other values keep the stored setting). `0x51B` bit 39 and a reset event force it on. The
  VCU stores the setting in standby RAM and echoes it in `0x57A` bits 62-63; uhi22's car reads 2.

### 11.5 Not established

- How the VCU gets the IGPM to close IG3 and the BMS to close the main relay. The "prepare" output
  reaches `0x57B` bit 30; nothing else from it was traced.
- That the clock's alarm powers the EPCU (section 10.2). The exact-minute match in 11.1 makes it
  the likely wake source.

## 12. Can the schedule be read from VCU memory?

**Not from the OBD port.** The schedules live in SRAM, and the KWP2000 `0x23` server cannot address
SRAM. A CCP slave on the EPCU's CCP-CAN can read SRAM, but the OBD port does not reach that bus.

### 12.1 Where the schedules are in memory

| what | address | written by |
|---|---|---|
| received copy of the departure slots and windows | `0x400054D6`-`0x400054FF` | `FUN_001C3168`, the model getters |
| active schedule (the source of `0x579` and `0x57A`) | `0x40006126`-`0x4000613E` | the scheduling model (section 10.1) |
| charge-schedule wake, climate-departure wake, 12 V check | `0x40006142`, `0x40006150`, `0x40006190` | section 11.1 |
| standby copy of the active schedule and the wake times | `0x40000A07`-`0x40000A2F`, `0x4000093E`-`0x40000943`, `0x40000978`-`0x4000097D` | `FUN_001D5AF8` |

`FUN_001D5AF8` copies the active schedule and the three wake times byte by byte into the low 8 KiB
of SRAM (`0x40000000`-`0x40001FFF`). The boot code zeroes SRAM only from `0x40002000` (the loop at
image offset `0x20`), and `.data` starts there too, so this low area probably survives a restart:
it is the standby RAM. No
diagnostic record overlaps any of these addresses. `21F2` is `0x4000005A`-`0x400000AD`, and the
`0x1A` records are at the addresses in section 3.5.

The application never writes flash at run time. Its flash-programming functions
(`FUN_00182B28`-`FUN_00183194`, the two flash controllers at `0xC3F88000` and `0xC3F8C000`) have no
caller and no pointer to them in the image. So the schedule has no copy in flash, and the 4 MiB
image read with `0x23` cannot contain it. The dumps agree: the image is `FF` at
`0x000940`-`0x000A2F`, and two reads of `0x000000`-`0x00FFFF` and of `0x140000`-`0x14FFFF` are
identical.

### 12.2 Why `0x23` reaches flash only

The customer `0x23` handler is `FUN_00191C58`. It accepts only a 5-byte request (`23`, a 3-byte
address and a 1-byte size; the request length at `0x400064C6` must be 5) and refuses size 0 or
`0xFF` with NRC `0x53`. It reads from this address:

```text
(byte at 0x400064A9) << 24  |  the 3 address bytes of the request
```

`0x400064A9` is 13 bytes past the start of the 8-byte request frame at `0x4000649C`. Nothing in
the image writes it: it has no reference in the decompiled code, and the only access in the
listing is the load in this handler (`lbz r4, 0xc5(r13)` at `0x191D78`). It is in `.bss`, so the
start-up code zeroes it. The address therefore always lies in `0x00000000`-`0x00FFFFFF`. There is
no range check, so the MMU decides what answers. The application's TLB set-up
(`0x180480`-`0x180960`) maps the flash at `0x000000`-`0x3FFFFF` in several entries. It probably maps
nothing at `0x400000`-`0xFFFFFF`, which fits the silence above `0x400000`. This was read from the
TLB code, not tested. SRAM is mapped only at `0x40000000`.

### 12.3 The CCP slave on CCP-CAN

The command table at `0x18CAC8` (section 2) is a CCP 2.1 slave. Its receive function
(`FUN_0018A110`) reads mailbox slot 3, which is CAN id `0x6C0` on FlexCAN C (CCP-CAN, section 4.2).
It answers on `0x6C2`. The frames follow CCP: a reply starts `FF`, then the return code, then the
command counter. The slots match the CCP command codes:

| code | CCP command | handler | code | CCP command | handler |
|---|---|---|---|---|---|
| `01` | CONNECT | `0x18B820` | `0F` | SHORT_UP | `0x18BAD0` |
| `02` | SET_MTA | `0x18C5D0` | `10` | CLEAR_MEMORY | `0x18BBB4` |
| `03` | DNLOAD | `0x18BA1C` | `11` | SELECT_CAL_PAGE | `0x18AC2C` |
| `04` | UPLOAD | `0x18BAD0` | `12`, `13` | GET_SEED, UNLOCK | refused |
| `05` | TEST | `0x18B9C4` | `14`-`16` | GET_DAQ_SIZE, SET_DAQ_PTR, WRITE_DAQ | `0x18B254`-`0x18B380` |
| `06` | START_STOP | `0x18AFF4` | `17` | EXCHANGE_ID | `0x18B750` |
| `07` | DISCONNECT | `0x18B8F8` | `18`, `22` | PROGRAM, PROGRAM_6 | refused |
| `08` | START_STOP_ALL | `0x18B15C` | `19` | MOVE | `0x18BFC0` |
| `09` | GET_ACTIVE_CAL_PAGE | `0x18ABC4` | `1B` | GET_CCP_VERSION | `0x18B7C0` |
| `0C`, `0D` | SET_S_STATUS, GET_S_STATUS | `0x18C14C`, `0x18C200` | `20`, `21` | DIAG_SERVICE, ACTION_SERVICE | `0x18C264`, `0x18C32C` |
| `0E` | BUILD_CHKSUM | `0x18AE4C` | `23` | DNLOAD_6 | `0x18BA1C` |

This corrects `NOTES.md` beside the image, which read the table as KWP2000 service ids.

CONNECT accepts station address `0x7234` or `0`. GET_SEED and UNLOCK are refused, so this build has
no CCP resource protection. SET_MTA (`FUN_0018AAF0`, `FUN_0018AA40`) accepts SRAM
`0x40000000`-`0x4003DFFF`, which an UPLOAD copies straight from memory, and the flash calibration
area `0x010000`-`0x05FFFF`. So on CCP-CAN a CCP master could read every schedule address in 12.1.

The OBD port does not reach CCP-CAN. The bus leaves the EPCU at its connector, and uhi22 found only
the VCU and the MCU on it (section 4.1). Using it needs a tap on the EPCU harness. CCP also writes
(DNLOAD, DNLOAD_6, CLEAR_MEMORY) and starts DAQ lists. The VCU accepts these without a key. Do not
use it on a car that is driven or charging; use uhi22's bench set-up instead. canair has no CCP
client.

### 12.4 What remains

- The schedule is readable from the OBD port only as the broadcast frames `0x579` and `0x57A`
  (sections 9.2 and 9.3), and those are on P-CAN behind the gateway. A P-CAN tap or a bench EPCU is
  the next step.
- Whether a bench EPCU keeps the standby RAM (and so the schedule) with the battery feed alone was
  not tested.
