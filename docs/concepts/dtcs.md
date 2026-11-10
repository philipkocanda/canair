# Reading & interpreting DTCs

A **Diagnostic Trouble Code (DTC)** is a fault an ECU has stored in its memory —
the machine-readable form of a "check engine"-style light. `canair dtc` reads
them off the bus, decodes their structure, layers on any known meaning, and logs
each scan so you can see what changed over time. This page explains what a code
*is*, how canair decodes it, and how to read the output.

For the full flag list see the [`canair dtc` CLI reference](../reference/cli/dtc.md).

## Quick start

```bash
canair dtc BMS          # read stored DTCs from one ECU
canair dtc --all        # sweep every ECU in the profile
canair dtc BMS --clear  # erase fault memory (asks to confirm)
```

`canair dtc` is **read-only** unless you pass `--clear`.

## Two protocols, two services

Like reads (see [ECU protocols & PID prefixes](ecu-protocols.md)), DTC access is
**protocol-aware** and auto-selected per ECU from its `id_protocol`:

| Protocol | Read service | Clear service | DTC record |
|---|---|---|---|
| **UDS** (ISO 14229) | `0x19` sub-function `0x02` — *reportDTCByStatusMask* | `0x14` — *ClearDiagnosticInformation* (3-byte group) | 4 bytes: 3-byte DTC + 1-byte status |
| **KWP2000** (ISO 14230) | `0x18` — *readDTCByStatus* | `0x14` (2-byte group) | 3 bytes: 2-byte DTC + 1-byte status |

You don't choose the service — canair reads `id_protocol` and sends the right
one, exactly as it does for a normal read. Force one with `--protocol uds|kwp`
if you need to override the auto-selection.

## Anatomy of a code

A UDS DTC prints as `Lxxxx-yy`, e.g. `B2915-11`:

```
 B 2 9 1 5 - 11
 │ │ └─┴─┴──── the fault (SAE J2012 code number)
 │ └────────── generic vs manufacturer-specific
 │             (second-char convention)
 └──────────── category letter
               └ failure-type byte (FTB), UDS only
```

- **Category letter** — the top two bits of the first byte:
  `P` Powertrain · `C` Chassis · `B` Body · `U` Network.
- **Generic vs manufacturer-specific** — from the SAE J2012 second-character
  convention. For `P` codes, digit `0`/`2` are generic (ISO/SAE-defined) and
  `1`/`3` are manufacturer-specific; for `C`/`B`/`U`, digit `0` is generic and
  `1`/`2` manufacturer-specific.
- **Failure-type byte (FTB)** — the `-yy` suffix (UDS only; ISO 14229-1 Annex D.3
  / SAE J2012-DA). It refines *how* the circuit failed, e.g. `-11` = short to
  ground, `-13` = open circuit, `-1C` = voltage out of range. canair knows the whole
  standard table; only the manufacturer-specific category (`-F0`…`-FF`) needs the
  profile. KWP2000 codes have no FTB, so they print without a suffix (`P0420`).

canair decodes the standardized structure of *every* code, even one it has never
seen. What it can't invent is the **manufacturer-specific text** — what Hyundai
means by `B2915` isn't encoded in the code itself. That meaning is layered on
from curated sources (below), never guessed.

### When a code is not SAE J2012

The `Lxxxx-yy` form is SAE J2012's. ISO 14229-1 lets an ECU use another format and
report which one (the DTCFormatIdentifier, Table D.14). When a UDS ECU reports codes,
`canair dtc` asks it with `19 01` (one more request, only for an ECU that has codes):

