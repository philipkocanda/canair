---
# Paths are relative to the repository root. The firmware image is local-only (never committed):
# it holds the manufacturer's software. See "Provenance".
title: "VCU firmware findings: the whole ECU — diagnostics, CAN, drive, power, LDC and runtime"
subject: >-
  The 2017 Ioniq Electric's VCU (Vehicle Control Unit, KWP2000 0x7E2), read from a byte-exact
  flash image with ReadMemoryByAddress and analysed statically: the customer diagnostic server and
  its session matrix, the 0x21/0x1A/OBD-II records, IOControl and routines, the DTC manager, the
  CAN mailbox map and every frame's use, the scheduling, climate and 12 V top-up models, the
  torque path to the motor controller, the power states, the integrated 12 V DC-DC converter
  (LDC), the hard-wired I/O, the platform and its runtime, and the requests that must never be
  sent
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
  machines are NOT named; claims about them are tagged. The fourth pass (§14-26) is static only:
  the image checksum reproduces, the LDC DTC thresholds match the service manual, and the creep
  and torque frames match uhi22's P-CAN trace; nothing in it was tried on the car.
sections:
  - section: "3"
    status: open
    date: 2026-11-08
    note: >-
      The 0x1A DID map, confirmed live. 8C/8D/8E/91/90(VIN) match ecus/vcu.yaml; the other readable
      records (80, 8A, 8B, 92, 98, 99, FF) are not defined yet. The read-flag-2 slots (84/86/87/88/
      94) are not 0x1A-readable (NRC 0x12 live). 1A80 embeds the VIN (pass 4).
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "2"
    status: open
    date: 2026-11-08
    note: >-
      Corrected in pass 4: sessions 85, 90 and F1 open without SecurityAccess, and session 90
      opens 0x30, 0x31, 0x11 and 0x28/0x29. The vcu.yaml header ("Only KWP2000 session 0x81 is
      accepted") and the sessions block still say otherwise; 85/90/F1 were never tried live.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "4"
    status: integrated
    date: 2026-11-08
    note: >-
      The CAN layer: three FlexCAN controllers, two used (P-CAN and the EPCU's CCP-CAN), 98
      mailbox slots, the RAM buffer and unpack/pack function of every frame. Bus assignment matches
      uhi22's EPCU transceiver test exactly. CCP-CAN is deliberately not in can_buses.yaml.
  - section: "5"
    status: open
    date: 2026-11-08
    note: >-
      Preconditioning/charge-timer is a CAN path (0x587/0x567/0x55C/0x588 in, 0x579-0x57B out) run
      by a scheduling model, with the VCU's own SPI real-time clock as time base and wake alarm. The
      state machines are not named.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "9"
    status: integrated
    date: 2026-11-08
    note: >-
      Frame layouts as the VCU reads and writes them. 0x567, 0x549 and most received frames
      matched signals/. The 0x55C/0x57A and 0x588/0x579 definitions in signals/ were replaced with
      the VCU's layouts on 2026-11-08; the smaller differences in section 9.6 are not applied.
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
  - section: "13"
    status: integrated
    date: 2026-11-08
    note: >-
      Drive mode and regen level (broadcast only), the paddle bits on 2101, 2102 as a copy of
      0x590 (B14 renamed VCU_LDC_OUTPUT_VOLTAGE_COARSE, now B14/10), and the 21F2 supply voltage
      and drive-mode/regen time shares. The 2101 drive-mode entry is done; the 21F2 entry is
      captured.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "18"
    status: open
    date: 2026-11-08
    note: >-
      The integrated LDC. 2102 B9/B10 are LDC states (not HV/relay states), B12/B13 are the LDC
      fault warning and output-limited bits, VCU_COOLANT_TEMP is the LDC's own temperature, and
      the LDC current is measured but never published (the "LDC output current" research entry's
      result). Not applied to vcu.yaml yet.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "21"
    status: open
    date: 2026-11-08
    note: >-
      The 45-code DTC table: P1A8A, U1005, U0103 and P1676/P1690/P1696/P1699 are missing from
      vcu.yaml; the U-code frame lists, LDC thresholds and the 12 00 00 freeze-frame layout are
      not recorded there yet.
    target: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
  - section: "19"
    status: open
    date: 2026-11-08
    note: >-
      The drivetrain frames (0x109, 0x200-0x202, 0x291, 0x10C, 0x153) and the 2101 bits named in
      sections 7.1 and 19.4; candidates for signals/powertrain.yaml and vcu.yaml.
    target: profiles/hyundai-ioniq-ae-ev/signals/
  - section: "23"
    status: open
    date: 2026-11-08
    note: >-
      KWP 31 F1 EE FF 00 11 (hangs the VCU, no READY gate) and 31 F3 CC DD 33 44 (silences every
      periodic frame) pass canair's guard after 10 90. Proposed: add F1 and F3 to
      BLOCKED_KWP_ROUTINES.
    target: src/canair/safety/routines.py
relations:
  expands_on:
    - path: profiles/hyundai-ioniq-ae-ev/ecus/vcu.yaml
      note: The identity block, the 2101/2102/21F2 records, and the pending service-ID entry.
    - path: plans/2026-11-05-read-memory-by-address.md
      note: The memory-read work that produced the image analysed here (KWP2000 results).
  related:
    - path: profiles/hyundai-ioniq-ae-ev/research/OBC_FIRMWARE_FINDINGS.md
      note: >-
        The OBC's firmware: the same bootloader and server code base. Its session matrix, OBD-II,
        DTC and freeze-frame sections were the template for sections 2 and 21 here.
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
  - name: 2017 Ioniq US service and wiring manuals
    kind: DTC thresholds and connector pinouts, used to check sections 18 and 21
    location: references/manuals/Ioniq_2017_US/ (local only)
---

# VCU firmware findings: the whole ECU

This collects what a static read of the VCU's flash image shows, cross-checked against this car's
captures. Sections 2-13 hold the first three passes (the diagnostic interface, the CAN map, the
scheduling, climate and 12 V top-up models). Sections 14-26 hold the fourth pass, which maps the
rest of the ECU: the platform and runtime, the hard-wired I/O, the integrated LDC, the torque
path, the power states, the DTC manager and every received frame. Section 23 lists the requests
that must never be sent, and sections 24-25 the traps and opportunities for later work. The image
itself stays on the machine (it is the manufacturer's software); only facts derived from it are
here.

## 1. Provenance and how much to trust it

The image was read on 2026-11-07 with `canair memory dump` over KWP2000 ReadMemoryByAddress
(`0x23`), `0x000000`-`0x3FFFFF`, while the car was AC charging. See the memory-read plan for the
read method and the "As built" results. The image is a 32-bit PowerPC program for an MPC55xx-family
microcontroller: an e200 (Book E) core, SRAM at `0x40000000`, and the MPC55xx peripheral map. The
CAN layer drives FlexCAN controllers at `0xFFFC0000`, `0xFFFC4000` and `0xFFFC8000` (message
buffers from offset `0x80`, 16 bytes each), and the real-time clock sits on the DSPI at
`0xFFF98000` (section 10). The exact part number was not determined; pass 4 makes it probably an
MPC5674F (section 14.1).

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

The switch (corrected in pass 4: the first pass misread its case labels) dispatches:

| SID | service | handler | SID | service | handler |
|---|---|---|---|---|---|
| `01` | OBD-II mode 01 | `FUN_0018D9D4` | `1A` | ReadEcuIdentification | `FUN_001902DC` |
| `02` | OBD-II mode 02 | `FUN_0018DEA8` | `20` | StopDiagnosticSession | `FUN_00191B60` |
| `03` | OBD-II mode 03 | `FUN_0018E8FC` | `21` | ReadDataByLocalIdentifier | `FUN_0019183C` |
| `04`, `14` | ClearDiagnosticInformation | `FUN_0018FFCC` | `23` | ReadMemoryByAddress | `FUN_00191C58` |
| `07` | OBD-II mode 07 | `FUN_0018EBA8` | `27` | SecurityAccess | `FUN_0018FAB8` (not read) |
| `09` | OBD-II mode 09 | `FUN_0018F100` | `28`, `29` | Disable/EnableNormalMessageTransmission | `FUN_001915D0`, `FUN_00191708` |
| `0A` | OBD-II mode 0A | `FUN_0018EE54` | `30` | InputOutputControlByLocalIdentifier | `FUN_001919A0` → `FUN_0020451C` |
| `10` | StartDiagnosticSession | `FUN_0018F7F4` | `31` | StartRoutineByLocalIdentifier | `FUN_00192564` |
| `11` | ECUReset | `FUN_0018FE88` | `3B` | WriteDataByLocalIdentifier | `FUN_00191F60` |
| `12` | ReadFreezeFrameData | `FUN_00191358` | `3E` | TesterPresent | `FUN_0018F97C` |
| `17` | ReadStatusOfDTC | `FUN_00190574` | `99` | (manufacturer) | `FUN_0018D220`: erases the VIN in the EEPROM |
| `18` | ReadDTCByStatus | `FUN_00190AE8` | | | |

`0x22`, `0x2C`, `0x2E` and `0x3D` have one-line handlers that always answer NRC `0x12`.
`0x34`/`0x36`/`0x37` are empty cases with no reply. An unknown SID below `0x10` gets no reply;
any other gets NRC `0x12`. The OBD-II modes are a stub (section 21.1).

**The server also answers on the functional id `0x7DF`.** `FUN_0018CDB8` reads mailbox slot 0
(`0x7DF`), then slot 1 (`0x7E2`), into the same request buffer. So every service in the table,
`10 90`, `0x30` and `0x31` included, answers a functional request exactly as a physical one.

### 2.1 Session and security gating (the permission matrix)

`FUN_00192E7C` reads the matrix at `0x193C70`: 5 bytes per row, the SID, then one byte for each
of the sessions `81`, `85`, `90` and `F1`. `02` allows the service. Any other value allows it
only when it equals the security state `0x400064B3` (set by `0x27`), else NRC `0x80`. So `01`
means "with SecurityAccess" and `03` means "never".

| SID | `81` | `85` | `90` | `F1` |
|---|---|---|---|---|
| `10` StartDiagnosticSession | yes | yes | yes | yes |
| `20` StopDiagnosticSession | yes | yes | yes | no |
| `3E` TesterPresent | **no** | yes | yes | no |
| `27` SecurityAccess | no | yes | yes | yes |
| `11` ECUReset | no | secured | **yes** | no |
| `1A`, `21`, `23` | yes | yes | yes | no |
| `28`, `29` | no | **yes** | **yes** | no |
| `3D` (stub) | secured | no | secured | no |
| `2C` (stub) | yes | no | yes | no |
| `3B` WriteDataByLocalIdentifier | no | secured | secured | no |
| `22`, `2E` (stubs) | no | no | no | no |
| `34`, `36`, `37` (empty) | no | secured | no | no |
| `12`, `14`, `17`, `18` | yes | no | yes | no |
| `31` StartRoutine | no | secured | **yes** | secured |
| `30` IOControl | no | no | **yes** | no |
| `99` | no | secured | secured | no |

What this means (corrected in pass 4; the first reading said every session above `81` needs a
key):

- **`10 85`, `10 90` and `10 F1` need no SecurityAccess.** `FUN_0018F7F4` switches to the
  session as soon as the request names it. The live 2026-11-08 probe tried only `81`, `82` and
  `83`; `85`, `90` and `F1` were not tried. canair hard-blocks `10 85`, and the profile's
  `hk_90_session` quirk lets `10 90` through.
- **In session `90`, `0x30` IOControl, `0x31` routines, `0x11` ECUReset and `0x28`/`0x29` need no
  key.** Section 3 has what they do and section 23 the requests that must never be sent.
- **TesterPresent is refused in `81`** (NRC `0x80`). `3E 00` answers `7E 00` in `85`/`90`;
  `3E 80` is silent.
- **Session timeout.** `FUN_00193150` drops back to `81` (and clears the security state and the
  `0x28` flag) after 500 server cycles in `90`/`F1`, or 6,000 in `85`. The server runs every
  10 ms (section 15), so that is 5 s and 60 s. **Any** request restarts the count
  (`FUN_00192EFC`), not only `3E`.
- **`10 90` latches `0x4000496A` = 1 until a reset.** Its only reader copies it to `0x400056DE`,
  and the regen-level logic (`FUN_001B744C`) then forces level 0 while the odometer word
  `0x40004B80 >> 8` is below 2: probably a factory mode, with no effect on a car with real
  mileage (probable).

SecurityAccess (`0x27`) itself is a seed/key exchange. The seed and key computation was not
analysed and must not be (project rule); only the presence of the handler is recorded here.

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

`FUN_0020451C` handles `30 <LID> <param> [<value>]` (single frame). It is open in session `90`
with no key (section 2.1). Corrected in pass 4: the clock test is LID `09`, and `07` is the
control-parameter byte (shortTermAdjustment), not a LID.

- **Support bitmap.** `30 00 01` answers `70 00 01 1C 81 F0 00` (`0x204E73`): LIDs `04`, `05`,
  `06`, `09`, `10`, `11`, `12`, `13`, `14`, exactly the implemented ones. `30 20|40|…|E0 01`
  answer an all-zero map. Other LIDs get NRC `0x12`; while a pending job runs (`0x400064A6`)
  every `0x30` gets NRC `0x22`.
- Gates below: `B0D` = `0x40004B0D` (the EV-ready flag, 2101 `B11:3`), `WAKE` = `0x400065B6`.

| LID | request | gate | effect |
|---|---|---|---|
| `04` | `30 04 07 FF` / `07 00` / `30 04 00` | `B0D` = 1 | writes `0x40009DEF`; nothing reads it |
| `05` | `30 05 07` | `B0D` = 0, `WAKE` | sets `0x40006110`, which blocks the ECU state manager's power-down: **the VCU stays awake until a reset** (no return-control path). It also switches the range estimator off its learned table |
| `06` | `30 06 07 01`, then `02` | enable `0x40009DDD` | end-of-line two-point calibration of the LDC input-voltage channel (AN0). Gain and offset go to standby RAM `0x40001A1C`/`0x40001A20` with the marker `0xABCD` at `0x40001A18` |
| `11` | `30 11 07 01`, then `02` | enable `0x40009DE8` | the same for the LDC output-voltage channel (AN2), `0x40001A24`-`0x40001A2C`; applied only if cal `0x306D8` = 1.0 |
| `12` | `30 12 07 01`, then `02` | enable `0x40009DE9` | the same for the LDC controller input `0x400061DC`, `0x40001A30`-`0x40001A38` |
| `09` | `30 09 07` / `30 09 00` | `B0D` = 0, `WAKE`, clock healthy | arms or clears the clock's countdown wake test (section 10.2); it cancels itself after about 10 min |
| `10` | `30 10 07` | `B0D` = 0, `WAKE` | forces digital-output channel `0x17` on while not READY (normally it follows the shifter-fault state); the pin is not known |
| `13`, `14` | `30 13 07 FF`, `30 14 07 FF` | none | answer NRC `0x78` and end after 400 cycles; no consumer found |

So the hardware-affecting LIDs are `05`, `06`/`11`/`12` (a bad reference value biases the LDC's
own measurements until the marker is cleared), `09` and `10`. Every write uses a per-LID state
byte at `0x40009DEB + LID`.

### 3.3 Service 0x31 StartRoutineByLocalIdentifier — the routines

Corrected in pass 4: `FUN_00204D5C` is a per-SID precondition check, not a list of routine ids.
For SIDs `0x11` and `0x31` it needs `0x40004BDD` = 1 (set from a hard-wired input, probably
ignition) and `0x40004B0D` = 0 (not EV-ready); `0x14` and `0x3B` always pass, mode `04` never
does. `FUN_00192564` (session `90`, no key) accepts four requests:

| request | length | precondition | effect |
|---|---|---|---|
| `31 00 xx`, `31 01 xx` | 3 | `FUN_00204D5C(0x31)` | `FUN_0018CB64`: interrupts off, then the bootloader main loop `FUN_00004048`. **The VCU leaves its application and waits for a reflash**, as the OBC does |
| `31 F1 EE FF 00 11` | 6 | none | replies, then `FUN_0018CB94`: interrupts off and the CCP-CAN RAM loader `FUN_0019C9F8` (section 22.4). It busy-waits for CAN id `0x011` on CCP-CAN, which the OBD port does not reach, and never services the watchdog. **The VCU hangs, then probably resets on its watchdog**, even while READY |
| `31 F3 CC DD 33 44` | 6 | none | replies and sets `0x40004954`. The KWP task then runs the test sequence `FUN_0019BFF0` (`FUN_0018CBC8`). When it completes it latches `0x400067F0`, which **stops every periodic VCU frame until a reset** (section 4.4) |

Any other request gets NRC `0x12`. Section 23 checks these against canair's guard.

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
| `1A80` | 97 | masked (canair treats it as sensitive: it embeds the VIN, see below) | not in profile |
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
reads: `1A84` (1 byte), `1A86` (1), `1A87` (4), `1A88` (2), `1A94` (10). Their RAM (and `1A92`'s)
is in `.bss` and nothing writes it, so all of them, and `1A92`, always read zero.

**`1A80` embeds the VIN.** `FUN_001901E4` rebuilds it on every request from the records listed at
`0x205004` (count 10): `8A`, `8B`, `90` (VIN), `91`, `98`, `99`, `8C`, `8D`, `8E`, `92`, which is
10 + 4 + 17 + 12 + 10 + 4 + 8 + 16 + 8 + 8 = 97 bytes. The VIN is data bytes 14-30, so `1A80`
must stay `sensitive`.

Only `1A90` (VIN) and `1AFF` are writable through WriteDataByLocalIdentifier (`0x3B`), both gated
to a secured session; everything else is read-only. Neither is a schedule. Without a key, VCU
state changes over diagnostics only through session `90`'s IOControl, routines and ECUReset
(sections 3.2, 3.3) and through `0x14`.

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
`can_buses.yaml` does not list this bus.

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
  diagnostic task (`FUN_0019BFF0`) sets it when it completes. Routine `31 F3 CC DD 33 44` starts
  that sequence (section 3.3); the calibration `0x2D5F0` would start it at power-up, but it is
  `00` in this build.
- `1AFF` = 1 does more than relay frames: it skips the whole LDC/12 V application layer (the
  1 ms, 10 ms and 100 ms groups `FUN_0021A980`, `FUN_0021AA0C`, `FUN_0021AA78`), the LDC trip
  interrupts and the 12 V top-up, and takes the pedal, brake and analog inputs from CCP-CAN `0x300`
  instead of the pins (sections 15 and 17).

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
- **Done 2026-11-08:** `signals/` now carries the VCU's `0x55C`/`0x57A` and `0x588`/`0x579`
  layouts (sections 9.2 and 9.3), with the same signal names.
- **Not applied, by the owner's choice:** a CCP-CAN entry in `can_buses.yaml` (section 4.1).
- **Done 2026-11-08:** the scheduled-wake entry in `ecus/vcu.yaml` and the top-up entry in
  `ecus/igpm.yaml` carry sections 10.2 and 11 as their results; both stay pending until the car
  shows that the clock's alarm powers the EPCU. `signals/` has the `0x590` top-up bits, the
  `0x51B` Aux. Battery Saver+ setting and its `0x57A` echo, and the `TOPUP` state description
  names the 24 h check.
- **The `0x57B` IG3-relay-mode entry** in `ecus/vcu.yaml`: on this VCU, byte 2 bits 4-7 are four
  separate flags from two subsystems (section 9.4), not one 4-bit mode.
- **Pass 4 adds a longer list** (section 25): the 2102 LDC-state renames, the 2101 bit names, seven
  DTCs, the LDC-current result, the freeze-frame layout and about twenty broadcast signals.

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
  **bit 4 is set when that state reads `7`**, the shifter-fault state (section 19.4), and bit 5 is
  the valid flag set separately by `FUN_00204480`.
- `B11` vehicle-state flags (D6): bit0 brake-lamp, bit1 not-braking, **bit2** = DIO 5, probably
  the smart key's start signal (section 20.1), bit3 EV-ready, bit4 IG3, bit6 the "HV up,
  start-ready" flag of power state 2 (section 20.1), bit7 the LDC-off request (1 = off; the
  profile's `VEHICLE_STATE_LDC_ENABLED` inverts it, section 18.3). **Bit5 is never written (always
  0, unused).**
- `B12/B13` (D7/D8) APS1, `B14/B15` (D9/D10) APS2, `B17/B18` (D11/D12) accelerator pedal,
  `B19/B20` (D13/D14) speed, `B21/B22` (D15/D16) 12 V supply — all 16-bit little-endian, matching
  the profile's (previously unverified) signals. The firmware confirms the width and byte order.
  The speed comes from motor rpm with a 2.0 m tyre (section 19.5).
- `B23` (D17): bits 0/1 the regen paddles (section 13.1), bit2 the hard-wired ignition input
  (section 20.1; the profile calls it inverter-enabled), bits 3-6 bits 0-3 of `0x40005888` (from
  the lever decode, not named; the profile's "armed" bits 4/5 are two of them); bit7 unused.
- `B25` (D18) is a whole byte = `VCU_POWER_STATE` (states in section 20.1). `B26` (D19) packs the
  park request and the shift-by-wire unit's state (section 19.4).

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
  relay is closed, and clears only in a deeper HV-off state. Pass 4 shows both are LDC states
  (section 18.3): `B10` is "LDC running" and `B9` is "LDC in state 3 or above", which explains the
  nesting.
- **`B11`, `B12` and `B13` read 0 in all 14,657 captures.** They are real status bits that are
  inactive on this car in the recorded states, not undecoded bytes, so they should not be counted
  as coverage gaps. Pass 4: `B11` is always 0 in this build, `B12` is the LDC fault warning and
  `B13` is "LDC output limited" (section 18.6).
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

| bits | field | VCU variable (in / out) | head-unit layout used before |
|---|---|---|---|
| 0\|5 | slot 1 hour | `554A` / `6128` | same |
| 5\|3 | slot 1 minute, ×10 | `554B` / `6129` | same |
| 8\|7 | slot 1 weekday mask, bit 0 Monday … bit 6 Sunday | `5519` / `612A` | same |
| 15\|1 | slot 1 enabled | `551E` / `6137` | same |
| 16\|6 | slot 1 cabin temperature: °C = raw × 0.5 + 15 | `5556` / `6138` | differed (`SET2_HOUR`/`SET2_MIN`) |
| 22\|1 | slot 1 temperature unit (inferred) | `5521` / `6139` | differed |
| 23\|1 | slot 1 defrost | `551A` / `613A` | differed |
| 24\|1 | slot 1 precondition (inferred) | `551B` / `6127` | differed |
| 25\|5 | slot 2 hour | `554C` / `612C` | differed |
| 30\|3 | slot 2 minute, ×10 | `554D` / `612D` | differed |
| 33\|7 | slot 2 weekday mask | `5525` / `612E` | differed |
| 40\|1 | slot 2 enabled | `552A` / `613B` | differed (`SET1_DEFROST`) |
| 41\|6 | slot 2 cabin temperature | `5557` / `613C` | differed |
| 47\|1 | slot 2 temperature unit (inferred) | `552D` / `613D` | differed |
| 48\|1 | slot 2 defrost | `5526` / `613E` | differed |
| 49\|1 | slot 2 precondition (inferred) | `5527` / `612B` | differed |
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
off. The IONIQ_AVN_FW_FINDINGS notes say preconditioning was off in that car. The head-unit
layout read the same frame as slot 2 at 16:40 Tuesday-Friday with temperatures 63 and 7, which
those notes could not explain.

### 9.3 `0x588` charge mode and off-peak windows (gateway) and `0x579` (the VCU's copy)

Unpacked by `FUN_001C5000`, packed by `FUN_001F2054`. Both frames use the same bits 0-33. The VCU
puts the charge mode **first** and every window 2 bits later than `signals/` does.

| bits | field | VCU variable (in / out) | head-unit layout used before |
|---|---|---|---|
| 0\|2 | charge mode (0 = Charge Now) | `550F` / `6126` | differed (at 32\|2) |
| 2\|5 | weekday window start hour | `5552` / `612F` | differed (at 0\|5) |
| 7\|3 | weekday window start minute, ×10 | `5554` / `6130` | differed |
| 10\|5 | weekday window end hour | `554E` / `6131` | differed |
| 15\|3 | weekday window end minute, ×10 | `5550` / `6132` | differed |
| 18\|5 | weekend window start hour | `5553` / `6133` | differed |
| 23\|3 | weekend window start minute, ×10 | `5555` / `6134` | differed |
| 26\|5 | weekend window end hour | `554F` / `6135` | differed |
| 31\|3 | weekend window end minute, ×10 | `5551` / `6136` | differed |
| 34\|2, 36\|2, 38\|2 | `0x579` only: other VCU state | — / `53AD`, `53B1`, `53B8` | — |
| 53\|2, 55\|1 | `0x579` only | — / `4F99`, `4F9A` | — |
| 56\|8 | `0x579` only (reads `FE` in the trace) | — / `53AF` | — |

The order of the four windows is confirmed by the off-peak subsystem (`0x207000`). It converts each
pair to minutes (hour × 60 + minute), repeats the first window for five weekdays and the second for
the weekend, and handles windows that cross midnight. `0x57B` bits 1-2 also follow the mode: they
read 1 only while the effective mode is 0 (a table at `0x1A8822`).

Decoded with this layout, uhi22's frame reads: Charge Now, weekday window 09:30-18:00, weekend
window 09:30-18:00. The head-unit layout read 04:50-09:20 for both.

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
  Otherwise it keeps a stored copy (`0x400054D6`-`0x400054FF`). That copy is ordinary RAM; the
  persistent copy is the one `FUN_001D5AF8` writes to standby RAM (section 12.1), which the
  EEPROM backs (section 14.4). A cleared slot reads hour 31 and minute 7 (all ones). It flags
  every change and writes the active schedule to `0x40006126`-`0x4000613E`, which `0x579` and
  `0x57A` send.
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

Pass 4 narrows this (section 17.5). The CPU takes no interrupt from the clock: every external IRQ
input is disabled, and the probable clock `/INT` line (pad 150) is only polled. At shutdown
(`FUN_00196330`) the VCU drops its power self-hold output and then polls the wake-line ADC channel;
it restarts only when that line rises again. So the alarm can wake the EPCU only through the
hardware: its `/INT` pin wired into the regulator enable or the wake line. A trace from the clock's
`INT` pin on the board is the check.

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
   `2101` `B11:7`). Once the VCU sees the LDC running (`0x590` byte 0 bit 2 = `2102`
   `VCU_HV_MAIN_RELAY_ON`, which pass 4 showed is the LDC's run state, section 18.3), it enters
   the running state.
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
image offset `0x20`), and `.data` starts there too, so this low area survives a restart: it is the
standby RAM. Pass 4 found that an 8 KiB SPI EEPROM backs it byte for byte (RAM offset = EEPROM
address), written back at every shutdown (section 14.4), so the schedule also survives a 12 V
disconnect (probable). The `0x23` server cannot read the EEPROM either. No diagnostic record
overlaps any of these addresses. `21F2` is `0x4000005A`-`0x400000AD`, and the `0x1A` records are
at the addresses in section 3.5.

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
- Whether a bench EPCU keeps the schedule with the battery feed alone was not tested. Pass 4 makes
  that likely even without the feed: the EEPROM backs the standby RAM (section 14.4).

## 13. Third pass: drive mode, regen level, the LDC frame and 21F2

### 13.1 Drive mode and regen level are broadcast only

The research entry on drive mode and regen level (`ecus/vcu.yaml`, target `2101`) found no
diagnostic byte that steps with them. The firmware explains why: neither is on 2101, 2102 or 21F2
as a live value.

- **Drive mode** is an index 0-2 at `0x40004F99`, kept across sleep. `FUN_001B7FE8` steps it on
  each rising edge of the cluster's drive-mode button (`0x578` `CF_Clu_DriveModeSW`, bit 47); with
  calibration `0x133E2` = 0 the order is 0 → 1 → 2 → 0. It is sent in **`0x579` bits 53-54**.
  Which index is Eco, Normal or Sport is not established; 13.4 and 19.2 have leads (index 2 is
  Sport, probable; 1 Normal and 0 Eco, inferred).
- **Regen level** is 0-3 at `0x40004F9D` (`FUN_001B744C`). Each drive mode starts from its own
  setting (`0x40004F99` selects one of `0x400053AD`/`53B1`/`53B8`, which `0x579` also carries at
  bits 34-39). Those defaults are user settings that arrive in `0x5B3` (section 22.2). A rising
  edge of a paddle switch steps the level down or up by one, in D only. It is
  sent in **`0x202` bits 20-23**. uhi22's logs read level 2 and mode 0 throughout.
- **The paddle switches** are on 2101: `B23:0` lowers the level, `B23:1` raises it (the 2101
  producer copies `0x40004BE1` and `0x40004BE0`). uhi22 wires the right paddle, "decrease
  recuperation", to EPCU pin 3H, and the left to 3J.

Defined: `signals/powertrain.yaml` `VCU_REGEN_LEVEL` (`0x202`) and `VCU_DRIVE_MODE` (`0x579`);
2101 `VCU_REGEN_PADDLE_MINUS`/`_PLUS`.

### 13.2 2102 is a diagnostic copy of the LDC frame `0x590`

`FUN_00219F0C` packs P-CAN `0x590` (and CCP-CAN `0x58F`) from LDC and 12 V values, byte-reversed
from a RAM buffer at `0x40006F8A`. `FUN_0021AD44` builds 2102 from that same buffer:

| 2102 | `0x590` | content |
|---|---|---|
| `B9`, `B10`, `B13` | byte 0 bits 1, 2, 3 | LDC state ≥ 3, LDC running, LDC output limited (pass 4, section 18.6; the profile names the first two HV available and HV main relay on) |
| `B11`, `B12` | byte 1 bits 0, 1 | `B11` always 0 in this build; `B12` the LDC fault warning (both 0 in every capture) |
| `B14` | byte 4 | 12 V rail at the LDC, 0.1 V: the same value as `B19`/`B20` rounded |
| `B15` | byte 2 | LDC output power, 10 W |
| `B17` | CCP-CAN `0x58F` byte 5 | DC-link voltage / 2 |
| `B18` | byte 7 | the LDC's own temperature + 100 (255 = its sensor is invalid; pass 4) |

`0x590` byte 0 bits 6 and 7 are the 12 V charge request and done bits (section 11.2). Byte 6
(`0x40006DC8`) is the LDC's HV input power in 10 W, not a loss estimate (pass 4, section 18.6).

The check against 14,657 stored 2102 replies holds: `B14/10` minus `[B19:B20]/1000` has median
0.00 V and standard deviation 0.03 V. The profile's `B14 × 0.0974` was fitted to the battery
voltage, about 0.08 V lower. The signal is now `VCU_LDC_OUTPUT_VOLTAGE_COARSE` with `B14/10`.
`signals/powertrain.yaml` has the `0x590` twins: `VCU_AUX_POWER_590`,
`VCU_LDC_OUTPUT_VOLTAGE_590`, `VCU_COOLANT_TEMP_590`.

### 13.3 `0x55F`/`0x561` feed a route-based range estimate

Each frame carries five pairs of a 4-bit and an 8-bit value (section 4.2). `FUN_001EE674` scales
each 8-bit value by a calibration factor and divides it by a per-segment consumption term
(`0x40006BB4`-`0x40006BC2`). This fits the head-unit findings' guess that the frames are
navigation road segments: road type and distance, ten segments in all, turned into an energy
figure for the range estimate. The names of the inputs were not traced further.

### 13.4 21F2: supply voltage and the drive-mode/regen time shares

21F2 is built once, at power-off (section 10.2), by `FUN_00204028`:

- `B10`/`B11` is the 12 V supply word, `/2072` as in 2101: 11.9-13.1 V in the stored replies.
- `B12`/`B13` is the BMS SoC × 256, and `B14`-`B18` the odometer × 256, as the profile has.
- **`B45`-`B71` are twelve 16-bit shares of the cycle's time per drive mode and regen level**
  (`FUN_001B7C68` counts each combination; the producer divides by the total). They sum to 97-100
  in every stored reply. Order: mode 1 levels 0-3 (`B45`, `B47`/`B49`, `B50`, `B52`), mode 0
  levels 0-3 (`B54`, `B57`, `B59`, `B61`), mode 2 levels 0-3 (`B63`, `B66`, `B68`, `B70`).
- On this car the time is in mode 1 at level 1 (mean 74 %), mode 0 at level 2 (20 %) or mode 1
  at level 0 (3 %), and never in mode 2. The owner's habitual mode is therefore index 1, which
  would name it.
- `B21`-`B37` (except the PCI bytes) copy range-estimation variables (`FUN_001E64B8`). `B38`/`B39`
  and `B41`-`B44` are a 16-bit and a 32-bit value whose writer was not found. `B73`/`B74` and
  `B75`/`B76` are outputs of scheduling subsystem `0x20A2D4`.

Defined: 21F2 `VCU_21F2_SUPPLY_VOLTAGE` and `VCU_21F2_SHARE_MODEm_REGENr`.

### 13.5 Still not traced

- How the VCU asks the BMS to close the main relay and the IGPM to close IG3, for a top-up or a
  remote climate session. Pass 4 named the power states (section 20.1) but found no contactor
  command (20.2).
- The charge-control state machine (`0x40006D2A`) and the names of the climate-request outputs.
- Pass 4 traced the DTC table (section 21) and the IOControl ids (section 3.2).

## 14. Fourth pass: the platform, the image and its persistence

Pass 4 set out to map the whole ECU: what it is responsible for and how it works inside. Sections
14-26 hold the results; sections 2, 3, 4.4, 10, 12 and 13 were corrected where it showed them
wrong. As before, nothing was tried on the car. Tags: **verified** (read from code or data),
**probable**, **inferred**.

### 14.1 The microcontroller: probably an MPC5674F

The peripheral set matches the NXP MPC5674F (e200z7, 4 MiB flash, 256 KiB SRAM) exactly
(probable, close to verified):

- the start-up code ECC-initialises SRAM up to `0x4003FFFF`, so there are 256 KiB of SRAM;
- separate instruction and data caches with a cache-locked stack, which means an e200z7;
- two eQADC (analog-to-digital) modules (`0xFFF80000`, `0xFFF84000`), two eDMA modules, three eSCI
  serial modules, DSPI B and C, the PIT/RTI timers and pad numbers up to 472;
- every timer reload assumes a 132 MHz peripheral clock (PIT 132,000 = 1 ms). With the FMPLL
  setting that gives a 264 MHz core (crystal inferred as 8 MHz).

The eTPU (`0xC3FC0000`) and FlexCAN D are not used. About 700 instructions are SPE float
operations, which capstone (`listing.txt`) shows as `.word` and the Ghidra `MPC8270` language does
not decode: check float arithmetic in a decompile against the raw words.

### 14.2 Image layout

| range | contents |
|---|---|
| `0x000000`-`0x0002CF` | BAM header (RCHW `0x005A`, reset vector `0x24`) and reset code; the first 4 KiB match the OBC byte for byte |
| `0x001000`-`0x003CFF` | boot block 1, tag `AEHHCU00`: a CAN boot manager (`0x1008`) and a CAN reflash loop |
| `0x004000`-`0x0065FF` | boot block 2: the KWP2000 bootloader, main loop `FUN_00004048` (entered by `31 00`/`31 01`) |
| `0x00FFF6`-`0x00FFFF` | ecu_id `AEVLDC53`, then the **app-valid word `C3 5A`** at `0xFFFE` |
| `0x010000`-`0x01007F` | identity header: dataset `AEO556E000DS01R5`, `C5210617` (1A8E), `EAEEO5L-NS7-D060` (1A8D), build string `AEEVVLDC_A0009182015 0915` at `0x1003C`, dates (1A8B/1A99), **checksum `0x366BADBF` at `0x10078`** |
| `0x010080`-`0x0306FF` | calibration (one dataset, ending in the tag `DS01R5` at `0x306F0`) |
| `0x140000`-`0x1451FF` | exception handlers, the interrupt vector table (`0x143000`, 13 of 480 entries used), start-up helpers |
| `0x180000`-`0x2208DB` | application code and constant tables |
| `0x2208DC`-`0x224CBF` | `.data` load image (copied to `0x40002000`) |

The boot manager (`0x1008`) polls FlexCAN A mailbox 0 up to 2,000 times after reset for the
8-byte frame `07 A3 21 33 E2 F7 B2 77`. If that frame arrives it stays in the CAN reflash loop;
otherwise it checks `0xFFFE` = `0xC35A` and starts the application. This is a bench reflash
entry: never send that frame to a car. The boot blocks are the same source as the OBC's,
rebuilt per ECU (about 15,000 bytes differ, mostly shifted code).

**The checksum proves the dump is byte-exact.** `FUN_001AB694` (from the ROM check
`FUN_001A9B94`) sums every byte of `0x4000`-`0x3FFFFF`, minus the 128-byte header, and compares
the result with `0x10078`. A recomputation over `vcu_flash_ff.bin` matches (verified). The same
algorithm does not reproduce the OBC's stored checksum, so the OBC dump or its range needs a
second look before it is trusted byte for byte. No separate CVN exists.

### 14.3 The calibration overlay (CCP SELECT_CAL_PAGE)

`FUN_00180770` maps the calibration `0x10000`-`0x4FFFF` as 16 TLB entries of 16 KiB. The idle
task copies a page into RAM (`0x40010000`+) in `0x200`-byte steps and then switches its TLB entry
to the copy, so a CCP master can tune calibration live. Only 11 RAM page addresses are valid
(table `0x180DD8`; the rest are code bytes), which covers `0x10000`-`0x3BFFF` and so the whole
calibration. The application TLB maps nothing at `0x400000`-`0xFFFFFF`, which confirms section
12.2: `0x23` can never reach SRAM.

### 14.4 Standby RAM and the SPI EEPROM

RAM `0x40000000`-`0x40001FFF` is the image of an 8 KiB SPI EEPROM (probably a 95640-class part)
on DSPI C chip select 0; the real-time clock is chip select 1 (verified driver, probable policy):

- **Driver.** `FUN_001993E4` reads (status `05`, then read `03` with a 16-bit address);
  `FUN_0019A364` queues writes, which the 10 ms diagnostic task drains (`FUN_0019A614`). An output
  (pad 191) releases the write protection around a write-enable (`06`).
- **Boot (`FUN_00199094`).** Keep the RAM image if its marker says warm reset or its byte sum over
  `0x40000000`-`0x400019FF` (stored at `0x40001FE3`) is valid. Otherwise reload it from the EEPROM
  if the EEPROM's status byte `0x1FE8` is "good", or load defaults.
- **Shutdown (`FUN_00196330` → `FUN_0019922C`).** Write the whole image back, then the marker.
- **So every standby variable persists through the EEPROM:** the schedule copy and wake times
  (section 12.1), drive mode, the Aux. Battery Saver+ setting, the `1AFF` mirror, the DTC records
  (section 21.2), the LDC event ring, the LDC sensor trims, and the VIN at `0x1FC0`. Shutdown,
  restore and failed-verify counters sit at `0x40001A00`-`0x40001A09`.

The low 8 KiB is also never ECC-initialised at reset, so its contents survive a warm reset
without the EEPROM.

### 14.5 Watchdog

The internal software watchdog (`0xFFF38000`) times out after 66,000,000 clocks (0.25-0.5 s,
depending on its clock) and resets the CPU. `FUN_001835B0` services it with a keyed sequence,
from the 10 ms state task, the EEPROM busy-waits and the shutdown loop. No external watchdog
toggle was found. Code that loops with interrupts off and no service, such as the CCP-CAN loader
(section 22.4), therefore ends in a watchdog reset (probable).

## 15. Fourth pass: the runtime

### 15.1 An OSEK-style kernel on a 1 ms tick

The application runs on a small priority-preemptive kernel (verified): 21 tasks run to
completion, 14 cyclic alarms on the PIT0 1 ms tick (`FUN_00195758`), task chaining, a CPU-load
meter (`0x40004972`, probably the free CPU percentage) and a task-switch trace ring at
`0x40008B80`. Task descriptors are at `0x1A7D58`; the API is `FUN_001958B4` (create task),
`FUN_00196168` (SetAlarm), `FUN_001955E8` (ActivateTask). **No CAN interrupt is used**: every
mailbox is polled, at 1 ms or 10 ms.

| prio | task | period | what it runs |
|---|---|---|---|
| `28` | `FUN_001815E8` | 1 ms | fast RX of `0x153`/`0x386`/`0x2C1`/`0x331`, the torque arbitration `FUN_001F50F0`, the `0x232` torque command to the MCU |
| `27`/`26` | `FUN_00181350` / `FUN_00181370` | chained, 1 ms | the LDC fast group `FUN_0021A980` (awake) or `FUN_0021A9B8` (after-run) |
| `25` | `FUN_00196B5C` | chained, 1 ms | a supervisor touching standby `0x4000004C`; role not known |
| `1D` | `FUN_00180E40` | 10 ms | **the ECU state manager** (15.2); activates the next three |
| `1C` | `FUN_00181394` | 10 ms | CAN supervision and timeouts `FUN_0019F9EC`, the RX copy `FUN_001A04DC` |
| `1B` | `FUN_001813E4` | 10 ms | the LDC 10 ms group `FUN_0021AA0C` |
| `1A`-`14` | `FUN_00181404`, `0x181440`-`0x18147C` | 10 ms, offsets 0-6 | the generated model steps: RX unpack `FUN_001D2770`, limits `FUN_001D9D28`, driver demand `FUN_001C592C`, power state `FUN_001AB95C` and the scheduling model `FUN_0020ED4C`, range estimation `FUN_001E6890`/`FUN_001E6490`, the TX packers and the 2101 producer |
| `13` | `FUN_00181488` | 10 ms (states 0, 1) | **diagnostics**: ISO-TP, the KWP server, the CCP slave, the clock, the ROM check, the EEPROM queue |
| `12` | `FUN_001A5D68` | 10 ms | TX `0x109`, `0x200`-`0x202` |
| `0F` | `FUN_001A64FC` | 20 ms | not traced |
| `09` | `FUN_001815A0` | 100 ms | the LDC/12 V group `FUN_0021AA78` (top-up decision `FUN_00218B90`) |
| `07` | `FUN_0018B5B0` | 100 ms | CCP DAQ event |
| `06` | `FUN_001A652C` | 100 ms | TX `0x590`, `0x58F`, `0x592`, `0x579`-`0x57B`, `0x549` |
| `00` | `FUN_0018160C` | idle | CCP background, calibration-page copy |

Interrupts in use: PIT0 (1 ms tick), PIT1 (100 µs: starts one ADC scan), PIT3 (0.5 ms,
`FUN_0019F138`, role not known), the eDMA completions of the ADC and DSPI B, the eMIOS input
captures and the three LDC trip inputs (section 17.3), and eSCI B (the LIN driver). The ADC scan
completion runs the LDC fast loop (`FUN_0021A95C`), so the converter's control clock is the 100 µs
ADC scan.

### 15.2 The ECU state manager (`FUN_00180E40`)

| state | meaning | what changes |
|---|---|---|
| 0 | init | starts every alarm on exit |
| 1 | awake | everything runs |
| 2 | after-run | cancels the 100 ms application alarm, later the 10 ms model alarms; the 1 ms chain switches to `FUN_00181370`; **the diagnostic task stops** |
| 3 | wake-again | saves and restarts (`FUN_0019EDF8`) |
| 4 | shutdown | `FUN_0019EDD4`: builds 21F2, programs the clock, writes the EEPROM image, drops the self-hold |

The wake input is ADC channel AN16 (`0x400065B6`, section 17.1). The after-run times come from
`0x2D614`/`0x2D618`. Two consequences for live work:

- **The VCU stops answering diagnostics in after-run**, because the diagnostic task runs only in
  states 0 and 1. A `2101` poll that goes silent while the 12 V rail is still up marks state 2.
- **Model outputs freeze in after-run.** A capture taken then does not show the live model.

## 16. Fourth pass: a responsibility map of the code

About 1,920 functions in `0x180000`-`0x2208DB`, grouped by what they touch and who calls them:

| range | ≈ fns | subsystem |
|---|---|---|
| `180000`-`18160F` | 47 | entry, TLB and calibration overlay, OS task bodies, the ECU state manager |
| `182560`-`1857FF` | ~85 | hardware init (pads, clocks, SWT, INTC), FlexCAN, DSPI, eSCI and eMIOS drivers |
| `185840`-`1890FF` | ~70 | eQADC with eDMA, the CAN slot table and mailbox layer, the digital-I/O table |
| `1890F0`-`189DDB` | ~20 | timer services, the LIN driver |
| `189DDC`-`18CB63` | ~80 | the CCP 2.1 slave (section 12.3) |
| `18CB64`-`195540` | ~170 | the bootloader jump, the KWP2000 server and ISO-TP transport (section 2), the DTC manager `FUN_001943CC`-`FUN_001950D0` (section 21.2) |
| `1955BC`-`1961FF` | ~25 | the OS kernel |
| `196200`-`198FFF` | ~80 | power-down helpers, the 1 ms supervisor, and a key/immobilizer-type module (P1676-P1699, section 21.2; not analysed) |
| `199000`-`19C9FF` | ~110 | the EEPROM/standby image manager, the `31 F3` test sequence, the CCP-CAN RAM loader |
| `19CA00`-`19EFFF` | ~45 | the real-time clock and wake alarm, start-up and shutdown |
| `19F9EC`-`1A7FFF` | ~25 | CAN supervision and timeouts, the RX copy, the TX tasks, the fast RX path, the `0x232` sender |
| `1A8000`-`1ABFFF` | ~100 | ROM and flash checks, the power-state machine `FUN_001ABEBC`, power-path outputs |
| `1AC000`-`1D8FFF` | ~250 | one unpack function per received frame, with override and default handling, and the per-signal conditioning: pedal, gear and shift-by-wire, brakes, drive mode and regen, LDC enable `FUN_001C1800` |
| `1D9000`-`1E63FF` | ~60 | vehicle functions in the 10 ms groups (not mapped) |
| `1E6400`-`1EEFFF` | ~70 | range and energy estimation |
| `1EF000`-`1FFFFF` | ~160 | drivetrain status (`0x592`), the TX packers, the 2101 producer, the 1 ms torque path |
| `200000`-`2057D7` | ~70 | 21F2, IOControl, routines, the record tables, the DTC report task `FUN_00205074` |
| `2057D8`-`20EDF7` | ~380 | the scheduling model (section 10), mostly one-line getters and setters |
| `211000`-`2208DB` | ~185 | **the LDC and 12 V application** (section 18): control law, PWM, trips, temperature, the 12 V top-up, the `0x590` and `0x549` builders, the 2102 producer |

Still unmapped: `181630`-`182560`, `1D9000`-`1E63FF` and `20EE00`-`210FFF`.

## 17. Fourth pass: the hard-wired inputs and outputs

The pad configuration (`FUN_001829FC`) and a 29-entry digital-I/O table at RAM `0x400073CC`
(`FUN_00187D08`; set with `FUN_00187C64(index, value)`, read with `FUN_00187F54(index)`) give
the pin map. The DIO index is not a pad number: map it through that table. Ghidra drops the
arguments of those two calls in many places, so read the `li r3`/`li r4` before each `bl` in
`listing.txt`.

### 17.1 Analog inputs (eQADC A every 100 µs, eQADC B for the pedal)

| channel | firmware | scale (cal) | meaning |
|---|---|---|---|
| A AN16 | `0x400065B0` → `0x400065B6` | on above 1330 counts, off below 760, 9-scan peak hold, off-delay | the wake/ignition supply sense (uhi22: CN1000 pin 3P) |
| A AN0 | → `0x4000A514` | 0.15635 V/count (`0x2D640`), optional EOL trim | **LDC HV input = DC-link voltage**, 2102 `VCU_DCLINK_VOLTAGE` |
| A AN1 | → `0x4000A550` | 0.006775 V/count + **0.6 V** (`0x2D64C`/`0x2D648`) | the 12 V supply behind the reverse-polarity diode; 2102 `VCU_AUX_BATTERY_VOLTAGE_FINE`, and (raw × 14 + `0x533`, filtered) the 2101 12 V word (/2072) |
| A AN2 | → `0x4000A52C` | 0.0061066 V/count (25 V full scale, `0x2D658`) | **LDC output voltage**, 2102 `VCU_LDC_OUTPUT_VOLTAGE` |
| A AN3 | → `0x4000A520` | 0.001221 V/count, plus a zero-current offset in LDC modes 2/3 | **LDC current sense** (inferred: the third input of the control law) |
| A AN4 | → `0x4000A538` | 20-point thermistor table | **LDC power-stage temperature** (probable) |
| A AN5 | → `0x4000A55C` | 0.001221 V/count | an unnamed LDC-side voltage |
| A AN6 | — | — | sampled, not used |
| B 0, 1 | `0x40005874`, `0x40005878` | raw × 2.5 | **APS1, APS2** (pins 3F, 3C; probable) |
| B 2, 3 | `0x40005872`, `0x40005876` | raw | the two 5 V pedal-sensor supplies (P0642/P0643, P0698/P0699) |

Every channel has a first-order filter with a calibrated cutoff (10 kHz loop `FUN_002134B0`,
1 kHz loop `FUN_002138F8`). Section 21.3 has the pedal DTCs; `FUN_001B9358` is the pedal
processing (the APS1/APS2 correlation itself was not traced).

### 17.2 Digital inputs

| DIO | pad | variable | meaning |
|---|---|---|---|
| 0, 1 | 114, 115 | `0x40004BE1`, `0x40004BE0` | regen paddles "−" / "+" (2101 `B23:0`/`B23:1`; uhi22 pins 3H/3J) |
| 4 | 118 | `0x40005881` | the hard-wired ignition-type input that 2101 `B23:2` reports; it blocks remote climate and the top-up |
| 5 | 122 | `0x4000588A` | not named |
| 6, 7 | 123, 124 | `0x4000587E`, `0x4000587D` | brake lamp switch and brake test switch (2101 `B11:0`/`B11:1`; DTC P0504) |
| 15 | 150 | `0x40005883` (inverted) | probably the clock's `/INT` alarm line; only polled |
| 20 | 198 | — | an LDC-side status input |
| — | 182 (eMIOS 3) | `0x40006400`/`0x4000640C` | a PWM-coded input (period and duty), not named; perhaps the electric water pump's status line |

The VCU also reads four unwired placeholders (`0x40005884`-`0x40005889`) from pad 114, so those
variables copy the "−" paddle. In `1AFF` mode the analog values, the brake switches and DIO 5 come
from CCP-CAN `0x300` instead.

### 17.3 Outputs

| DIO / channel | pad | driven by | meaning |
|---|---|---|---|
| 8 | 128 | `FUN_00196330` drops it at shutdown | **power self-hold** of the EPCU regulator (probable) |
| 10, 11, 12 | 130-132 | set only by the LDC chart (`FUN_0021EFA4`, LDC state 7, wake sensed); cleared by the trips | **the LDC enables** (probable) |
| eMIOS 4 | 183 | duty from `FUN_00212294` | **the LDC PWM**: period 1,320 counts, so 100 kHz at the inferred 132 MHz clock (cal `0x2D6D2` = 100 kHz) |
| 18 | 191 | the EEPROM driver | EEPROM write-protect release |
| 21, 22 | 200, 201 | the power-state module (`0x1A9xxx`) | power-path outputs, not named |
| 23 (`0x17`) | — | follows the shifter-fault state; forced by IOControl `30 10 07` | not named |
| 28 | 437 | `FUN_001AA0F0`, follows "ignition input on" | a wake or enable request inside the EPCU (inferred) |

Three edge-interrupt inputs (eMIOS 5, 6 and 8, pads 184, 185, 187; `FUN_0021A740`,
`FUN_0021A678`, `FUN_0021A6DC`) switch the LDC enables off at once: a hardware trip path. Which is
over-current, over-voltage or over-temperature was not traced. They do nothing in `1AFF` mode.

### 17.4 Serial links

- **FlexCAN A** = P-CAN, **C** = CCP-CAN, **B** pads set up but unused (section 4.1).
- **LIN for the 12 V battery sensor** behind `0x549` (uhi22: an L9637 transceiver at pin 1O). The
  LIN driver (`FUN_0018949C`, `FUN_00189540`, `FUN_00189844`) runs on the eSCI B interrupt; eSCI
  A is also configured with DMA. Which module drives the sensor was not settled. A LIN frame
  error sets U1112.
- **DSPI C:** CS0 the EEPROM, CS1 the clock. **DSPI B:** one device with continuous DMA traffic,
  not identified (perhaps a watchdog, safety or power-stage IC).

### 17.5 Does the clock's alarm power the EPCU?

No external interrupt input is enabled (SIU DIRER, IREER and IFEER are 0), so the clock cannot
interrupt the CPU. At shutdown the VCU drops the self-hold (DIO 8) and polls AN16 in a loop; it
restarts only when the wake line rises again. So the alarm can start the EPCU only through the
board wiring, from the clock's `INT` pin into the regulator enable (the diode-OR beside CN1000.3P)
or into the wake line. The firmware cannot show that wiring; a board trace can. The exact-minute
check of section 11.1 suggests it exists (inferred).

## 18. Fourth pass: the integrated LDC (12 V DC-DC converter)

The ecu_id `AEVLDC53` ("VLDC") says it: the VCU's processor runs the EPCU's 12 V converter. The
code at `0x210000`-`0x2208FF` is the LDC's own software unit inside the VCU image.

### 18.1 Architecture

- **It behaves like a separate ECU.** `FUN_0021A178` copies received mailboxes into a private
  image, byte-reversed (image byte k = frame byte 7−k): `0x010`, `0x291`, `0x331`, `0x490`,
  `0x523`, `0x524`, `0x541`, `0x553`, `0x57F`, `0x594`, `0x595`, `0x5D0`, `0x5F5`, `0x5F6`,
  `0x5F8`, **and the VCU application's own transmit frames `0x200`, `0x592` and `0x57B`**. The
  application commands the LDC only through those frames, and the LDC owns `0x590` (P-CAN) and
  `0x58F` (CCP-CAN) (verified).
- **The commands in `0x592`:** bit 56 the **LDC-off request** (`0x40005357`, the same variable
  as 2101 `B11:7`); bits 37-38 a voltage mode 0/1/2; byte 6 a voltage target used only in test
  mode.
- **Test mode.** With cal `0x306D0` = 1 or the `31 F3` test latch `0x400067F0`, the three command
  frames come from CCP-CAN `0x050`, `0x051` and `0x055` instead: an end-of-line tester commands
  the LDC directly. That is the `0x050`/`0x051` relay of section 4.4.
- **Rates.** `FUN_0021A95C` runs on every 100 µs ADC scan (scaling, the HV-input monitors, the
  state machine and the enables); `FUN_0021A980` every 1 ms (power estimate, output and 12 V
  monitors, temperature, the high-load output, the PWM); `FUN_0021AA0C` every 10 ms (battery-sensor
  monitors, the CAN image, `0x590`/`0x58F`, `0x549`); `FUN_0021AA78` every 100 ms (the set-point,
  the 12 V top-up decision, cooling requests, the 2102 producer).

### 18.2 The power-stage interface

The VCU does not switch the converter's transistors (probable). It measures the converter
(section 17.1: AN0 HV input, AN2 output, AN3 current sense, AN4 temperature) and drives:

- **two enable outputs (DIO 10, 11)**, both on only in LDC state 7 (`FUN_0021EFA4`, verified), and
  read back by `FUN_0021E8F4` as the "converter enabled" test;
- **a voltage-reference PWM on eMIOS 4**: duty = **4 % per volt** (table `0x3027C`→`0x302A0`;
  7.7 V → 30.8 %, 15.7 V → 62.8 %), independent of the HV voltage. A real switching duty would
  depend on V_HV, so this is a set-point for an analog control loop in the converter (probable);
- **a high-load output (DIO 12)** (`FUN_0021F210`): on after 200 ms of HV ≥ 270 V, target ≥ 13.8 V
  and output above about 220 W; off below 140-180 W for 3 s, when limited, or at HV < 270 V.
  Probably a second phase or the synchronous rectifier (inferred).

Three hardware comparators on eMIOS 5, 6 and 8 switch DIO 10-12 off at once and clear a
"hardware OK" flag (probable mapping, from the recovery logic):

| input | trip | recovery |
|---|---|---|
| eMIOS 5 | **input over-current** (manual P1A8C: 23.8 A) | re-enable after 100 ticks; after 10 trips latch P1A8C |
| eMIOS 6 | **HV input over-voltage** | when HV < 440 V for 1,000 ticks |
| eMIOS 8 | **output over-voltage** (manual P0A94 case 2: above 22 V) | re-enable after 100 ticks; latch after the retry limit |

### 18.3 Enable: two layers

**The application (`FUN_001C1800` → `0x40005357`, 2101 `B11:7`, `0x592` bit 56).** The LDC may
run (0) only when:

| case | LDC allowed when |
|---|---|
| power state 8 (fault) or 9 (low-SoC stop) | never |
| not READY | AC or DC charging (`0x595` bit 9 or 10), **or** a climate session (`0x4000617B`), **or** a 12 V top-up (`0x4000618E`); **and** the BMS reports HV ready (`0x594` bit 34) |
| READY | not (HV ready and (HV SoC ≤ 2.4 % or no discharge power left)); `0x594` present; no crash (`0x010` bit 0); no MCU fault (`0x523` bit 8) |

**The LDC's own state machine (`0x40006384`, `FUN_0021EE28`, 100 µs):**

| state | when | `0x590` |
|---|---|---|
| 0 off | 12 V outside 8-18 V, or the wake supply lost | |
| 1 hard fault A | over-temperature shutdown, regulation fault, permanent output or input fault | |
| 2 hard fault B | HV over-voltage, current-sensor offset fault, pin-monitor fault | |
| 3 recovering | a retry after an output or input trip | byte 0 bit 1 |
| 4 inhibit | the off request; BMS fault flags; another ECU's fault flag (`0x523`, `0x594`, `0x5F5`, `0x57F`) | byte 0 bit 1 |
| 5 standby | mode 3 | byte 0 bit 1 |
| 6 calibrate | the current-sensor auto-zero: 100 samples, offset to read 1.0 V at 0 A | byte 0 bit 1 |
| 7 run | otherwise; sets DIO 10 and 11 | byte 0 bits 1 and 2 |

**This corrects two 2102 names.** `B10` (`VCU_HV_MAIN_RELAY_ON`, `0x590` byte 0 bit 2) is "LDC in
run state 7", and `B9` (`VCU_HV_AVAILABLE`, bit 1) is "LDC in state 3 or above" (verified,
`FUN_0021EFA4`, `FUN_0021F08C`). Both imply HV is up, so the cross-tab of section 8.4 still holds,
but they are converter states, not relay states: `B9 = 1, B10 = 0` (450 captures) is the LDC
awake but inhibited, calibrating or in standby. Section 11.3 step 3 therefore waits for the LDC
to run, not for the relay.

### 18.4 The output voltage set-point (`FUN_002190E8`, 100 ms)

1. A priority chain of target modes (`FUN_00216B08`) picks a target. The default is a table of
   **12 V battery SoC × battery temperature** from the LIN battery sensor (for example 14.8 V at
   −10 °C and 85 % SoC, 13.0 V at 70 °C and 100 %), chosen by a charge band with SoC hysteresis
   (92/89/85/80 %) and a READY-cycle counter in standby RAM. A long-term charge-history mode reads
   standby counters at `0x40000BBC`-`0x40000BCC`. Without a valid sensor a mode-by-temperature
   table applies.
2. Clamp to **12.7-15.1 V** (`0x2D6AC`/`0x2D6A8`).
3. **Ramp at 0.2 V/s**, starting from the measured bus voltage at every start: the soft start.
4. **Remote-sense trim:** a PI loop on the target minus the battery sensor's voltage, 0 to +1.5 V,
   for the cable drop.
5. **12 V charge-current cap** (PI, can only lower the set-point, to 10 V): 41 A for 10 s, 36 A to
   5 min, 32 A to 30 min, 27 → 23 A at 2 h in the lowest charge band; 32 → 14 A in the highest.
6. **The power and thermal limiter** (1 ms) lowers the command by up to 7.5 V (18.5).

Overrides: a lost `0x592` (`0x58F` byte 0 bit 5) or `0x523` byte 7 bit 4 force **13.8 V**; no BMS
HV-ready bit gives 12.7 V. **The `0x592` voltage mode has no effect in this calibration**: it only
selects a column of two tables whose columns are all equal (verified). The VCU sets it to 0 in P,
1 at low speed, 2 when moving.

### 18.5 Power limit and derating (`FUN_00212CA8`, 1 ms)

- **1.4 kW** at HV ≥ 250 V (1.3 kW at 15.1 V); at 220 V, 1.2 kW at 12.8 V falling to 410 W at
  15.1 V (table `0x2D860`). The maximum output voltage drops to 14.7 V below about 260 V and
  14.5 V below about 230 V, with hysteresis.
- **Thermal derating** from the LDC temperature: 100 % at 85 °C, 80 % at 90 °C, 60 % at 95 °C,
  40 % at 100 °C, 20 % at 105 °C, and 20 % if the sensor is invalid.
- When the limiter acts, `0x590` byte 0 bit 3 (= **2102 `B13`, "output limited"**) and `0x58F`
  byte 6 bit 3 are set; byte 6 bit 2 adds "above 85 °C".

### 18.6 Every LDC value the VCU publishes

| where | content |
|---|---|
| `0x590` byte 0 | bit 0 software running; **1 LDC state ≥ 3 (2102 `B9`)**; **2 LDC running (2102 `B10`)**; **3 output limited (2102 `B13`)**; 4-5 delayed "not running while READY or charging" flags (probably lamp requests); 6-7 the 12 V top-up request and done |
| `0x590` byte 1 | bit 0 always 0 (2102 `B11`); **bit 1 LDC fault warning (2102 `B12`)**; bits 4-5 a post-charge flag |
| `0x590` byte 2 | output power, 10 W (2102 `B15`) |
| `0x590` byte 3 | probably the **cooling-fan request** / 20, from the LDC temperature (75-82.5 °C) and vehicle speed |
| `0x590` byte 4 | output voltage, 0.1 V (2102 `B14`) |
| `0x590` byte 5 | probably the **water-pump request** / 20: 0 below 55 °C, then 1000-3320 (rpm-like) up to 75 °C |
| `0x590` byte 6 | **HV input power, 10 W** (not a loss figure); byte 2 / byte 6 is the converter efficiency |
| `0x590` byte 7 | **the LDC's own temperature + 100**, 255 when its sensor is invalid (2102 `B18`) |
| `0x58F` (CCP-CAN only) | bytes 0, 1, 6 fault and limit bits; byte 2 the final voltage command × 10; byte 5 HV / 2 (2102 `B17`); byte 7 the PWM duty % |
| 2102 `B19/B20`, `B21/B22`, `B23/B25`, `B26/B27` | output voltage (AN2) × 1000; output power (`0x40006DCC`) × 100; DC-link voltage (AN0) × 100; the 12 V supply (AN1) × 1000 |

So `VCU_COOLANT_TEMP` is the LDC's temperature sensor (P0C3A/P0C3B: "DC/DC converter temperature
sensor"). It tracks the motor coolant because the converter sits on the coolant plate.

**The LDC current is measured but never published.** AN3 is auto-zeroed before every start (the
P1A89 check), and the firmware turns it into power through 2-D tables of output voltage × sense
voltage per HV band (`0x21B8F0`+). It never computes an output current. The estimate that follows
the firmware is `VCU_AUX_POWER / VCU_LDC_OUTPUT_VOLTAGE`, and the HV input current is
(`0x590` byte 6 × 10 W) / V_HV. A raw current exists only in the LDC event snapshots (18.7).

### 18.7 LDC faults and the event ring

The LDC calls `FUN_0021B300(event, …)`, which logs a 112-byte snapshot (state word, HV and output
voltage, current sense, power, 12 V, temperature, the battery sensor's current, voltage,
temperature and SoC, the odometer, flags) into a 9-entry ring in standby RAM at `0x40000BD4`, so
the EEPROM keeps it. Only a CCP master can read it. Events below `0x12` that are enabled also
report a DTC and store six freeze-frame bytes:

| DTC | firmware detector | manual |
|---|---|---|
| P0A94 | target − output ≥ 1.0 V too long; output below 8.4 V, latched on the 5th trip; the output over-voltage trip | 1 V for 10 s; over 22 V |
| P0C3A / P0C3B | temperature sensor raw ≥ 4.8 V for 10 s (only while the MCU's `0x523` temperatures are valid) / < 0.3 V for 1 s | match |
| P1A88 | LDC temperature above 105 °C for 2 s: shutdown (recovery at 85 °C) | match |
| P1A89 | current-sense offset above +0.070 V or below −0.129 V | 0.078 / −0.13 V |
| P1A8C | the input over-current trip, 10 times | 23.8 A, at once (probable) |
| P1A8D | output sense and 12 V sense differ by more than 8 V for 1 s while running | match |
| P1A8A | the temperature-sensor chart's state 4 (not traced) | not in the manual set |
| U04B1 | the battery-sensor (LIN) fault monitor (probable) | |

The 12 V monitor at 8.0/18.0 V (recovery at 8.6/16.6 V) is separate from P0562/P0563.

## 19. Fourth pass: propulsion, from pedal to torque command

### 19.1 Units and the chain

The VCU works in **wheel torque, 1 Nm**, and converts to motor torque at the end with the
reduction ratio **7.412** (cal `0x21BAA` = 30359 = 7.412 × 4096). Motor torque inside the VCU is
in 1/8 Nm (verified). The chain:

1. **Driver wheel torque** = coast/creep curve + pedal factor × (motoring envelope − coast curve),
   rate-limited and filtered (`FUN_001C592C` group, 10 ms) → `0x400056F6`, sent in `0x201`.
2. **Arbitration** (1 ms, `FUN_001F50F0` → `FUN_001BC4D0` chain): wheel → motor torque, the main
   limiter, anti-jerk and zero-crossing tables, the drive-mode change blend.
3. **Limits** (10 ms, `FUN_001CB38C`): the BMS power limits from `0x594` less the auxiliary load,
   divided through motor speed, then the minimum with the MCU's available torque (`0x2C1` byte 5)
   and the temperature derates → the motoring limit `0x40006A1A`, sent in `0x109` byte 2.
4. **Traction control** (`FUN_001D9D68`): with `0x153` `TCS_REQ` the drive torque is limited to
   the smaller of `TQI_TCS` and `TQI_SLW_TCS` (unless `TCS_MFRN`); with `MSR_C_REQ`, `TQI_MSR`
   raises the regen side. ABS active (`0x153` `ABS_ACT`) zeroes the coast/creep torque in the regen
   direction.
5. **Command** `0x40005AEC` → CCP-CAN **`0x232`** every 1 ms, mirrored in P-CAN `0x202` every
   10 ms. Both send 0 unless EV-ready (`0x40004B0D`) is set.

### 19.2 The pedal maps, creep and coast regen

- **Motoring envelope vs speed** (14 points per drive mode, `0x12F4A`/`0x12F82`/`0x12FBA`): 1969 Nm
  at the wheel = **265.6 Nm motor** in modes 0 and 1, 2187 Nm = **295 Nm** in mode 2, constant
  power from about 48 km/h (82 kW in modes 0/1, 87 kW in mode 2), and **0 at an internal 109.2
  "mph"** (about 169 km/h true): the envelope is the top-speed limiter. The reverse envelope
  reaches 0 at about 30 km/h true.
- **13 pedal maps** (pedal × speed, 20 × 16) chosen by gear, drive mode and regen level. Mode 2
  reaches 100 % at 70 % pedal and is the only one with 295 Nm: **index 2 is Sport** (probable).
  Mode 1 is more responsive than mode 0 on the same envelope, and the owner spends most time in
  index 1 (section 13.4): **1 = Normal, 0 = Eco** is the best reading (inferred). Higher regen
  levels give more torque per pedal percent at speed, which offsets the stronger coast regen.
- **Coast/creep curves** (57 points vs speed, one per mode × level, `0x1C7DD0`-`0x1C7E54`). Creep is
  **+350 Nm at the wheel** (47 Nm motor) at standstill, falling to 0 at about 9 km/h true, and up
  to +1700 Nm when rolling backwards in D (anti-rollback). Coast regen above about 26 km/h is 0,
  −230, −480 and −740 Nm at the wheel for levels 0-3 in modes 0/1; mode 2 is softer (−109 to −660).
  Level 3 is the base table the code falls back to. uhi22's trace agrees: `0x201` reads 350 at
  standstill with the brake released (verified).
- A drive-mode change holds the pedal value until the command is within 5 Nm of the new target,
  or 3 s pass (`FUN_001BF224`).
- No "hold the paddle to stop" logic was found; the paddles produce edges only (inferred).

### 19.3 The MCU interface and the drivetrain frames

| frame | dir, bus, period | layout (DBC little-endian start bits) |
|---|---|---|
| `0x232` | TX, CCP-CAN, 1 ms | 0\|12 signed motor torque command, ≈ 0.1465 Nm/bit (2048 ≈ 300 Nm); nothing else, no counter or checksum |
| `0x2C1` | RX, CCP-CAN, 1 ms | 0\|12 signed actual torque (same scale); 16\|16 signed motor rpm (`0x7FFF` invalid); 32\|8 a derate input; 40\|8, 48\|8 available torque limits (× 800/255) |
| `0x291` | RX, P-CAN, 10 ms | the same layout as `0x2C1`; the VCU falls back to it (and `0x524`) when the CCP-CAN link fails |
| `0x202` | TX, P-CAN, 10 ms | flags 0-4, 7; counter 5-6; **8\|12 final torque command (= `0x232`)**; 20\|4 regen level; 24\|8; **32\|12 torque before traction control**; XOR checksum in byte 7 |
| `0x201` | TX, P-CAN, 10 ms | flags 0-5; counter 6-7; 8\|16 a torque (0 unless EV-ready); **24\|16 driver wheel-torque demand, 1 Nm**; 40\|16 a brake-related word from `0x2A2`; XOR checksum in byte 7 |
| `0x200` | TX, P-CAN, 10 ms | **11\|4 gear** (P 0, D 5, N 6, R 7, `E` pending, `F` shifter fault); 24\|4 the power state (2101 `B25`); **32\|8 pedal × 2.55, `FF` on a pedal fault**; 44 IG3; 45 EV-ready; counter 46-47; 49\|6 a constant 30 |
| `0x109` | TX, P-CAN, 10 ms | **0\|8 the park request to the shift-by-wire unit** (probable); 8\|4; **16\|8 the motoring torque limit**, ≈ 0.39 %/bit of 300 Nm; counter and nibble checksum in byte 7 |

Two corrections to earlier readings: the `fields_tx.txt` probe missed or mis-split the `0x201`
fields that are gated by EV-ready, and **`0x200` bit 28 is not "close contactors"** on this VCU
(uhi22's DBC): bits 24-27 are the power state.

### 19.4 Gear and shift-by-wire

`FUN_001AE390` turns the lever (`0x354`) into two codes: 2101 `B10`'s state (P 1, R 2, N 3, D 4,
6 a fifth lever position, **7 a shifter fault**) and the `0x200` code. So **2101 `B10` bit 4 is
the shifter-fault flag**. `0x10C` is the shift-by-wire (SBW) unit's status (byte 0: 4 parked, 2
moving, 1 released in uhi22's trace; its loss sets U1017), and `FUN_001C1ADC` writes the park
request into `0x109` byte 0 below a speed threshold (`0x1CA56`). **2101 `B26` is not a VCU gear
state:** bits 0-2 are `0x109` byte 0, bits 3-5 `0x10C` byte 0, bit 6 `0x10C` bit 7, which is why
it reads `0x20` in P and `0x08` otherwise. P1060-P1066 are shift-by-wire faults (section 21.3).

### 19.5 Vehicle speed

`FUN_001B3630` (1 ms) computes the 2101 speed word from **motor rpm**, not from the wheel speeds:
rpm / 7.412 × 3817/512, which assumes a **2.0 m tyre circumference**. That is the cause of the
profile's known +3.6 % speed error. The word is signed (negative in reverse).

### 19.6 Not traced

Brake-pressure-to-regen blending (the brake system's `0x2A2` and `0x462` feed the regen torque,
section 22.2), brake-accelerator override, limp-home torque modes, the full shift and auto-P
conditions, cruise control (`FUN_001CD364` replaces the pedal sum with a target torque and reads
`0x394` ACC bits; the switch frame was not found), and which of `0x594`'s two power limits is
charge and which discharge.

## 20. Fourth pass: power states, HV sequencing, charging and cooling

### 20.1 The power-state machine (`FUN_001ABEBC`)

Its output `0x40004B16` is 2101 `B25` and `0x200` bits 24-27. Values and transitions are verified;
the names are probable.

| value | state | entered when |
|---|---|---|
| 0 | inactive | no wake supply: all outputs 0 |
| 1 | standby (awake, not READY) | the wake supply appears; return from 3, 9 or the end of a charge |
| 2 | HV up, start-ready | ignition input on, SoC not low, BMS HV ready (`0x594` bit 34 or 59), DC link above 200 V (`0x524`), no SBW fault, MCU and BMS frames present; sets 2101 `B11:6` |
| 3 | start inhibited | P-CAN bus-off or lost MCU or BMS frames, with the ignition on and not plugged |
| 4 | **READY** | a start request (a rising edge of DIO 5 in P, or a restart while rolling in R/N/D), no remote immobilisation (`0x587` bit 1), MCU ready (`0x523` bit 2), BMS ready (`0x594` bit 38) |
| 5 | READY, drive inhibited | READY with the BMS not ready, a frame loss or another inhibit |
| 6 / 7 | charge connected / **charging** | a charge request (`0x553` bits 0-1, `0x57F` bits 1-2, `0x595` bit 10, debounced); 7 while `0x594` bit 63 |
| 8 | **fault**, latched until power-off | crash (`0x010` bit 0), BMS faults (`0x594` bits 36, 55, 58), MCU faults (`0x523` bits 0, 9), climate (`0x5F5` bit 42), OBC (`0x57F` bit 43). uhi22's bench, with no BMS, reads 8 |
| 9 | low-SoC stop, latched | READY, HV SoC ≤ 2.4 % and low motor speed |
| 10 | HV lost in READY, latched | `0x594` bit 34 dropped |
| 11 | READY off, one tick | ignition input off, or `0x541` IgnSw 0 or 2 |

Two inputs follow from this: **DIO 4** (2101 `B23:2`, profile `VCU_INVERTER_ENABLED`) is the
ignition ON/START input, and **DIO 5** behaves as the smart key's start signal. 2101 `B11:2` is
that input directly (`FUN_001AE2C4`: `0x40004BEA` = (`0x4000588A` == 1), verified), so the
"internal flag" of section 7.1 is now placed. Its rising edge in P starts READY, and the chart
does not test the brake (the smart-key module gates the start on it; inferred). The smart-key
unit's pinout lists a "[VCU Ready] signal input" (shop manual page 0414), so a hard-wired line
runs from the VCU back to the smart key; the start wire into the EPCU was not checked in the
wiring manual. Both names need an ON-not-READY capture to confirm.

### 20.2 HV sequencing: still not traced

No frame bit was found that commands the BMS contactors. The VCU waits for `0x594` bit 34 (HV
ready) and a DC link above 200 V before state 2 and before it lets the LDC run. The candidates
for the request are `0x200` bits 24-27 (the power state), 44 and 45, and `0x57B` bit 30
("prepare", top-up and climate). No output follows the IG3 flag `0x40004B17`, so the VCU does not
drive the IG3 relay itself in this code (probable): the IGPM does that.

### 20.3 Charging and cooling

- The power state only tells 6 (connected) from 7 (BMS charging). The charge-control state machine
  of the scheduling model (`0x40006D2A`, section 10.1) is still not named.
- **Cooling:** the LDC requests cooling and does not drive it (probable): `0x590` bytes 3 and 5
  (18.6). The manual's cooling chapter says the EPCU commands the electric water pump and the
  fan; `0x523` byte 6 carries the MCU's pump figure, and an eMIOS PWM input (pad 182) may be the
  pump's status line. Who merges the LDC's and the MCU's requests into the pump and fan commands
  was not traced. Battery thermal management is the BMS's.
- **Range and energy:** `0x598` 32\|16 (probably the remaining energy in Wh), the climate power
  from `0x5F5`/`0x5F6`, the route segments in `0x55F`/`0x561` and the map stream `0x419`-`0x41C`
  feed the range estimate (`0x1E6400`-`0x1EEFFF`); its outputs go into 21F2 (section 13.4). The
  published distance-to-empty frame was not identified.

## 21. Fourth pass: the diagnostic interface, completed

### 21.1 OBD-II is a stub

The OBD-II handlers skip the session matrix and answer in every session, physically and on
`0x7DF`. Every table has one entry, backed by RAM that nothing writes (verified):

| request | reply |
|---|---|
| `01 00`, `01 20` … `01 E0` | none (the supported-PID entries are flagged "not served") |
| `01 01` | `41 01 00` (one byte, always 0) |
| `02 02 00` | `42 02 00 00 00` |
| `03`, `07` | `43 00`, `47 00` |
| `0A` | `4A 00` |
| `04` | `7F 04 22`, always (`FUN_00204D5C(4)` returns 0) |
| `09 04` | `49 04 01 00` |
| `09 00`, `09 02`, `09 06`, `09 0A` | none |

Modes 03 and 07 list a DTC only when its class marks it emission-relevant, and no class does.
**Do not plan OBD-II reads of the VCU: there is nothing to read.**

### 21.2 The DTC table and the fault manager

The firmware has **45 DTCs** (`0x204E18`, and the manager's config at `0x20566C`). Every detector
reports through `FUN_001943CC(index, failed)`. One 44-byte record per DTC lives in standby RAM at
`0x400000AE + 0x2C × index` (so the EEPROM keeps it): state (none, pending, confirmed, healing,
aging), the code, counters, the occurrence count, a status word and a 27-byte freeze frame.

Seven cycle classes (`0x205720`) set the failed cycles to confirm (1 or 2) and the lamp:

| class | confirm | lamp | DTCs |
|---|---|---|---|
| 0 | 1 cycle | yes | P2122 P2123 P2127 P2128 P2138 P0642 P0643 P0698 P0699 P062F P0630 P0A94 P0C3A P0C3B P1A88 P1A89 P1A8A P1A8C P1A8D |
| 1 | 2 | yes | P1A78 |
| 2 | 2 | yes, after 2 cycles | P0504 U0001 U0110 U0111 U0129 U04B1 U1001 U1005 |
| 3 | 2 | no | P0562 P0563 U0146 U0164 U1310 U1112 U1017 U0103 |
| 4 | 1 | no | P1060 P1061 P1062 P1065 P1066 |
| 5, 6 | 2 / 1 (second operation cycle) | no | P1676 P1690 P1696 / P1699 |

A confirmed DTC heals after 39 passing cycles (probable). Any lamp-class DTC sets
`0x40005FD0`, which goes out in `0x592` bit 3 and `0x202` bit 4: the service-lamp request
(probable).

**Against `ecus/vcu.yaml` (38 codes):** the firmware has seven the profile lacks: **P1A8A** (a
second LDC temperature-sensor fault), **U1005** (lost the MCU's `0x2C1` on CCP-CAN), **U0103**
(never reported: this firmware cannot set it), and **P1676, P1690, P1696, P1699** from a
key/immobilizer-type module (`0x196xxx`-`0x198xxx`; Hyundai uses P16xx for immobilizer faults;
inferred, not analysed). The profile has no code the firmware lacks. The manual's confirm cycles
and lamp agree for 33 of the 38; the exceptions are P1A78 (manual: at once; firmware: 2 cycles)
and U1112/U1017 (manual: lamp on; firmware: no lamp).

### 21.3 What each DTC tests

The DTC counters run every 10 ms: P0562's 3,000 counts are 30 s, as the manual says.

| DTC | detector input | threshold |
|---|---|---|
| P2122/P2123, P2127/P2128, P2138 | APS1/APS2 range flags, and their correlation | |
| P0642/P0643, P0698/P0699 | the two 5 V sensor supplies (eQADC B) | < 4.5 V / > 5.5 V, 10 checks |
| P0504 | brake lamp vs brake test switch, when EV-ready | |
| P1A78 | the IG3 relay / back-up lamp check, when EV-ready | |
| P062F, P0630 | the EEPROM access result; the VIN check (cal `0x2D5F1` = 1) | |
| P0562/P0563 | the 2101 12 V word, when EV-ready | below ≈ 9.9 V / above ≈ 15.8 V for 30 s |
| U0001, U1001 | P-CAN / CCP-CAN controller error-passive or bus-off | |
| U0111 | timeout of `0x594`-`0x598`, `0x542` (BMS) | |
| U0129 | timeout of `0x2A2`, `0x220`, `0x153`, `0x394`, `0x507`, `0x386` (ESC and brakes) | |
| U0146 | timeout of `0x541`, `0x553`, `0x490`, `0x5D0`, `0x450`, `0x578`, `0x462`, `0x5B3`, `0x4FE`, `0x588`, `0x55C`, `0x55F`, `0x561`, `0x567`, `0x5DF` (via the gateway) | |
| U0164 | timeout of `0x5F5`-`0x5F9` (climate unit) | |
| U0110 / U1005 | timeout of `0x291`, `0x523`, `0x524` (MCU on P-CAN) / `0x2C1` (MCU on CCP-CAN) | |
| U1310, U1017 | timeout of `0x57F` (OBC), `0x10C` (shift-by-wire) | |
| U1112 | LIN frame error from the battery sensor | |
| P1060-P1066 | shift-by-wire fault flags | |
| LDC codes | section 18 | |

The RX timeout per frame is section 22.1. `0x587` (the telematics unit) and `0x570`, `0x010`,
`0x354` set no DTC.

### 21.4 Reading and clearing

- **Status byte** (`FUN_0018D748`): bits 0-3 a failure-type class from the config (not an ISO
  15031-6 failure type), bit 4 not tested this cycle, bits 5-6 `01` confirmed and present, `10`
  pending, `11` stored, bit 7 lamp requested. uhi22's bench read (status `0x44` on six U-codes) is
  "pending, class 4".
- **`18 MM GG GG`**: `MM` = `00`/`20`/`40`/`60` (four status selections, filters not mapped),
  `GG GG` = `FF 00` all, `00 00` P, `40 00` C, `80 00` B. canair's `18 00 FF 00` is valid.
- **`14 GG 00`** (`81` and `90`, no key): `GG` = `00` P, `40` C, `80` B, `FF` all. U-codes have no
  group, so only `14 FF 00` clears them. A clear also resets every CAN timeout state.
- **`12 00 00`** (`81` and `90`): the newest stored DTC's 27-byte freeze frame, then `00` and the
  code; NRC `0x12` with none stored. Layout (16-bit little-endian): 0-1 `0x40004EEC` (BMS SoC,
  see section 22.2), 2-3 the 12 V word, 4-5 vehicle speed, 6-9 the odometer, 10 flags (EV-ready,
  …), 11-12 an operating-time counter, 13-18 APS1, APS2 and pedal, 19 the gear state as 2101
  `B10`, 20 the brake switches, 21-26 LDC detail bytes (byte 23 is the LDC event sub-code that
  tells the four P0A94 causes apart). canair has no KWP2000 freeze-frame decoder.

## 22. Fourth pass: CAN reception, timeouts and the rest of the frame map

### 22.1 Timeouts, plug masking and calibration overrides

- **Timeouts** (`FUN_0019F9EC`, 10 ms): each slot counts 10 ms steps without a new frame up to its
  class limit (`0x1878E0`): class 0-7 = 0.5, 1, 1.5, 2, 3, 4, 5, 5 s. The monitor is off outside
  the supply window and for the first 0.5 s awake. `0x010`, `0x418`-`0x41C` and `0x51B` never time
  out.
- **While plugged in** (`0x595` bit 9 or 10) the timeout flags of `0x153`, `0x220`, `0x2A2`,
  `0x386`, `0x394`, `0x507`, `0x291`, `0x523`, `0x524`, `0x2C1` and `0x578` are forced to 0: a
  missing chassis or MCU frame during a charge is no fault.
- **Overrides.** Every unpacked signal has a calibration enable/value pair, and every timeout flag
  another (`0x100DD`-`0x10136`). Of 396 signal enables and 43 flag enables, **one override is
  active: `0x22298`/`0x22299` force `0x5DF` bits 2-3 to 1**, so those bits do nothing on this car.
  On timeout a signal holds its last value or takes a calibrated default.

### 22.2 Received frames identified in pass 4

| id | sender | what the VCU uses |
|---|---|---|
| `0x010` | airbag unit, `ACU13` | bit 0 crash active → the power state and LDC off; no timeout |
| `0x10C` | shift-by-wire unit (probable) | park-lock state, 2101 `B26` (section 19.4) |
| `0x153` | ESC, `TCS11` | traction-control and MSR torque requests, ABS flags (opendbc layout confirmed) |
| `0x291` | MCU (P-CAN) | torque, rpm, limits; same layout as `0x2C1` |
| `0x2A2` | brake system (probable) | bytes 3-4 non-zero exactly while braking in uhi22's trace: a brake demand that feeds the regen torque |
| `0x418` | head unit (inferred) | received, never read |
| `0x419`-`0x41C` | head unit (inferred) | a multiplexed stream (type field, segment index, all-ones invalid), probably map data for the range estimate; absent in the EU logs |
| `0x462` | via the gateway, sender unknown | two values into the regen torque and `FUN_001E52BC` |
| `0x490` | electric parking brake, `EPB11` | `EPB_STATUS` |
| `0x4FE` | via the gateway | received, never read; its loss counts toward U0146 |
| `0x507` | ESC, `TCS15` | TCS lamps, for the range estimate |
| `0x51B` | cluster, `CLU16` | Aux. Battery Saver+ (46\|2), 39\|1 (forces it on), 22\|2 and 44\|2 |
| `0x523` | **MCU** (not opendbc's gateway frame) | flags (bit 0 fault), temperatures in bytes 2-5 (raw − 40 °C), 48\|8 × 0.156 (uhi22: cooling pump %) |
| `0x553` | gateway, `CGW2` | rear doors; 2\|4 = 2 opens the unplugged remote-climate path |
| `0x570` | unknown | two bits and a byte into a statistics buffer; no DTC |
| `0x594` | BMS | 0\|16 and 16\|16 × 10 power limits (73.7 / 98.0 kW in the trace); 34 HV ready; 36 fault; **40\|8 SoC × 0.5 %** |
| `0x598` | BMS | flags (19 fault) and 32\|16, probably the remaining energy in Wh (21,325 at 79.5 % on a 28 kWh pack) |
| `0x5B3` | cluster or head unit, user settings | seven 2-bit settings incl. **the regen default per drive mode**, and **16\|8 the Eco-mode maximum speed** (90-130 km/h, `FE`/`FD` off), echoed in `0x579` 56\|8 |
| `0x5DF` | via the gateway | 0\|2 unread; 2\|2 forced to 1 by calibration |
| `0x5F5`-`0x5F9` | climate unit | its power (two fields), fault flags, temperatures; the VCU reads `0x5F8` bytes 5-7 as signed bytes, not uhi22's 0.5 × raw − 40 |

The bit probe in `fields_rx.txt` sets one bit at a time with every other state zero, so it misses
fields that a branch guards: `0x5B3` shows one field there and carries eight. **Read the unpack
C before calling a byte unused.**

### 22.3 Transmitted frames not covered elsewhere

- **`0x592`** (100 ms, `FUN_001F2340`): drivetrain status from the `0x1EF000`-`0x1F0C54`
  functions. Probable fields: bits 0-3 flags (bit 3 the service-lamp request), 4\|4 a mode, 8\|8,
  16\|7 (varies while driving), 24\|8, 32-36 flags, 37\|2 a gear-related state, 40\|8 signed
  −100..100, 48\|8, 58\|4 a state, 62\|2 counter. Names are not established.
- **`0x58F`** (CCP-CAN) carries the `0x590` buffer; byte 5 is the DC-link voltage / 2 (2102
  `B17`).
- **`0x6FC`** is disabled (`0x2D612` = 0) and its buffer is never written; **`0x060`/`0x061`** are
  registered and never sent. Do not look for them on a bus.

### 22.4 The CCP-CAN RAM loader (`0x011`-`0x013` / `0x001`-`0x003`)

Not CCP or XCP: a bespoke download-and-execute loader (`FUN_0019C9F8`, verified). It busy-waits
for `0x011` and echoes it on `0x001`, takes a target address and length from `0x012` (echo
`0x002`), copies each 8-byte `0x013` to memory (echo `0x003`), and then calls the downloaded code.
Routine `31 F1 EE FF 00 11` enters it with interrupts off (section 3.3), probably to load a flash
driver from a bench or end-of-line tool. A second entry, `FUN_001A9254`, was not traced.

### 22.5 Traps when reading frames

- In Hyundai's DTC text, **CCP-CAN is the "H-CAN"** (U1001).
- CCP-CAN `0x2C1` has `0x291`'s layout and `0x58F` carries the `0x590` buffer: a capture that
  merges buses, or a definition with no bus, would mix them. `can_buses.yaml` has no H-CAN entry.
- opendbc names that do not apply here: P-CAN `0x523` is the MCU's, not `GW_SWRC_PE`; `0x570` is
  not `HU_TMU_P_01`; `0x2A2`, `0x050`, `0x051` and `0x060` collide with unrelated CAN-FD frames.

## 23. Requests canair must never send to this VCU

canair's guard refuses `0x11`, `0x28`, `0x29`, `0x2C`, `0x2E`, `0x34`-`0x39`, `0x3B`, `0x3D`,
`0x84`, `0x87`, `0x99`, KWP `10 85` and KWP `31 00`/`31 01`. The profile's `hk_90_session` quirk
lets `10 90` through, and session `90` needs no key. Each item below follows `10 90`, and each
also works on the functional id `0x7DF` (canair's functional allow-list refuses them there):

| request | effect | guard |
|---|---|---|
| `31 00 xx`, `31 01 xx` | **the VCU enters its bootloader** and waits for a reflash (gate: ignition input on, not EV-ready) | refused |
| `31 F1 EE FF 00 11` | **the VCU hangs** in the CCP-CAN loader with interrupts off, then probably resets on its watchdog. **No READY gate**: it would act while driving | **passes** |
| `31 F3 CC DD 33 44` | runs the test sequence; when it completes, **every periodic VCU frame stops until a reset**, and the other ECUs set U-codes | **passes** |
| `30 05 07` | the VCU stays awake until a reset (no release): a 12 V drain | passes `raw` and the REPL; `canair io` refuses a KWP `0x30` ON without a `release:` |
| `30 06`/`11`/`12 07 01`/`02` | overwrites the LDC sensor calibration in standby RAM (and so the EEPROM) | as above |
| `30 10 07` | forces output channel `0x17` on | as above |
| `30 09 07` | arms the clock's wake timer (cancels itself after about 10 min) | as above |
| `11 01` | software reset | refused |
| `28 01` | silences the VCU on P-CAN | refused |
| `23` above `0x3FFFFF` | unmapped; probably a bus error and a reset (not tested) | passes; keep reads inside the flash |
| `14 FF 00` | clears every DTC, freeze frame and timeout state: evidence loss, not a hazard | passes, by design |
| `99` | erases the VIN in the EEPROM; on the VCU it needs SecurityAccess | refused |

**Recommendation:** refuse KWP2000 `31 F1` and `31 F3` on KWP ECUs, as `31 00`/`31 01` already
are (`safety/routines.py`, `BLOCKED_KWP_ROUTINES`). On the OBC `31 F1` is a no-op, so the refusal
costs nothing there.

## 24. Surprises and traps for future reverse engineering

**Safety and the car**

- **Session `90` needs no key**, and it opens IOControl, routines and ECUReset. With the profile's
  `hk_90_session` quirk, canair can send `10 90` to the VCU. Section 23 lists what then must
  never follow; `31 F1` and `31 F3` still pass the guard.
- **The VCU serves every KWP2000 service on the functional id `0x7DF`**, not only OBD-II.
- `30 05 07` cannot be undone except by a reset.
- The boot manager accepts a CAN reflash token for about 2,000 polls after reset (14.2).

**Reading the firmware**

- Ghidra misread the KWP switch's case labels in the first pass, and `FUN_00204D5C` checks SIDs,
  not routine ids (sections 2 and 3 are corrected).
- About 700 SPE float instructions are `.word` in `listing.txt`, and Ghidra misses some task
  bodies and the PIT1 interrupt (`0x181440`-`0x18147C`, `0x181584`, `0x181590`, `0x1815D0`,
  `0x1815DC`, `0x183488`): `fn.py` does not find them.
- Ghidra drops the arguments of the DIO calls `FUN_00187C64`/`FUN_00187F54`; read the `li`
  instructions. A DIO index is not a pad number.
- The bit probes (`fields_rx.txt`, `fields_tx.txt`) miss fields behind a condition (`0x5B3`, the
  EV-ready-gated `0x201` fields). Treat them as a lower bound.
- Calibration arrays in the LDC area overlap by one element; read a table's bounds from the
  code's index range.
- The VCU works in wheel torque (1 Nm) in `0x201` and in motor torque (≈ 0.1465 Nm/bit) in
  `0x202`, `0x232`, `0x291` and `0x2C1`. They read similar numbers only by coincidence.

**Reading the captures**

- **2102 `B9`/`B10` are LDC states, not relay states** (18.3). `B10` reads 0 with HV up whenever
  the LDC is inhibited, calibrating or in standby.
- **`VCU_COOLANT_TEMP` is the LDC's own temperature sensor**, and 255 means the sensor is invalid.
- **`0x590` byte 6 is the LDC's input power**, not a loss figure.
- **2101 `B26` is not a VCU gear state**: it packs the park request and the SBW unit's state.
- **2101 speed is motor rpm with a 2.0 m tyre**, hence the +3.6 % bias.
- **The VCU goes silent on diagnostics in after-run** (15.2), and its model outputs freeze there.
- The LDC set-point ramps at 0.2 V/s and the charge-current cap lowers it within seconds, so the
  12 V level is noisy for the first minutes of READY. The `0x592` voltage mode does not change it.
- `0x5DF` bits 2-3 are overridden by calibration and do nothing on this car.
- Timeouts of chassis and MCU frames are ignored while plugged in, and every timeout is
  suppressed outside the supply window and for 0.5 s after wake.
- TesterPresent is refused in session `81` (NRC `0x80`); canair's keepalive there is harmless.
- `0x200` bit 28 is not "close contactors" here, and `0x523`/`0x570` are not opendbc's frames.

## 25. Opportunities

**Profile edits that need no car** (to do with `canair pids`/`signals`, in a separate change):

- 2102: rename `B10` to an LDC-running signal and `B9` to an LDC-state-3-or-above signal, name
  `B13` "LDC output limited" and `B12` "LDC fault warning", mark `B11` constant 0, and rename
  `VCU_COOLANT_TEMP` to the LDC temperature (255 = invalid). Add the firmware ADC scales to the
  2102 voltage notes.
- 2101: `B10` bit 4 = shifter fault; `B11:2` = DIO 5, the start signal (probable); `B23:2` = the
  ignition input (probable; the current name `VCU_INVERTER_ENABLED` needs checking); `B26` = park
  request + SBW state; the speed's 2.0 m tyre.
- DTCs: add P1A8A, U1005, U0103 (cannot be set), P1676, P1690, P1696, P1699; add the firmware
  frame lists to the U-codes, the LDC thresholds, and the confirm and lamp classes.
- The research entry "LDC output current": result = measured, never published; derive it as
  `VCU_AUX_POWER / VCU_LDC_OUTPUT_VOLTAGE`.
- A research entry for the `12 00 00` freeze-frame layout (21.4), readable in session `81`.
- `signals/`: `0x201` driver wheel torque, `0x202` torque command and pre-TCS torque, `0x291` MCU
  torque and rpm, `0x200` pedal, power state, EV-ready and the 4-bit gear, `0x109` park request
  and torque limit, `0x10C` SBW state, `0x153` `TCS11`, `0x594` SoC and power limits, `0x598`
  remaining energy, `0x5B3` Eco maximum speed (and its `0x579` 56\|8 echo), `0x590` bytes 3, 5,
  6, `0x010` crash bit, `0x490` `EPB_STATUS`, `0x507` lamps, `0x523` temperatures.
- Vehicle-state predicates: LDC running (2102 `B10`), LDC inhibited with HV up (`B9` and not
  `B10`), VCU fault latch (2101 `B25` = 8), HV up but not READY (`B25` = 2), low-SoC stop
  (`B25` = 9).

**Captures to propose (each needs the user's approval):**

- 2102 at 1-2 s through a READY start and through a top-up start: the ramp, the current cap, and
  `B9` before `B10`.
- 2101 `B11:2` and `B23:2` through a start-button press with and without the brake, and in
  ON-not-READY: names DIO 5 and DIO 4.
- A 2101 poll through power-off: the after-run silence gives the after-run time.
- `18 00 FF 00` after a known frame loss: names the missing ECU through the U-code groups.
- `12 00 00` when a DTC is stored.

**With a P-CAN tap:** full-pedal starts in each drive mode (peak `0x202` torque names the
modes), coasting at each regen level (`0x201`), a P→D→P shift (`0x109`/`0x10C`), a traction event
(`0x153` against `0x202`), `0x590` bytes 3/5/6 and `0x592`.

**On a bench EPCU with CCP-CAN** (never on the car): the CPU load `0x40004972` and the task trace,
the LDC event ring `0x40000BD4`, the DTC records `0x400000AE`, `0x58F`, and whether the clock's
`INT` pin reaches the regulator enable.

## 26. Not established after pass 4

- How the VCU asks the BMS to close the contactors (20.2), and the charge-control state machine.
- Brake blending, brake-accelerator override, limp-home modes, shift and auto-P conditions,
  cruise-control inputs.
- The DSPI B device, the PIT3 0.5 ms task, the 1 ms supervisor `FUN_00196B5C`, the 20 ms task, and
  the unmapped code ranges of section 16.
- Which eSCI drives the LIN battery sensor, and its frame layout.
- The pins of DIO 21, 22, 28 and channel `0x17`, and the eMIOS 3 PWM input.
- The senders of `0x462`, `0x4FE`, `0x570`, `0x5DF` and `0x418`-`0x41C`; most `0x592` fields.
- The `0x18` status filters and the `0x17` format; LDC events 5, 10, 14 and 17; the P16xx module.
- Whether the clock's alarm powers the EPCU (17.5).
