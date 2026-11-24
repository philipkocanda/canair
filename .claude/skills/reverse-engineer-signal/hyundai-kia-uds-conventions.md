# Reference: UDS decoding conventions (Hyundai/Kia example)

Marque-specific reference for the `reverse-engineer-signal` skill. Load this when working the
**bundled Ioniq** (or another Hyundai/Kia) and you need the DID conventions. **These are
marque-specific, not universal.** They show the kind of manufacturer pattern worth discovering
for *your* car. DID range semantics, paging and identity-DID offsets vary by OEM, so for another
car expect a different scheme and re-derive it.

The generic protocol split (UDS `0x22` vs KWP2000 `0x21`, tied to each ECU's `id_protocol`, and
why the identifier widths differ) is in `docs/concepts/ecu-protocols.md`. On the bundled Ioniq,
BMS/MCU/VCU/OBC are KWP2000 and page by `21xx`: the `xx` is a page number, and each page returns
a different block (`2101`, `2102`, `21F2`). CLU/IGPM/BCM/ESC and other body ECUs are UDS (`22xxxx`).

## Where the DIDs are

Hyundai/Kia's own spec (ES95486, summarized in
`profiles/hyundai-ioniq-ae-ev/research/HKMC_UDS_SPEC_FINDINGS.md`) assigns the UDS DID space
roughly as below. It is a guide, not a fence: a supplier's WPC uses `BExx`, inside a range the map
calls legislative.

| DIDs | Holds |
|---|---|
| `0100`-`A5FF`, `A800`-`ACFF`, `B000`-`B1FF`, `C000`-`C2FF`, `CF00`-`EFFF` | record (live-data) DIDs |
| `F010`-`F0FF` | IOControl DIDs (HVAC, VESS: `F011` up) |
| `F100`-`F17F`, `F1A0`-`F1EF` | Hyundai/Kia identification options (`F100` CAN DB version, `F1A0` SW version) |
| `F180`-`F19F` | ISO identification (`F187` part number, `F18B` manufacture date, `F18C` serial) |
| `C0DE` | EOL coding number (the ECU's variant coding) |

Never write (`2E`) any of them; canair's command guard refuses it anyway.

## Record DIDs come in families

A body ECU keeps its live data in a few DIDs that share a high byte, numbered up from `xx01`:
IGPM `BC01`-`BC08`, BCM `B001`-`B00F`, SKM `B002`-`B00B`. Newer ECUs fix the slots (`xx01` user
options, `xx02` byte-wide I/O, `xx03` bit-wide I/O) and start each record with a 4-byte support
map (see Support maps below). Three consequences for scanning:

- **Sweep every family, not a fixed range.** A sweep of `B001`-`B010` alone misses an ECU whose
  family is `B4xx` or `BExx`. `canair scan range <ECU>` with no `--range` already sweeps each
  family the profile shows (whole, since real DIDs also sit at the top: IGPM `BCFD`/`BCFF`).
- **Read an ECU's IOControl DIDs with `22`.** On this car the IGPM's `BCxx` and the BCM's `B0xx`
  are both read and actuated. HVAC's `F0xx` is the exception: `22` answers NRC there.
- **An ECU with IOControl hits but no record DIDs is a gap**, not a dead end. `canair research
  --gaps` lists every such family with the scan to run; after it, try the neighbouring families.
- **An NRC-only IOControl "hit" is not evidence.** The DDM answers NRC 0x22 to every DID of
  `B000`-`B524`; only its positive `B1xx` releases say where its actuators are.

## Support maps on this car

Hyundai/Kia records start with a 4-byte "supported" bitmap, and `canair investigate <ECU>
--support-map` detects it (method, verdicts and declaring: `docs/concepts/analysis-techniques.md`).
What a map bit marks differs by DID:

| Layout | Where | What a map bit marks |
|---|---|---|
| per bit (a bitmapped DID) | IGPM `22BC03`-`07`, BCM `22B004`/`07` | one data bit; a multi-bit field is marked on its first bit only (`field continuation`) |
| per byte | HVAC `220100` | one data byte; unmarked bytes read `FF` |
| per field | BMS `2101`-`2104`, VCU `2101`, HVAC `220102`, BCM `22C00B` | one parameter of any width, in order |

Rules of thumb:

- **An unmarked parameter is filler on this unit, not a signal that never moved.** The Kona's
  BMS `220101` leaves a fifth module temperature and the two battery-fan parameters unmarked:
  it has a liquid-cooled pack, the Ioniq an air-cooled one with a fan.
- **The maps are fixed per unit**, never per state, and shared between models (Kona and Ioniq
  carry identical HVAC and IGPM maps).
- **Not every supplier counts the same way.** The MCU pages fit only least significant bit first
  (`LSB first` in the report). VCU `2102`'s `F8 F8` and BCM `22B005`'s light-stalk bits fit no
  reading: check before you declare, and never assume a map from the family alone.

## Identity DIDs, and the reply that looks one lower

Hyundai/Kia store the part number at `F187`, the manufacture date at `F18B` (4 bytes `YYYYMMDD`)
and the HW number at `F191`, as ISO assigns them; a tester that expects the part number at `F188`
sees them as "one less". `canair identity` queries both.

Some of this car's ECUs (DDM, ADM; PSM answers a bare `62`) answer an F1xx DID they do not
support by repeating their previous reply. When the previous DID was the one just below,
the repeat looks like a "one lower" answer (`22F101` → `62F100…`). It is not: the bytes are
identical to the previous reply. Echo validation refuses it as stale.
