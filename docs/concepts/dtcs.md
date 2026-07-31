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
  ground, `-13` = open circuit, `-1C` = voltage out of range. KWP2000 codes have
  no FTB, so they print without a suffix (`P0420`).

canair decodes the standardized structure of *every* code, even one it has never
seen. What it can't invent is the **manufacturer-specific text** — what Hyundai
means by `B2915` isn't encoded in the code itself. That meaning is layered on
from curated sources (below), never guessed.

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

canair layers meaning from three sources, most specific first:

1. **The profile's per-ECU `dtcs:` sections** (`ecus/<name>.yaml`) — the
   authoritative, curated code text for *this* car.
2. **The profile-wide `failure_types:`** (`profile.yaml`) — FTB descriptions
   beyond canair's built-in subset.
3. **A small bundled generic (ISO/SAE) table** — a handful of well-known codes
   (`P0300`, `P0420`, `U0100`, …).

Manufacturer meanings are **never invented** — an unknown manufacturer code shows
its structure only. To teach canair a code's meaning, add it to the ECU's `dtcs:`
section (this is authored data — edit it through the profile, not by guessing).

## The scan history log

Every scan is recorded to the profile's `dtc_log.yaml` (disable with `--no-log`)
and compared against the previous same-scope scan, so canair reports **what
changed** — codes that **appeared** and codes that **cleared** (the car
self-healed or a code aged out) since last time. Tag a scan for context:

```bash
canair dtc --all --state ready --label "before fix"
canair dtc BMS --label "after replacing sensor"
```

- `--label` annotates the log entry.
- `--state` records the vehicle power state(s) during the scan (comma-separated,
  from the [`vehicle_states.yaml`](captures-and-states.md) vocabulary, e.g.
  `ready` or `sleep, plugged`).

Review history **without touching the device** (handy when the car is off) with
`--history`:

```bash
canair dtc --history        # the last logged full sweep, offline
canair dtc BMS --history    # the last logged scan for one ECU, offline
```

## Clearing codes

```bash
canair dtc BMS --clear          # asks to confirm
canair dtc BMS --clear --yes    # skip the prompt (scripting)
```

Clearing sends `ClearDiagnosticInformation` (`0x14`) and **mutates the ECU's
fault memory** — so it prompts for confirmation unless `--yes` is given. By
default it clears all groups (`--group FFFFFF` for UDS, `FFFF` for KWP2000). When
logging is on, canair reads the current codes first so the log records exactly
what was cleared, writes a `manual` clear event, and resets that ECU's scan
baseline to clean.

!!! warning "Clearing hides symptoms, not causes"
    Erasing a DTC doesn't fix the underlying fault — if the condition persists
    the code will return. Clear codes to confirm a repair (does it come back?),
    not to make a warning light go away. See [Safety](safety.md).

## See also

- [`canair dtc` CLI reference](../reference/cli/dtc.md) — every flag.
- [ECU protocols & PID prefixes](ecu-protocols.md) — why UDS vs KWP2000 matters.
- [Captures & states](captures-and-states.md) — the vehicle-state vocabulary.
- [Safety](safety.md) — what canair will and won't do to the car.
