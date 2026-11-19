# Which chip is it: name an ECU's microcontroller and read its layout from the boot block

Status: **PROPOSED** (2026-11-09). No code yet.

Paths are relative to `src/canair/`. Builds on `plans/2026-11-08-memory-image.md` and
`plans/2026-11-08-memory-default-space.md`. The evidence comes from the live tests in
`plans/2026-11-05-read-memory-by-address.md` and from the VCU and OBC firmware findings
(`profiles/hyundai-ioniq-ae-ev/research/`).

**Accuracy note.** The addresses, header layouts and part sizes in this plan are starting points
taken from memory of the public reference manuals. Phase 0 confirms each one against the manual.
The catalogue ships an entry only together with its source (document number and section).

## Problem

1. **An ECU that answers every address cannot be imaged.** The Ioniq's motor controller (the
   profile's `MCU` ECU) answered every 1-byte probe of its 16 MiB space. Its survey then went
   silent at `0x864000` and the AC charge stopped. `memory image` now stops there and asks for
   `--range` "from the chip's memory map, or what its boot code names". canair cannot help the
   user find either one. The bytes at address 0 (`00 5A`, then PowerPC code) already point to an
   NXP MPC5xxx part, but no tool reads them that way.
2. **A UDS ECU with 4-byte addresses has no default space.** The BCM's SRAM and data flash were
   found with a coarse scan by hand. That scan also read 1 byte at a few addresses from
   `0xC3F90000` up, which is the peripheral space of the MPC560xB family the map fits. The docs
   warn against exactly that read.
3. **An image does not know what its areas are.** The VCU's layout (boot blocks, the identity
   header, calibration, program, the `.data` load image) took hours of static analysis. Part of
   it is in a documented header at address 0, and part is in the OEM bootloader's tables.
4. **What is known about each chip is free text.** It sits in `research:` notes (`ecus/bcm.yaml`,
   `ecus/mcu.yaml`, `ecus/obc.yaml`) and in the findings documents. No field holds it, so no
   command can use it.

## What a boot block can tell, and what it cannot

The documented boot headers of automotive microcontrollers say **where execution starts**. Most
of them do not say **how large the application is**. This sets what the feature can deliver:

| Source | Gives | Example |
|---|---|---|
| A standard boot header | the family, the core or instruction-set mode, the entry point(s) | MPC5xxx reset configuration halfword (RCHW), S32K3 image vector table (IVT) |
| A boot header with a check range | the start and end of the checked application | AURIX TC2xx boot mode header (BMHD) |
| A vector table | entry points, so a lower bound for the end of the program | Cortex-M, SH-2, ARM exception vectors |
| The part's flash size | the upper bound, once the part is known | the catalogue, or a declared part |
| The OEM bootloader's block table | each flashable block's start and end | the VCU's and the OBC's table up to `0x380000` |
| An OEM checksum header | the range a checksum covers, and proof the dump is exact | the VCU's byte sum over `0x4000`-`0x3FFFFF` at `0x10078` |
| The survey | where data and erased flash really are | `memory image` today |

So "the size of the application blocks" is a set of bounds from several sources, each with a
confidence. The standard headers come from public manuals and fit a canair catalogue. The OEM
tables and checksums are make-specific, so they belong in the profile (phase 5).

## Goal

1. **Offline:** from a dump, name the chip family and the candidate parts, read its boot header,
   and give the program's entry points and bounds, each with its evidence and confidence.
2. **A catalogue** of the microcontroller families that automotive ECUs commonly use. Each entry
   holds the memory map by role (flash, data flash, SRAM, peripheral), the boot header format and
   its search addresses, the instruction set and byte order, the parts and their sizes, and the
   sources.
3. **Record** the chip and the layout in the ECU's `memory:` section, through a tool.
4. **`memory image` uses them.** It labels the areas of its plan, never reads a peripheral range,
   and turns the "answers everywhere" stop into a bounded `--range` proposal. The image stays
   complete: every byte the ECU returns is in the dump.
