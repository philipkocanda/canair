# Reading & interpreting DTCs

A **Diagnostic Trouble Code (DTC)** is a fault that an electronic control unit (ECU) has stored in
its memory. It is the machine-readable form of a "check engine"-style light. `canair dtc` reads
DTCs off the bus, decodes their structure, adds any known meaning, and logs each scan so you can
see what changed over time. This page explains what a code *is*, how canair decodes it, and how to
read the output.

For the full flag list, see the [`canair dtc` CLI reference](../reference/cli/dtc.md).

## Quick start

```bash
canair dtc BMS          # read stored DTCs from one ECU
canair dtc --all        # sweep every ECU in the profile
canair dtc BMS --clear  # erase fault memory (asks to confirm)
```

`canair dtc` is **read-only** unless you pass `--clear`.

## Two protocols, two services

Like reads (see [ECU protocols & PID prefixes](ecu-protocols.md)), DTC access is
**protocol-aware**. canair selects Unified Diagnostic Services (UDS) or Keyword Protocol 2000
(KWP2000) for each ECU from its `id_protocol`:

| Protocol | Read service | Clear service | DTC record |
|---|---|---|---|
| **UDS** (ISO 14229) | `0x19` sub-function `0x02` — *reportDTCByStatusMask* | `0x14` — *ClearDiagnosticInformation* (3-byte group) | 4 bytes: 3-byte DTC + 1-byte status |
| **KWP2000** (ISO 14230) | `0x18` — *readDTCByStatus* | `0x14` (2-byte group) | 3 bytes: 2-byte DTC + 1-byte status |

You do not choose the service. canair reads `id_protocol` and sends the right one, as it does for
a normal read. To override the auto-selection, force a protocol with `--protocol uds|kwp`.

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
- **Generic vs manufacturer-specific** — from the SAE J2012 second-character convention. For `P`
  codes, digits `0`/`2` are generic (ISO/SAE-defined) and `1`/`3` are manufacturer-specific. For
  `C`/`B`/`U`, digit `0` is generic and `1`/`2` are manufacturer-specific.
- **Failure-type byte (FTB)** — the `-yy` suffix (UDS only, from ISO 14229-1 Annex D.3 / SAE
  J2012-DA). It refines *how* the circuit failed. For example, `-11` = short to ground, `-13` =
  open circuit, `-1C` = voltage out of range. canair knows the whole standard table. Only the
  manufacturer-specific category (`-F0`…`-FF`) needs the profile. KWP2000 codes have no FTB, so
  they print without a suffix (`P0420`).

canair decodes the standardized structure of *every* code, even one it has never seen. It cannot
invent the **manufacturer-specific text**. What Hyundai means by `B2915` is not encoded in the
code itself. canair adds that meaning from curated sources (below). It never guesses.

### When a code is not SAE J2012

The `Lxxxx-yy` form is the SAE J2012 form. ISO 14229-1 lets an ECU use another format and report
which one (the DTCFormatIdentifier, Table D.14). When a UDS ECU reports codes, `canair dtc` asks
it for the format with `19 01`. This is one more request, and canair sends it only to an ECU that
has codes:

- `00` and `04` are SAE J2012-DA formats. The codes print as above.
- `01` (the format of ISO 14229-1 itself, decoded as the maker decides), `02` (SAE J1939-73) and
  `03` (ISO 11992-4) have no standard text form. canair prints those codes as six hex digits
  (`A91900`) and says which format the ECU reported. It does not look them up as SAE J2012 codes.
  `--json` carries the format as `dtc_format`.
- An ECU that does not answer `19 01` (the amplifier of the Ioniq refuses it) is read as SAE
  J2012. `dtc_format` is then `null`.

A record whose code is `000000` is not a fault, so canair skips it.

## The status byte

Each code carries a one-byte **statusOfDTC**. For UDS, canair decodes the eight ISO 14229-1 bits
by name:

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

The most useful distinction is **`confirmedDTC`** (a real stored fault) versus **`pendingDTC`** (an
intermittent fault that the ECU saw but has not confirmed). The status byte of KWP2000 uses a
different, ECU-specific layout. So canair reports its raw hex value without named bits.

KWP2000 documents disagree on the `0x18` request itself. canair tries `18 00 FF 00` first (the
form that the ECUs of the Ioniq answer). If the ECU rejects it, canair tries `18 02 FF 00` and
then `18 00 FF FF`. When only an alternate form is answered, the output carries a `Note:` line
(and a `note` field in `--json`). The all-ECU scan shows `(via 1802FF00)`. `18 02` in particular
may select 2-byte hex DTCs rather than SAE J2012 ones. In that case, the printed `P`/`C`/`B`/`U`
letters are not meaningful.

`canair dtc BMS --supported` sends a different request, `18 03 FF 00`. It lists every DTC that a
KWP2000 ECU can report, whatever its status. That is a catalogue, not a fault list (a healthy ECU
returns a long one). So it is never a fallback. It runs only on request and prints raw hex codes
with their raw status byte. canair never writes it to the DTC log.

### Status masks

