# The bundled Hyundai Ioniq Electric (AE) profile

The repo's most developed profile, `hyundai-ioniq-ae-ev`, is the **worked reference example** for
the [bring-your-own-car](../bring-your-own-car/overview.md) journey. See
[Bundled profiles](index.md) for the full list of profiles that ship. The profile makes canair a
ready-to-use diagnostics toolkit for the **2017 Hyundai Ioniq Electric (28 kWh, `AE` platform)**.
You can read live battery, motor, charging, climate and body data over WiFi with no dealer tools.

Explore its electronic control units (ECUs), parameter identifiers (PIDs) and signals in the
[profile explorer](explorer.md). You can also browse it on GitHub:
[`profiles/hyundai-ioniq-ae-ev/`](https://github.com/philipkocanda/canair/tree/main/profiles/hyundai-ioniq-ae-ev).

Set it as your default profile once. Then every `canair` command below works without `--profile`:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # or 38kwh — see below
```

## Two variants: the 28 kWh car and the 38 kWh facelift

| Variant | Built | State |
|---|---|---|
| `28kwh` | 2016 – 2019-05 | **Verified** on the author's 2017 car: everything below |
| `38kwh` | 2019-06 – 2022 | **Seed**: nothing verified on a real car yet |

The battery management of the facelift follows the Kona/e-Niro family. So its battery management
system (BMS) is a different [revision](../concepts/variants.md#ecu-revisions) of the ECU, named
`UDS`. This revision answers the Unified Diagnostic Services (UDS) pages `220101`/`220105`. The
`KWP` BMS of the 28 kWh car uses Keyword Protocol 2000 (KWP2000) and answers `2101`–`2105`.

The `UDS` pages come from the
[Kona profile](https://github.com/philipkocanda/canair/tree/main/profiles/hyundai-kona-os-ev).
They are all **unverified**. So the default (verified-only) AutoPID build ships none of them.
Every other ECU (the body, chassis and climate modules) is shared. canair assumes that these ECUs
behave the same on a facelift, but this is also unverified. With `--variant 38kwh`, canair reads
only the `uds` BMS pages.

**Own a 38 kWh Ioniq?** One recorded session confirms or refutes most of this:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 38kwh
canair discover --save --label "38 kWh discovery"
canair identity BMS                       # names the BMS revision it matches
canair monitor BMS --save --label "38 kWh BMS"
```

Then [share it](../bring-your-own-car/08-share.md). The facelift becomes a profile of its own if
the discovery shows any of these:

- other ECU addresses
- a gateway in front of the on-board diagnostics (OBD-II) port
- body ECUs that do not answer the 28 kWh PIDs

This profile is also the best one to *study*. It shows what a mature profile looks like: verified
signals with sources, `research:` backlogs, IOControl actuators and scan logs. That is the shape
you are building toward for your own car.

!!! warning "Your car may differ"
    This profile was built and verified against **one vehicle**: a 2017 28 kWh Ioniq Electric from
    the Netherlands (NL) market. PIDs, byte layouts and available signals can differ across
    **model years and markets**. They can even differ between two cars of the same year that run
    different ECU firmware. Treat the profile as a strong starting point, not a guarantee. If a
    value looks wrong on your car, verify it again against your own captures (see
    [Define & verify](../bring-your-own-car/07-define-and-verify.md)).
    Check the part number and firmware of an ECU with `canair identity <ECU>`.

## At a glance

| | |
|---|---|
| Vehicle | 2017 Hyundai Ioniq Electric, 28 kWh, `AE` platform (NL market) |
{{ profile_stats_rows("hyundai-ioniq-ae-ev") }}

> The build computes these numbers from the `ecus/` files of the profile whenever it builds this
> site. It counts them the same way as `canair validate pids --stats` and `canair research`.

## What it decodes

- **Battery (BMS)** — state of charge, pack voltage, current and power, **all 96 individual cell
  voltages**, module temperatures and state of health.
- **Motor & drive (MCU / VCU)** — gear, motor RPM and torque, high-voltage (HV) and 12 V voltages,
  coolant temperature and vehicle speed.
- **Chassis (ESC / EPS)** — vehicle speed, **individual wheel speeds** (front left, front right,
  rear left and rear right: FL/FR/RL/RR, from the ESC) and the steering angle. canair reads the
  steering angle independently on both the ESC and the EPS.
- **Charging (BMS / OBC / IGPM)** — alternating current (AC) and direct current (DC) charging state
  (Combined Charging System (CCS) for DC), AC input and pilot values, and the charge-port lock. The
  charge control module (CCM) handles power line communication (PLC) for DC fast charging. canair
  identifies the CCM on the bus, but it exposes no live data.
- **Climate & body (HVAC / BCM / IGPM)** — climate state (heating, ventilation and air
  conditioning (HVAC)), tyre pressures and temperatures (tire pressure monitoring system (TPMS)),
  and body controls (locks, trunk, lights, indicators).

## IOControl actuators

Never send `0x30` to the OBC. On the OBC, `0x30` is the factory sensor calibration, and it drives
the power stage. See the OBC research entries in `canair research`.

The profile defines IOControl data identifiers (DIDs) (UDS `0x2F`) for hardware that you can
toggle from the CLI: lights, horn, locks, charge-cable lock, mirrors, wipers and seat motors. The
[At a glance](#at-a-glance) table gives the count.
The IGPM actuators release when the diagnostic session ends. They work from deep sleep with
`--wake`. Other actuators differ. For example, the power-seat motors run until told to stop. Read
the notes of an actuator before you drive it.

The KWP2000 ECUs (BMS, VCU, MCU, OBC, AAF, EWP) have no actuators defined yet. Nobody knows which
`0x30` request hands each output back. `canair io` refuses to switch a `0x30` actuator on without
such a request.

```bash
canair io IGPM          # interactive actuator TUI
canair io IGPM --did BC01  # e.g. low beam (held until Ctrl+C)
canair monitor IGPM:BC03 --actuate IGPM   # switch outputs while you watch IGPM 22BC03
```

> Actuation asks you to confirm first, and you can reverse it. It *does* drive real hardware.
> See [Safety](../concepts/safety.md).

## ECUs on the bus

The [At a glance](#at-a-glance) table gives the number of registered ECUs. The most decoded are:

| ECU | TX | Role |
|-----|----|------|
| BMS | 0x7E4 | Battery Management System |
| MCU | 0x7E3 | Motor Control Unit (inverter) |
| VCU | 0x7E2 | Vehicle Control Unit |
| OBC | 0x7E5 | On-Board Charger |
| ESC | 0x7D1 | Electronic Stability Control (wheel speeds) |
| EPS | 0x7D4 | Electric Power Steering |
| IGPM | 0x770 | Integrated Gateway & Power Module (body, IOControl) |
| BCM | 0x7A0 | Body Control Module / TPMS |
| HVAC | 0x7B3 | Climate control |
| CLU | 0x7C6 | Cluster / instrument panel (odometer) |

The profile also registers AAF, ADM, AMP, AVN, BSD-L/R, CCM, DDM, EPB, EWP, GSA, MFC, PSM, RCAM,
SCC, SKM, SRS, VESS, WPC, and one module that has no name yet, `Unknown-7C0`. List all ECUs with:

```bash
canair ecu               # every ECU + PID, verified-signal and capture counts
canair ecu BMS           # one ECU's identity + per-PID breakdown
```

## Exploring the profile

canair can query everything that the profile knows. You do not need to read the YAML by hand:

```bash
canair read BMS:2101         # read + decode the battery PID (live car)
canair decode BMS 2101 --stats  # value ranges from captures (offline)
canair coverage               # what's still undecoded
canair research --summary     # the open RE backlog
```

The source of truth is
[`profiles/hyundai-ioniq-ae-ev/ecus/`](https://github.com/philipkocanda/canair/tree/main/profiles/hyundai-ioniq-ae-ev/ecus).
It holds one file per ECU. Each file carries the identity, scan log, diagnostic trouble code (DTC)
meanings, PIDs, signals and research leads of that ECU. See [Profiles](../concepts/profiles.md) for
the bundle layout.

## A note on the hardware quirks

Ioniq ECUs drop to a low-power state when idle. So the **first** diagnostic request after a pause
is slow and can even time out. Retry once before you conclude that a PID or ECU is dead.

The OBD-II port is also gateway-isolated. ECUs answer request/response, but there is almost no
unsolicited broadcast traffic to sniff passively. `profiles/hyundai-ioniq-ae-ev/profile.yaml`
records these quirks.

## Sources & attribution

This profile builds on the work of the wider Hyundai/Kia EV reverse-engineering community. We
seeded, cross-checked or corrected many signals against the public sources below. Thank you to
their authors. Cross-vehicle sheets often differ by a byte or two, so we validated everything
again against the actual car (see [Define & verify](../bring-your-own-car/07-define-and-verify.md)).

- [uhi22/Ioniq28Investigations](https://github.com/uhi22/Ioniq28Investigations)
  — detailed investigations of the same 28 kWh Ioniq Electric.
- [Hyundai Ioniq EV PID spreadsheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  — community PID reference.
- [Hyundai Ioniq EV PID spreadsheet (2)](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI/edit)
  — another community PID reference.
- [JejuSoul/OBD-PIDs-for-HKMC-EVs — Ioniq EV 28 kWh VMCU CSV](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs/blob/master/Ioniq%20EV%20-%2028kWh/extendedpids/Hyundai_Ioniq_EV_VMCU_data.csv)
  — VMCU/VCU extended-PID definitions for this exact car.
- [JejuSoul/OBD-PIDs-for-HKMC-EVs — Kia Soul EV 27 kWh](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs/tree/master/Soul%20EV%20-%2027kWh/extendedpids)
  — a close cousin. Note the ~1-byte offset against the Ioniq.
- [projectgus/kona-ev-dbc — Diagnostics.md](https://raw.githubusercontent.com/projectgus/kona-ev-dbc/main/Diagnostics.md)
  — Kona EV diagnostics. It is a useful cross-reference for shared HKMC modules.
- [abstractionnl/wican-esphome — wican-ioniq.yaml](https://github.com/abstractionnl/wican-esphome/blob/main/wican-ioniq.yaml)
  — an ESPHome/WiCAN Ioniq PID config.

If your work is referenced here and you want different attribution, or you see a decode that
traces back to a source that is not listed, please
[open a pull request or issue](https://github.com/philipkocanda/canair). See
[Contributing](../contributing/index.md).
