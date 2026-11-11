# The bundled Nissan Leaf ZE1 profile

`nissan-leaf-ze1-ev` is canair's profile for the **second-generation Nissan Leaf (`ZE1`,
2018–2024, 40 kWh and 62 kWh e+)**. It covers battery, drive, charging and climate signals. We
seeded them from public sources. On-car captures of a 2018 40 kWh car have verified most battery
and charging signals. The drive and climate signals still await a drive capture. See
[Bundled profiles](index.md) for the full list of profiles that ship. See
[the Ioniq Electric page](hyundai-ioniq-ae-ev.md) for a profile that is verified across every
subsystem.

Explore its electronic control units (ECUs), parameter identifiers (PIDs) and signals in the
[profile explorer](explorer.md). You can also browse it on GitHub:
[`profiles/nissan-leaf-ze1-ev/`](https://github.com/philipkocanda/canair/tree/main/profiles/nissan-leaf-ze1-ev).

Set it as your default profile once. Then every `canair` command below works without `--profile`:

```bash
canair profile use nissan-leaf-ze1-ev
```

!!! warning "Partly verified — check before you trust a value"
    The verified signals are the ones that captures of a 2018 40 kWh car confirmed: most of the
    battery signals (including all 96 cell voltages), the odometer and the charging signals. We
    decoded the rest from public sources (see [Sources & attribution](#sources-attribution)).
    They stay unverified until a capture confirms them. Offsets also differ between **model
    years, packs and markets**. Check a value against your own captures before you rely on it (see
    [Define & verify](../bring-your-own-car/07-define-and-verify.md)).

    The 40 kWh and 62 kWh e+ cars are the two [variants](../concepts/variants.md) of the profile
    (`40kwh`, `62kwh`). Name yours with `canair profile use nissan-leaf-ze1-ev --variant 40kwh`.
    `LBC:2101` `SOH_40KWH` uses the 115.44 Ah reference of the 40 kWh pack. So canair reads it only
    on the `40kwh` car. `LBC:2161` `SOH`, which the battery controller reports itself, suits either
    pack.

## At a glance

| | |
|---|---|
| Vehicle | Nissan Leaf ZE1, 2018–2024, 40 kWh and 62 kWh e+ |
{{ profile_stats_rows("nissan-leaf-ze1-ev") }}

> The build computes these numbers from the `ecus/` files of the profile whenever it builds this
> site. It counts them the same way as `canair validate pids --stats` and `canair research`.

## What it decodes

- **Battery (LBC)** — state of charge (SOC, both the true and the dashboard value), state of
  health (SOH), usable capacity, pack voltage and current, insulation, GIDs, the 12 V rail, **all
  96 cell-pair voltages** with min/max, and four pack temperatures. Verified: SOC, SOC_D, SOH,
  SOH_40KWH, HV_CAPACITY, pack voltage, the first three pack temperatures, and the cell voltages
  with their min/max (the 96 cells sum exactly to the reported pack voltage). The battery serial
  number (`LBC:2184`) decodes as text. It is a static PID, and canair reads it only on request.
- **Drive (VCM / INVERTER / ABS)** — gear, vehicle speed, motor RPM, torque and power, motor
  temperature, accelerator pedal and steering angle, and the Eco and e-Pedal modes.
- **Charging (VCM / LBC)** — plug state, charge mode, on-board charger output power, and the quick-
  and AC-charge counters. All of them except the quick-charge counter are verified against an AC
  charge.
- **Climate (VCM / HVAC)** — A/C and positive temperature coefficient (PTC) heater power, ambient,
  cabin and evaporator temperatures, blower voltage and compressor speed.
- **Dashboard (METER)** — odometer (verified), the four tyre pressures, dashboard SOC and range.
- **Broadcast frames** — three signals in `signals/` in the style of a Controller Area Network
  (CAN) database (DBC) file: vehicle speed `0x284`, motor RPM `0x1DA` and odometer `0x5C5`. The
  profile defines them, but canair has **not seen them on the on-board diagnostics (OBD-II) port
  yet**. The gateway of the ZE1 sits between the port and the vehicle buses.

## Ready-made groups

The profile ships named selector groups, so one command shows a whole view:

| Group | What it polls |
|-------|---------------|
| `@battery` | SOC, SOH, capacity, pack voltage/current, cell min/max, temperatures |
| `@cells` | All 96 cell-pair voltages plus min/max (cell-balance check) |
| `@driving` | Gear, speed, motor RPM/torque/power, pedal, steering, pack current |
| `@charging` | Plug state, charge mode, charger output power, pack voltage/current |
| `@climate` | A/C and PTC power, cabin/ambient/evaporator and battery temperatures |
| `@power` | Where the energy goes: pack, motor, A/C, PTC, auxiliary and 12 V |
| `@tyres` | Four tyre pressures |
| `@lifetime` | Slow counters: charge counts, odometer, SOH, distance counter |

```bash
canair read LBC:2161         # state of health, either pack
canair monitor @battery      # live battery view
canair monitor @driving --save --label "commute" --state DRIVING
```

## ECUs on the bus

The profile registers 16 ECUs. The addresses come from public tables that span several Leaf
generations. So the ECUs without a known PID are marked tentative. A discovery sweep in READY
confirmed all 16 addresses.

| ECU | TX | Role |
|-----|----|------|
| LBC | 0x79B | Li-ion Battery Controller (HV battery management) |
| VCM | 0x797 | Vehicle Control Module (drive, charging, climate power) |
| INVERTER | 0x784 | Traction motor inverter |
| ABS | 0x740 | ABS / vehicle dynamics (also pedal and steering inputs) |
| HVAC | 0x744 | Climate control |
| METER | 0x743 | Combination meter (odometer, tyre pressure, range) |
| BCM | 0x745 | Body control module (actuators) |
| CHARGER | 0x792 | On-board charger (part of the PDM) |

The profile also registers AIRBAG, BRAKE, EPS, IPDM, MULTI_AV, SHIFT, TCU and VSP. They have no
decoded PIDs yet. List all ECUs with:

```bash
canair ecu               # every ECU + PID/signal/verified counts
canair ecu LBC           # one ECU's identity + per-PID breakdown
```

### Addressing and protocol

The Leaf uses ISO 15765-4 with 11-bit identifiers at 500 kbit/s. Most ECUs answer at `tx + 0x20`
(the profile-wide `addressing.rx_offset`). Several ECUs do not. The modules that follow a
different rule carry their own `rx_id`:

- VCM `0x797 → 0x79A`
- INVERTER `0x784 → 0x78C`
- CHARGER `0x792 → 0x793`
- BRAKE `0x70E → 0x70F`
- VSP `0x73F → 0x761`
- TCU `0x746 → 0x783`

The profile records `id_protocol: KWP2000` for the LBC, VCM, HVAC and BCM. Their identity notes
cite poll lists that show positive replies to service `0x21` reads. The battery PIDs of the LBC are
`0x21` reads (`2101`).

The VCM answers both services. Its PID `2181` is a `0x21` read, and its other 25 PIDs are `0x22`
reads (`221156`). The profile notes say that a public poll list shows the VCM answering both. So a
`0x22` PID can sit in an ECU that is marked KWP2000. canair sends the prefix of a PID as written.

The METER, ABS and INVERTER have only `0x22` PIDs and no recorded `id_protocol`. See
[ECU protocols & PID prefixes](../concepts/ecu-protocols.md).

## Sleep behaviour

Most ZE1 modules answer only while the car is on. The first captures show that the gate is **per
module, not port-wide**:

- **Car off:** The VCM answered 16 of its 25 defined `0x22` reads. The METER answered 6 of 7 (not
  the odometer). Some of those answers are placeholders (dashboard SOC 100 %, range 2047, motor RPM
  16383). The ABS, INVERTER, HVAC and BCM stayed silent. The LBC answered for a few minutes after
  the car was last active, then went quiet. `read --wake` did not revive it. The LBC is a
  fast-sleeping ECU.
- **READY:** All 16 registered ECUs answered.
- **Charging (car off, AC):** The LBC, VCM, METER, INVERTER and BCM answered every defined read.
  The ABS and HVAC stayed silent.

So a read of a real PID is a better liveness test than a `10 01` discovery sweep. In the READY
sweep, the BCM answered `10 01` with a negative response code (NRC) `0x12`. The `vehicle_states:` of
each PID lists the states in which it answered.

## Vehicle states

canair suggests the states of a recording made without `--state` from VCM signals:

| State | Rule |
|-------|------|
| `READY` | `VCM.POWER_SWITCH == 1` |
| `PLUGGED` | `VCM.PLUG_STATE == 2` |
| `CHARGING` | `VCM.CHARGE_MODE > 0 or VCM.OBC_OUT_PWR > 0` |
| `PARKED` | `VCM.GEAR == 1` |
| `DRIVING` | `VCM.VEHICLE_SPEED > 0.5` |

`DRIVING` deliberately ignores motor RPM, which reads a placeholder 16383 with the car off.
Nobody has checked the `READY` rule against accessory (ACC) mode yet. List the vocabulary with
`canair states`.

## IOControl actuators

The BCM defines **5 actuators**: door lock/unlock, mirror fold/unfold, horn, wiper and headlights.
They use KWP2000 `InputOutputControlByLocalIdentifier` (service `0x30`). The layout is Nissan's
own: `30 {LID} {function} {value}`, with LID for local identifier. The actuators run behind
Nissan's OEM diagnostic session `10 C0`.

The profile opts into that session with the `nissan_c0_session` quirk. The command blocklist of
canair would otherwise refuse the session. The quirk permits it on the KWP2000 ECUs of this
profile only.

Each actuator declares its OFF frame as its `release:`. canair sends this frame on exit and on
Ctrl+C. A one-shot command (function `00`) leaves nothing to release. Function `20` with value `0`
cancels an earlier request. The value keys of the TUI build UDS `0x2F` requests, so this profile
refuses them.

```bash
canair io BCM            # interactive actuator TUI
```

All five frames come from public CONSULT III captures and are **unverified on the ZE1**. The
slides list only the ON frames, so we inferred every OFF frame. Actuation drives real hardware.
See [Safety](../concepts/safety.md).

## Exploring the profile

canair can query everything that the profile knows. You do not need to read the YAML by hand:

```bash
canair read LBC:2101         # read + decode the battery PID (live car)
canair decode LBC 2101 --stats  # value ranges from captures (offline)
canair captures uds --sessions  # what's been recorded, per state
canair coverage               # what's still undecoded
canair research --summary     # the open RE backlog
```

The source of truth is
[`profiles/nissan-leaf-ze1-ev/ecus/`](https://github.com/philipkocanda/canair/tree/main/profiles/nissan-leaf-ze1-ev/ecus).
It holds one file per ECU. See [Profiles](../concepts/profiles.md) for the bundle layout.

## Where help is needed

The research backlog is the quickest way to contribute. The highest-value leads are:

- **Verify `LBC:2102`** (the 96 cell voltages). The layout is public, and an on-car capture now
  exists to check it against. This includes the cell words that straddle a frame boundary.
- **Decode `LBC:2106`**, the cell-balancing flags. Two public sources disagree on the layout.
- **Explain why `HV_CURRENT_1` and `HV_CURRENT_2` differ** in `LBC:2101`, and confirm the sign
  convention.
- **Decode the BCM lever-status PID `2109`** and confirm which module actually answers it.
- **Test on a 62 kWh e+.** The verification so far comes from 40 kWh cars, and the e+ capacity
  reference differs.

If you have a ZE1, [contribute your captures
back](../bring-your-own-car/08-share.md#contribute-your-profile-back).

## A note on the hardware quirks

The CAN gateway of the ZE1 isolates the OBD-II port from the vehicle buses. So there is almost no
unsolicited broadcast traffic to sniff passively. Request/response is the reliable path. The
ELM327 init of the profile (`ATSP6;ATS0;ATAL;ATST96;`) applies only to the `wican-ws` transport.
The default `slcan-tcp` path ignores it.

For `canair hunt --physical` and `canair investigate`, `profile.yaml` widens the high-voltage (HV)
pack band to 240–410 V (96 cells, about 355 V nominal). This way canair does not miss a Leaf at
low charge. Details are in `profiles/nissan-leaf-ze1-ev/profile.yaml`.

## Sources & attribution

We built this profile without a device, from the public work of the Leaf community. The
`source_links` of each signal name its origin. Thank you to their authors. Sources often differ by
a byte or two across vehicles and generations. So we validate everything again against the actual
car (see [Define & verify](../bring-your-own-car/07-define-and-verify.md)).

- [meatpiHQ/wican-fw#750](https://github.com/meatpiHQ/wican-fw/pull/750) — the community WiCAN
  profile that seeded this one. It fixed the SOH and added capacity, temperature, odometer and
  range signals, verified on a 2018 40 kWh car. Upstream later shipped those fixes as
  [`leaf-ze1-2018-.json`](https://github.com/meatpiHQ/wican-fw/blob/main/vehicle_profiles/nissan/leaf-ze1-2018-.json).
- [OBDb/Nissan-Leaf](https://github.com/OBDb/Nissan-Leaf) (CC BY-SA 4.0) — per-model-year
  signal set and real 2018–2024 ZE1 test responses for both packs.
- [dalathegreat/leaf_can_bus_messages](https://github.com/dalathegreat/leaf_can_bus_messages)
  — the Leaf CAN bus documentation, ECU address table and poll lists.
- [OVMS v3 `vehicle_nissanleaf`](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3)
  and [pbutterworth/nissan-leaf-obd-ble](https://github.com/pbutterworth/nissan-leaf-obd-ble)
  — cross-checks for the LBC and VCM decodes.
- [PCAutomotive, "Remote Exploitation of Nissan Leaf"](https://i.blackhat.com/Asia-25/Asia-25-Evdokimov-Remote-Exploitation-of-Nissan-Leaf.pdf)
  (Black Hat Asia 2025) — the BCM actuator frames.
- [balrog-kun/nissan-qashqai-can-info](https://github.com/balrog-kun/nissan-qashqai-can-info)
  — the Qashqai J10 BCM table that explains the IOControl function byte.

If your work is referenced here and you want different attribution, please
[open a pull request or issue](https://github.com/philipkocanda/canair). See
[Contributing](../contributing/index.md).
