---
# Paths are relative to the repository root. The firmware image is local-only (never committed):
# it holds the manufacturer's software. See "Provenance".
title: "OBC firmware findings: the KWP2000 server, every 2101/2102 byte, the CAN frames"
subject: >-
  The 2017 Ioniq Electric's on-board charger (OBC, KWP2000 0x7E5, part 36400-0E150, sw
  EAEEH5L-OS3-D000), read from a flash image with ReadMemoryByAddress and analysed statically:
  the diagnostic server and its session matrix, the 0x21/0x1A/OBD-II records, the source of every
  2101 and 2102 byte, the P-CAN frames it reads and sends, the AC charge-current setting, and the
  requests that canair's guard lets through but must never be sent
created: 2026-11-08
updated: 2026-11-08
status: active
verification: >-
  Static reverse-engineering of the OBC flash image (Ghidra 12.2, PowerPC e500 language for the
  SPE float instructions), cross-checked against this car's 13,346 stored OBC 2101 captures and
  the 2102 captures. The 2101 byte map reproduces every verified profile signal and a full live
  payload byte for byte. The 0x57F, 0x581, 0x58E and 0x5D0 layouts agree with the profile's
  signals/, the Kona bench notes, uhi22's DBC and uhi22's two Ioniq P-CAN logs. Nothing in this
  document was tried on the car.
sections:
  - section: "3"
    status: integrated
    date: 2026-11-08
    note: >-
      The session matrix: 10 90 opens without SecurityAccess, and 0x30, 0x31 and 0x11 then need
      none. The OBC iocontrol_scan research entry is closed (no LED LID; do not send 0x30), and
      the obc.yaml header comment names the forbidden requests.
    target: profiles/hyundai-ioniq-ae-ev/ecus/obc.yaml
  - section: "5"
    status: integrated
    date: 2026-11-08
    note: >-
      Every varying 2101 byte is now a signal. LDC_TEMP, LDC_OUTPUT_V and LDC_HV_INPUT_V became
      OBC_TEMP, OBC_12V_SUPPLY_V and OBC_BATTERY_SIDE_V (AAF_LDC_TEMP became AAF_OBC_TEMP);
      OBC_CHARGE_STATE became OBC_PILOT_V; OBC_AC_NOT_CONNECTED, OBC_MODE and OBC_DC_CHARGE_MODE
      became OBC_PROXIMITY_V; OBC_SESSION_FLAGS became its bits. 2102 has its 14 words. The
      constant header and bits are in the PID notes, not signals.
    target: profiles/hyundai-ioniq-ae-ev/ecus/obc.yaml
  - section: "7"
    status: integrated
    date: 2026-11-08
    note: >-
      The AC charge-current setting arrives on P-CAN 0x414 byte 0 and scales the pilot current
      (station 100/90/60 %, portable 100/75/50 %). In the OBC charge-current research entry (still
      pending a bus check), the AVN entry, the 0x562 entry (closed), and signals/ (0x414, 0x58E).
    target: profiles/hyundai-ioniq-ae-ev/ecus/obc.yaml
  - section: "9"
    status: integrated
    date: 2026-11-08
    note: >-
      The guard now refuses SID 0x99 and KWP2000 31 00/31 01 (safety/blocklist.py,
      docs/concepts/safety.md). 0x30 06/07 stays refused by canair io only; raw would send it.