5. **`scan memory` proposes ranges** for a UDS ECU with 4-byte addresses from its chip.

## Non-goals

- **Firmware analysis.** No disassembler, no control flow, no decompiling, no search of the code.
  canair reads header fields at fixed offsets, checks that pointers are plausible, and counts a
  few fixed byte patterns (the census below). Ghidra and similar tools stay the place for the
  rest. This keeps the non-goal of the memory-image plan.
- **Any service but `0x23`.** The blocklist does not change.
- **Security access.** No SecurityAccess (`0x27`) and no use of security material. The image
  holds the flash as the ECU returns it, with any key or password words in it. canair does not
  locate, decode or extract them, and the catalogue does not describe them. The dump stays on the
  local machine. The rules of the **contributing-code** skill hold unchanged.
- **A complete catalogue.** An unknown chip gives "unknown", with the evidence found.

## How to identify a chip: the evidence

| # | Evidence | Cost on the car | Strength | Tells apart |
|---|---|---|---|---|
| 1 | **Declared** by the user: a chip marking from a teardown, a datasheet | none | strongest, the only way to `verified` | the part |
| 2 | **Boot header** at a documented address: RCHW boot id `0x5A`, BMHD id `0xB359`, IVT marker `0x5AA55AA5` | a few 64-byte reads in flash | strong | the family, often the mode (the RCHW's VLE bit) and the entry point |
| 3 | **Vector table shape**: a Cortex-M stack pointer into SRAM and an odd reset handler in flash, the ARM exception vectors, the SH-2 reset PC and SP, the RH850 table of one jump every 16 bytes | the same reads | medium | the architecture and the byte order |
| 4 | **Instruction-set census** over code already read: the count of each instruction set's fixed return and prologue patterns, in both byte orders | none (offline) | medium | the instruction set and the byte order |
| 5 | **Memory-map signature**: which family landmarks answer | 1-byte probes | weak alone | the family class |
| 6 | **Readable span** against the parts' flash sizes | none | weak alone | the part |
| 7 | **Identity strings** | none | weak | rarely names the chip |

Examples of evidence 4: Power Architecture `blr` (`4E 80 00 20`) and `mflr r0` (`7C 08 02 A6`),
VLE `se_blr`, Thumb `bx lr` (`70 47`), A32 `bx lr` (`1E FF 2F E1`), SuperH `rts` (`00 0B`),
TriCore `ret`, RH850 `jmp [lp]`. Phase 0 takes every encoding from the architecture manual.

Examples of evidence 5: SRAM at `0x40000000` with flash at 0 points to MPC5xxx. SRAM at
`0x1FFF8000`/`0x20000000` points to S32K1 or Kinetis. `0x80000000`/`0xA0000000` with
`0x70000000` points to AURIX. Flash at `0x08000000` points to STM32. RAM that ends at
`0xFEDFFFFF` points to RH850. This evidence is weak: the BCM answers zeros at
`0xFEDC0000`-`0xFEE0FFFF` (an RH850 local-RAM address), but its map is an MPC560xB. A landmark
that answers only zeros counts as no evidence.

**The verdict.** Each catalogue family collects points from the evidence. The result is a ranked
list of candidates with the tags that the findings documents use:

- **verified**: the part is declared.
- **probable**: a boot-header or vector-table match, plus one independent piece of evidence
  (the census, the map, the span).
- **inferred**: one piece of evidence.

A contradiction (a PowerPC header with a Thumb census) is shown, and it lowers the verdict.

**What the reference car would give** (expected, to check in phase 0):

| ECU | Evidence available | Expected verdict |
|---|---|---|
| VCU | RCHW `0x005A` at 0, reset vector `0x24`, a Power census, span 4 MiB, silent above | MPC55xx Book E, probable; parts MPC5674F or another 4 MiB part, inferred |
| OBC | the same boot block | the same |
| MCU | the 64 bytes at 0 from phase 0 (`captures/2026-11-05.json`), `00 5A` | MPC5xxx, inferred (one read only) |
| BCM | code flash refused, SRAM at `0x40000000`, data flash at `0x00800000`, shadow block `0x00200000` refused | MPC56xx, inferred from the map |
| HVAC | its `0x23` returns its transmit buffer | no memory (the alias test already finds this) |

## The catalogue

One entry per family, with its parts. A sketch of the shape (values to confirm):

```yaml
mpc55xx:
  name: NXP/ST MPC55xx (SPC55)
  isa: power-booke          # some parts also run VLE; the RCHW says which
  endian: big
  boot:
    header: rchw            # the parser in memory/chips/headers.py
    search: [0x000000, 0x004000, 0x010000, 0x01C000, 0x020000, 0x030000]
  map:
    - {start: 0x00000000, role: flash, size: part}
    - {start: 0x40000000, role: sram, size: part}
    - {start: 0xC3F00000, end: 0xC3FFFFFF, role: peripheral}
    - {start: 0xFFF00000, end: 0xFFFFFFFF, role: peripheral}
  parts:
    MPC5674F: {core: e200z7, flash: 4 MiB, sram: 256 KiB}
  sources: ["MPC5674F reference manual, BAM chapter", …]
```

The first families, in order of how often automotive ECUs use them:

| Family | Core, instruction set, byte order | Boot header | Memory | Peripherals (never read) |
|---|---|---|---|---|
| NXP/ST MPC55xx (SPC55) | e200z1-z7, Power Book E, big | RCHW: boot id `0x5A` in byte 1 of a boot block, reset vector at +4 | flash `0x0`, SRAM `0x40000000` | the peripheral bridges |
| NXP/ST MPC56xx (SPC56) | e200z0/z4, VLE, big | RCHW, as above, at the low flash sectors | flash `0x0`, data flash `0x00800000`, SRAM `0x40000000` | from `0xC3F00000`, from `0xFFE00000` |
| NXP MPC57xx, ST SPC58 | e200z2/z4/z7, VLE, big | a boot header with id `0x5A` and a reset vector per core | per part | per part |
| NXP S32K1xx | Cortex-M4F/M0+, Thumb, little | the vector table at 0, the flash configuration field at `0x400`-`0x40F` | P-flash `0x0`, FlexNVM `0x10000000`, SRAM around `0x20000000` | from `0x40000000`, from `0xE0000000` |
| NXP S32K3xx | Cortex-M7, Thumb, little | an IVT with marker `0x5AA55AA5` at fixed code-flash addresses | code flash `0x00400000`, data flash `0x10000000`, SRAM `0x20400000` | from `0x40000000`, from `0xE0000000` |
| Infineon AURIX TC2xx | TriCore 1.6, little | BMHD: id `0xB359`, start address, check range, CRCs | program flash `0x80000000` (cached) and `0xA0000000`, data flash `0xAF000000`, scratchpads `0x70000000`/`0xD0000000` | from `0xF0000000` |
| Infineon AURIX TC3xx | TriCore 1.62, little | BMHD in the UCB: id `0xB359`, start address, CRC, a confirmation word | as TC2xx | from `0xF0000000` |
| Renesas RH850 (F1x, P1x, E1x) | G3K/G3M/G3KH, little | the exception table at the reset base (0 by default) | code flash `0x0`, local RAM that ends at `0xFEDFFFFF`, data flash `0xFF200000` | the peripheral space |
| Renesas SH-2/SH-2A | SuperH, big | the vector table at 0: reset PC, then reset SP | flash `0x0`, RAM high, per part | the on-chip peripheral area |
| TI TMS570 (Hercules) | Cortex-R4F/R5F, big | the ARM exception vectors at 0 | flash `0x0`, RAM `0x08000000` | per part |
| ST STM32, generic Cortex-M | Thumb, little | the vector table at `0x08000000` (also seen at 0) | flash `0x08000000`, SRAM `0x20000000` | from `0x40000000`, from `0xE0000000` |

**Later, after research:** NXP S12/S12X (vectors at the top of the space), Infineon AUDO
(TC17xx), Infineon XC2000/C166, Cypress/Infineon Traveo and Fujitsu FR. These are common in older
body and engine ECUs.

## Reading the boot block: from header to layout

1. **Find.** Start with the bytes at the start of each readable region. The survey reads these
   bytes already. A match names one or more candidate families. Then read 64 bytes at each search
   address of a candidate family, but only where the address is inside the readable map and below
   the smallest flash of every catalogued part of that family. Such an address exists on every
   part, so the read cannot hit an unimplemented address.
2. **Parse.** One pure parser per header format returns the named fields and their offsets, or
   `None`. A match needs the magic value, the reserved bits as documented, the CRC or complement
   where the format has one (the BMHD has both), and plausible pointers.
3. **Follow the pointers.** For each entry point or vector that lands in the family's flash and
   inside the readable map, read 64 bytes there and nothing else. The CPU executes at these
   addresses, so they are implemented memory. Their census also confirms the instruction set.
4. **Derive the layout.** Blocks with a role (boot, entry, vector table, checked application,
   OEM block) and the bounds of the program, each with its source and confidence:
   - the lower bound is the highest pointer, rounded up to the family's flash block;
   - the TC2xx check range, where the header has one;
   - the upper bound is the flash size of the part, when the part is declared or probable;
   - the OEM block table, when the profile declares one (phase 5).
5. **Report.** The candidates, the header fields, the layout, the bounds, and what it did not
   establish.

A sketch of `canair memory identify` over the VCU's dump (the numbers are placeholders):

```text
VCU/20261107-…  0x000000-0x3FFFFF, software 95:…
chip      NXP MPC55xx (Power Book E, big-endian)                      probable
  from    RCHW 0x005A at 0x000000 (boot id 5A, VLE off), reset vector 0x000024
          census: blr …, mflr r0 … (Power, big-endian); no other instruction set
          readable span 4 MiB, silent above
  parts   MPC5674F (e200z7, 4 MiB), … (4 MiB)                         inferred
layout    0x000000-0x0000FF  boot block (BAM header, entry 0x000024)  header
          0x000000-0x3FFFFF  flash (4 MiB part)                       inferred
not found the size of the application: no header names it; see the survey
```

For an MPC55xx the standard header gives little more than this. The VCU's application blocks
come from the Hyundai bootloader's table, which is phase 5.

## Using it

### `memory image`

A new step, **chip**, runs between the map and the size and alias test. It identifies the chip
from the bytes the run has, plus the boot reads above. A recorded or declared chip skips the
identification. The pure planner then:

- **removes the peripheral ranges** of the candidate families from the regions. Removing an
  address is always safe, so this applies at any confidence;
- **labels each area** of the plan and the manifest with its role (boot, calibration if the OEM
  layout names it, application, data flash, SRAM);
- **for an ECU that answers everywhere**, prints a `--range` proposal from the chip and its
  bounds, with the evidence for each bound, and stops (decision 2). The user runs the proposal.

### `scan memory`

For a UDS ECU with 4-byte addresses and no `--range`:

- **With a chip** (recorded or declared), the default space is the family's memory ranges
  (flash, data flash, SRAM) of the part, or of the largest part when none is declared, less the
  peripheral ranges. The confirmation names every range.
- **With no chip**, a landmark probe reads 1 byte at the memory bases of each catalogued family.
  It runs only when the ECU has shown that it checks the range: some address has drawn an NRC
  refusal, as `0x0` does on the BCM. The prompt names every landmark address.

### `memory show`, `memory ls`

`memory show DUMP` prints the chip and the layout when the dump's ECU has them.

## Recording

The ECU's `memory:` section gains two fields, written by the tools only:

```yaml
memory:
  format: kwp
  chip:
    family: mpc55xx
    part: MPC5674F          # only with `memory set --chip` or a probable part
    confidence: probable    # verified (declared), probable, inferred
    evidence: ["RCHW at 0x000000", "census power-be", "span 4 MiB"]
    found: "2026-11-09"
  layout:                   # per software build, like regions:
    - {start: 0x000000, end: 0x0065FF, role: boot, source: header, software: "…"}
```

The chip is hardware, so a reflash does not change it. The layout belongs to one software build,
like `regions:`. Both are addresses and names, never bytes, so a profile with them is shareable.
The dumps stay local, as before.

## Where the code goes

| Piece | Module | Kind |
|---|---|---|
| The catalogue | `memory/chips/catalogue.yaml`, `schema/chips_schema.yaml`, `CHIPS_FILE` in `core/paths.py` | data |
| Its loader and model | `memory/chips/catalogue.py` | pure |
| One parser per header format | `memory/chips/headers.py` | pure |
| The census | `memory/chips/isa.py` | pure |
| Evidence to ranked candidates | `memory/chips/identify.py` | pure |
| The layout, the bounds, narrowing a plan | `memory/chips/layout.py` | pure |
| The boot and pointer reads | `live/memory/boot.py` | live |
| The chip step | `live/memory/image.py`, `live/memory/survey.py`, `live/memory/scan.py` | orchestration |
| `chip:` and `layout:` read from `ecus/` | `ecus/memory.py` | definitions |
| Their editor | `yaml_edit/memory.py` | editor |
| Known chip names, layout inside the regions | `validation/memory.py` | validation |
| `memory chips`, `memory identify`, `memory set --chip` | `cli/commands/memory/` | CLI |

- `ecus/` sits below `memory/`, so `ecus/memory.py` keeps the chip as strings, and
  `validation/memory.py` resolves them against the catalogue.
- `live/memory/image.py` and `live/memory/scan.py` both have 399 lines. The new step lives in
  `boot.py`, and each of them gets one call.
- The `memory/` charter gains "the chip catalogue and the boot-header recognition (`chips`)".
- **Naming.** The reference car has an ECU called `MCU` (the motor controller). Code says `chip`,
  never `mcu`, and prose says "microcontroller".

## Commands

- `canair memory chips [FAMILY|PART] [--json]` lists the catalogue, or shows one entry with its
  map and sources. Offline.
- `canair memory identify DUMP [--json] [--write]` identifies the chip and the layout of a dump.
  Offline. `--write` records them in the ECU's `memory:` section (`--profile` as for every
  write).
