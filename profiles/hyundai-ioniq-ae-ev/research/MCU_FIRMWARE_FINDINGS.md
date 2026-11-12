---
# Paths are relative to the repository root. The firmware image is local-only (never committed):
# it holds the manufacturer's software. See "Provenance".
title: "MCU firmware findings: the motor controller's flash image, diagnostics, records and CAN"
subject: >-
  The 2017 Ioniq Electric's MCU (motor controller / inverter, KWP2000 0x7E3), read from a byte-exact
  flash image with ReadMemoryByAddress and analysed statically: the image layout and MMU set-up,
  the exception handlers, the reach of the 0x23 handler, the customer diagnostic server and its
  session matrix, the source, scaling and update rate of every byte of the 0x21 records (2101,
  2102, 2103, 21F2), the DTC manager and its 44 codes, the CAN mailbox map and the transmitted
  frames, and the requests that must never be sent
created: 2026-11-12
updated: 2026-11-12
status: active
verification: >-
  Static reverse-engineering of the MCU flash image (Ghidra 12.2, 32-bit PowerPC e500 language,
  SPE float instructions decoded), cross-checked against this car's captures and uhi22's P-CAN
  trace. The image is byte-exact: the header checksum at 0x10078 reproduces. The record layouts
  (§5) were replayed against the captured 2101, 2102 and 21F2 payloads (8,871 2102 captures, 10,811
  2101 captures, 200 21F2 captures): every predicted constant, range and mirror matched. The
  transmitted frames (§7) were decoded from the firmware's own signal descriptors and match
  uhi22's 0x291, 0x523, 0x524, 0x540, 0x5DC and 0x5DE bytes. Nothing was tried on the car. The
  state machines that produce most of the source variables are NOT named; claims about their
  meaning are tagged. Tags: verified (read from code or data), probable, inferred.
sections:
  - section: "2"
    status: open
    date: 2026-11-12
    note: >-
      Layout: boot blocks, identity header, calibration, the six boot-time TLB entries and the
      calibration-overlay TLB entries, the interrupt vector table at 0x63000 and the exception
      handlers at IVPR 0x60000, the RAM map and the watchdog.
  - section: "3"
    status: integrated
    date: 2026-11-12
    note: >-
      The 0x23 handler has no address check and reads a 24-bit address through a 16 MiB TLB entry.
      What a read in 0x400000-0xFFFFFF does depends on the hardware, which the image cannot show.
      Folded into the mcu.yaml research entry about the 2026-11-08 charge stop.
    target: profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml
  - section: "4"
    status: open
    date: 2026-11-12
    note: >-
      The KWP2000 server: the same dispatcher, permission matrix and 0x1A table as the VCU's.
      Sessions 85, 90 and F1 open without a key. The mcu.yaml sessions block still says only 81 is
      supported; 85, 90 and F1 were never tried live.
    target: profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml
  - section: "5"
    status: integrated
    date: 2026-11-12
    note: >-
      Every byte of 2101, 2102 and 21F2 comes from a table-driven converter (three tables, 14, 36
      and 49 entries). Corrections and additions are recorded as unverified signals in mcu.yaml
      (§5.6). 2103 is never written.
    target: profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml
  - section: "6"
    status: open
    date: 2026-11-12
    note: >-
      44 DTC codes; 8 are missing from the profile (P0A2B, P0A40, P0A5F, P0A65, P0AEE, P0CEF, U1001,
      U1004). The fault manager has 7 classes and about 100 fault ids; thresholds were read for the
      motor and inverter temperature and the motor-temperature sensor voltage, and match the
      service manual.
    target: profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml
  - section: "7"
    status: open
    date: 2026-11-12
    note: >-
      Two FlexCAN controllers (CCP-CAN and P-CAN), 128 mailbox slots, the seven transmitted P-CAN
      frames with every byte's source, the received frames, and a CCP slave on 0x632/0x633. Only
      0x524 and 0x291 are in signals/powertrain.yaml.
    target: profiles/hyundai-ioniq-ae-ev/signals/powertrain.yaml
  - section: "8"
    status: open
    date: 2026-11-12
    note: >-
      Requests canair must never send to this MCU. The guard already refuses 99, 11, 28, 29, KWP
      31 00/01/F1/F3 and 10 85. 30 xx 07 and 23 above 0x3FFFFF still pass raw and the REPL.
    target: src/canair/safety/blocklist.py
relations:
  expands_on:
    - path: profiles/hyundai-ioniq-ae-ev/ecus/mcu.yaml
      note: The identity block, the 2101/2102/2103/21F2 records, the DTC list and the memory entry.
    - path: plans/2026-11-12-mcu-flash-image.md
      note: The memory-read work that produced the image analysed here.
  related:
    - path: profiles/hyundai-ioniq-ae-ev/research/VCU_FIRMWARE_FINDINGS.md
      note: >-
        The VCU's firmware: the same boot code, the same KWP2000 server design and the same
        checksum routine. Its sections 14-15 and 22 were the template; section 19.3 and 22.2 give
        the VCU's side of the 0x232, 0x2C1 and 0x291 frames.
    - path: profiles/hyundai-ioniq-ae-ev/research/OBC_FIRMWARE_FINDINGS.md
      note: The OBC's firmware, and the model for the "never send" section (§8).
    - path: profiles/hyundai-ioniq-ae-ev/signals/powertrain.yaml
      note: 0x524 EPCU_DC_VOLTAGE and 0x291 MCU_291, checked in §7.
sources:
  - name: MCU flash image
    kind: >-
      byte-exact dump of the MCU (36600-0E250, sw EAEEHEL-MS2-D000) read on 2026-11-12 with
      ReadMemoryByAddress over KWP2000 in READY; two dumps merged (a sparse image plus a full read
      of the areas it sampled as erased); holds the manufacturer's software
    location: references/firmware/mcu/ (local only, never committed)
  - name: Ghidra 12.2
    kind: static analysis (32-bit PowerPC BE, e500 language); scripts kept beside the image
  - name: this car's captures
    kind: live 2101/2102/21F2 reads (captures/), used to replay the record layouts
    location: profiles/hyundai-ioniq-ae-ev/captures/
  - name: uhi22 Ioniq28Investigations
    kind: >-
      P-CAN trace of a 2018 EU Ioniq 28 kWh and the EPCU bench readme (which transceiver sends
      which frame)
    location: https://github.com/uhi22/Ioniq28Investigations
  - name: 2017 Ioniq US service manual
    kind: DTC thresholds, used to check §6
    location: references/manuals/Ioniq_2017_US/ (local only)
---

# MCU firmware findings: the motor controller's flash image

