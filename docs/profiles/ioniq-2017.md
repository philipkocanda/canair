# The bundled Ioniq 2017 profile

The repo ships one fully-developed profile, `ioniq-2017`, as both the **default**
and a **worked reference example** for the [bring-your-own-car](../bring-your-own-car/overview.md)
journey. It turns canair into a ready-to-use diagnostics toolkit for the
**2017 Hyundai Ioniq Electric (28 kWh, `AE` platform)** — read live battery,
motor, charging, climate, and body data over WiFi with no dealer tools.

It's also the best thing to *study*: it shows what a mature profile looks like
(verified parameters with sources, `research:` backlogs, IOControl actuators,
scan logs), which is exactly the shape you're building toward for your own car.

## At a glance

| | |
|---|---|
| Vehicle | 2017 Hyundai Ioniq Electric, 28 kWh, `AE` platform (NL market) |
| ECUs mapped | **30** (all registered with identity) |
| PIDs | **97** active/draft |
| Parameters | **336** decoded (**215 verified**, 121 unverified/candidate) |
| IOControl DIDs | **107** actuators (UDS `0x2F`) |
| Research backlog | **81** open reverse-engineering leads |

> These numbers drift as the profile grows. The live figures come from
> `canair --profile ioniq-2017 validate pids --stats`.

## What it decodes

- **Battery (BMS)** — state of charge, pack voltage/current/power, **all 96
  individual cell voltages**, module temperatures, and state of health.
- **Motor & drive (MCU / VCU)** — gear, motor torque and RPM, inverter/motor
  temperatures, and vehicle speed.
- **Chassis (ESC / EPS)** — vehicle speed and **individual wheel speeds**
  (FL/FR/RL/RR from the ESC), plus a strong-but-unverified steering-angle
  candidate on the EPS.
- **Charging (OBC / CCM)** — AC and DC (CCS) charging state, charge-port lock;
  the CCM (Charge Control Module, PLC for DC fast-charging) is identified on the
  bus.
- **Climate & body (HVAC / BCM / IGPM)** — HVAC/climate state, tyre
  pressures/temperatures (TPMS), and body controls (locks, trunk, lights,
  indicators).

## IOControl actuators

The profile defines **107 IOControl DIDs** (UDS `0x2F`) for hardware you can
safely toggle from the CLI — lights, horn, locks, charge-cable lock, mirrors,
wipers — all of which auto-release when the diagnostic session ends. The IGPM
actuators work from deep sleep with `--wake`.

```bash
canair --profile ioniq-2017 io IGPM          # interactive actuator TUI
canair --profile ioniq-2017 io IGPM --did BC01  # e.g. low beam (held until Ctrl+C)
```

> Actuation is confirm-first and reversible, but it *does* drive real hardware.
> See [Safety](../concepts/safety.md).

## ECUs on the bus

30 ECUs are registered. The most decoded:

| ECU | TX | Role |
|-----|----|------|
| BMS | 0x7E4 | Battery Management System |
| MCU | 0x7E3 | Motor Control Unit (inverter) |
| VCU | 0x7E2 | Vehicle Control Unit |
| OBC | 0x7E5 | On-Board Charger + LDC/DC-DC |
| ESC | 0x7D1 | Electronic Stability Control (wheel speeds) |
| EPS | 0x7D4 | Electric Power Steering |
| IGPM | 0x770 | Integrated Gateway & Power Module (body, IOControl) |
| BCM | 0x7A0 | Body Control Module / TPMS |
| HVAC | 0x7B3 | Climate control |
| CLU | 0x7C6 | Cluster / instrument panel (odometer) |

…plus AAF, AMP, AVN, BSD-L/R, CCM, GSA, MFC, PSM, RCAM, SCC, SKM, SRS, SWRC-L/R,
VESS, WPC, and a few still-`Unknown-*` modules. List them all with:

```bash
canair --profile ioniq-2017 ecu               # every ECU + PID/param/verified counts
canair --profile ioniq-2017 ecu BMS           # one ECU's identity + per-PID breakdown
```

## Exploring the profile

Everything the profile knows is queryable through canair — no need to read the
YAML by hand:

```bash
canair --profile ioniq-2017 query BMS:2101         # read + decode the battery PID (live car)
canair --profile ioniq-2017 decode BMS 2101 --stats  # value ranges from captures (offline)
canair --profile ioniq-2017 coverage               # what's still undecoded
canair --profile ioniq-2017 research --summary     # the open RE backlog
```

The source of truth is `profiles/ioniq-2017/ecus/` — one file per ECU, each
carrying that ECU's identity, scan log, DTC meanings, PIDs, parameters, and
research leads. See [Profiles](../concepts/profiles.md) for the bundle layout.

## A note on the hardware quirks

Ioniq ECUs drop to a low-power state when idle, so the **first** diagnostic
request after a pause is slow and can even time out — retry once before
concluding a PID/ECU is dead. The OBD-II port is also gateway-isolated: ECUs
answer request/response, but there's almost no unsolicited broadcast traffic to
sniff passively. These are captured in `profiles/ioniq-2017/profile.yaml`.

## Sources & attribution

This profile stands on the shoulders of the wider Hyundai/Kia EV
reverse-engineering community. Many parameters were seeded, cross-checked, or
corrected against the following public sources — thank you to their authors.
(Cross-vehicle sheets often differ by a byte or two, so everything here was
re-validated against the actual car — see [Define & verify](../bring-your-own-car/07-define-and-verify.md).)

- [uhi22/Ioniq28Investigations](https://github.com/uhi22/Ioniq28Investigations)
  — detailed investigations of the same 28 kWh Ioniq Electric.
- [Hyundai Ioniq EV PID spreadsheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  — community PID reference.
- [Hyundai Ioniq EV PID spreadsheet (2)](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI/edit)
  — another community PID reference.
- [JejuSoul/OBD-PIDs-for-HKMC-EVs — Ioniq EV 28 kWh VMCU CSV](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs/blob/master/Ioniq%20EV%20-%2028kWh/extendedpids/Hyundai_Ioniq_EV_VMCU_data.csv)
  — VMCU/VCU extended-PID definitions for this exact car.
- [JejuSoul/OBD-PIDs-for-HKMC-EVs — Kia Soul EV 27 kWh](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs/tree/master/Soul%20EV%20-%2027kWh/extendedpids)
  — close cousin (note the ~1-byte offset vs the Ioniq).
- [projectgus/kona-ev-dbc — Diagnostics.md](https://raw.githubusercontent.com/projectgus/kona-ev-dbc/main/Diagnostics.md)
  — Kona EV diagnostics, useful cross-reference for shared HKMC modules.
- [abstractionnl/wican-esphome — wican-ioniq.yaml](https://github.com/abstractionnl/wican-esphome/blob/main/wican-ioniq.yaml)
  — an ESPHome/WiCAN Ioniq PID config.

If your work is referenced here and you'd like different attribution (or you
spot a decode that traces back to a source not listed), please
[open a PR or issue](https://github.com/philipkocanda/canair) — see
[Contributing](../contributing/index.md).