- `00` and `04` are SAE J2012-DA formats: the codes print as above.
- `01` (ISO 14229-1's own format, decoded as the maker decides), `02` (SAE J1939-73) and
  `03` (ISO 11992-4) have no standard text form. canair prints those codes as six hex
  digits (`A91900`), says which format the ECU reported, and does not look them up as
  SAE J2012 codes. `--json` carries the format as `dtc_format`.
- An ECU that does not answer `19 01` (the Ioniq's amplifier refuses it) is read as
  SAE J2012, as before; `dtc_format` is then `null`.

A record whose code is `000000` is not a fault, so canair skips it.

## The status byte

Each code carries a one-byte **statusOfDTC**. For UDS, canair decodes the eight
ISO 14229-1 bits by name:

| Bit | Name | Meaning |
|---|---|---|
| 0 | `testFailed` | failing right now |
| 1 | `testFailedThisOperationCycle` | failed this drive cycle |
| 2 | `pendingDTC` | failed this or last cycle (not yet confirmed) |
| 3 | `confirmedDTC` | stored/confirmed fault |
| 4 | `testNotCompletedSinceLastClear` | monitor hasn't run since the last clear |
| 5 | `testFailedSinceLastClear` | failed at least once since the last clear |
| 6 | `testNotCompletedThisOperationCycle` | monitor hasn't run this cycle |
| 7 | `warningIndicatorRequested` | driver warning lamp requested |

The most useful distinction: **`confirmedDTC`** (a real stored fault) vs
**`pendingDTC`** (an intermittent one seen but not yet confirmed).
KWP2000's status byte uses a different, ECU-specific layout, so canair reports
its raw hex value without named bits.

KWP2000 documents disagree on the `0x18` request itself, so canair tries `18 00 FF 00`
first (the form the Ioniq's ECUs answer), then `18 02 FF 00` and `18 00 FF FF` if the
ECU rejects it. When only an alternate form is answered the output carries a `Note:`
line (and a `note` field in `--json`), and the all-ECU scan shows `(via 1802FF00)`.
`18 02` in particular may select 2-byte hex DTCs rather than SAE J2012 ones, in which
case the printed `P`/`C`/`B`/`U` letters are not meaningful.

`canair dtc BMS --supported` sends a different request, `18 03 FF 00`, which lists every
DTC a KWP2000 ECU can report whatever its status. That is a catalogue, not a fault list (a
healthy ECU returns a long one), so it is never a fallback: it runs only on request, prints
raw hex codes with their raw status byte, and is never written to the DTC log.

### Status masks

The UDS read takes a **statusOfDTC mask** — only codes matching the mask are
returned. `--mask FF` (the default) asks for everything; `--mask 08` returns only
confirmed codes. Some ECUs reject `FF` with `requestOutOfRange` (NRC `0x31`); when
that happens canair automatically retries with `0x08` (confirmed), the most widely
supported mask.

## Reading the output

```
  DTC read: BMS
  Protocol: KWP2000 18 (readDTCByStatus)

  2 DTC(s):

  P0AA6  0x08  confirmedDTC
         → Powertrain · manufacturer-specific
  U0111  0x0C  confirmedDTC, pendingDTC
         → Lost communication with battery energy control module
```

Each row is `DTC  status  flags`; the `→` line is the decoded meaning. Where a
code's text is known (from the profile or the small bundled generic table) it's
shown; otherwise you get the structural interpretation (category ·
generic/manufacturer-specific · failure type).

A **full sweep** (`canair dtc --all`) prints a per-ECU status column
(clean / N DTC(s) / not supported / no response), retries any silent ECU once with
a wake + longer timeout, then lists every faulty ECU's codes. Add `--json` for a
machine-readable result.

## Where meanings come from

canair layers a code's meaning from these sources, most specific first:

1. **The reporting ECU's `dtcs:` section** (`ecus/<name>.yaml`) — the
   authoritative, curated code text for *this* car. A code is looked up on the
   ECU that reported it, never borrowed from another: two units can give one code
   different meanings (`C1616` is "CAN communication line malfunction" on a blind-spot
   radar and "CAN bus off" on the steering).
2. **The profile-wide `dtcs:` section** (`profile.yaml`) — codes that mean the same
   on every unit that reports them (a manufacturer often keeps "bus off" or a
   module time-out consistent across its ECUs), stated once instead of on each ECU.
   An ECU's own entry for the code wins.
3. **A small bundled generic (ISO/SAE) table** — a handful of well-known codes
   (`P0300`, `P0420`, `U0100`, …).

The failure-type byte (the `-XX` suffix) is named from the standard SAE J2012 /
ISO 14229-1 table, which manufacturers such as Hyundai/Kia adopt unchanged; a
manufacturer-specific value (`0xF0`–`0xFF`) is shown by its number.

Manufacturer meanings are **never invented** — an unknown manufacturer code shows
its structure only. To teach canair a code's meaning, add it to the ECU's `dtcs:`
section, or to the profile-wide one if it means the same on every unit (this is
authored data — edit it through the profile, not by guessing).

Where every code in a section comes from the same place, state `source`,
`source_links` and `verified` once, directly under `dtcs:`. A code that sets one of
them itself keeps its own value:

```yaml
dtcs:
  source: Ioniq Electric 2017 service manual, ESP/AHB diagnosis (EV)
  source_links:
    - https://example.com/esp-ahb-diagnosis
  verified: false
  C1101:
    description: Battery voltage high
  C1102:
    description: Battery voltage low
    source: Ioniq 2017 US shop manual   # overrides the section's source
```

Keys are told apart by shape: a code is a letter and four hex digits (`C1101`),
without the `-FTB` suffix. The profile-wide `dtcs:` in `profile.yaml` takes the same
shape.

## The DTC log

Every scan is recorded to the DTC log (disable with `--no-log`)
and compared against the previous same-scope scan, so canair reports **what
changed** — codes that **appeared** and codes that **cleared** (the car
self-healed or a code aged out) since last time. Tag a scan for context:

```bash
canair dtc --all --state ready --label "before fix"
canair dtc BMS --label "after replacing sensor"
```

- `--label` annotates the log entry.
- `--state` records the vehicle power state(s) during the scan (comma-separated,
  from the [`vehicle_states.yaml`](vehicle-states.md) vocabulary, e.g.
  `ready` or `sleep, plugged`).

Review the log **without touching the device** (handy when the car is off) with
`canair dtc log`:

```bash
canair dtc log         # the last logged full sweep, and every ECU read on its own since
canair dtc log BMS     # the last logged scan for one ECU, offline
```

A full sweep is only as current as its date. When you read or clear one ECU after it
(`canair dtc IGPM`), the bare `canair dtc log` lists that ECU's latest reading under
**Read since this sweep**, so the newer codes are not hidden behind the older sweep.

A bare `canair dtc` says how many codes the log holds and how many are still open,
`canair ecu <ECU>` lists that ECU's **open** codes in red (the codes its most recent
logged scan read), and `canair config` shows each profile's log totals.

**Where it lives.** The log is the only record of your car's fault history, so it is
kept in user state, `~/.config/canair/dtc_log/<profile>.yaml`, and not in the profile
directory, which a reinstall, a re-clone or a `git clean` deletes. A
`dtc_log.yaml` left in a profile by an older canair is moved there automatically:
the first time that profile's log is used, and for every profile at once by
`canair config` and before `canair update` reinstalls. A log written by an earlier
version is converted to the current format the same way.

**It records readings, not names.** Each entry names an ECU only by its response
address, as a capture does; the name you see comes from the profile, so renaming an
ECU changes nothing in the log. Only scans and clears are stored — which codes
cleared on their own is worked out from consecutive scans when you look.

**It belongs to one car.** Each entry records the [variant](variants.md) it was
read from and the revision of each multi-revision ECU it covers. A code read from
another variant, or from another revision of the ECU (a swapped module starts with
its own fault memory), says nothing about this car, so it is left out of the
comparison, the open codes and the totals. With several variants and none selected,
pick one with `--variant`. A full sweep also records which ECUs it could not read:
an ECU that was asleep is neither "cleared" nor "clean". An entry logged before
the log carried these tags is given them where the profile settles them on its own
(its only variant, or the only revision that car can carry); otherwise it is read as
the selected car.

## Snapshots

Many ECUs store a **snapshot** with a code: the values of a few signals at the moment
the code was set. It is the only record of the car's state when the fault happened,
and clearing the code erases it.

`canair dtc` reads them by default. For every code a UDS ECU reports, it asks which
snapshot records the ECU holds (`19 03`), reads each one (`19 04 <DTC> <record>`) and
prints it under the code, each DID decoded with the profile's signals:

```text
  C182C-00  0x08  confirmedDTC
            → CCS session failed: …
            snapshot 01  DID 4710
              CCM_SNAP_EV_READY  0
              …
```

- **With `--all`**, snapshots are a final step, after every ECU's codes are read, so a
  slow snapshot read never delays them. On a terminal, `s` or `esc` skips the rest of
  that step; the codes and any snapshots already read are kept.
- **`--no-snapshots`** leaves the step out, for one ECU or a sweep.
- An ECU that refuses the record list (many do) is asked once and skipped; `--verbose`
  says why. KWP2000 ECUs keep their freeze frames in a different service, which canair
  does not read yet.
- **The DTC log keeps the raw replies** with each code and decodes them when you look,
  so `canair dtc log` shows a snapshot recorded before its DID was defined, decoded once
  it is. A snapshot identical to the previous scan's says "unchanged since".
- **`--clear` reads the snapshots first** and logs them with the clear, since clearing
  erases them; `canair dtc log` lists what a clear erased.
- **`--save`** also keeps the replies as captures (with `--label`, `--state`, `--notes`).
  Each is stored under its own request (`1904582C0001`), never as a live read of the
  DID, and its time is when it was read, not when the fault was set.

A snapshot DID is defined like any other PID, under its read form (`224710`), so its
signals decode a snapshot's data exactly as they would a live read. Some DIDs answer
only inside a snapshot: a live `22` read of them draws an NRC. Mark those so a bare-ECU
sweep never polls them and they never ship to the device:

```bash
canair pids add-pid CCM 224710
canair pids set-snapshot-only CCM 224710 --length 9
```

`--length` records the DID's data length in a record. The reply does not carry it, so
a record holding several DIDs cannot be split without it; a record of one DID is
self-describing, and a DID that also answers live takes its length from those replies
(unless its PID is `variable_length`, whose replies have no one length: declare it).
`canair dtc` prints these two commands for any snapshot DID it cannot decode, and
`canair coverage` audits a snapshot-only PID against its saved snapshots. To look at a
reply byte by byte, `canair bix -a 5904582C0008…` labels its DTC, status, record, count
and DID.

## Clearing codes

```bash
canair dtc BMS --clear          # asks to confirm
canair dtc BMS --clear --yes    # skip the prompt (scripting)
```

Clearing sends `ClearDiagnosticInformation` (`0x14`) and **mutates the ECU's
fault memory** — so it prompts for confirmation unless `--yes` is given. It clears
one ECU at a time: `--all` cannot be combined with `--clear`. By default it clears
every DTC: group `FFFFFF` on UDS, and `FF00` on KWP2000 (ISO 14230-3), then `FFFF`
(SSF 14230-3) if the ECU rejects `FF00`. A `--group` you give is sent as given, and
must fit the protocol's width (3 bytes on UDS, 2 on KWP2000). When logging is on,
canair reads the current codes first so the log records exactly what was cleared,
and an ECU that does not answer that read is not sent the clear. The ECU counts as
clean from then until it is next read, and the next scan lists the codes the clear
wiped separately from those that went away on their own; one that is back shows as
new. A refused or unanswered clear exits 1.

**The power state matters.** An ECU answers only in some power states. The profile
lists them in each ECU's `vehicle_states:`, and canair names them before it asks to
clear and again when the ECU does not answer. An ECU that is awake may still refuse
with `conditionsNotCorrect` (NRC `0x22`): many clear only with the ignition on and
the engine or drive system off. `--wake` rouses a sleeping ECU and holds a session
on it, as `--session` does.

!!! warning "Clearing hides symptoms, not causes"
    Erasing a DTC doesn't fix the underlying fault — if the condition persists
    the code will return. Clear codes to confirm a repair (does it come back?),
    not to make a warning light go away. See [Safety](safety.md).

## See also

- [`canair dtc` CLI reference](../reference/cli/dtc.md) — every flag.
- [ECU protocols & PID prefixes](ecu-protocols.md) — why UDS vs KWP2000 matters.
- [Vehicle states](vehicle-states.md) — the vehicle-state vocabulary.
- [Safety](safety.md) — what canair will and won't do to the car.