This collects what a static read of the MCU's flash image shows, cross-checked against this car's
captures. It follows `VCU_FIRMWARE_FINDINGS.md` in method and in format. Sections 2-3 cover the
image and the `0x23` handler, section 4 the diagnostic server, section 5 every byte of the `0x21`
records, section 6 the DTCs, section 7 the CAN frames and section 8 the requests that must never
be sent. The image itself stays on the machine (it is the manufacturer's software); only facts
derived from it are here. Tags: **verified** (read from code or data), **probable**, **inferred**.
Function names are Ghidra's (`FUN_<image offset>`); no function is named by the firmware.

## 1. Provenance and how much to trust it

The image was read on 2026-11-12 in READY with `canair memory image MCU` over KWP2000
ReadMemoryByAddress (`0x23`). Two dumps were merged: a sparse image of 836.7 KiB and a full read
of 826.2 KiB of the areas the first one had sampled as erased. The merge starts from 4 MiB of
`FF`, applies the first dump, then the second. **The merged image passes the header checksum**:
the byte sum of `0x4000`-`0x3FFFFF`, less the 128-byte header at `0x10000`, is `392DADE6`, the
big-endian word at `0x10078` (verified). The routine at `0x119CFC`-`0x119F10` computes the same
sum as the VCU's `FUN_001AB5AC`-`FUN_001AB694`. So the flash is 4 MiB, the image is byte-exact,
and the unread part above `0x167CC0` is `FF` (verified by the checksum, not by a read: a stray
non-`FF` byte there would have broken the sum).

Evidence classes, strongest first:

- **This car's live captures.** Where they disagree with the firmware reading, they win.
- **The firmware image**, read statically in Ghidra. Function and data addresses below are image
  offsets. Values in RAM (`0x40000000`+) are not in the image, so a RAM-backed value's content is
  live-only.
- **uhi22's P-CAN trace**, from a different car and possibly a different software version. It
  agrees byte for byte with the MCU's transmit tables (§7.3), which is why that section is tagged
  verified.

The chip is an NXP MPC5674F-class part: an e200z7 core (Power Book E, big-endian, SPE float
instructions), 4 MiB flash and 256 KiB SRAM at `0x40000000` (probable, close to verified). The
peripheral map matches: FlexCAN at `0xFFFC0000`/`0xFFFC4000`/`0xFFFC8000`, the software watchdog
(SWT) at `0xFFF38000`, the interrupt controller (INTC) at `0xFFF48000`, two eDMA modules, two
eQADC modules (`0xFFF80000`, `0xFFF84000`), DSPI at `0xFFF94000`/`0xFFF98000`, the system
integration unit (SIU) at `0xC3F90000` and the eMIOS timers at `0xC3FA0000`. The first 4 KiB of
the image equal the VCU's except 3 bytes (§2.3). The application has 835 functions with a stack
frame in `0x100000`-`0x16296C`, plus leaf functions (Ghidra lists about 1,900, a count inflated by
tables it decodes as code). The VCU has about 1,920 in `0x180000`-`0x2208DB`. The code is
generated (a Simulink-style model with generated state machines), so most functions are small
getters, setters and one-state-machine steps.

The Ghidra project and the analysis scripts are kept locally under `references/firmware/mcu/` (a
gitignored path). They are not part of this repository.

## 2. The image: layout, MMU and exception set-up

### 2.1 Layout

Data areas (the rest reads `FF`, or `00` in `0x060000`-`0x060FFF` and `0x163800`-`0x16493F`):

| range | contents |
|---|---|
| `0x000000`-`0x0002CF` | BAM header (RCHW `0x005A` at 0, reset vector `0x24` at 4) and the reset code (§2.3) |
| `0x001000`-`0x003CFF` | boot block 1, tag `LFHMCU03` at `0x1000`: a CAN boot manager (`0x1008`) and a CAN reflash loop |
| `0x004000`-`0x0062BF` | boot block 2: the KWP2000 bootloader, server `FUN_00005848`, entered by `31 00`/`31 01` or when the app is invalid |
| `0x00FFF6`-`0x00FFFF` | ecu_id `AEEMCU83`, then the **app-valid word `C3 5A`** at `0xFFFE` |
| `0x010000`-`0x01007F` | identity header (§2.2) |
| `0x010080`-`0x014057` | calibration page 1: bytes at `0x10080`+ are configuration (the CAN-mode flags `0x10095`-`0x10098`, override enables at `0x101AC`) and gains, limits and DTC thresholds |
| `0x062100`-`0x0653FF` | exception handlers (IVPR `0x60000`), the interrupt vector table at `0x63000`, the external-interrupt dispatcher at `0x64000`, C start-up and library code (`0x65000`: copy `.data`, clear `.bss`) |
| `0x080000`-`0x0A57BF` | constant tables (150 KiB): look-up tables and maps (floats); calibration page 2 (§2.4) |
| `0x100000`-`0x16296C` | application code (835 stack-frame functions plus leaf functions) and constant tables between them: the CAN slot table (`0x100268`), the permission matrix (`0x1129E0`), the CCP command table (`0x10CDE8`), DTC and fault tables (`0x162098`-`0x1626FF`) |
| `0x162970`-`0x167CBF` | `.data` load image, copied to RAM `0x4000CD6C` (§2.5) |
| `0x167CC0`-`0x3FFFFF` | erased (`FF`) |

The data areas of the survey (`0x000000`-`0x0062BF`, `0x00FFC0`-`0x010CBF`, `0x011140`-`0x013F3F`,
`0x062100`-`0x0653FF`, `0x080000`-`0x0A57BF`, `0x100000`-`0x1637FF`, `0x164940`-`0x167CBF`) hold
all of it except 105 non-`FF` bytes in `0x010CC0`-`0x01113F`, four in `0x013F40`-`0x014057` and
the zero fill noted above (the full read of the erased areas found those). The application
starts at `0x100000` (the VCU's at `0x180000`). Both validity markers must hold for the boot code
to start it: the word `C35A` at `0xFFFE` (checked by the boot manager at `0x1108`) and the
halfword `5AA5` at `0x1007C` (checked by the entry at `0x4010`).

### 2.2 The identity header (`0x10000`-`0x1007F`)

| offset | content |
|---|---|
| `0x10000` | dataset name `AEEMES07R0` (padded to 16 bytes) |
| `0x10010` | four date bytes `14 10 0C 05` |
| `0x10014` | calibration id `00000001` (1A8E, in flash) |
| `0x1001C` | software id `EAEEHEL-MS2-D000` (the flash copy; 1A8D reads the RAM copy) |
| `0x1003C` | build string `AEEV_MCUC_00010120120312` |
| `0x10054`, `0x10062` | the version/date bytes `1E 06 0A 14` that 1A8B and 1A99 return |
| `0x10078` | image checksum `392DADE6` |
| `0x1007C` | header-valid halfword `5AA5` |

The part number `36600-0E250` and a second copy of the software id sit in the `.data` image at
`0x16712E` and `0x16711E`; 1A91 and 1A8D read the RAM copies at `0x4001152A` and `0x4001151A`.

### 2.3 Reset code, boot blocks and the boot-time TLB

The reset vector (`0x24`) runs the same code as the VCU's. Three bytes differ: the RAM clear
starts at `0x40008000` (the VCU's at `0x40002000`), its count is `0x700` blocks of 128 bytes,
and the final call goes to `0x100000` (the VCU's to `0x180000`). It does, in order (verified):

1. Clears SRAM `0x40008000`-`0x4003FFFF` with `stmw` (224 KiB). **`0x40000000`-`0x40007FFF` is
   not cleared**: it keeps its contents across a warm reset (it holds the exception record at
   `0x40002000`, the retained calibration values and the DTC records, §2.5; probably the 32 KiB
   standby-RAM block of the part, kept alive by a keep-alive supply).
2. Writes the byte `3` to `0xFFF40043` (probable: the ECC error-reporting enable of the platform
   error module).
3. Writes **six TLB entries** (TLB1, all valid, protected, user and supervisor read/write/execute,
   page size = 4^TSIZE KiB):

   | entry | effective = real address | size | cache |
   |---|---|---|---|
   | 0 | `0xFFF00000` (peripheral bridge B) | 1 MiB | inhibited |
   | 1 | `0x00000000` (the flash) | **16 MiB** | cached |
   | 2 | `0x20000000` | 16 MiB | cached |
   | 3 | `0x40000000` (SRAM) | 256 KiB | inhibited, until the cache set-up below |
   | 4 | `0xC3F00000` (peripheral bridge A) | 1 MiB | inhibited |
   | 5 | `0x40040000` | 4 KiB | cached (the locked stack) |

4. Enables the instruction and data caches (`L1CSR0`/`L1CSR1`), writes `DBCR0`, then locks 128
   cache lines (4 KiB) at `0x40040000` with `dcbz`/`dcbtls` and puts the stack pointer there.
   `0x40040000` is above the SRAM; the stack lives only in the locked cache.
5. Sets `MSR[SPE]` and calls, in order, the boot manager `0x1008`, the entry `0x4000` and the
   application `0x100000`.

The boot manager (`0x1008`) is the VCU's: it polls FlexCAN A mailbox 0 up to 2,000 times after
reset for an 8-byte boot frame (first byte `07`). If that frame arrives it stays in the CAN
reflash loop (`0x1118`); otherwise it checks the word `C35A` at `0xFFFE` and returns. The frame
is **not** the VCU's: this MCU's comparison constants differ, and they are deliberately not
recorded here (§8). `0x4000` then returns if `5AA5` is at `0x1007C` and otherwise enters the
KWP2000 bootloader loop. The boot blocks are the VCU's source rebuilt for this part (9,716 bytes
differ in `0x1000`-`0x3FFF`, mostly shifted code).

The application replaces entries 1, 3 and 5 as needed (the calibration overlay, §2.4) and never
removes the 16 MiB flash entry. **Entry 1 is why the `0x23` handler can address 16 MiB** (§3).

### 2.4 The calibration overlay (TLB entries 10-26 and 32)

The application maps calibration through TLB entries, as the VCU does for its CCP
`SELECT_CAL_PAGE` (verified, replayed with a small script over all 69 `tlbwe` instructions):

- `FUN_00109588` (and three start-up copies at `0x1EF8`, `0x4324` and `0x44E0`) writes entry 10 as
  `0x10000` (16 KiB), entries 11-26 as 1 KiB pages `0x14000`-`0x17FFF`, and entry 32 as
  `0x80000` (256 KiB), all cache-inhibited. The flash page (real = effective address) is the
  power-on state.
- The working page maps the same effective addresses to RAM: `0x10000` to `0x40008000`,
  `0x14000`-`0x17FFF` to `0x40039C00`-`0x4003DBFF` and `0x80000`-`0xBFFFF` to `0x20000000`.
  The CCP command `SELECT_CAL_PAGE` (`FUN_0010AA94`, §7.6) picks the page by calling
  `FUN_00109588`. A `0x23` read of `0x10000`-`0x17FFF`
  or `0x80000`-`0xBFFFF` returns whichever page is mapped. The checksum matches, so the dump was
  taken from the flash page.
- The application reads its tuning (gains, limits, DTC thresholds, CAN configuration) at fixed
  effective addresses in these two ranges. A CCP master that writes the RAM page changes the
  running car without a reflash (§8.5).

### 2.5 RAM, `.data` and the start-up copy

`FUN_00114F5C` (a jump to `0x65000`) sets the small-data bases and copies the `.data` image:
`r13 = 0x400115B8`, `r2 = 0x001222B4`; `0x162970` is copied to `0x4000CD6C` (the count is
`0x6198`, but `.bss` from `0x40012198` is cleared afterwards, so `.data` is the 21,548 bytes
`0x4000CD6C`-`0x40012197`); `.bss` is `0x40012198`-`0x40022120`. The RAM below `0x4000CD6C` is
the retained area and the part cleared by the reset code.

| RAM | content |
|---|---|
| `0x40000000`-`0x40001FFF` | retained: the start-up and DTC records (`0x4000004C`+, 60 bytes per DTC), the VIN image at `0x40001FC0` (32 bytes), the current-sensor offsets and learned values at `0x40001E0C`-`0x40001EA0`, the last-cycle statistics at `0x40002464`-`0x40002476` |
| `0x40002000`-`0x40002008` | exception record: a code (§2.6) and the saved `SRR0`/`SRR1` |
| `0x4000CD6C`-`0x40012197` | `.data`: tables copied from flash (the three record-converter tables, the frame descriptor tables, the identity strings) |
| `0x40013DDC`-`0x40013EEC` | the diagnostic request, response and ISO-TP state (§4.1) |
| `0x40015DDA`-`0x40015E6C` | the record source buffer (§5.1) |
| `0x40021A20`-`0x40021B1F` | the four records, 70 bytes each (§5.1) |

### 2.6 Interrupts, exceptions and the watchdog

`FUN_00103C78` programs `IVPR = 0x60000` and the offsets `IVOR0`-`IVOR15`, `IVOR32`-`IVOR34`
(verified). Each handler is 208 bytes at `0x62130 + n*0xD0`; the external-interrupt dispatcher is
at `0x64000`. The INTC runs in software-vector mode with its table at `0x63000` (`INTC_IACKR =
0x63000`, one 4-byte entry per source); 22 sources have their own handler (two of them empty)
and the rest point to a stub that does nothing.

| exception | handler | what the handler does |
|---|---|---|
| machine check (IVOR1) | `0x62200` → `FUN_00118EB4` | stores `1`, `SRR0` and `SRR1` at `0x40002000`/`4`/`8`, then returns with `rfmci`. **No reset, no cause decoding**: `MCSR`, `MCAR` and `MCSRR0/1` are never read |
| critical input, DSI, ISI, alignment, program, FP unavailable, system call, AP unavailable, decrementer, FIT, watchdog, **data TLB error (IVOR13)**, instruction TLB error, debug, SPE unavailable/data/round | `FUN_00118E7C`, `118EDC`, … | store the vector number at `0x40002000` (`0x24` for critical input), then call `FUN_00103714` and spin forever. `FUN_00103714` sets bit 31 of the SIU system reset control register (`SIU_SRCR`, `0xC3F90010`): **a software system reset** |

So every exception except the machine check ends in a reset. The machine check returns to the
instruction pointed to by `MCSRR0`; if that instruction is a load that faults again, the same
machine check repeats. **`MSR[ME]` is never set** (verified: all six `mtmsr` sites only add the
SPE bit, and `wrteei` changes only `EE`). The machine-check enable is therefore at its reset
value, which is 0 on an e200 core (probable, from the core manual). With `ME` clear a machine
check does not reach the handler at all: the core checkstops (inferred; whether the part then
resets depends on the SIU, which the image does not show).

The software watchdog (`0xFFF38000`) is set to 13,200,000 clocks (100 ms at 132 MHz) at start-up
and to 1,980,000 clocks (15 ms) once the application runs (`FUN_00114FC4(4)` then `(2)` from
`FUN_00100000`). It is serviced by the 1 ms task (`FUN_001216D8` → `FUN_00115130`, the fixed
sequence `A602`/`B480`), and the bootloader and RAM-loader entries switch it off first
(`FUN_00115124`). A CPU stuck in a load for more than about 15 ms is reset by the watchdog
(probable). `FUN_00103728` decodes the reset status register at `0xC3F9000C` into a cause code
0-8 for the application.

The operating system is a small cyclic scheduler (`FUN_00114184`, `FUN_00114654`, `FUN_00114EA8`)
with seven tasks on a 1 ms tick (probable, from the VCU's kernel and the offsets and periods):

| task | period | what it runs |
|---|---|---|
| `FUN_001216D8` → `FUN_0012155C` | 1 ms | the fast model step `FUN_001237DC` (ADC read-in, the control and protection groups), the `0x2C1` sender `FUN_00121EC0`, watchdog service |
| `FUN_00121900` | 10 ms | the second model step `FUN_0012397C` (the temperature chain, the DTC reporter `FUN_001373A4`, the fault monitors) |
| `FUN_0012194C` | 10 ms (offset 9) | **diagnostics and communication**: the CCP slave, the diagnostic intake `FUN_0010D0BC`, the server `FUN_00111E18`, the DTC manager `FUN_00112EAC`, the `0x291`/`0x523`/`0x524` senders |
| `FUN_00121994` | 100 ms | the slow model step `FUN_00123A8C`, **which refreshes the 0x21 records** (`FUN_00136FE0`) |
| `FUN_001219B8` | 100 ms (offset 81) | the `0x2A1` sender, and `0x540` every second run |
| `FUN_001219E8` | 1000 ms | the after-run counters |
| `FUN_00121A38` | 1000 ms (offset 101) | the `0x5DC` and `0x5DE` senders |

Besides these, the PWM-synchronous interrupts of the eMIOS channels (`FUN_001241A0`, `FUN_001241E0`,
`FUN_0012434C`, `FUN_001242B4`, `FUN_00124400`) run the current and position control loops. Their
rate was not measured.

## 3. Why the 0x23 handler answers all 16 MiB

This matters for safety: on 2026-11-08 a survey read at `0x864000` ended an AC charge (the
mcu.yaml research entry "ReadMemoryByAddress (0x23) survey stopped the AC charge").

**What the handler does** (`FUN_00110E7C`, verified):

1. Checks the permission matrix row for `23` (`FUN_00111E98(9)`): allowed in sessions `81`, `85`
   and `90`, never in `F1` (§4.3).
2. Requires the single-frame request length to be 5 (`23 AA AA AA SS`). Any other length is NRC
   `0x79`; a size of 0 or above `0xFE` is NRC `0x53`.
3. Builds the address as `(byte 4 of the request buffer) << 24 | A2 << 16 | A1 << 8 | A0`. The
   high byte is `0x40013DE7`, which **nothing in the image ever writes** (the request buffer
   `0x40013DDC` is eight bytes wide, and the byte is in `.bss`, which the start-up clears). So
   the address is 24 bits: `0x000000`-`0xFFFFFF`.
4. Calls `FUN_0010D1E8` as an address check. **It is a stub: it always returns 0 ("fine")**, so
   the handler's NRC `0x22` and `0x52` paths are dead.
5. Reads `SS` bytes one at a time with plain byte loads (`lbz`) through that address, with no
   `try`, no range test and no cache-line or alignment logic, into a 255-byte stack buffer. It
   replies with `63` + the bytes (multi-frame from 7 bytes up).

**Why 16 MiB.** TLB entry 1 (§2.3) maps `0x00000000`-`0x00FFFFFF` to the same real addresses,
cached, read/write/execute, protected. A 24-bit address always hits it. The handler therefore
never takes a data-TLB error (that exception, IVOR13, would reset the MCU through
`FUN_00119144`). The load goes onto the crossbar to whatever answers at that real address. The
flash occupies `0x000000`-`0x3FFFFF`; the VCU doc's argument that "an unmapped read is a TLB
miss" does not apply here, because this TLB maps the whole 16 MiB.

**What follows a read the bus does not answer.** Proven by the code:

- There is no handler that recovers. The only machine-check handler records the fault and
  returns; `MSR[ME]` is never set (§2.6); every other exception resets.
- The watchdog (15 ms) is serviced by the 1 ms task, so a stalled bus transaction ends in a
  watchdog reset, and so does an endless machine-check loop.
- A load that never completes blocks the core. No task runs, so the MCU stops sending `0x2C1`,
  `0x291`, `0x523` and `0x524` until the watchdog resets it (probable).

**Not proven.** The code cannot show what the part's bus bridge does for a flash-space address
above the 4 MiB array. Either it aliases or returns data (the 1-byte probes of the earlier survey
answered across the whole range), or it terminates the cycle with an error. The 2026-11-08
survey answered up to about `0x863000` and not at `0x864000`; that is consistent with a hit on an
unimplemented block, but the image does not say which address that is. Also not proven: that an
MCU reset ends an AC charge. The VCU masks the timeout flags of `0x291`, `0x523`, `0x524` and
`0x2C1` while the car is plugged in (VCU doc §22.1), so a silent MCU should not by itself end a
charge. **The MCU, BMS and OBC all went silent on 2026-11-08, which an MCU reset alone does not
explain**; the cause of that event stays open. Keep reads inside `0x000000`-`0x3FFFFF`.

The handler also answers on the functional id `0x7DF` (§4.1): a functional `23` reaches it too.

## 4. The diagnostic server

### 4.1 Transport and intake

The MCU answers on `0x7E3` (response `0x7EB`) and on the functional id `0x7DF`. Mailbox slots 125,
126 and 127 (CAN C mailboxes 61-63, §7.1) are the three. `FUN_0010D0BC` (10 ms task) reads `0x7DF`
first, then `0x7E3`, copies the eight data bytes to `0x40013DDC` and sets the pending flag
`0x40013DE4`. `FUN_00111E18` then calls the dispatcher `FUN_001117C8`. A functional request is
therefore handled exactly like a physical one: **every service in §4.2 also answers on `0x7DF`**,
`10 90`, `0x30` and `0x31` included, and canair's functional allow-list is the only guard there.

`FUN_00111F00` reads the ISO-TP byte: a single frame (high nibble 0; length = low nibble, service
at byte 1), a first frame (`10`; service at byte 2), a consecutive frame (`20`) or a flow-control
frame (`30`). The response is sent by `FUN_00112284`/`FUN_00112504`, in single frames up to six
data bytes and in first/consecutive frames above that. The single-frame length is not compared
with the bytes actually sent: a handler looks only at the length it expects.

### 4.2 Services

`FUN_001117C8` is a switch on the service id with one handler per service (verified, the same
design as the VCU's `FUN_00192788`):

| SID | service | handler | notes |
|---|---|---|---|
| `01`, `02`, `03`, `07`, `09`, `0A` | OBD-II modes | `FUN_0010D528`, `D8C8`, `E0A4`, `E30C`, `E7DC`, `E574` | answer in the profile's tests; `01 00` and `01 20` return their support maps (profile research entry) |
| `04`, `14` | clear diagnostic information | `FUN_0010F54C` | `04` (OBD-II) needs the stationary test `FUN_00162014`; `14 gg 00` (`gg` = `00`, `40`, `80` or `FF`) sets the clear request `0x40013EC1` (1 all, 2 for `00`, 3 for `40`, 4 for `80`) that the DTC manager carries out |
| `10` | StartDiagnosticSession | `FUN_0010ED90` | `81`, `85`, `90`, `F1`; none needs a key (§4.3) |
| `11` | ECUReset | `FUN_0010F408` | `11 01` only; **gated on the vehicle being stationary** (§8) |
| `12` | ReadFreezeFrameData | `FUN_0011063C` | |
| `17`, `18` | ReadStatusOfDTC, ReadDTCByStatus | `FUN_0010FA7C`, `FUN_0010FEF8` | `18 ss gg 00`: the status mask `ss` is `00`, `20`, `40` or `60`, the group `gg` is `00`, `40`, `80` or `FF` |
| `1A` | ReadEcuIdentification | `FUN_0010F818` | table at `0x162200` (§4.4) |
| `20` | StopDiagnosticSession | `FUN_00110D84` | returns to `81`, drops the key state |
| `21` | ReadDataByLocalIdentifier | `FUN_00110AAC` | table at `0x1622CC` (§5) |
| `22`, `2C`, `2E`, `3D` | stubs | `FUN_00111564`, `11111C`, `111584`, `1110FC` | always NRC `0x12` |
| `23` | ReadMemoryByAddress | `FUN_00110E7C` | §3 |
| `27` | SecurityAccess | `FUN_0010F048` | **present; not read** (project rule) |
| `28`, `29` | Disable/EnableNormalMessageTransmission | `FUN_00110840`, `FUN_00110978` | `28 01` stops the MCU's periodic frames (§8) |
| `30` | InputOutputControlByLocalIdentifier | `FUN_00110BE4` → `FUN_00161B3C` | §4.5 |
| `31` | StartRoutineByLocalIdentifier | `FUN_001115A4` | §8 |
| `34`, `36`, `37` | empty cases | none | no reply |
| `3B` | WriteDataByLocalIdentifier | `FUN_0011113C` | writes only the `1A` entries marked writable: `1A90` (the VIN image at `0x40001FC0`); needs the stationary test for `3B` |
| `3E` | TesterPresent | `FUN_0010EF0C` | `3E 00` answers `7E 00`; `3E 80` is silent; refused in session `81` |
| `99` | manufacturer service | `FUN_0010D28C` | **no permission-matrix call in the handler**; erases the VIN image (§8) |

An unknown service below `0x10` gets no reply; any other unknown service gets NRC `0x12`. The
wire NRC bytes are the ones in the handlers: `0x12` (sub-function not supported), `0x22`
(conditions not correct), `0x31`, `0x33`, `0x35`, `0x52`, `0x53`, `0x78` (response pending),
`0x79` (wrong length) and `0x80` (service not supported in this session).

### 4.3 Sessions and the permission matrix

`FUN_00111E98(row)` looks the request's service up in the 24-row matrix at `0x1129E0`, five bytes
per row (the service id, then the sessions `81`, `85`, `90`, `F1`; the session state is
`0x40013ED3`). **The matrix is byte-for-byte the VCU's** (VCU doc §2.1). `02` allows, `03`
refuses (NRC `0x80`), `01` allows only with the security state `0x40013EB9` set:

| SID | `81` | `85` | `90` | `F1` |
|---|---|---|---|---|
| `10` | yes | yes | yes | yes |
| `20` | yes | yes | yes | no |
| `3E` | **no** | yes | yes | no |
| `27` | no | yes | yes | yes |
| `11` | no | secured | **yes** | no |
| `1A`, `21`, `23` | yes | yes | yes | no |
| `28`, `29` | no | **yes** | **yes** | no |
| `3D` (stub) | secured | no | secured | no |
| `2C` (stub) | yes | no | yes | no |
| `3B` | no | secured | secured | no |
| `22`, `2E` (stubs) | no | no | no | no |
| `34`, `36`, `37` | no | secured | no | no |
| `12`, `14`, `17`, `18` | yes | no | yes | no |
| `31` | no | secured | **yes** | secured |
| `30` | no | no | **yes** | no |

- **`10 85`, `10 90` and `10 F1` need no SecurityAccess** (verified): `FUN_0010ED90` switches the
  session as soon as it sees the byte. The mcu.yaml `sessions` block says only `81` is supported;
  `85`, `90` and `F1` were never tried live. canair hard-blocks `10 85`; the profile's
  `hk_90_session` quirk lets `10 90` through.
- **In session `90`, `0x30`, `0x31`, `0x11` and `0x28`/`0x29` need no key** (verified).
- A request with a session byte outside these four is NRC `0x12`.
- Session `81` is the power-on state and the state `20`/`10 81` return to. The 5 s (session `90`)
  and 60 s (session `85`) time-outs of the VCU were not traced here (the intake code is the
  same); treat them as probable.

### 4.4 The 0x1A table

`FUN_0010F818` (read) and `FUN_0011113C` (write) use the 17-entry table at `0x162200`, 12 bytes
per entry: id, length, two zero bytes, the data pointer, then a read flag (`1` readable, `2` not)
and a write flag (`1` writable, `2` not). **The seven entries with read flag 2** (`84`, `86`,
`87`, `88`, `89`, `92`, `94`; all RAM-backed) **are not readable**; the VCU shows the same split
live (NRC `0x12`).

| 1Axx | length | source | content |
|---|---|---|---|
| `80` | 89 | RAM `0x400219AC` | an aggregate of 8A, 8B, 90, 91, 98, 99, 8C, 8D and 8E; **it contains the VIN** (sensitive) |
| `8A` | 10 | RAM `0x40011510` | ten spaces |
| `8B` | 4 | flash `0x010054` | `1E 06 0A 14` |
| `8C` | 8 | flash `0x00FFF6` | `AEEMCU83` (profile ecu_id) |
| `8D` | 16 | RAM `0x4001151A` | `EAEEHEL-MS2-D000` (profile sw_id) |
| `8E` | 8 | flash `0x010014` | `00000001` (profile calibration) |
| `90` | 17 | RAM `0x40001FC0`, **writable** | the VIN (sensitive; never recorded) |
| `91` | 12 | RAM `0x4001152A` | `36600-0E250` (profile part number) |
| `98` | 10 | RAM `0x40011536` | ten spaces |
| `99` | 4 | flash `0x010062` | `1E 06 0A 14` |

`1A80` is not defined in the profile and must stay unread on any shared capture, because it
embeds the VIN. The four profile identity strings match the image.

### 4.5 IOControl (`30`)

`FUN_00161B3C` handles `30 LID CTRL [PARAM]` in session `90` only. Local id `0x00` returns four
bytes from flash `0x16210B`. For local ids `0x01`, `0x02`, `0x04`, `0x05`, `0x06`, `0x07` and
`0x08` a per-LID enable byte at `0x400173B4` must be 1 or the reply is NRC `0x22`. Control `07`
(short-term adjustment) latches the LID (`0x400173BC+LID` = 1); `30 01 07` also starts a long
action that the server answers with repeated NRC `0x78`. `30 06 07 xx` and `30 07 07 xx` store `xx`
in `0x40015E6E`, the trigger of the current-sensor offset calibration state machine
`FUN_001363CC` (§5.3). Only LIDs `05` and `08` accept control `00` (release); LIDs `02`, `04`,
`06` and `07` have no release in this handler. Local ids `0x20`, `0x40`, `0x60`, `0x80`, `0xA0`,
`0xC0` and `0xE0` answer a fixed reply to control `01`. Treat every `30 .. 07` as a latched
actuation (§8). Whether a session timeout or reset clears the latches was not traced.

## 5. The 0x21 records: the source of every byte

### 5.1 How a record is built

The server reads four records from the table at `0x1622CC` (stride 8: id, data length, two zero
bytes, RAM pointer; four entries, `bRam001226C8` = 4):

| request | data bytes | response length | RAM record | build |
|---|---|---|---|---|
| `21 01` | 28 | 30 | `0x40021A20` | copied from the source buffer at each request |
| `21 02` | 54 | 56 | `0x40021A66` | as above |
| `21 03` | 25 | 27 | `0x40021AAC` | **never written**: all zero (the profile's 697 captures) |
| `21 F2` | 65 | 67 | `0x40021AF2` | copied from the source buffer |

`FUN_00110AAC` first calls `FUN_0016169C(lid)`, which copies the 147 current bytes
(28 + 54 + 65) from the **source buffer** `0x40015DDA`-`0x40015E6C` into the records, then sends
the record. So a reply shows the model state as of the last refresh, not the instant of the
request.

The source buffer is filled by `FUN_00136FE0`, which runs the **record converter**
`FUN_00136DD0` over three tables in `.data` (RAM `0x4000D6E8`, `0x4000D7D8`, `0x4000D998`; flash
`0x1632EC`, `0x1633DC`, `0x16359C`). **The task is the 100 ms one** (`FUN_00121994` →
`FUN_00123A8C` → `FUN_00136FE0`), so all three records refresh every 100 ms (probable: the period
comes from the scheduler set-up, §2.6). Each table entry is 16 bytes: a type, a source RAM
pointer, a destination pointer into the source buffer and a float scale. The converter writes
`source × scale` as follows (verified):

| type | source | output | note |
|---|---|---|---|
| 1 | float | unsigned byte | negative values become 0 |
| 2 | float | signed byte | |
| 3 | float | unsigned 16-bit, little-endian | |
| 4 | float | signed 16-bit, little-endian | |
| 5 | unsigned 16-bit | unsigned byte | |
| 6 | unsigned byte | unsigned byte | scale 1: a plain copy |
| 7 | unsigned 16-bit | unsigned 16-bit, little-endian | |
| 8 | signed 16-bit | signed 16-bit, little-endian | |
| `0E` | unsigned 32-bit | unsigned 16-bit, little-endian | clipped at `0xFFFF` |
| `17` | signed byte | unsigned byte | |

The first four data bytes of a record are constants written once by `FUN_00136370`: `00 00 03 FF`
(2101), `07 FF FF FF` (2102) and `FF FF FF 3F` (21F2). They are support-map-like and are not
signals. Everything after them is little-endian, which is why the profile says "words are
little-endian".

WiCAN byte indices below follow `canair bix`: data byte *i* is `B04+i` for *i* < 4, otherwise
`B05+i+⌊(i−4)/7⌋`, and a two-byte value that straddles a frame boundary skips the PCI byte
(`B16`, `B24`, `B32`, `B40`, `B48`, `B56`, `B64`). "Obs." is the range in this car's captures.
The status column compares with the profile: **confirmed** (the profile's signal is read from
code), **contradicts** (the code shows it differently) or **new** (no profile signal).

### 5.2 Record 2101 (28 data bytes)

| data | WiCAN | form | source (RAM) | → value | what it is | vs profile |
|---|---|---|---|---|---|---|
| 0-3 | B05-B08 | constant | `00 00 03 FF` | | header | new (constant) |
| 4 | B09 | u8 | `0x400166B4` | flags | status byte A. The same byte is `0x523` byte 0. Bit 4 is "any of three sensor checks failed" (`0x40016260`/`61`/`63`); other bits come from the model (not traced). Obs. 8, 12, 140 | new |
| 5 | B10 | u8 | `0x400166B6` | flags | status byte B (`0x523` byte 1). **Bit 7 = the position sensor is not learned** (§5.3, P0C17). Obs. 56 | new |
| 6 | B11 | u8 | `0x400166B8` | flags | status byte C (`0x523` byte 7). Bit 3 is the result of `FUN_0012ECE0`. Obs. 0, 1 | new |
| 7-8 | B12:B13 | u16 | `0x40011D00` (float) | ×100 → V | **DC-link voltage in 0.01 V**. 65535 (655.35 V) means the voltage sensor is flagged faulty. The integer-volt copy goes out as `0x524` bytes 0-1 | **confirmed**, and finer: `MCU_HV_VOLTAGE = B13*2.56` is its high byte (verified live: same mean within 1.2 V over 10,811 captures, max 399.80 V) |
| 9-10 | B14:B15 | u16 | `0x40015E74` (float) | ×1000 → mV | 12 V supply, first reading: `FUN_001259C4`, the ADC supply channel (raw `0x400152EC`) times a gain plus an offset (calibration `0x10238`/`0x1023C`) | **confirmed** `MCU_SUPPLY_VOLTAGE_1` |
| 11 | B17 | u8 | `0x4001658A` | flag | bit 0 of `0x40016588` (written by `FUN_001559A0`). Obs. 0 | new |
| 12-13 | B18:B19 | s16 | `0x40016584` (float) | ×1 | an unnamed float; also in the fault freeze frame (`FUN_0014EFFC`). Obs. 0 | new |
| 14-15 | B20:B21 | u16 | `0x400117E2` | ×1 | output of a 9-point curve (10-90) driven by an unnamed float (`FUN_0012E26C`); probably a coolant-pump duty request in percent (inferred). Obs. 10, 45, 59, 75 | new |
| 16-17 | B22:B23 | u16 | `0x400153A8` (float) | ×1000 → mV | ADC channel voltage (raw `0x40015318` × 0.001221 V). Obs. 518-681 mV | **contradicts** `MCU_2101_B22` (a single byte): it is the low byte of a 16-bit millivolt value |
| 18-19 | B25:B26 | u16 | `0x400153AC` (float) | ×1000 → mV | ADC channel voltage (raw `0x4001531C`). Obs. 1062-1145 mV, slow, falls as the coolant warms (the profile's r = −0.72) | **contradicts** `MCU_2101_B25` (single byte): the low byte of a millivolt value. Probably a temperature-sensor divider (inferred) |
| 20-21 | B27:B28 | u16 | `0x40015490` (float) | ×1000 → mV | 12 V supply, second reading: `FUN_001252C0(0,4,raw 0x400152F4)`, a second gain and offset | **confirmed** `MCU_SUPPLY_VOLTAGE_2` |
| 22-23 | B29:B30 | s16 | `0x40015408` (float) | ×50 | phase current 1 (probably amperes: 0.02 A per count) | **confirmed** layout; scale is new |
| 24-25 | B31, B33 | s16 | `0x40015410` (float) | ×50 | phase current 2 (B32 is a PCI byte) | confirmed |
| 26-27 | B34:B35 | s16 | `0x40015418` (float) | ×50 | phase current 3 | confirmed |

The three phase currents are separate floats in the code; that they sum to within one count is
the profile's finding and was not re-derived. Two captured payloads decode to 0, +73, −73 counts
(±1.46 A) and 0, +24, −24 counts (±0.48 A) at standstill. If the unit is amperes, the profile's
"multiples of about 24.4 counts" is one ADC step of 0.488 A, which is a 12-bit converter over
about ±1000 A (probable).

### 5.3 Record 2102 (54 data bytes)

| data | WiCAN | form | source (RAM) | → value | what it is | vs profile |
|---|---|---|---|---|---|---|
| 0-3 | B05-B08 | constant | `07 FF FF FF` | | header | new (constant) |
| 4-5 | B09:B10 | s16 | `0x40016650` (float) | ×1 → rpm | motor speed. `FUN_001559A0` takes it from `0x40016284`, the speed estimate. Also `0x523`/`0x5DC` | **confirmed** `MCU_MOTOR_RPM` |
| 6-7 | B11:B12 | s16 | `0x40016780` (float) | ×10 → 0.1 Nm | motor torque (written by `FUN_00158390`) | **confirmed** `MCU_MOTOR_TORQUE` |
| 8-9 | B13:B14 | s16 | `0x400167A8` (float) | ×10 → 0.1 Nm | a second torque value (`FUN_00157598`); the profile found it byte-identical to the first in all 8,231 captures, which fits a limited and an unlimited copy that agree in the states captured. It is the source of the `0x291` torque field | confirmed `MCU_MOTOR_TORQUE_2` |
| 10-11 | B15:B17 | u16 | `0x40015EAC` (float) | ×10 → 0.1 | motor current magnitude: `0.70721 × √(0x400164E4)` (`FUN_00136FE0`), i.e. an RMS-style figure from the sum of two squared current components | **confirmed** `MCU_MOTOR_CURRENT` (never negative, as the profile found) |
| 12 | B18 | **s8** | `0x40016680` (float) | ×0.5 | temperature, °C = raw × 2. Output of the temperature chain (`FUN_00153A74`); in the fault freeze frame | **contradicts** in the sign: the profile reads `B18*2` as unsigned, which is wrong below 0 °C (`S18*2`) |
| 13 | B19 | **s8** | `0x40016678` (float) | ×0.5 | temperature (`FUN_00153A94`); the profile's heatsink | contradicts in the sign |
| 14 | B20 | **s8** | `0x40016690` (float) | ×0.5 | temperature (`FUN_001539CC`); the profile's coolant | contradicts in the sign |
| 15 | B21 | **s8** | `0x40016688` (float) | ×0.5 | temperature (`FUN_00153A64`); the profile's motor coolant | contradicts in the sign |
| 16-17 | B22:B23 | s16 | `0x40001E20` | ×1 | current-sensor offset 1 (standby RAM; the profile's range −11 to −7) | **confirmed** `MCU_CURRENT_SENSOR_OFFSET_1` |
| 18-19 | B25:B26 | s16 | `0x40001E24` | ×1 | current-sensor offset 2 | confirmed (`_2`) |
| 20-21 | B27:B28 | u16 | `0x40001E14` (float) | ×10000 | **probably the learned position-sensor (resolver) offset angle in radians**: 5.4909 in all 8,871 captures (the default `FUN_00132630` loads is 5.49; 2π = 6.2832, the range of an angle) | new (constant) |
| 22-23 | B29:B30 | u16 | `0x40001E0C` | ×1 | marker `0xABCD`: **learned data valid**. If it is anything else, `FUN_00132630` raises fault class 5 #11 (DTC P0C17 "position sensor not learned") and sets bit 7 of status byte B. `0xABCD` = 43,981 in every capture | new (constant) |
| 24-25 | B31, B33 | u16 | `0x40001E80` | ×1 | marker `0xCDC2` = 52,674 in every capture; its readers are `FUN_0013DF54` and `FUN_0014A1CC` | new (constant) |
| 26-27 | B34:B35 | s16 | `0x40001E88` (float) | ×10000 | stored value 0.0163 (163), written only by an end-of-line routine guarded by a calibration word `0xCAFE` that reads 0 here | new (constant) |
| 28-29 | B36:B37 | s16 | `0x40001E90` (float) | ×10000 | stored value 0.0432 (432), as above | new (constant) |
| 30-31 | B38:B39 | s16 | `0x40001E98` (float) | ×10000 | stored value 0.0050 (50), as above | new (constant) |
| 32-33 | B41:B42 | s16 | `0x40001EA0` (float) | ×10000 | stored value 0.1311 (1311), as above | new (constant) |
| 34-35 | B43:B44 | s16 | `0x40001E78` (float) | ×10000 | result of the current-sensor offset calibration (`FUN_001363CC`, `FUN_00133EB4`): 0.9975 (9975) | new (constant) |
| 36-37 | B45:B46 | s16 | `0x40001E7C` (float) | ×10000 | second result of the same calibration: 0.1782 (1782) | new (constant) |
| 38-39 | B47:B49 | u16 | `0x40001E74` | ×1 | marker `0xCDC1` = 52,673: **the offset calibration has completed** (`FUN_001363CC` writes it after the last state) | new (constant) |
| 40-41 | B50:B51 | u16 | `0x40000DF4` | ×1 | a counter in standby RAM, written by `FUN_00123B64`, `FUN_00124688` and `FUN_0013A118`. Obs. 9 | new |
| 42-43 | B52:B53 | u16 | `0x40011C18` (float) | ×10000 → V | **an analog sensor voltage of about 1.1-1.45 V** (mean 1.28 V, 8,871 captures). Probably the motor temperature sensor (a rising KTY-type signal: it rises after driving). It is also `0x540` bytes 0-1 and the source of the 21F2 `W27`/`W31` statistics | **contradicts** the "B52 varies over 0-253 and correlates with no signal" note: B52 is the low byte of this 16-bit value |
| 44-45 | B54:B55 | u16 | `0x40000DCC` | ×1 | counter written by the interrupt `FUN_001241E0`. Obs. 0 | new |
| 46-47 | B57:B58 | u16 | `0x40000DC8` | ×1 | counter written by `FUN_00123B64`, `FUN_00124688`, `FUN_0013A118`. Obs. 0 | new |
| 48-49 | B59:B60 | u16 | `0x40000DD0` | ×1 | counter written by the interrupt `FUN_001242B4`. Obs. 0 | new |
| 50-51 | B61:B62 | u16 | `0x40000DD2` | ×1 | counter written by the interrupt `FUN_0012434C`. Obs. 0 | new |
| 52-53 | B63:B65 | u16 | `0x40001E6C` | ×1 | a 32-bit variable clipped to 16 bits; no writer in the image. Obs. 0 | new |

The profile's "B54 to B65 read 0 in all captures" is explained: all six are event counters or an
unused slot. The three counters `0x40000DCC`, `DD0` and `DD2` are each owned by one eMIOS
interrupt, so they are probably per-channel fault-trip counts (inferred).

**Motor power** (`MCU_MOTOR_POWER`) is derived and needs no source. The unit of the torque and
the current is not stated in the code. The 100 % torque scale of the CAN frames is 300 Nm
(§7.3).

### 5.4 Record 21F2 (65 data bytes)

21F2 mixes live copies of the 2102 values with **last-cycle statistics**. `FUN_0013A334` (100 ms
task) accumulates them while the model state `0x40016A28` is 1; on every run in which the state is
not 1 it copies the accumulators to the retained block `0x40002464`-`0x40002476`. So the
block holds the figures of the last period in state 1 and survives a reset.

| data | WiCAN | form | source (RAM) | what it is | vs profile |
|---|---|---|---|---|---|
| 0-3 | B05-B08 | constant | `FF FF FF 3F` | header | new (constant) |
| 4-5 | B09:B10 | s16 | `0x40001E20` | live copy of current-sensor offset 1 | confirmed |
| 6-7 | B11:B12 | s16 | `0x40001E24` | live copy of offset 2 | confirmed |
| 8-9 | B13:B14 | u16 | `0x40001E14` ×10000 | the learned angle (2102 B27:B28) | new (constant) |
| 10-11 | B15:B17 | u16 | `0x40000DF4` | the counter (2102 B50:B51) | new |
| 12-19 | B18:B19, B20:B21, B22:B23, B25:B26 | 4 × u16 | `0x40000DC8`, `DCC`, `DD0`, `DD2` | the four counters (2102 B57, B54, B59, B61) | new |
| 20-21 | B27:B28 | u16 | `0x40002472` | **maximum of the sensor voltage `0x40011C18`, in mV** (accumulator `0x4001EDFC`); 1,275-1,469 | confirmed layout `MCU_21F2_W27`; the meaning (a maximum) is new |
| 22-23 | B29:B30 | u16 | `0x40002474` | the **minimum** of the same voltage (`0x4001EDFE`). **It reads 0 in all 200 captures**: the minimum starts at 0 and is only ever lowered, so it never changes | new (constant 0; a firmware quirk) |
| 24-25 | B31, B33 | u16 | `0x40002476` | **average** of the same voltage (`0x4001EDFA`), 1,258-1,297 | confirmed layout `MCU_21F2_W31`; the meaning (an average) is new |
| 26-27 | B34:B35 | u16 | `0x40001E0C` | the `0xABCD` marker | new (constant) |
| 28-29 | B36:B37 | u16 | `0x40001E80` | the `0xCDC2` marker | new (constant) |
| 30-37 | B38:B39, B41:B42, B43:B44, B45:B46 | 4 × s16 | `0x40001E88`, `E90`, `E98`, `EA0` ×10000 | the four stored values (2102 B34-B42) | new (constant) |
| 38-39 | B47:B49 | s16 | `0x4000246A` | **average heatsink temperature** of the cycle, °C (`0x4001EDF2`) | **confirmed** `MCU_21F2_HEATSINK_TEMP_AVG`: it is a signed 16-bit value in °C, not a byte |
| 40-41 | B50:B51 | s16 | `0x4000246C` | **maximum heatsink temperature**, °C (`0x4001EDF4`) | confirmed `..._MAX` (16-bit) |
| 42-43 | B52:B53 | u16 | `0x40001E6C` | unused slot, 0 | new |
| 44-45 | B54:B55 | u16 | `0x40000DF8` | a counter | new |
| 46-47 | B57:B58 | s16 | `0x4000246E` | **average of the temperature behind 2102 B18**, °C (`0x4001EDF6`) | confirmed `MCU_21F2_TEMP_B18_AVG` (16-bit) |
| 48-49 | B59:B60 | s16 | `0x40002470` | **maximum of the same temperature**, °C (`0x4001EDF8`) | confirmed `..._MAX` (16-bit) |
| 50-51 | B61:B62 | u16 | `0x40001E74` | the `0xCDC1` marker | new (constant) |
| 52 | B63 | u8 | `0x40002464` | **maximum 12 V supply**, 0.1 V (`FUN_0013A334` keeps the maximum of `0x40015E74 × 10`) | **confirmed** `MCU_21F2_SUPPLY_VOLTAGE_MAX` |
| 53 | B65 | u8 | `0x40002465` | **minimum 12 V supply**, 0.1 V | confirmed `..._MIN` |
| 54 | B66 | u8 | `0x40002466` | **average 12 V supply**, 0.1 V | confirmed `..._AVG` |
| 55 | B67 | **s8** | `0x40002467` | **average coolant temperature**, °C (average of `0x40016690`) | confirmed `MCU_21F2_COOLANT_TEMP_AVG`, but signed |
| 56 | B68 | **s8** | `0x40002468` | **maximum coolant temperature**, °C | confirmed `..._MAX`, signed |
| 57-64 | B69-B77 | 8 × u8 | `0x4001151D`-`0x40011524` | the ASCII tag `EHEL-MS2`: bytes 3-10 of the software id copy in `.data` | **confirmed** `MCU_21F2_TAG`; B72 and B64 are PCI bytes |

The four temperature statistics are kept as `sum / sample count` with the count `0x40011A94`,
which `FUN_00123A8C` increments once per 100 ms run while the state is 1, so they are real
means over the powered cycle (verified in `FUN_0013A334`).

`FUN_0013A334` is the only function that stores to `0x40002464`-`0x40002476`, so 21F2 changes
only after a period in state 1 ends, which is what the profile found.

### 5.5 Record 2103

`21 03` returns 25 zero bytes. The record `0x40021AAC` is written by no function; the support-map
byte and the data both stay 0 (verified from the whole image, and in the profile's 697 captures).
There is nothing to decode.

### 5.6 What the profile should take (unverified)

The corrections below are recorded in `mcu.yaml` as unverified signals (none from code alone is
marked verified):

- `MCU_DC_LINK_VOLTAGE` = `((B13<<8)|B12)/100` V, the full-resolution form of `MCU_HV_VOLTAGE`.
- `MCU_2101_B22` becomes the 16-bit millivolt value `(B23<<8)|B22`; `MCU_2101_B25` becomes
  `(B26<<8)|B25`.
- `MCU_2101_PUMP_CURVE` = `(B21<<8)|B20` (new, probable pump duty percent).
- The four 2102 temperatures read `S18*2`, `S19*2`, `S20*2`, `S21*2` (signed, 2 °C per count).
- `MCU_2102_SENSOR_V` = `((B53<<8)|B52)/10000` V (new).
- The learned-position values are listed as constants with their markers.
- The 21F2 temperature and supply statistics get their 16-bit and signed forms.

The unit of the phase currents and the physical meaning of the 2101 flag bytes B09-B11 stay open
(research entries).
