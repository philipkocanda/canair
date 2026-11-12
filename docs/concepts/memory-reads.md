# Reading ECU memory

Some data that an electronic control unit (ECU) holds is not behind any data identifier (DID).
Examples are RAM variables, calibration tables and constants. ReadMemoryByAddress (service `0x23`,
in Unified Diagnostic Services (UDS) and Keyword Protocol 2000 (KWP2000)) reads this data
directly. canair uses it in four ways:

1. **`canair scan memory`** maps which address ranges an ECU answers. It records the map in the
    `memory:` section of the ECU.
2. **`canair memory dump`** reads ranges into a dump on your machine. `canair memory diff`
    compares two dumps. A RAM variable shows up when you dump in two vehicle states.
3. **`canair memory image`** reads the whole readable memory of an ECU into one dump, unattended.
    It plans the read, checks it, and can pause and resume (see
    [A whole memory image](#a-whole-memory-image)).
4. **A memory PID** reads a few bytes at one address like any other PID. `read`,
    `monitor --save`, `decode`, `hunt` and `correlate` work on it.

Every request is a read. canair never writes memory (`0x3D`), never starts a transfer (`0x34` to
`0x37`), and never unlocks an ECU (`0x27`). It reports a region that answers securityAccessDenied
as locked and leaves it alone.

## Safety

- **Name the ranges yourself, unless the space is small.** On some chips, a read of a peripheral
  register clears a flag or pops a queue. So a read is not always free of side effects. Take the
  ranges from the memory map of the microcontroller. Most ECUs answer only inside ranges that
  their diagnostic stack allows. They refuse the rest with `requestOutOfRange`.
- **The default space.** With no `--range`, `scan memory` and `memory image` map the whole space of
  the memory format of the ECU when its addresses are 3 bytes or fewer. This covers every KWP2000
  ECU (`0x000000-0xFFFFFF`, 16 MiB) and a UDS ECU whose addressAndLengthFormatIdentifier (ALFID)
  has a 1- to 3-byte address (see [The memory format](#the-memory-format)). The probes are then
  about 256, 64 KiB apart across 16 MiB. A UDS ECU with 4-byte addresses (4 GiB) still needs
  `--range`. `memory dump` has no default range. The confirmation names the range.
- **Every command that reads memory asks first.** Pass `--yes` to skip the question. Without a
  terminal, these commands refuse unless you pass `--yes`.
- **The scan reads one byte at a time** until it finds a region that answers. Only inside a region
  does it read more, to find the largest block that comes back whole. It doubles the size from 16
  bytes up to `--block-limit` (default 128). It stops at the first size that fails.
- **A read larger than an ECU supports can hang it.** On the 2017 Ioniq, the climate unit stopped
  answering every diagnostic request after a 192-byte read. It stayed silent until someone cycled
  its supply (charging off and on). So the block search stays small by default. `memory dump`
  refuses a `--block` above the `max_block` recorded for the ECU. The scan stops if a 1-byte read
  fails after a failing large one.
- **A read of an address the chip does not implement can stop the ECU.** On the same car, a
  `memory image` survey of the motor controller (MCU) went silent at `0x864000`, and the AC charge
  stopped. That ECU answered every address of its 16 MiB space, so it does not check the range of
  a memory read. `memory image` stops after its map when an ECU answers its whole default space,
  or when `ecus/` records such a map. It reads nothing larger than the 1-byte probes of the map.
  Name the ranges that its memory occupies with `--range`. Take them from the memory map of the
  chip, or from what a dump of its boot block names. `scan memory` still records such a map.

## Dumps stay on your machine

**Do not share a dump or an exported file.** A dump can hold OEM software (copyright) and secrets
such as immobilizer data. So dumps are user state, under
`~/.config/canair/memory/<profile>/<ECU>/`, never part of a profile. `contribute`, the blind-test
strip, the docs explorer and the AutoPID export never see them.

canair writes a dump as it reads: each extent of 4 KiB goes to disk when it finishes. If the
process is killed or the link drops, the dump keeps everything except the extent in flight.
`canair memory ls` shows it as incomplete (or resumable, for an image job).

A memory PID reads at most 64 bytes. canair does not save a larger memory read to `captures/` (the
save says so). `canair validate captures` reports one that got there anyway, which also stops
`canair contribute`.

## An address belongs to one software build

A dealer reflash moves what an address holds. A memory PID then reads plausible wrong bytes with
no error. So:

- `scan memory` records the software version of the ECU with each readable region, and each dump
  records it too. `memory diff` refuses two dumps of different builds.
- Gate a memory PID to an ECU revision (`canair pids set-revisions`) whose fingerprint names the
  software version (`canair ecu revisions ECU set-fingerprint ID --software 'F195:1.04*'`). canair
  reads the version at connect and skips the PID on another build. `canair validate` warns about a
  memory PID that is not gated.
- **AutoPID never ships a memory PID**, whatever its status, because the device cannot check the
  build before each read.

The software version has the form `RECORD:TEXT`. It comes from the first identity record that the
ECU answers: UDS `F189`, `F195`, `F1A0` or `F194`, KWP2000 `95`, `88`, `8D` or `8C`.

## The memory format

A UDS memory request carries the ALFID. Its high nibble is the
width of the size field, and its low nibble is the width of the address (`24` = a 2-byte size and
a 4-byte address). A KWP2000 request has a fixed 3-byte address and 1-byte size. So a KWP2000 ECU
needs no format, and it reaches only the low 16 MiB.

`scan memory` tries the usual ALFIDs when the ECU has no format yet. Some ECUs answer a wrong
format with `requestOutOfRange`, the same as for a wrong address. The scan then asks for
`--format`. Set one by hand with `canair memory set ECU --format 24`.

## Reply checks

A memory reply is `63` and the bytes read. It repeats nothing of its request. So its length is its
only proof that it answers this request, and canair checks the length on every read. canair counts
a reply of the wrong length as the reply to another request, and the ELM327 transports realign
their pipe. `memory dump` alternates its block size (`N`, `N-1`, `N`, …), so a reply to the
previous request always has the wrong length.

## Silence

Some ECUs refuse an address by not answering at all. The VCU of the Ioniq does this above
`0x400000`. Silence also looks like an ECU that stopped answering. So after a silent read, canair
reads one byte at the last address that answered:

- **It answers**: the silent address is a refusal (`no_answer`). `scan memory` maps it and goes
  on. Above the last readable address, it steps to the next aligned boundary, so a large silent
  space costs a few timeouts. `memory dump` records the rest of the range as a gap.
- **It does not answer**: the ECU is offline. `scan memory` and `memory dump` stop.
  `memory image` pauses.

A lost reply of a size the ECU is known to deliver (at or below its `max_block`) does not mean
the block is too large. canair reads the same size again once, and halves the block only when
that read is lost too. The motor controller of the Ioniq loses about one multi-frame reply in 40
in READY, at every size. Halving each time took its survey down to 16-byte reads.

## A whole memory image

`canair memory image ECU` reads the readable memory of one ECU into one dump:

1. **Preflight.** It reads the software version and the health PID of the ECU (`--health`, by
    default its first active PID). With `--state`, it also reads the signals in the `when:`
    predicate of that vehicle state. It stops if the state does not hold.
2. **Map.** It takes the readable regions that `scan memory` recorded for this software build.
    When there are none (or with `--range`), it first maps the space itself, using `--range` or
    the default space above. With no `max_block` recorded, it then finds the largest block the way
    `scan memory` does, from 16 bytes up to `--block-limit` (64 by default, lower than the scan's
    128). It writes the map, the format and the block to the `memory:` section of the ECU
    (`--dry-run` does not). Without `--range`, it stops here when the readable regions cover the
    whole default space (see Safety).
3. **Size and aliases.** It matches the readable span against the common flash sizes. It also
    tests whether the ECU ignores its high address bits and repeats one image. If it does, the
    image reads the first period only.
4. **Survey.** It reads a sample every `--stride` (4 KiB) and sorts it into erased (all `FF` or
    all `00`) or data. It bisects every change between two samples to 64 bytes.
5. **Plan.** It prints the areas, the number of reads and a time estimate. It asks before it
    reads (`--plan-only` stops here). The default plan is **sparse**: data areas in full, erased
    areas sampled 64 bytes every 1 KiB. A sampled extent that turns out to hold data is read in
    full. `--thorough` reads every readable byte, twice.
6. **Read.** It cuts the plan into extents of 4 KiB and writes each one to the dump when it
    finishes. It checks health and state every `--health-every` seconds (30).
7. **Verify.** A sparse image reads again every extent that had an event, the first and last
    extent of each area, and 5 % of the rest. If two reads of an extent differ, a third read
    decides which one the link spoiled. A third read that matches neither means a volatile range
    (RAM, a mapped peripheral or a link fault), and the job stops.
8. **Report.** It reports the rate, every pause, the stability checks, and the fault codes of the
    ECU and its bus neighbours against the baseline from the start. It also reports where the
    identity strings of the ECU (part number, `sw_id`, calibration, `ecu_id`, never the VIN) sit
    in the image.

### Speed

The block stays within the `max_block` of the ECU and `--block-limit`. On an ELM327 transport, the
run prefers 61-byte blocks. A 62-byte reply fits the 9 Controller Area Network (CAN) frames that
the expected-frame-count digit of the adapter can ask for. The adapter then answers without
waiting out its `ATST` budget ([frame counts](../development/frame-counts.md)). Two errors within
50 reads halve the block (down to 16 bytes) and add 20 ms of throttle. A lost reply that is read
again at the same size (see [Silence](#silence)) is not an error. After 500 clean reads, the
block doubles (up to the preferred size) and the throttle drops by 20 ms.

`memory dump` uses the same controller. Its ceiling is `--block`, else the `max_block` of the ECU,
else 64, and it never reads above it. When a reply comes back too large, cut short or lost, the
dump halves the block and keeps it there. After 500 clean reads, the block doubles again until it
reaches the ceiling. Each error adds 20 ms of throttle. The `--throttle-ms` you pass is the lowest
throttle: recovery never goes below it. The block still alternates between `N` and `N-1` bytes.

### Pause and resume

A silent ECU, a failed health or state check, or a dropped link pauses the job. The run then reads
one byte every 30 s for at most `--wait-minutes` (15). When every check passes again, it re-reads
the first extent of the job and its last two, and continues. Otherwise it saves the job and prints
the command that resumes it:

```bash
canair memory image --resume VCU/20261108-101500-vcu-flash
```

A resume runs the preflight again and refuses another software build (the ECU was reflashed, or it
is another ECU). It starts at the block that the run had reached, never above it. Press `s` or
escape during the read to stop and save the job. canair never acts on the car to restore a state.
Keep it charging or in READY yourself.

## The loop that finds a RAM variable

```bash
canair scan memory BCM --range FEDE0000-FEDFFFFF
canair memory dump BCM FEDE0000-FEDFFFFF --label "lights off"
canair memory dump BCM FEDE0000-FEDFFFFF --label "lights on"
canair memory ls BCM
canair memory diff BCM/20261105-2210-lights-off BCM/20261105-2212-lights-on
canair read BCM:23/FEDE1234+02            # read the candidate live
canair pids upsert-param BCM 23/FEDE1234+02 LIGHT_STATE B04   # then define it
canair monitor BCM:23/FEDE1234+02 --save --label "lights"
```

The query token `23/ADDRESS+SIZE` names a memory read. The `memory.format` of the ECU turns it
into the request, which is also the key of the PID in `ecus/`.
`canair memory export DUMP --format ihex` writes a dump for a disassembler. Intel HEX and S-record
keep the holes of the dump.

## What the reference car answers

On the 2017 Ioniq Electric, the BCM, the climate unit, the VCU, the MCU and the OBC answer `0x23`
in the default session. The gateway (IGPM), the BMS and the CCM refuse the service, so you cannot
dump them this way. What each one gives:

- **BCM:** its SRAM (`0x40000000`-`0x4000BFFE`) and its data flash (`0x00804000`-`0x0080FFFE`).
  It refuses its code flash. Other windows answer with zeros and are not memory.
- **VCU:** its flash (`0x000000`-`0x3FFFFF`), not its RAM.
- **OBC:** its flash (`0x000000`-`0x3FFFFF`), read with that `--range` while charging.
- **Climate unit:** no memory. It ignores the low 16 address bits and returns its transmit buffer.
  An address with a high byte of `01`-`FF` draws NRC `0x52`.
- **MCU:** its flash (`0x000000`-`0x3FFFFF`), read with that `--range`. It answers every address
  of its 16 MiB space, and a survey that went above `0x3FFFFF` ended an AC charge on 2026-11-08.
  In READY it ignores about 1 in 40 reads whose reply needs more than one CAN frame.

The details are in `plans/2026-11-05-read-memory-by-address.md` and in the `research:` entries of
each ECU.