The UDS read takes a **statusOfDTC mask**. The ECU returns only codes that match the mask.
`--mask FF` (the default) asks for everything. `--mask 08` returns only confirmed codes. Some ECUs
reject `FF` with a negative response code (NRC), `requestOutOfRange` (`0x31`). When that happens,
canair automatically retries with `0x08` (confirmed), the most widely supported mask.

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

Each row is `DTC  status  flags`. The `→` line is the decoded meaning. When the text of a code is
known (from the profile or the small bundled generic table), canair shows it. Otherwise you get
the structural interpretation (category · generic/manufacturer-specific · failure type).

A **full sweep** (`canair dtc --all`) prints a status column for each ECU (clean / N DTC(s) / not
supported / no response). It retries any silent ECU once with a wake and a longer timeout. It
then lists the codes of every faulty ECU. Add `--json` for a machine-readable result.

## Where meanings come from

canair layers the meaning of a code from these sources, most specific first:

1. **The `dtcs:` section of the reporting ECU** (`ecus/<name>.yaml`). This is the authoritative,
    curated code text for *this* car. canair looks a code up on the ECU that reported it and never
    borrows from another ECU. Two units can give one code different meanings. For example, `C1616`
    is "CAN communication line malfunction" on a blind-spot radar and "CAN bus off" on the
    steering.
2. **The profile-wide `dtcs:` section** (`profile.yaml`). It holds codes that mean the same on
    every unit that reports them. A manufacturer often keeps "bus off" or a module time-out
    consistent across its ECUs. The profile states such a code once instead of on each ECU. If
    an ECU has its own entry for the code, that entry wins.
3. **A small bundled generic (ISO/SAE) table.** It holds a handful of well-known codes (`P0300`,
    `P0420`, `U0100`, …).

canair names the failure-type byte (the `-XX` suffix) from the standard SAE J2012 / ISO 14229-1
table. Manufacturers such as Hyundai/Kia adopt this table unchanged. canair shows a
manufacturer-specific value (`0xF0`–`0xFF`) by its number.

Manufacturer meanings are **never invented**. An unknown manufacturer code shows its structure
only. To teach canair the meaning of a code, add it to the `dtcs:` section of the ECU. Add it to
the profile-wide section if it means the same on every unit. This is authored data. Edit it
through the profile and do not guess.

When every code in a section comes from the same place, state `source`, `source_links` and
`verified` once, directly under `dtcs:`. A code that sets one of them itself keeps its own value:

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

canair tells keys apart by shape. A code key is a letter and four hex digits (`C1101`), without
the `-FTB` suffix. The profile-wide `dtcs:` in `profile.yaml` takes the same shape.

## The DTC log

canair records every scan in the DTC log (disable with `--no-log`). It compares each scan with the
previous scan of the same scope and reports **what changed**. These are the codes that
**appeared** and the codes that **cleared** since last time (the car self-healed or a code aged
out). Tag a scan for context:

```bash
canair dtc --all --state ready --label "before fix"
canair dtc BMS --label "after replacing sensor"
```

- `--label` annotates the log entry.
- `--state` records the vehicle power state(s) during the scan (comma-separated, from the
  [`vehicle_states.yaml`](vehicle-states.md) vocabulary, e.g. `ready` or `sleep, plugged`).

Review the log **without touching the device** (handy when the car is off) with `canair dtc log`:

```bash
canair dtc log         # the last logged full sweep, and every ECU read on its own since
canair dtc log BMS     # the last logged scan for one ECU, offline
```

A full sweep is only as current as its date. When you read or clear one ECU after the sweep
(`canair dtc IGPM`), the bare `canair dtc log` lists the latest reading of that ECU under **Read
since this sweep**. So the newer codes are not hidden behind the older sweep.

A bare `canair dtc` says how many codes the log holds and how many are still open.
`canair ecu <ECU>` lists the **open** codes of that ECU in red (the codes that its most recent
logged scan read). `canair config` shows the log totals of each profile.

**Where it lives.** The log is the only record of the fault history of your car. So canair keeps
it in user state, `~/.config/canair/dtc_log/<profile>.yaml`, and not in the profile directory. A
reinstall, a re-clone or a `git clean` deletes the profile directory. An older canair left a
`dtc_log.yaml` in the profile. canair moves that file to the new location automatically. This
happens the first time that the log of that profile is used. `canair config` and `canair update`
(before it reinstalls) move the logs of every profile at once. canair converts a log written by an
earlier version to the current format the same way.

**It records readings, not names.** Each entry names an ECU only by its response address, as a
capture does. The name you see comes from the profile, so renaming an ECU changes nothing in the
log. canair stores only scans and clears. It works out which codes cleared on their own from
consecutive scans when you look.

**It belongs to one car.** Each entry records the [variant](variants.md) that it was read from and
the revision of each multi-revision ECU that it covers. A code read from another variant, or from
another revision of the ECU (a swapped module starts with its own fault memory), says nothing
about this car. So canair leaves it out of the comparison, the open codes and the totals. With
several variants and none selected, pick one with `--variant`.