- `canair memory set ECU --chip FAMILY[/PART]` declares the chip (`verified` with a part).
  `--clear chip` drops it.
- `memory image` and `scan memory` get no new flag in phases 3 and 4. The step runs by itself,
  and the confirmation names what it reads.

## Safety

- **The catalogue narrows and never widens.** canair never reads an address because of the
  catalogue when the map shows that address as unreadable or the chip marks it as peripheral. For
  an ECU that answers everywhere, the proposal is printed and the user runs it.
- **The boot reads are bounded.** 64 bytes at most, within `max_block`, only at a family's search
  addresses below its smallest flash and at pointers inside its flash and the readable map. On an
  ECU that answers everywhere, the first read is at address 0, which the survey reads anyway.
- **The landmark probe** reads 1 byte at each landmark, only on an ECU that checks the range.
- **The MCU.** No live test on the Ioniq's motor controller on a car in use. The phase-0 capture
  at address 0 is the test input, and a bench unit is the place for anything more.
- **Agents.** The `AGENTS.md` car rule holds. `memory chips` and `memory identify` are offline.
  Every live step needs the user's approval of the exact command.

## Phases

0. **Research, no code, no car.** Confirm each family's header layout, search addresses and memory
   map against its reference manual, and record the source. Check
   the expected verdicts above against the local VCU and OBC dumps and the MCU's phase-0
   capture. Record the results in this plan, with no dump bytes.