relations:
  expands_on:
    - path: profiles/hyundai-ioniq-ae-ev/ecus/obc.yaml
      note: >-
        The identity block, the 2101/2102 records, the DTC list, and the research entries on the
        2101 bytes, the B20:B21 word, the LDC host, the charge-current setting and the 0x30 scan.
    - path: plans/2026-11-05-read-memory-by-address.md
      note: The memory-read work that produced the image analysed here (KWP2000 results).
  related:
    - path: profiles/hyundai-ioniq-ae-ev/research/VCU_FIRMWARE_FINDINGS.md
      note: >-
        The VCU's firmware. Same bootloader and microcontroller family, the same server layout
        (session matrix, 0x1A table, routines F1/F3), and the VCU's side of 0x57F.
    - path: profiles/hyundai-ioniq-ae-ev/research/IGPM_FINDINGS.md
      note: Section 8 (the Kona OBC's frames, the charge-port lock handshake, 0x414 on the Kona).
    - path: profiles/hyundai-ioniq-ae-ev/research/IONIQ_AVN_FW_FINDINGS.md
      note: HU_EV_PE_00 (MM-CAN 0x1AA) byte 3, the head unit's charge-current setting.
    - path: profiles/hyundai-ioniq-ae-ev/signals/powertrain.yaml
      note: OBC1 (0x57F), BMS_597, EMS20 (0x200), checked in section 6.
sources:
  - name: OBC flash image
    kind: >-
      dump of the OBC (36400-0E150, sw EAEEH5L-OS3-D000) read on 2026-11-08 with
      ReadMemoryByAddress over KWP2000 while AC charging; holds the manufacturer's software
    location: references/firmware/obc-36400-0E150-EAEEH5L-OS3-D000/ (local only, never committed)
  - name: Ghidra 12.2
    kind: static analysis (PowerPC:BE:32:e500); scripts kept beside the image in analysis/
  - name: this car's captures
    kind: OBC 2101/2102 reads, 2026-04 to 2026-11-08
    location: profiles/hyundai-ioniq-ae-ev/captures/
  - name: projectgus Kona EV bench and logs
    kind: the Kona OBC's 0x581/0x58E/0x414 behaviour, via IGPM_FINDINGS.md section 8
---

# OBC firmware findings: diagnostic server, records, CAN frames

This collects what a static read of the OBC's flash image shows, cross-checked against this car's
captures. The image stays on the machine (it is the manufacturer's software). Only facts derived
from it are here. Function and data addresses are image offsets or RAM addresses, as in the VCU
document.

## 1. Provenance and how much to trust it

The image was read on 2026-11-08 with `canair memory image OBC --range 000000-3FFFFF` over
KWP2000 ReadMemoryByAddress (`0x23`), `wican-ws`, while the car was AC charging. The data areas
were read in full and the erased areas were sampled (61 bytes every 1 KiB). Two sampled areas read
`00`, not `FF`: `0x140000`-`0x14203C` and `0x19B040`-`0x19BC3C`. The `obc_flash_ff.bin` file fills
them with `FF`, so their contents are not known. No code in the analysed program reads either
area, as far as the decompiler shows.

Evidence classes, strongest first:

- **This car's live captures.** Where they disagree with the firmware reading, they win.
- **The firmware image.** Values stored in RAM (`0x40000000`+) are not in the image, so a
  RAM-backed value is live-only. A name given to a firmware variable here comes from what the code
  does with it (a DTC threshold, a CAN frame, a known physical relation), and each is marked
  **probable** where only that kind of evidence supports it.

No SecurityAccess (`0x27`) seed or key logic was analysed, searched for, or recorded. The handler
exists (section 3); its contents were not read.

## 2. Image layout and platform

| area | contents |
|---|---|
| `0x000000`-`0x0065FF` | bootloader (`00 5A` RCHW at 0; the first 4 KiB match the VCU's byte for byte) |
| `0x00FFC0`-`0x010EFF` | identity and calibration block: `AEEOBC51` at `0xFFF6`, `AEEOES08R0` at `0x10000`, `00000001` at `0x10014`, `AEEVOBC_A0000163201505 14` at `0x1003C`; calibration constants (floats and words) from `0x10080` |
| `0x143000`-`0x1437FF` | interrupt vector table (pointers into the program) |
| `0x144000`-`0x1440EF`, `0x145000`-`0x14518F` | interrupt entry and start-up code |
| `0x180000`-`0x1BEE0F` | application program and constant tables |
| `0x1BEE10`-`0x1C0AFB` | `.data` load image, copied to RAM `0x40002000` at start-up |

- **CPU.** 32-bit PowerPC with the SPE single-precision float instructions (`efsadd`, `efsmul`,
  …; about 1,600 words carry the SPE opcode), the MPC55xx interrupt controller at `0xFFF48000`
  and an eTPU at
  `0xC3FC0000`. That fits an MPC55xx e200z part, as the VCU is. The exact part was not determined.
  Ghidra's `PowerPC:BE:32:e500` language decodes the SPE instructions; the VCU project's
  `MPC8270` language does not.
- **Small-data bases** (start-up code at `0x1450F4`): r13 = `0x40003CEC`, r2 = `0x00198978`.
  `.data` is `0x40002000`-`0x40003CEB`; `.bss` is cleared from `0x40003CEC` to `0x40009304`. The
  start-up copy loop copies `0x806E` bytes, which runs past the end of the programmed image. The
  `.bss` clear then overwrites the copy up to `0x40009304`, so only RAM `0x40009305`-`0x4000A06D`
  keeps copied bytes, from flash `0x1C6115`-`0x1C6E7D`, which sampled as erased (`FF`).
- **RAM `0x40000000`-`0x40001FFF` is not cleared at start-up.** It holds values that survive a
  reset: the sensor calibration (`0x40001E0C`-`0x40001E57`), the VIN (`0x40001FC0`), the
  charge-current settings and the charge-port lock mode (`0x400019FC`-`0x40001A00`, section 7),
  the OBD-II distance and warm-up counters (`0x40001A04`-`0x40001A14`).
- **External EEPROM.** The VIN (`1A90`) and `1A87` are written to an SPI EEPROM (read-status
  `05`, write-enable `06`, write `02`) at addresses `0x1FC0` and `0x1FA0`.
- **ECU name.** OBD-II Mode 09 PID 0A returns `BCCM-B+ChargerCtrl` (flash `0x1C043C`).
- **Two CAN layouts.** A constant at `0x198B28` (`1` in this build) selects which set of RX
  signal variables the application uses. Layout 2's variables (`0x40004134`-`0x4000423F`) are
  never filled in this build, so layout 1 is the only active one.

The analysis project is in `$TMPDIR/opencode/obc/ghidra` and is rebuilt by
`analysis/ghidra_prep.java` (RAM blocks, small-data bases and function seeds). It holds 1,002
functions and covers 253 KB of the 284 KB code area.

## 3. The KWP2000 server and its session matrix

The server (`FUN_0018F5D8`, task `FUN_0018FE18`) switches on the request SID, one handler per
service. A positive reply is `SID + 0x40`; a negative reply is `7F <SID> <NRC>`. The server does
its own ISO-TP segmentation. It accepts requests on `0x7E5` and on the functional id `0x7DF`, and
answers on `0x7ED`.

### 3.1 Services

| SID | service | handler | note |
|---|---|---|---|
| `01`, `02`, `03`, `04`/`14`, `07`, `09`, `0A` | OBD-II modes 1, 2, 3, 4, 7, 9, 10 | `FUN_0018A45C` … | no session check (section 4.4) |
| `10` | StartDiagnosticSession | `FUN_0018C388` | `81`, `85`, `90`, `F1`; others NRC `0x12` |
| `11` | ECUReset | `FUN_0018CA38` | only `11 01`; then restarts through `FUN_00193328` |
| `12` | ReadFreezeFrameData | `FUN_0018E064` | |
| `14` | ClearDiagnosticInformation | `FUN_0018CBA8` | shares the handler with OBD-II mode 4 |
| `17`, `18` | ReadStatusOfDTC, ReadDTCByStatus | `FUN_0018D388`, `FUN_0018D7C0` | |
| `1A` | ReadEcuIdentification | `FUN_0018CEE0` | section 4.2 |
| `20` | StopDiagnosticSession | `FUN_0018E948` | back to `81` |
| `21` | ReadDataByLocalIdentifier | `FUN_0018E5CC` | section 4.1 |
| `23` | ReadMemoryByAddress | `FUN_0018EA6C` | 3-byte address, 1-254 bytes, **no address check** |
| `27` | SecurityAccess | `FUN_0018C698` | not analysed (project rule) |
| `28`, `29` | Disable/EnableNormalMessageTransmission | `FUN_0018E308`, `FUN_0018E46C` | |
| `30` | InputOutputControlByLocalIdentifier | `FUN_0018E75C` → `FUN_001BDE38` | section 4.3 |
| `31` | StartRoutineByLocalIdentifier | `FUN_0018F388` | section 4.3 |
| `3B` | WriteDataByLocalIdentifier | `FUN_0018EDA0` | `1A87` and `1A90` only |
| `3E` | TesterPresent | `FUN_0018C530` | `3E 00` replies, `3E 80` is silent |
| `99` | (manufacturer) | `FUN_00189FB8` | **erases the stored VIN**, no session check, no reply |
| `22`, `2C`, `2E`, `3D` | | one-line handlers | always NRC `0x12` |
| `34`, `36`, `37` | | empty cases | no reply |

An unknown SID below `0x10` gets no reply; any other unknown SID gets NRC `0x11`.

### 3.2 The session matrix

`FUN_0018FE98` reads a matrix at `0x190C5C` (5 bytes per row: SID, then one byte for each of the
sessions `81`, `85`, `90`, `F1`). Bit 0 = allowed in that session (else NRC `0x80`), bit 1 = also
needs SecurityAccess (else NRC `0x33`).

| SID | `81` | `85` | `90` | `F1` |
|---|---|---|---|---|
| `10` StartDiagnosticSession | yes | yes | yes | no |
| `20` StopDiagnosticSession | yes | yes | yes | no |
| `3E` TesterPresent | **no** | yes | yes | no |
| `27` SecurityAccess | no | yes | yes | yes |
| `11` ECUReset | no | secured | **yes** | no |
| `1A`, `21`, `23` | yes | yes | yes | no |
| `28`, `29` | no | yes | yes | no |
| `3D` (stub) | secured | no | secured | no |
| `2C` (stub) | yes | no | yes | no |
| `3B` WriteDataByLocalId | no | secured | secured | no |
| `22`, `2E` (stubs) | no | no | no | no |
| `34`, `36`, `37` (empty) | no | secured | no | no |
| `12`, `14`, `17`, `18` | yes | no | yes | no |
| `31` StartRoutine | no | secured | **yes** | secured |
| `30` IOControl | no | no | **yes** | no |

What this means:

- **`10 90` needs no SecurityAccess.** The `0x10` handler switches to `85`, `90` or `F1` as soon
  as the request names one. Only the services in the matrix are gated. This matches the car: the
  2026-07-22 probe found `81` accepted and `82`/`83` refused with NRC `0x12`. `85`, `90` and
  `F1` were not tried. canair hard-blocks `10 85`; the profile's `hk_90_session` quirk lets
  `10 90` through.
- **In session `90`, IOControl (`0x30`), StartRoutine (`0x31`) and ECUReset (`0x11`) need no
  SecurityAccess.** Section 4.3 has what they do.
- **TesterPresent is refused in the default session** (NRC `0x80`). It is accepted only in `85`
  and `90`.
- **Session timeout.** `FUN_00190168` drops back to `81` after 500 server cycles in `90` or `F1`,
  or 6,000 in `85`, without a TesterPresent. The cycle period was not measured.
- **DTC services `0x12`/`0x14`/`0x17`/`0x18` are not allowed in `85`.**

## 4. The DID, actuator and routine inventory

### 4.1 Service 0x21 — two records

Table at `0x1BE674`, 8 bytes per record (id, length, 2 pad bytes, source pointer), count at
`0x198B3E` = 2.

| DID | data bytes | source | profile |
|---|---|---|---|
| `2101` | 42 | RAM `0x40008DA6` | the live record (`response_frames: 7` ✓) |
| `2102` | 32 | RAM `0x40008DD0` | the calibration record (34-byte payload ✓) |

No other `21xx` exists, which matches the 2026-07-22 scan (only 2101 and 2102 answer, no 21F2).
Before each read, `FUN_001BDBBC` copies a staging struct (`0x40003FA0` for 2101, `0x40003FCA` for
2102) into the record and swaps each 16-bit pair, so every 16-bit value goes out big-endian.
Section 5 has the byte map.

### 4.2 Service 0x1A — 17 records

Table at `0x1BE5A8`, 12 bytes per record (id, length, pad, source pointer, read flag, write flag,
pad), count at `0x198B44` = 17. As on the VCU, only a record with read flag `1` answers `0x1A`;
flag `2` gives NRC `0x12`. A write needs write flag `1` (service `0x3B`, section 3).

| DID | len | source | read | write | content |
|---|---|---|---|---|---|
| `1A80` | 21 | RAM `0x40008D80` | 1 | - | built by `FUN_0018CDEC` from `1A91` + `1A94`: `36400-0E150AEEOES08R0` |
| `1A84` | 1 | RAM | 2 | - | |
| `1A86` | 1 | RAM | 2 | - | |
| `1A87` | 1 | RAM `0x40001FA0` (EEPROM `0x1FA0`) | 1 | **1** | one byte; `3B 87` accepts only `00`, `01`, `02`. No application code reads it |
| `1A88` | 2 | RAM | 2 | - | |
| `1A89` | 1 | RAM | 2 | - | |
| `1A8A` | 10 | flash (`.data`) | 1 | - | ten spaces |
| `1A8B` | 4 | flash `0x10054` | 1 | - | `1E 09 0D 14` (the same bytes as the VCU's) |
| `1A8C` | 8 | flash `0xFFF6` | 1 | - | `AEEOBC51` — ecu_id (profile ✓) |
| `1A8D` | 16 | flash (`.data`) | 1 | - | `EAEEH5L-OS3-D000` — sw_id (profile ✓) |
| `1A8E` | 8 | flash `0x10014` | 1 | - | `00000001` — calibration (profile ✓) |
| `1A90` | 17 | RAM `0x40001FC0` (EEPROM `0x1FC0`) | 1 | **1** | VIN — sensitive |
| `1A91` | 11 | flash (`.data`) | 1 | - | `36400-0E150` — part number (profile ✓) |
| `1A92` | 1 | RAM | 2 | - | |
| `1A94` | 10 | flash (`.data`) | 2 | - | `AEEOES08R0` |
| `1A98` | 10 | flash `0x10066` | 1 | - | `20` then nine `00` |
| `1A99` | 4 | flash `0x10062` | 1 | - | `1E 09 0D 14` |

The readable set (80, 87, 8A-8E, 90, 91, 98, 99) is a firmware prediction; only 8C/8D/8E/91 were
read live (the profile's identity block).

### 4.3 Services 0x30 and 0x31 — the end-of-line functions

**IOControl (`0x30`), session `90` only.** `FUN_001BDE38` parses
`30 <LID> <param> <op> [<value hi> <value lo>]`:

- `30 00 01` (reportCurrentState of LID 0) returns `70 00 01 06 00 00 00`: a support bitmap that
  lists LIDs `06` and `07`.
- Every other LID needs the frame length 4 or 6 (else NRC `0x79`), a per-LID enable flag set to
  `1` (else NRC `0x22`) and `param = 07` shortTermAdjustment (else NRC `0x31`). Then it stores
  the request and answers NRC `0x78` (response pending); a background task does the work.
- Only LIDs `06` and `07` are enabled, at start-up (`FUN_001B4A18`). No code ever enables
  `01`-`05`, `20`, `40`, … `E0`.
- **LID `06` is a two-point gain and offset calibration of five sensor channels.** It steps
  through the channels, compares each reading with the reference value in the request, and
  stores new gains and offsets at `0x40001E10`/`0x40001E24` with the marker `0xCDC1`. While it
  runs it calls `FUN_001B7E0C`, which switches output 9 on and programs eTPU PWM channels at a
  calibrated frequency: it runs the power stage. It aborts when the voltage at `0x40002304`
  (2101's `OBC_CHARGE_V`) is below 100 V. **LID `07`** probably does the same for the three
  current channels (marker `0xCDC2`, reference values ×0.001); its steps were not traced, so
  whether it also runs the power stage is not known.
- So the OVMS Soul EV reading (`30 {led} 00` lights the charge-port LED) does not apply here:
  `30 01 00` gets NRC `0x80` in session `81`, and NRC `0x79` in session `90`. The research entry's
  LED probe would do nothing, and a `0x30` sweep could start a calibration run.

**StartRoutineByLocalIdentifier (`0x31`).** The same shapes as the VCU:

- `31 00 xx` and `31 01 xx` (length 3), session `90` without SecurityAccess (or `85`/`F1` with
  it), if `FUN_001BE324` passes (an input-voltage condition and a bit of the VCU's `0x200`): it
  disables interrupts and calls the bootloader's main loop (`FUN_00004048`). **The OBC leaves its
  application and waits for a reflash.**
- `31 F1 EE FF 00 11` replies and does nothing (`FUN_0018995C` is empty).
- `31 F3 CC DD 33 44` replies and sets a flag at `0x400020CF`, effect not traced.

### 4.4 OBD-II (SAE J1979)

The OBD-II modes have no session check. They answer on `0x7E5` and on the functional `0x7DF`.

- **Mode 01.** Table at `0x1BE4D0` (12 bytes per PID), count at `0x198B41` = **4**: PIDs `01`
  (monitor status, 4 bytes), `21` (distance with MIL on, 2), `30` (warm-ups since DTC clear, 1),
  `31` (distance since DTC clear, 2). These are the four that answered live on
  2026-11-01. The table holds 11 more (`03`, `04`, `05`, `06`, `07`, `0B`, `0C`, `0D`, `11`,
  `51`, `5B`) past the count.
- **No supported-PIDs reply.** The entries for `00`, `20`, … `E0` at `0x1BE470` all carry flag
  `02`, not `01`, so the server stays silent. That is why `01 00`, `01 20` and `01 40` gave NO DATA
  live. The same holds for Mode 09 PID `00` (`0x198B2C`).
- **`01 31` is the odometer difference** since the last clear: `FUN_001BD8C8` subtracts the
  odometer at the clear (`0x40001A08`, kept across resets) from the current one. The odometer is
  the gateway's `0x5D0` (24-bit ×0.1 km, section 6.2). `01 30` counts the upward steps of an
  internal counter (`FUN_0019155C(0)`) and saturates at 255; `01 21` is fixed at `00 00`.
- **Mode 09.** Table at `0x1BE584`, count 3: PID `04` calibration ID (16 bytes, the sw_id), `06`
  CVN (4 bytes, RAM `0x40000000`), `0A` ECU name (20 bytes, `BCCM-B+ChargerCtrl`). Not tried live.

## 5. Record internals — the source of every 2101 and 2102 byte

The staging structs are filled by a table-driven builder (`FUN_001A27B8`) from descriptor tables
in `.data`: 2101's at `0x40002C44` (21 entries), 2102's at `0x40002DA4` (14 entries). Each entry
is `type, source, destination, scale`; the types convert a float, u8, u16 or s16 source. The
decoder is `analysis/records.py`. `B` indices are WiCAN (`canair bix` confirms them on a live
payload).

### 5.1 Record 2101

Checked against the payload of 2026-11-08 (charging at a 6 A pilot):
`6101 FFFFF800 00 99EA 9649 5777 010B 99 024B 00BB 05BC 43 1020 00C8 0210 0062 76 0012 000B 01F3
963E 7388 0013`.

| bytes | data | firmware source | meaning | profile signal before 2026-11-08 | verdict |
|---|---|---|---|---|---|
| `B4-B7` | D0-D3 | constants | header `FF FF F8 00` | - | constant |
| `B9` | D4 | `0x40000A00` | latch: 1 after the AC input current stayed above 22 A for 70 cycles in charger state 12; cleared once when 0x57F byte 1 bit 0 sets | - | new (0/1 in READY captures) |
| `B10:B11` | D5-6 | `0x40002304` ×100 | voltage, ADC channel | `OBC_CHARGE_V` | ✓ |
| `B12:B13` | D7-8 | `0x40002314` ×100 | voltage, ADC channel | `OBC_OUTPUT_V` | ✓ |
| `B14:B15` | D9-10 | `0x40002324` ×100 | AC input voltage (RMS) | `AC_INPUT_V` | ✓ |
| `B17:B18` | D11-12 | `0x400022A4` ×100 | DC output current | `OBC_DC_A` | ✓ |
| `B19` | D13 | `0x400023B4` + 100 | **an OBC thermistor**: one of its own ADC channels through an 18-point table (`0x10224`/`0x1026C`) | `LDC_TEMP` | **misnamed** |
| `B20:B21` | D14-15 | `0x40002384` ×100 | **control-pilot voltage**: 0.50-0.53 V unplugged, 8.9 V state B, 5.8-5.9 V state C | `OBC_CHARGE_STATE` (B20 only) | **B20 is the high byte** |
| `B22:B23` | D16-17 | `0x40002334` ×100 | **proximity (PP) voltage** (probable): 4.44-4.45 V unplugged, 1.86-1.88 V with the AC plug, 3.71 V with the CCS plug | `OBC_AC_NOT_CONNECTED` (B22:0), `OBC_MODE` (B23), `OBC_DC_CHARGE_MODE` (B23:6) | **one 16-bit word** |
| `B25:B26` | D18-19 | `0x40002344` ×100 | **the OBC's own 12 V supply**, an ADC channel | `LDC_OUTPUT_V` | **misnamed** |
| `B27` | D20 | bit field | bit 0 = 0x57F byte 0 bit 7 (set at start-up), bit 1 = 0x57F byte 0 bit 0, bit 2 = 0x57F byte 0 bit 6 (`OBC_FAULT`), bit 3 = 0x57F byte 5 bit 2, bit 4 = 0x57F byte 4 bit 5, bits 5-6 = calibration byte `0x10383` + 1 (= 2) | - | new (`0x41`/`0x43` seen) |
| `B28:B29` | D21-22 | BMS `0x597` bytes 3-4 LE ×0.1, ×10 | charge voltage limit from the BMS (probable): 412.8 V | - | new |
| `B30:B31` | D23-24 | BMS `0x597` bytes 5-6 LE ×0.1, ×10 | charge current limit from the BMS (probable): 20.0 A | - | new |
| `B33:B34` | D25-26 | `0x40002374` ×100 | AC input current | `AC_INPUT_A` | ✓ |
| `B35:B36` | D27-28 | `0x400023C0` ×1000 | pilot duty (fraction ×1000) | `OBC_PILOT_DUTY` (`/10`) | ✓ |
| `B37` | D29 | bit field | bits 0-1 = 0x57F byte 0 bits 1-2 (`OBC_PLUG_STATE`), bit 2 = byte 0 bit 4 (`OBC_EVSE_READY`), bit 3 = byte 0 bit 3, bit 4 = byte 4 bit 1 (`OBC_B4_1`), bit 5 = sensor calibration 1 present (`0xCDC1`), bit 6 = calibration 2 present (`0xCDC2`) | `OBC_SESSION_FLAGS`, `OBC_CABLE_SESSION` (B37:4) | bits named |
| `B38:B39` | D30-31 | `0x400037B0` ×100 | zero offset (A) of input current sensor A, measured before each charge | - | new |
| `B41:B42` | D32-33 | `0x400037B8` ×100 | zero offset (A) of the output current sensor | - | new |
| `B43:B44` | D34-35 | `0x40002364` ×100 | an ADC channel that reads 4.99-5.00 V in every state: the 5 V sensor supply (probable) | - | new |
| `B45:B46` | D36-37 | `0x40002394` ×100 | **HV voltage at the OBC's output terminals, battery side of its output relay**, an ADC channel | `LDC_HV_INPUT_V` | **misnamed** |
| `B47`, `B49` | D38-39 | `0x400022F4` ×100 | one 16-bit value split by the PCI byte `B48`: an instantaneous AC voltage sample, 0 to 317 V while charging (the peak of 224 V RMS) | - | new (was "checksum-like") |
| `B50:B51` | D40-41 | `0x400037B4` ×100 | zero offset (A) of input current sensor B | - | new |

Notes on the verdicts:

- **The three `LDC_*` signals are the OBC's own measurements.** All three come from the OBC's
  ADC, not from CAN. The firmware has no DC-DC converter control. This answers the research
  entry "which module hosts the LDC": the LDC is the EPCU's (uhi22's teardown), and these bytes
  were named after it by mistake. The data agrees: `B45:B46` reads the pack voltage at READY
  while `OBC_OUTPUT_V` reads about 4 V, so it is on the battery side of the OBC's output relay
  (the "output relay voltage sensor" of P1E16/P1E19 is the probable match).
- **`OBC_CHARGE_STATE` is the coarse pilot voltage.** `B20` = volts / 2.56, so its 0/2/3 are
  "under 2.56 V", "5.12-7.67 V" (state C) and "7.68-10.23 V" (state B). The US logs' 1 and 4 are
  2.56-5.11 V and 10.24-12.79 V. The research entry on `[B20:B21]` is answered: it is one word,
  `[B20:B21]/100` volts.
- **`B22:B23` is one word.** `OBC_AC_NOT_CONNECTED` (B22 bit 0) is set when the word is at least
  2.56 V; `OBC_DC_CHARGE_MODE` (B23 bit 6) is set by 3.71 V (`0x0173`) and not by 1.87 V or
  4.45 V. Both match the captures by accident of the encoding. The PP reading is probable: the
  three levels match the unplugged, Type 2 AC and CCS states, and the DTCs P0D58/P0D59 test the
  proximity circuit against 0.5 V, 1 V and 4.9 V.
- **The offset bytes** (`B38:B39`, `B41:B42`, `B50:B51`) are averaged over 1,000 samples while
  the AC input is below 65 V, before the input relay closes, and checked against ±1.5 A (the
  P0D39/P0D52 thresholds). A u16 cannot hold a negative offset; it reads 0 then.

### 5.2 Record 2102 — the stored sensor calibration

`B4-B7` are a constant header `FF FC 00 00`. The 14 words from `B9` are seven pairs of (offset,
gain) of the end-of-line sensor calibration (section 4.3), each stored as `(value + 30) × 1000`,
so decode `[Bx:By]/1000 - 30`. A gain near 1.0 encodes as `0x79xx`, which is why every fourth byte
is `0x79`. Two words straddle a PCI byte (`B16`, `B32`); write them as `(B15*256+B17)` and
`(B31*256+B33)`, because `[B15:B17]` would include the PCI byte. Live (2026-08-10):

| offset word | gain word | channel | offset | gain |
|---|---|---|---|---|
| `[B9:B10]` | `[B11:B12]` | the AC voltage sample (2101 `B47`/`B49`) | -3.297 | 1.018 |
| `[B13:B14]` | `B15`, `B17` | AC input voltage (2101 `B14:B15`) | -3.046 | 1.016 |
| `[B18:B19]` | `[B20:B21]` | 2101 `B10:B11` | -1.708 | 1.015 |
| `[B22:B23]` | `[B25:B26]` | 2101 `B12:B13` | -4.129 | 1.003 |
| `[B27:B28]` | `[B29:B30]` | AC input current, one-sensor path (2101 `B33:B34`) | -0.151 | 1.007 |
| `B31`, `B33` | `[B34:B35]` | AC input current, two-sensor sum | -0.669 | 1.019 |
| `[B36:B37]` | `[B38:B39]` | DC output current (2101 `B17:B18`) | -0.174 | 1.025 |

The record is static because the calibration only changes on an end-of-line run. The `B45:B46`
channel's calibration (`0x40001E1C`/`0x40001E30`) is not in 2102.

## 6. The CAN layer

### 6.1 The slot table

`FUN_00185AAC` registers 50 slots with `FUN_001863B8(id, slot, dir, controller, class)`, all on
one controller (P-CAN). Frame buffers sit at `0x40003E56 + 8 × n`, where n is the slot for slots
`00`-`02` and the slot minus `0x14` from slot `17` on. `FUN_001BCFCC` copies each
received frame into a working copy **with its bytes reversed**, so a decompiled `CONCAT11(w0, w1)`
of the copy is the little-endian word at frame bytes 6-7. The layouts below are in frame bytes.

| slot | id | dir | class | what the OBC reads or sends |
|---|---|---|---|---|
| `00` | `57F` | TX | 0 | OBC1, section 6.3 |
| `01` | `581` | TX | 0 | measurements, section 6.3 |
| `02` | `58E` | TX | 0 | state and settings, section 6.3 |
| `03` | `6AC` | RX | 0 | probably a CCP command channel (16 TX slots on `6AD`), as on the VCU; not analysed |
| `04`-`13` | `6AD` | TX | 0 | |
| `14`, `15`, `16` | `7DF`, `7E5`, `7ED` | RX, RX, TX | 0 | diagnostics |
| `17` | `57B` | RX | 3 | VCU status (copied, fields not traced) |
| `18` | `594` | RX | 3 | byte 5 ×0.5 (BMS) |
| `19` | `595` | RX | 3 | bytes 6-7 LE ×0.1 (BMS) |
| `1A` | `597` | RX | 3 | bytes 3-4 LE ×0.1 = charge voltage limit, bytes 5-6 LE ×0.1 = charge current limit (to 2101 `B28`-`B31`) |
| `1B`, `1C` | `599`, `59C` | RX | 3 | BMS; `599` copied, `59C` buffered only |
| `1D` | `523` | RX | 0 | EPCU: byte 6 ×20, byte 4 − 40, byte 2 − 40 |
| `1E` | `570` | RX | 3 | EWP: bytes 0-1 ×20 |
| `1F` | `5F5` | RX | 3 | byte 6 ×0.5 − 40 (a temperature) |
| `20`, `25` | `5E8`, `5E9` | RX | 3 | copied |
| `21` | `200` | RX | 0 | VCU EMS20; byte 5 bit 5 gates `0x11`/`0x14`/`0x31`/`0x3B` (`FUN_001BE324`) |
| `22` | `386` | RX | 0 | wheel speeds, bytes 4-5 and 6-7 14-bit ×0.03125 |
| `23` | `524` | RX | 0 | EPCU, copied |
| `24` | `5E6` | RX | 3 | buffered only |
| `26` | `5EC` | RX | 3 | gateway; the IGPM's charge-port lock answer on the Kona |
| `27`, `28`, `2F` | `5A3`, `5D5`, `5D7` | RX | 3 | BMS, copied |
| `29` | `5D0` | RX | 6 | gateway odometer, bytes 0-2 LE ×0.1 km (to OBD `01 31`) |
| `2A` | `590` | RX | 3 | VCU: byte 4 ×0.1 |
| `2B` | `567` | RX | 0 | gateway clock; byte 4 bit 0 (clock valid) gates the charge-current setting |
| `2C` | `5DF` | RX | 1 | gateway; bits 6-13 of bytes 0-1 must read `0x60`, `0x67`, `0x68` or `0x70` for the setting to count |
| `2D` | `414` | RX | 3 | **the AC charge-current setting**, section 7 |
| `2E` | `587` | RX | 3 | TMU remote command (copied) |
| `30` | `579` | RX | 3 | VCU charge window (copied) |
| `31` | `553` | RX | 3 | gateway (copied) |

"Copied" means the frame is copied into a working buffer, but this pass did not trace which
fields the application uses.

### 6.2 Checks against known layouts

- `0x5D0` bytes 0-2 LE ×0.1 km matches the profile's `EV_CP1.ODOMETER` and uhi22's DBC.
- `0x386` 14-bit ×0.03125 at bytes 4-5 and 6-7 matches `WHL_SPD11` (the rear wheels).
- `0x581` bytes 0-1 ×0.01 A and 2-3 ×0.1 V match the Kona bench notes (AC current and voltage).
  On the Ioniq, bytes 4-5 are the output voltage ×0.1 and bytes 6-7 the DC current ×0.01, not
  the Kona's power.
- `0x57F` bits 1-2, 3, 4, 6, 33 and byte 7 match the profile's `OBC1` signals.
- uhi22's two Ioniq P-CAN logs (READY, no cable) fit every builder: `0x57F` reads
  `80 0A 66 …` (bit 7 set, pilot 0.5 V, OBC at 2 °C), `0x581` is all zero (no cable session),
  `0x58E` reads `00 01 00 EF 28 20 00 C0` (state 1, both charge-current settings Maximum,
  variant 2, lock mode 1), and `0x414` is all zero. Only `0x58E` byte 3 differs: `0xEF` on
  uhi22's 2018 car against the constant `0xE8` in this firmware.

### 6.3 What the OBC sends

The builders are `FUN_001BCFF4` (`0x57F`), `FUN_001BD17C` (`0x581`) and `FUN_001BD2A0`
(`0x58E`), with the status bits set in `FUN_001AEFD4`.

**`0x57F` (OBC1):**

| bits | content |
|---|---|
| byte 0 bit 0 | a charge state-machine flag (also 2101 `B27` bit 1) |
| byte 0 bits 1-2 | plug state (`OBC_PLUG_STATE`; 2101 `B37` bits 0-1) |
| byte 0 bit 3 | `OBC_STATUS_B0_3` (2101 `B37` bit 3) |
| byte 0 bit 4 | `OBC_EVSE_READY` (2101 `B37` bit 2) |
| byte 0 bit 6 | `OBC_FAULT`: any of four fault groups active |
| byte 0 bit 7 | set at start-up |
| byte 1 bit 0 | a flag that clears the 2101 `B9` latch |
| byte 1 bits 1-7 | pilot voltage ×10 (0-12.7 V) |
| byte 2 | OBC temperature + 100 °C (the 2101 `B19` thermistor), 255 on a sensor fault |
| byte 3, byte 6 | two power values in 20 W steps, set from temperature tables (derating, probable) |
| byte 4 bit 0, bit 2 | outputs 8 and 2 of `FUN_00186200` |
| byte 4 bit 1 | `OBC_B4_1`: the charge cable session (2101 `B37` bit 4); `0x581` is all zero without it |
| byte 4 bit 5, byte 5 bit 2 | 2101 `B27` bits 4 and 3 |
| byte 5 bits 3, 7 | further flags (`0x4000393C`, `0x40003916`) |
| byte 7 | pilot duty in % (`OBC_CP_DUTY`) |

**`0x581`:** bytes 0-1 AC input current ×0.01 A, bytes 2-3 AC input voltage ×0.1 V, bytes 4-5
output voltage ×0.1 V, bytes 6-7 DC output current ×0.01 A, all LE. All zero unless the cable
session bit is set.

**`0x58E`:**

| bits | content |
|---|---|
| byte 0 | `0x400038BA` (a charge sub-state) |
| byte 1 | the charger's main state (`0x40003C84`; 12 while charging, as on the Kona) |
| byte 2 | `0x40000A04` ×0.01 |
| byte 3 | `0xE8`, constant |
| byte 4 bits 0-2 | the charge-port lock state machine (`0x40001A00`, states 0-5) |
| byte 4 bits 3-4 | `0x40003CA8` |
| byte 4 bit 5 | the charge-port lock mode (`0x400019FE`), toggled by the inlet lock-mode switch |
| byte 5 bits 0-1 | the stored station charge-current setting (section 7) |
| byte 5 bits 2-3 | the stored portable charge-current setting |
| byte 5 bits 4-5 | calibration-defined variant (`0x10383` + 1 = 2) |
| byte 5 bits 6-7, byte 6 | pilot frequency / 10 (10 bits) |
| byte 7 bits 3, 4, 5-7 | flags (`0x400038CF`, state 15 with `0x400038BB` = 2, `0x40003C8A`) |

## 7. The AC charge-current setting

`FUN_001B8390` takes the head unit's setting from **P-CAN `0x414` byte 0**: bits 2-3 for a
charging station and bits 0-1 for the portable cable, the same split as HU_EV_PE_00 (MM-CAN
`0x1AA`) byte 3. It stores them in reset-proof RAM (`0x400019FC` station, `0x400019FD` portable)
and reports them back in `0x58E` byte 5 (bits 0-1 station, bits 2-3 portable: the other order).
It takes a new value only while none of `0x414`, `0x5DF` and `0x567` is flagged in the slot
table (probably a timeout flag), `0x567`'s clock-valid bit is 1, and `0x5DF` carries one of four
codes. So the P-CAN copy of HU_EV_PE_00's setting is `0x414`, not `0x5DF` or `0x5B3`. That
matches the Kona (IGPM_FINDINGS section 8: "`0x414` byte 0 is the AC charging current setting").

The setting scales the current the OBC takes from the pilot:

| setting | station factor (`0x10EC8`) | portable factor (`0x10ED4`) |
|---|---|---|
| 0 Maximum | 1.00 | 1.00 |
| 1 Reduced | 0.90 | 0.75 |
| 2 Minimum | 0.60 | 0.50 |
| no valid frame and setting 0 | 0.95 (`0x10EE0`) | 0.98 (`0x10EE4`) |

`FUN_001B86AC` turns the pilot duty into amps by the SAE J1772 rule (7-10 % → 6 A, 10-85 % →
duty × 0.6, 85-96 % → (duty − 64) × 2.5, 96-99 % → 80 A, else 0) and multiplies it by the factor.
A calibration byte (`0x10093`, 0 here) would force 15 A instead.

**How the OBC tells a station from a portable cable** (`FUN_001AFA5C`): it averages the pilot
duty over 100 samples after the cable session starts. At least 23 % (13.8 A) is a station, 8 % or
less a third class (digital communication, as CCS uses), anything between a portable cable. It
does not use the proximity resistor. So on this car's wallbox, a 16 A pilot (26.5 %) counts as a
station and a 10 A or 13 A pilot (16.5 %, 21.5 %) as a portable cable.

What this does not establish: which `0x5DF` codes the EU gateway sends, and whether the EU
gateway sends `0x414` at all. The research entry's 92-97 % of pilot on this car fits factor 1.00
plus the measurement offset as well as it fits the 0.95 fallback.

## 8. DTCs

The DTC table at `0x1BE3E0` holds 52 codes (count at `0x198B45`). Compared with `ecus/obc.yaml`
(from the service manual):

- **In the firmware, not in the profile:** P0D08, P0D18, P1CF5, P1E15, P1E1C, P1E22, P1E23,
  U1116.
- **In the profile, not in the firmware:** P0D27, P0D28, P0D90. This OBC cannot set them; the
  manual's chapter covers more OBC versions than this one.

The other 44 match.

## 9. Requests canair must never send to this OBC

canair's guard (`safety/blocklist.py`) refuses `0x11`, `0x2C`, `0x2E`, `0x3B`, `0x3D`, the KWP
`10 85` programming session and the `0x34`-`0x37` transfer services. On this OBC, four requests
passed it until 2026-11-08 (the profile's `hk_90_session` quirk allows `10 90`). The guard now
refuses the first two; `--unsafe` turns that refusal into a prompt:

1. **`99`.** Erases the stored VIN in RAM and in the EEPROM, in any session, without a reply.
   The guard refuses SID `0x99` on every ECU.
2. **`10 90`, then `31 00 xx` or `31 01 xx`.** The OBC jumps into its bootloader and waits for a
   reflash. The guard refuses KWP2000 `31 00` and `31 01` on every ECU the profile says
   speaks KWP2000.
3. **`10 90`, then `30 06 07 …` or `30 07 07 …`.** Starts the end-of-line sensor calibration. It
   runs the power stage and overwrites the calibration in 2102. `canair io` refuses a KWP `0x30`
   ON until a `release:` is declared, but `raw` and the REPL do not refuse it.
4. **`23` above `0x3FFFFF`.** The handler has no address check. The 24-bit address space above
   the 4 MiB flash is mostly unmapped on an MPC55xx, and a read there probably raises a bus error
   and resets the OBC. Keep reads within `0x000000`-`0x3FFFFF`, as the dump did.

A `10 90` session by itself changes only what the server accepts. The OBC's charging code does
not read the session variable.

## 10. What went into the profile (2026-11-08)

- **Renamed:** `LDC_TEMP` → `OBC_TEMP`, `LDC_OUTPUT_V` → `OBC_12V_SUPPLY_V`, `LDC_HV_INPUT_V` →
  `OBC_BATTERY_SIDE_V`, and AAF 2181 `AAF_LDC_TEMP` → `AAF_OBC_TEMP` (the same value). Every
  note that named them was updated. The ECU alias `LDC` stays, so `LDC:2101` still resolves.
- **Replaced:** `OBC_CHARGE_STATE` by `OBC_PILOT_V` (`[B20:B21]/100`, verified against the J1772
  levels); `OBC_AC_NOT_CONNECTED`, `OBC_MODE` and `OBC_DC_CHARGE_MODE` by `OBC_PROXIMITY_V`
  (`[B22:B23]/100`); `OBC_SESSION_FLAGS` by `OBC_PLUG_STATE`, `OBC_EVSE_READY` and
  `OBC_STATUS_B0_3` (`OBC_CABLE_SESSION` stays).
- **New 2101 signals:** `OBC_AC_ABOVE_22A_LATCH` (B9), `OBC_STATUS_B0_0`, `OBC_FAULT` and
  `OBC_STATUS_B5_2` (B27), `OBC_BMS_VOLTAGE_LIMIT` and `OBC_BMS_CURRENT_LIMIT`, the three
  current-sensor offsets, `OBC_5V_RAIL_V`, and `AC_INPUT_V_SAMPLE` (`(B47*256+B49)/100`). The
  constant header and bits (B27 bits 0 and 4-7, B37 bits 5-7) are described in the PID notes.
- **2102:** 14 offset and gain signals (`/1000-30`), all constant.
- **Research:** the 2101 charge-state entry, the `[B20:B21]` entry, the LDC-host entry, the 0x562
  entry and the `0x30` scan entry are done; the charge-current entry has the firmware result and
  stays pending until the setting is seen on a bus; the 2101 decode entry stays open on what
  `OBC_CHARGE_V` and `OBC_OUTPUT_V` each measure.
- **DTCs:** the eight codes of section 8 are added (U1116 with the MCU's manual text); P0D27,
  P0D28 and P0D90 note that this firmware cannot set them.
- **signals/:** the rest of `0x57F`, the `0x581` and `0x58E` layouts, the `0x597` limits and
  `0x414` byte 0, all unverified.
- **The guard** refuses SID `0x99` and KWP2000 `31 00`/`31 01` (section 9).

## 11. Not established

- The meaning of the bits and fields marked with a RAM address only in sections 5 and 6, and the
  semantics of the charger states in `0x58E` bytes 0 and 1.
- The CCP channel on `0x6AC`/`0x6AD` (not analysed; the OBD port does not reach P-CAN anyway).
- What `1A87` controls. No application code reads it.
- The fields of `0x57B`, `0x599`, `0x5A3`, `0x5D5`, `0x5D7`, `0x5E8`/`0x5E9`, `0x587`, `0x579`
  and `0x553` that the OBC uses.
- The physical meaning of `0x57F` bytes 3 and 6 and of `0x58E` byte 2.
- The content of the two areas that sampled as `00` (section 1).