A full sweep also records which ECUs it could not read. An ECU that was asleep is neither
"cleared" nor "clean". An entry logged before the log carried these tags gets them where the
profile settles them on its own (its only variant, or the only revision that car can carry).
Otherwise canair reads the entry as the selected car.

## Snapshots

Many ECUs store a **snapshot** with a code: the values of a few signals at the moment the code was
set. It is the only record of the state of the car when the fault happened. Clearing the code
erases it.

`canair dtc` reads snapshots by default. For every code that a UDS ECU reports, it asks which
snapshot records the ECU holds (`19 03`) and reads each one (`19 04 <DTC> <record>`). It prints
each snapshot under the code, with each data identifier (DID) decoded with the signals of the
profile:

```text
  C182C-00  0x08  confirmedDTC
            → CCS session failed: …
            snapshot 01  DID 4710
              CCM_SNAP_EV_READY  0
              …
```

- **With `--all`**, snapshots are a final step, after canair has read the codes of every ECU. So a
  slow snapshot read never delays the codes. On a terminal, `s` or `esc` skips the rest of that
  step. canair keeps the codes and any snapshots already read.
- **`--no-snapshots`** leaves the step out, for one ECU or a sweep.
- canair asks an ECU that refuses the record list (many do) once and then skips it. `--verbose`
  says why. KWP2000 ECUs keep their freeze frames in a different service, which canair does not
  read yet.
- **The DTC log keeps the raw replies** with each code and decodes them when you look. So
  `canair dtc log` shows a snapshot that was recorded before its DID was defined, decoded once the
  DID is defined. A snapshot identical to the snapshot of the previous scan says "unchanged
  since".
- **`--clear` reads the snapshots first** and logs them with the clear, because clearing erases
  them. `canair dtc log` lists what a clear erased.
- **`--save`** also keeps the replies as captures (with `--label`, `--state`, `--notes`). canair
  stores each under its own request (`1904582C0001`), never as a live read of the DID. Its time
  is when canair read it, not when the fault was set.

You define a snapshot DID like any other PID, under its read form (`224710`). Its signals then
decode the data of a snapshot exactly as they decode a live read. Some DIDs answer only inside a
snapshot, and a live `22` read of them draws an NRC. Mark those DIDs, so that a bare-ECU sweep
never polls them and they never ship to the device:

```bash
canair pids add-pid CCM 224710
canair pids set-snapshot-only CCM 224710 --length 9
```

`--length` records the data length of the DID in a record. The reply does not carry it. So a
record that holds several DIDs cannot be split without it. A record of one DID is
self-describing. A DID that also answers live takes its length from those replies, unless its PID
is `variable_length`. The replies of such a PID have no one length, so declare it.

`canair dtc` prints these two commands for any snapshot DID that it cannot decode.
`canair coverage` audits a snapshot-only PID against its saved snapshots. To look at a reply byte
by byte, `canair bix -a 5904582C0008…` labels its DTC, status, record, count and DID.

## Clearing codes

```bash
canair dtc BMS --clear          # asks to confirm
canair dtc BMS --clear --yes    # skip the prompt (scripting)
```

Clearing sends `ClearDiagnosticInformation` (`0x14`) and **changes the fault memory of the ECU**.
So it prompts for confirmation unless you pass `--yes`.

- It clears one ECU at a time. `--all` cannot be combined with `--clear`.
- By default, it clears every DTC: group `FFFFFF` on UDS, and `FF00` on KWP2000 (ISO 14230-3). If
  the ECU rejects `FF00`, canair sends `FFFF` (SSF 14230-3).
- canair sends a `--group` that you give as given. It must fit the width of the protocol (3 bytes
  on UDS, 2 on KWP2000).
- When logging is on, canair reads the current codes first, so the log records exactly what was
  cleared. canair does not send the clear to an ECU that does not answer that read.
- The ECU counts as clean from then until it is next read. The next scan lists the codes that the
  clear wiped separately from those that went away on their own. A code that is back shows as new.
- A refused or unanswered clear exits 1.

**The power state matters.** An ECU answers only in some power states. The profile lists them in
the `vehicle_states:` of each ECU. canair names them before it asks to clear, and again when the
ECU does not answer. An ECU that is awake may still refuse with `conditionsNotCorrect` (NRC
`0x22`). Many ECUs clear only with the ignition on and the engine or drive system off. `--wake`
rouses a sleeping ECU and holds a session on it, as `--session` does.

!!! warning "Clearing hides symptoms, not causes"
    Erasing a DTC does not fix the underlying fault. If the condition persists, the code returns.
    Clear codes to confirm a repair (does it come back?), not to make a warning light go away.
    See [Safety](safety.md).

## See also

- [`canair dtc` CLI reference](../reference/cli/dtc.md) — every flag.
- [ECU protocols & PID prefixes](ecu-protocols.md) — why UDS vs KWP2000 matters.
- [Vehicle states](vehicle-states.md) — the vehicle-state vocabulary.
- [Safety](safety.md) — what canair will and won't do to the car.
