# Reading ECU memory

Some of what an ECU knows is behind no DID: RAM variables, calibration tables and constants.
ReadMemoryByAddress (service `0x23`, UDS and KWP2000) reads it directly. canair uses it in three
ways:

1. **`canair scan memory`** maps which address ranges an ECU answers, and records the map in the
   ECU's `memory:` section.
2. **`canair memory dump`** reads ranges into a dump on your machine. `canair memory diff`
   compares two dumps, so a RAM variable shows up when you dump in two vehicle states.
3. **A memory PID** reads a few bytes at one address like any other PID: `read`, `monitor
   --save`, `decode`, `hunt` and `correlate` work on it.

Every request is a read. canair never writes memory (`0x3D`), never starts a transfer (`0x34`
to `0x37`), and never unlocks an ECU (`0x27`): a region that answers securityAccessDenied is
reported as locked and left alone.

## Safety

- **Name the ranges yourself.** Neither command has a default range. Take the ranges from the
  microcontroller's memory map. On some chips a read of a peripheral register clears a flag or
  pops a queue, so a read is not always free of side effects. Most ECUs answer only inside
  ranges their diagnostic stack allows, and refuse the rest with `requestOutOfRange`.
- **Both commands ask first.** Pass `--yes` to skip the question. Without a terminal they refuse
  unless you do.
- **The scan reads one byte at a time** until it finds a region that answers. Only inside one
  does it read more, to find the largest block that comes back whole. It doubles the size from
  16 bytes up to `--block-limit` (default 128) and stops at the first size that fails.
- **A read larger than an ECU supports can hang it.** On the 2017 Ioniq the climate unit
  stopped answering every diagnostic request after a 192-byte read, until its supply was
  cycled (charging off and on). So the block search stays small by default, `memory dump`
  refuses a `--block` above the ECU's recorded `max_block`, and the scan stops if a 1-byte
  read fails after a failing large one.

## Dumps stay on your machine

A dump can hold OEM software (copyright) and secrets such as immobilizer data. So dumps are user
state, under `~/.config/canair/memory/<profile>/<ECU>/`, never part of a profile. `contribute`,
the blind-test strip, the docs explorer and the AutoPID export never see them. Do not share a
dump or an exported file.

A memory PID reads at most 64 bytes. A larger memory read is not saved to `captures/` (the save
says so), and `canair validate captures` reports one that got there anyway, which also stops
`canair contribute`.

## An address belongs to one software build

A dealer reflash moves what an address holds. A memory PID then reads plausible wrong bytes
with no error. So:

- `scan memory` records the ECU's software version with each readable region, and each dump
  records it too. `memory diff` refuses two dumps of different builds.
- Gate a memory PID to an ECU revision (`canair pids set-revisions`) whose fingerprint names the
  software version (`canair ecu revisions ECU set-fingerprint ID --software 'F195:1.04*'`).
  canair reads the version at connect and skips the PID on another build. `canair validate`
  warns about a memory PID that is not gated.
- **AutoPID never ships a memory PID**, whatever its status: the device cannot check the build
  before each read.

The software version reads as `RECORD:TEXT`, the first identity record the ECU answers: UDS
`F189`, `F195`, `F1A0` or `F194`, KWP2000 `95`, `88`, `8D` or `8C`.

## The memory format

A UDS memory request carries an addressAndLengthFormatIdentifier (ALFID): its high nibble is the
width of the size field, its low nibble the width of the address (`24` = a 2-byte size and a
4-byte address). A KWP2000 request has a fixed 3-byte address and 1-byte size, so a KWP2000 ECU
needs no format, and reaches only the low 16 MiB.

`scan memory` tries the usual ALFIDs when the ECU has no format yet. Some ECUs answer a wrong
format with `requestOutOfRange`, the same as a wrong address; the scan then asks for
`--format`. Set one by hand with `canair memory set ECU --format 24`.

## Reply checks

A memory reply is `63` and the bytes read: it repeats nothing of its request. So its length is
its only proof that it answers this request, and canair checks it on every read. A reply of the
wrong length counts as another request's reply, and the ELM327 transports realign their pipe.
`memory dump` alternates its block size (`N`, `N-1`, `N`, …), so a reply to the previous request
always has the wrong length.

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

The query token `23/ADDRESS+SIZE` names a memory read; the ECU's `memory.format` turns it into
the request, which is also the PID's key in `ecus/`. `canair memory export DUMP --format ihex`
writes a dump for a disassembler; Intel HEX and S-record keep its holes.

## What the reference car answers

On the 2017 Ioniq Electric the BCM, the climate unit, the VCU, the MCU and the OBC answer `0x23`
in the default session. The gateway (IGPM), the BMS and the CCM refuse the service, so they
cannot be dumped this way. The details are in `plans/2026-11-05-read-memory-by-address.md`.
