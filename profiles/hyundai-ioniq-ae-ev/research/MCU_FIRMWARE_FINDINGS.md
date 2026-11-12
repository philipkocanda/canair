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
