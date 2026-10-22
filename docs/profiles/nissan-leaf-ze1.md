# The bundled Nissan Leaf ZE1 profile

`nissan-leaf-ze1` is canair's profile for the **second-generation Nissan Leaf (`ZE1`,
2018–2024, 40 kWh and 62 kWh e+)**. It covers battery, drive, charging and climate signals
seeded from public sources. On-car captures of a 2018 40 kWh car have verified most battery and
charging signals; the drive and climate signals still await a drive capture. See
[Bundled profiles](index.md) for the full list of what ships, and
[the Ioniq 2017 page](ioniq-2017.md) for a profile verified across every subsystem.

Browse it on GitHub:
[`profiles/nissan-leaf-ze1/`](https://github.com/philipkocanda/canair/tree/main/profiles/nissan-leaf-ze1).

Set it as your default profile once, and every `canair` command below works without repeating
`--profile`:

```bash
canair profile use nissan-leaf-ze1
```

!!! warning "Partly verified — check before you trust a value"
    The verified signals are the ones confirmed against captures of a 2018 40 kWh car: most of
    the battery signals (including all 96 cell voltages), the odometer and the charging signals.
    The rest were decoded from public sources (see [Sources & attribution](#sources-attribution))
    and are marked unverified until a capture confirms them. Offsets also differ between **model
    years, packs and markets**, so re-check a value against your own captures before relying on
    it (see [Define & verify](../bring-your-own-car/07-define-and-verify.md)).

    Two pack-specific traps: `LBC:2101` `SOH_40KWH` uses the 40 kWh pack's 115.44 Ah reference
    and is **wrong on a 62 kWh e+**. Prefer `LBC:2161` `SOH`, which the battery controller reports
    itself and suits either pack.

## At a glance

| | |
|---|---|
| Vehicle | Nissan Leaf ZE1, 2018–2024, 40 kWh and 62 kWh e+ | <!-- BEGIN generated-stats -->
| Counts | *Filled in when the [docs site](https://philipkocanda.github.io/canair/profiles/nissan-leaf-ze1/) is built* | <!-- END generated-stats -->

> These numbers are computed from the profile's `ecus/` files whenever this site is built,
> counted the same way as `canair validate pids --stats` and `canair research`.

## What it decodes

- **Battery (LBC)** — state of charge (both the true and the dashboard value), state of health,
  usable capacity, pack voltage and current, insulation, GIDs, the 12 V rail, **all 96 cell-pair
  voltages** with min/max, and four pack temperatures. Verified: SOC, SOC_D, SOH, SOH_40KWH,
  HV_CAPACITY, pack voltage, the first three pack temperatures, and the cell voltages with their
  min/max (the 96 cells sum exactly to the reported pack voltage). The battery serial number
  (`LBC:2184`) decodes as text; it is a static PID, read only on request.
- **Drive (VCM / INVERTER / ABS)** — gear, vehicle speed, motor RPM, torque and power, motor
  temperature, accelerator pedal and steering angle, and the Eco and e-Pedal modes.
- **Charging (VCM / LBC)** — plug state, charge mode, on-board charger output power, and the
  quick- and AC-charge counters. All but the quick-charge counter are verified against an AC
  charge.
- **Climate (VCM / HVAC)** — A/C and PTC heater power, ambient, cabin and evaporator
  temperatures, blower voltage and compressor speed.
- **Dashboard (METER)** — odometer (verified), the four tyre pressures, dashboard SOC and
  range.
- **Broadcast frames** — three DBC-style signals in `signals/` (vehicle speed `0x284`, motor
  RPM `0x1DA`, odometer `0x5C5`). They are defined but **not seen on the OBD-II port yet**: the
  ZE1's gateway sits between the port and the vehicle buses.

## Ready-made groups

The profile ships named selector groups, so a whole view is one command:

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

16 ECUs are registered. Addresses come from public tables that span several Leaf generations,
so those without a known PID are marked tentative. A discovery sweep in READY confirmed all 16
addresses.

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

…plus AIRBAG, BRAKE, EPS, IPDM, MULTI_AV, SHIFT, TCU and VSP, which are registered but have no
decoded PIDs yet. List them all with:

```bash
canair ecu               # every ECU + PID/signal/verified counts
canair ecu LBC           # one ECU's identity + per-PID breakdown
```

### Addressing and protocol

The Leaf is ISO 15765-4, 11-bit identifiers at 500 kbit/s. Most ECUs answer at `tx + 0x20`
(the profile-wide `addressing.rx_offset`), but several don't, so the modules that follow a
different rule carry their own `rx_id`: VCM `0x797 → 0x79A`, INVERTER `0x784 → 0x78C`, CHARGER
`0x792 → 0x793`, BRAKE `0x70E → 0x70F`, VSP `0x73F → 0x761` and TCU `0x746 → 0x783`.

The LBC, VCM, HVAC and BCM speak **KWP2000**, so the battery PIDs are service `0x21` reads
(`2101`) while the VCM's are `0x22` reads (`221156`). See
[ECU protocols & PID prefixes](../concepts/ecu-protocols.md).

## Sleep behaviour

Most ZE1 modules answer only while the car is on, and the first captures show the gate is
**per module, not port-wide**:

- **Car off:** VCM answered 16 of its 25 defined reads and METER 6 of 7 (not the odometer), but
  some of those return placeholders (dashboard SOC 100 %, range 2047, motor RPM 16383). ABS,
  INVERTER, HVAC and BCM stayed silent. The LBC answered for a few minutes after the car was
  last active, then went quiet, and `read --wake` did not revive it — it is a fast-sleeping ECU.
- **READY:** all 16 registered ECUs answered.
- **Charging (car off, AC):** LBC, VCM, METER, INVERTER and BCM answered every defined read; ABS
  and HVAC stayed silent.

Because of that, a read of a real PID is a better liveness test than a `10 01` discovery sweep:
with the car asleep, the KWP2000 ECUs don't answer a UDS session request. Each PID's
`vehicle_states:` lists the states it answered in.

## Vehicle states

A recording made without `--state` gets its states suggested from VCM signals:

| State | Rule |
|-------|------|
| `READY` | `VCM.POWER_SWITCH == 1` |
| `PLUGGED` | `VCM.PLUG_STATE == 2` |
| `CHARGING` | `VCM.CHARGE_MODE > 0 or VCM.OBC_OUT_PWR > 0` |
| `PARKED` | `VCM.GEAR == 1` |
| `DRIVING` | `VCM.VEHICLE_SPEED > 0.5` |

`DRIVING` deliberately ignores motor RPM, which reads a placeholder 16383 with the car off. The
`READY` rule has not yet been checked against accessory (ACC) mode. List the vocabulary with
`canair states`.

## IOControl actuators

The BCM defines **5 actuators**: door lock/unlock, mirror fold/unfold, horn, wiper and
headlights. They use KWP2000 `InputOutputControlByLocalIdentifier` (service `0x30`) in Nissan's
own `30 {LID} {function} {value}` layout, behind Nissan's OEM diagnostic session `10 C0`. The
profile opts into that session with the `nissan_c0_session` quirk, which canair's command
blocklist would otherwise refuse.

```bash
canair io BCM            # interactive actuator TUI
```

All five frames come from public CONSULT III captures and are **unverified on the ZE1**; the
slides list only the ON frames, so every OFF frame is inferred. Actuation drives real hardware.
See [Safety](../concepts/safety.md).

## Exploring the profile

Everything the profile knows is queryable through canair — no need to read the YAML by hand:

```bash
canair read LBC:2101         # read + decode the battery PID (live car)
canair decode LBC 2101 --stats  # value ranges from captures (offline)
canair captures uds --sessions  # what's been recorded, per state
canair coverage               # what's still undecoded
canair research --summary     # the open RE backlog
```

The source of truth is
[`profiles/nissan-leaf-ze1/ecus/`](https://github.com/philipkocanda/canair/tree/main/profiles/nissan-leaf-ze1/ecus)
— one file per ECU. See [Profiles](../concepts/profiles.md) for the bundle layout.

## Where help is needed

The research backlog is the quickest way to contribute. The highest-value leads are:

- **Verify `LBC:2102`** (the 96 cell voltages) — the layout is public and an on-car capture now
  exists to check it against, including the cell words that straddle a frame boundary.
- **Decode `LBC:2106`**, the cell-balancing flags — two public sources disagree on the layout.
- **Explain why `HV_CURRENT_1` and `HV_CURRENT_2` differ** in `LBC:2101`, and confirm the sign
  convention.
- **Decode the BCM lever-status PID `2109`** and confirm which module actually answers it.
- **Test on a 62 kWh e+** — the verification so far comes from 40 kWh cars, and the e+ capacity
  reference differs.

If you have a ZE1, [contribute your captures
back](../bring-your-own-car/08-share.md#contribute-your-profile-back).

## A note on the hardware quirks

The ZE1's CAN gateway isolates the OBD-II port from the vehicle buses, so there is almost no
unsolicited broadcast traffic to sniff passively; request/response is the reliable path. The
profile's ELM327 init (`ATSP6;ATS0;ATAL;ATST96;`) only applies to the `wican-ws` transport; the
default `slcan-tcp` path ignores it.

For `canair hunt --physical` and `canair investigate`, `profile.yaml` widens the high-voltage
pack band to 240–410 V (96 cells, about 355 V nominal) so a Leaf at low charge isn't missed.
Details are in `profiles/nissan-leaf-ze1/profile.yaml`.

## Sources & attribution

This profile was built device-free from the Leaf community's public work, and each signal's
`source_links` names where it came from. Thank you to their authors. (Cross-vehicle and
cross-generation sources often differ by a byte or two, so everything is re-validated against
the actual car — see [Define & verify](../bring-your-own-car/07-define-and-verify.md).)

- [meatpiHQ/wican-fw#750](https://github.com/meatpiHQ/wican-fw/pull/750) — the community WiCAN
  profile this one was seeded from, fixing the SOH and adding capacity, temperature, odometer
  and range signals, verified on a 2018 40 kWh car. Upstream later shipped those fixes as
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

If your work is referenced here and you'd like different attribution, please
[open a PR or issue](https://github.com/philipkocanda/canair) — see
[Contributing](../contributing/index.md).
