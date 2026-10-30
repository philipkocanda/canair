# What Hyundai/Kia's UDS specs say, beyond functional addressing

This document collects facts from Hyundai/Kia's own diagnostic specifications that matter to canair
and to this car, written for triage like `IGPM_FINDINGS.md` and `FUNCTIONAL_ADDRESSING_FINDINGS.md`.
That second document already covers functional (`0x7DF`) requests, timing and the reprogramming
sequence; nothing here repeats it.

## Provenance and how much to trust it

- **ES95486-00 "UDS on CAN", revision 30 (2025-10-16).** The rulebook for classic-CAN UDS ECUs.
  Revision 30 is about ten years newer than the car. Its change history dates each rule, and only
  rules from revision 10 (2016-07) or earlier can be expected on this car; later ones are marked
  below.
- **ES95486-02 "UDS integrated specification", revision 23 (2025-10-16).** The successor for
  CAN-FD and Ethernet cars (the Ioniq 5 generation). Used here for routine ids and CAN-FD framing.
- **A supplier's diagnostic specification for a Hyundai/Kia wireless phone charger (WPC),
  2018-2025.** It applies ES95486 to one ECU, so it shows how an ECU spec fills in what ES95486
  leaves to "each ECU's specification". It describes a later WPC than this car's `95560G7000`.

All three are confidential Hyundai/Kia or supplier documents with no redistribution licence. They
live only in the private references repository. Cite facts from them; never commit them or copy
their text and tables.

## 1. Sessions

- **`10 05` is a programming session.** ISO 14229 reserves `0x05`; ES95486 defines it as "ECU
  Programming Mode", an alias of `10 02` kept for compatibility with Hyundai/Kia's older KWP2000
  spec, where the same mode is `10 85`. canair now refuses `10 05` on every protocol.
- `10 10` and `10 90` are aliases of the extended session, `10 81` of the default session.
- `10 D0` (quiescent-current diagnostic mode, `10`/`22` only) and `10 D1` (put the ECU to sleep)
  existed from revision 3 (2014) until revision 30 removed them. canair's OEM-band rule already
  refuses both.
- The positive response `50 xx` carries the ECU's P2 (≤ 50 ms) and P2* (≤ 5000 ms) limits. canair
  does not read them yet.

## 2. Which services work when

The spec's service table (section 3.1) sets three rules that explain NRCs seen on this car:

- **IOControl (`0x2F`) works only in the extended session**, without security access. Its
  engine-condition rule is left to each ECU.
- **RoutineControl (`0x31`), ECUReset, WriteDataByIdentifier and the flash services need "engine
  stop, IGN on".** On an EV, engine stop means "not READY"; the reference signal is VCU READY
  (`VCU_EvDrvRdySta` on the broadcast bus). In canair's vocabulary that is **ACC2**. An NRC 0x22
  at READY, or no answer at SLEEP, is the expected refusal. The BCM's `0x31` sweep ran in SLEEP,
  so it is worth repeating in ACC2 (BCM `research:`).
- CommunicationControl (`0x28`) needs engine stop. Hyundai/Kia's `28 01`/`28 02` stop normal and
  network-management traffic (unlike ISO), and the ECU resumes by itself within 500 ms of its 5 s
  session timeout.
- ReadScalingDataByIdentifier (`0x24`) and DynamicallyDefineDataIdentifier (`0x2C`) are optional
  and allowed in the default session, ReadDataByPeriodicIdentifier (`0x2A`) in the extended
  session. `0x24` would let an ECU describe a DID's scaling itself, and `0x2C`/`0x2A` could cut
  polling round trips. None is known to exist on this car; the service-ID sweep proposed in
  `plans/2026-10-29-service-id-enumeration.md` would answer that cheaply.

## 3. Identification DIDs