1. **The offline core.** The catalogue and its schema, the header parsers, the census, the
   identification, `memory chips` and `memory identify` (`--json`). *Useful alone: it names the
   chip in every dump on disk.*
2. **Recording.** `memory set --chip`, `memory identify --write`, the `ecus/` schema, the editor,
   the validation, the layout in `memory show`.
3. **`memory image`.** The chip step, the narrowing (peripheral ranges, roles), the proposal for
   an ECU that answers everywhere. With the user's approval: `memory
   image OBC --plan-only` while charging, which reads the recorded map, the boot reads and the
   survey.
4. **`scan memory`.** The default ranges from a chip, and the landmark probe. With the user's
   approval: `scan memory BCM --dry-run` with no range, while charging.
5. **Optional: the OEM layout.** A profile-declared block table (a table at an address with a
   fixed entry layout, for example two big-endian 32-bit addresses per entry until a terminator)
   and a checksum declaration (algorithm, range, where the stored value is). `memory image`
   then labels the OEM blocks and checks the checksum after the read. The VCU's byte sum would
   have proved its dump exact without a second read. The OBC's sum does not match yet, which is
   the kind of fault this check exists to find.

## Tests

- **Synthetic bytes only.** Fixtures build each header from the documented format with invented
  addresses and valid CRCs. No real firmware, no real dump, no capture payload from a real ECU's
  flash goes into `tests/`.
- **Parsers:** a match per format; random data, erased flash and an aliased window give no
  match; a lone `0x5A` at byte 1 gives no probable verdict.
- **Identification:** combinations of evidence; a contradiction lowers the verdict; a window of
  zeros at an RH850 address does not make RH850 probable.
- **The fake ECU** (`tests/support/memory_ecu.py`) gets a chip. It answers everywhere and has an
  RCHW at 0: `memory image` stops with a proposal and reads nothing outside the boot reads. The
  boot reads stay below the smallest flash. No peripheral address is read.
  The prompt names every address the step reads.
- **The catalogue:** it passes its schema. Every family has a source. The roles do not overlap.
  Every search address lies inside the smallest part's flash.

## Decisions to settle before phase 1

My recommendation is first in each.

1. **The catalogue's format.**
   - *(Recommended)* **YAML and a schema.** Each entry carries its sources and a confidence. That
     reads better as data, and a contributor can add a chip without writing Python.
   - *Python tables.* Type-checked and need no loader, but a long table module needs a
     `long_modules` entry.
2. **An ECU that answers everywhere.**
   - *(Recommended)* **Propose and stop.** The run prints a `--range` from the chip and its
     bounds, and the user runs it. A wrong bound stopped a charge once.
   - *Proceed after the prompt.* The run reads the bounded range when the user confirms. This is
     faster, but `--yes` would then accept a range that no person has read.
3. **The instruction-set census.**
   - *(Recommended)* **Built in, fixed patterns only.** Small, deterministic and testable.
   - *An external tool* (such as the statistical `cpu_rec`) when it is installed. Better on
     unknown instruction sets, but a dependency and a second answer.
4. **OEM bootloader tables.**
   - *(Recommended)* **Declared in the profile** (phase 5). The tables are make-specific, and
     the profile is where make-specific facts live.
   - *Built-in decoders per bootloader vendor.* No public specification to build them from.
5. **The chip's scope.**
   - *(Recommended)* **Per ECU.** One field, and a revision gate can come later.
   - *Per ECU revision.* Exact when one ECU ships with two parts, at the cost of the revision
     machinery for every chip.

## Open questions

1. **Sources.** Most reference manuals need a vendor account. The catalogue holds facts
   (addresses, field offsets), not text. Is a document number and section enough as a source, or
   should each entry also name the public datasheet?
2. **Which families first.** Only the Ioniq profile has evidence today. Do the other bundled
   profiles' research notes name chips that change the order above?
3. **Census thresholds.** How much code does the census need for a verdict? The pointer reads
   give 64 bytes each, and a survey sample gives 64 more. Measure on the local dumps in phase 0.