| DID | ES95486 meaning | Since | What it means for this profile |
|---|---|---|---|
| `F100` | Hyundai/Kia identification option; the WPC spec defines it as the CAN database version | - | Several `boot_sw` values are probably CAN database versions (RCAM's `CAN_15.11.01` reads as one) |
| `F186` | Active diagnostic session (ISO) | - | A cheap check that a session switch took effect |
| `F187` | Spare part number, ASCII, 10-15 bytes, mandatory | early | See the `hk_f1xx_minus_one` quirk |
| `F18B` | ECU manufacturing date, 4 bytes `YYYYMMDD` (hex digits) | rev 9 | `mfg_date` |
| `F18C` | ECU serial number, ASCII, mandatory | early | `serial` |
| `F18D` | Supported functional units (ISO), not a date | - | canair had it labelled as a date |
| `F1A0` | Hyundai/Kia's own SW version, ASCII, mandatory for batch reading | rev 10 | SKM's `diag_address: "190"` is probably a SW version |
| `F1A1` | Supplier code, 4 ASCII bytes | rev 15 (2018) | Probably absent on this car |
| `F1B0`-`F1BF` | Number of SW units, then each unit's version | undated; the WPC spec adds them in 2020 | Probably absent |
| `C0DE` | EOL coding number: the ECU's variant coding, 4 or 8 bytes | - | Now probed by `canair identity` on this profile |

`F1A2` (application version) and `F1A4` were assigned in 2023 and later, so they say nothing about
how this car uses them. The DID map also reserves `F010`-`F0FF` for Hyundai/Kia IOControl DIDs
(HVAC and VESS use `F011` up), and lists `0100`-`A5FF`, `A800`-`ACFF`, `B000`-`B1FF`,
`C000`-`C2FF` and `CF00`-`EFFF` for record (live-data) DIDs. The WPC spec's `BE01`-`BE03` lie
outside these, so treat the map as a guide, not a fence.

## 4. Record DIDs and their support maps

ES95486's example record DID (Annex C, table C-1) starts with four bytes that say which of the
following items are supported: byte 0 bit 7 is item 1, byte 3 bit 0 is item 32. canair found these
maps empirically on the IGPM, BCM, SKM and HVAC (`plans/2026-10-29-support-bitmap-coverage.md`);
the spec shows they are a Hyundai/Kia template, not a coincidence.

The WPC spec shows both granularities the plan found: `BE02` maps items to whole bytes, `BE03` to
single bits, with item 1 at bit 0 of the first data byte (the plan's bit order). A multi-bit field
takes the number of its first bit, which is the plan's "field continuation" tolerance. The same
spec's own numbering skips and repeats items across bytes, so real ECUs need not be exactly
positional either. Declaring a map per PID after review, as the plan does, remains right.

## 5. IOControl

- An ECU must answer `returnControlToECU` (`00`) positively for every DID it supports, even when
  the tester is not controlling it. That is what `scan iocontrol` relies on. This car's HVAC breaks
  the rule for `F012` (accepts `03`, rejects `00`), so a `00` sweep can miss a DID.
- Hyundai/Kia time values in a control state: `00`-`3F` mean 0-63 s, `80`-`F4` mean 0-1000 ms.
- The WPC spec adds a **supported check**: `2F F000 02` returns a 4-byte bitmap of the supported
  IOControl DIDs `F001`-`F020`, and `F020`/`F040` cover the next 32 each. No `F000` answered the
  HVAC sweep, so this generation probably lacks it (HVAC `research:`).

## 6. Routines

- Manufacturer routine ids are `0200`-`DFFF`; `F000`-`FEFF` is the supplier range, which is what
  `scan routines` sweeps by default. The BCM's known `12A1` and `DDDD` are in the manufacturer
  range.
- ISO's `FF00` (eraseMemory) and `FF01` (checkProgrammingDependencies) are reflash steps.
- ES95486-02 adds `0200` check memory, `0210`-`0220` firmware-bank and delta-patch routines,
  `0300`/`0301` over-the-air update readiness, `0400`-`0440` features on demand, and the gateway's
  `04F0` RXSWIN collection. canair now refuses starting or stopping the state-changing ones
  (`canlib/safety.py::BLOCKED_ROUTINES`); reading their results stays allowed.

## 7. Security access

Only facts about the protocol, for designing probes; canair never attempts an unlock.

- Requesting a seed (`27 01`) never counts as a failed attempt; only a wrong key does. A probe that
  sends `27 01` and no key cannot lock an ECU.
- From revision 18 (2021): three wrong keys lock the ECU for 180 s, and the counter survives a
  reset. Before that, two wrong keys locked it for 10 s, which is what this car's ECUs should do.
- An ECU that is already unlocked answers a seed request with an all-zero seed.

## 8. Negative responses and DTCs

- ES95486 uses the ISO 14229 condition codes `0x81`-`0x93` (vehicleSpeedTooHigh,
  shifterLeverNotInPark, voltageTooLow, ...); canair now names them. Its own `F0`-`F5` are gateway
  certificate failures (`F0`-`F2`), access denied by the central gateway (`F3`), a refused
  downgrade (`F4`) and a service not supported on the active connection (`F5`). ISO leaves
  `F0`-`FE` to each make, so canair prints only the range name for them.
- DTC failure-type bytes follow the SAE J2012 / ISO 15031-6 table unchanged. `B1285-77` is
  therefore "commanded position not reachable", which matches the defog actuator description.
  canair now carries the whole standard table, and `profile.yaml` no longer needs a `0x77` entry.

## 9. CAN-FD ECUs (ES95486-02, for the Ioniq 5 profile)

- Responses are always sent in 8-byte frames with the classic single- and first-frame layout,
  so a tester that only speaks classic ISO-TP still works. canair's classic frame-count arithmetic
  therefore holds for the replies.
- Requests are padded with `0x55`, responses with `0xAA`, on CAN and CAN-FD alike.
- 29-bit physical ids are `0x17FF{target}{source}`, with the tester at source address `0x00`.
