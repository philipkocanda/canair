# The bundled Hyundai Ioniq 5 (NE) profile

`hyundai-ioniq-5-ne-ev` is canair's profile for the **Hyundai Ioniq 5 (`NE`, 2021–2026)**. The car
uses the 800 V E-GMP platform of Hyundai. This is a **seed profile**. We built it from public
sources, mainly the recorded replies of Ioniq 5 cars in the OBDb project. We have not verified it
on a car with canair. Only the odometer is verified. See [Bundled profiles](index.md) for the full
list of profiles that ship. See [the Ioniq Electric page](hyundai-ioniq-ae-ev.md) for a profile
that is verified across every subsystem.

Explore its electronic control units (ECUs), parameter identifiers (PIDs) and signals in the
[profile explorer](explorer.md). You can also browse it on GitHub:
[`profiles/hyundai-ioniq-5-ne-ev/`](https://github.com/philipkocanda/canair/tree/main/profiles/hyundai-ioniq-5-ne-ev).

Set it as your default profile once. Then every `canair` command below works without `--profile`.
Name your battery variant at the same time (see [Variants](#variants)):

```bash
canair variants                                          # list the variants
canair profile use hyundai-ioniq-5-ne-ev --variant 77kwh   # replace 77kwh with yours
```

!!! warning "Seed profile — check every value before you trust it"
    One signal is verified: the odometer (`CLU:22B002`). The other signals are **unverified**.
    They come from public sources (see [Sources & attribution](#sources-attribution)). Their
    offsets fit recorded replies from the OBDb project. Nobody has confirmed the meaning of the
    values on a car with canair. Offsets also differ between **model years and battery packs**.
    Check a value against your own captures before you rely on it (see
    [Define & verify](../bring-your-own-car/07-define-and-verify.md)).

    Because of this, the default (verified-only) AutoPID build ships almost nothing for this car.
    You can still read and decode every signal with `canair read` and `canair monitor`.

## At a glance

| | |
|---|---|
| Vehicle | Hyundai Ioniq 5 (NE), 2021–2026, six battery variants, E-GMP 800 V platform |
{{ profile_stats_rows("hyundai-ioniq-5-ne-ev") }}

> The build computes these numbers from the `ecus/` files of the profile whenever it builds this
> site. It counts them the same way as `canair validate pids --stats` and `canair research`.

## Variants

The profile covers the whole range as six [variants](../concepts/variants.md). All of them have the
status `seed`, and no capture exists for any of them yet.

| Variant | Built | Battery | Markets |
|---|---|---|---|
| `58kwh` | 2021–2024 | 58 kWh, 144 cells in series (about 523 V) | EU, US |
| `72kwh` | 2021–2022 | 72.6 kWh, 180 cells in series (about 653 V) | EU |
| `77kwh` | 2021–2024 | 77.4 kWh, 192 cells in series (about 697 V) | EU, US, AU |
| `63kwh` | 2024–2026 (facelift) | 63 kWh, 144 cells in series (about 523 V) | EU, US, AU |
| `84kwh` | 2024–2026 (facelift) | 84 kWh, 192 cells in series (about 697 V) | EU, US, AU |
| `84kwh-n` | 2023–2026 | 84 kWh, 192 cells in series (about 697 V), Ioniq 5 N | EU, US, AU |

Each variant lists its sources in
[`variants.yaml`](https://github.com/philipkocanda/canair/blob/main/profiles/hyundai-ioniq-5-ne-ev/variants.yaml).
The 58 kWh and 63 kWh packs have 144 cells in series. Their pack voltage is lower than the default
plausibility band of the profile (450–820 V for `hv_pack`). So these two variants set their own
band of 350–620 V. This band is used by `canair hunt --physical` and `canair investigate`.

The signals are not tied to a variant yet. The charger unit (ICCU) shows why this matters. It has
two layouts for the output of the 12 V converter: `22E011` for 2021–2024 and `22E101` for 2025 and
later. The profile defines both, and the `22E101` page is a draft. It has not split the two
layouts into ECU [revisions](../concepts/variants.md#ecu-revisions) yet.

## What it decodes

- **Battery (BMU)** — state of charge (SOC), state of health (SOH), pack current and voltage,
  temperatures, relay states, and the charge counters. The profile also defines **192 voltage
  signals** across six PIDs (`220102`, `220103`, `220104`, `22010A`, `22010B`, `22010C`). The packs
  differ in cell count (see [Variants](#variants)). The profile does not limit these signals per
  variant yet.
- **Charging (ICCU, VCMS)** — AC and DC charge targets and limits, the AC input voltage, current
  and frequency of the on-board charger (OBC), the output of the 12 V low-voltage DC converter
  (LDC), the charge state, the limits that the charging station reports, the control pilot (CP)
  signal, and the temperatures of the DC inlet.
- **Drive (VCU, MCU, IEB, MDPS, Unknown-730)** — accelerator pedal, vehicle speed, motor speed and
  torque, inverter and motor temperatures (model years up to 2024), the coolant and oil pumps, the
  disconnector actuator, the four wheel speeds, the steering angle, and the motor current of the
  power steering. The module at `0x730` also reports
  high-resolution wheel speeds, yaw rate and accelerations.
- **Climate (HVAC)** — cabin, ambient and evaporator temperatures, the temperatures at the vents,
  and the coolant and inverter temperatures.
- **Body (ICU, IBU, CLU)** — lights, doors and trunk, the brake switch, the heated steering wheel,
  the tyre sensor IDs, tyre pressures and temperatures, and the odometer.
- **Not decoded yet** — the front camera (MFC) and the front radar (RDR). The profile knows only
  their addresses and identity strings. The profile has no IOControl actuators, routines or
  diagnostic trouble codes (DTCs).

Three PIDs have the status `draft`, because no Ioniq 5 is known to answer them yet: `BMU:220106`,
`ICCU:22E101` and `ICU:22BC04`.

## ECUs on the bus

The profile registers 14 ECUs. Twelve of them have PIDs.

| ECU | TX | Role |
|-----|----|------|
| ICU | 0x770 | Integrated central control unit: junction block and the gateway to all CAN segments |
| BMU | 0x7E4 | Battery management unit (alias `BMS`) |
| ICCU | 0x7E5 | Integrated charging control unit: on-board charger and 12 V converter |
| VCMS | 0x744 | Vehicle charging management system |
| VCU | 0x7E2 | Vehicle control unit |
| MCU | 0x7E3 | Motor control unit (traction inverter) |
| IEB | 0x7D1 | Integrated electronic brake (brake booster with ABS and ESC) |
| MDPS | 0x7D4 | Motor-driven power steering |
| IBU | 0x7A0 | Integrated body control unit (the body control module) |
| HVAC | 0x7B3 | Climate control |
| CLU | 0x7C6 | Instrument cluster |
| MFC | 0x7C4 | Front camera (identity only) |
| RDR | 0x7D0 | Front radar (identity only) |
| Unknown-730 | 0x730 | Unidentified module that reports wheel speeds, steering angle and accelerations |

List all ECUs with:

```bash
canair ecu               # every ECU + PID/signal/verified counts
canair ecu BMU           # one ECU's identity + per-PID breakdown
```

Public recorded replies confirm most of these addresses. Two cases are weaker:

- **MFC and RDR.** The addresses come from the `openpilot` fingerprint lists. That software reads
  them from the driver-assistance bus. Nobody has tested whether they answer through the OBD-II
  port.
- **IEB and Unknown-730.** Nobody read an identity from them. The profile infers that the IEB
  answers at `0x7D1`, the Hyundai ABS and ESC address. It cannot name the module at `0x730`.
  After an identity read names it, rename it with `canair ecu Unknown-730 rename NAME`.

On all-wheel-drive cars, nobody has established which inverter answers at the MCU address (the
front or the rear one).

### Addressing and protocol

The Ioniq 5 uses ISO 15765-4 with 11-bit identifiers at 500 kbit/s. The ECUs answer at the usual
address of `tx + 8`. It sets the ISO-TP fill bytes that the Hyundai/Kia specification
(ES95486) names: `0x55` for requests and `0xAA` for responses. No ECU firmware that we examined
checks the padding. So this is conformance to the specification, not a fix. The profile also sets
`ATST96` in its ELM327 init string. That applies only to the `wican-ws` and `elm327-tcp`
transports.

All PIDs are Unified Diagnostic Services (UDS) `0x22` reads. See
[ECU protocols & PID prefixes](../concepts/ecu-protocols.md).

### The gateway

The ICU is a central security gateway. The OBD-II port sits on the diagnostic CAN (D-CAN) segment
at 500 kbit/s. It reaches the other segments only through the ICU. The profile's `can_buses.yaml`
describes the eight segments on the ICU-H connector of the ICU (B-CAN, P-CAN, C-CAN, M-CAN,
D-CAN, E-CAN, G-CAN and I-CAN). It uses `ALL` for the ICU, which bridges them. The names, the CAN
FD split and the pin numbers come from public sources and the service manual. Only D-CAN has an
established bit rate.

This also means that you should not expect broadcast frames on the OBD-II port.
`canair sniff` sees almost nothing there. Request and response is the reliable path.

```bash
canair bus               # CAN segments and ECU counts
```

## Vehicle states

The profile has the standard state vocabulary (`CHARGING`, `READY`, `PLUGGED`, `ACC`, `ON`,
`PARKED`, `DRIVING`, `SLEEP`, `DEEPSLEEP`, `ALL`). Only `DEEPSLEEP` is detected automatically
(no ECU responds). The rules for `CHARGING` and `READY` are in the file as comments until a
capture shows which signals to use. When you record, pass `--state` yourself:

```bash
canair monitor BMU:220101 --save --label "charging at 11 kW" --state CHARGING
```

List the vocabulary with `canair states`.

## Exploring the profile

canair can query everything that the profile knows. You do not need to read the YAML by hand:

```bash
canair read BMU:220101       # read + decode the main battery PID (live car)
canair read ICCU             # all known signals of the charger unit
canair coverage              # what has no capture yet
canair research --summary    # the open reverse-engineering backlog
```

The source of truth is
[`profiles/hyundai-ioniq-5-ne-ev/ecus/`](https://github.com/philipkocanda/canair/tree/main/profiles/hyundai-ioniq-5-ne-ev/ecus).
It holds one file per ECU. See [Profiles](../concepts/profiles.md) for the bundle layout.

## Where help is needed

Almost every signal here needs a capture that you record on an Ioniq 5 with canair. The most
useful contributions are:

- **Record a session on any variant.** A short recording of the `BMU`, `ICCU`, `VCMS` and `VCU`
  pages with the car in READY and while charging would verify many signals at once. Use `--save`
  and `--state`, and tell us your variant and model year.
- **Verify the drafts.** Check `BMU:220106` (it answers on the EV6, Ioniq 6, GV60 and EV9), and
  `ICU:22BC04` (front door locks and rear seat belts).
- **Decode the lights and indicators.** `ICU:22BC07` and `ICU:22BC09` need a narrated capture.
  The sources disagree on the bits for the rear defogger, the indicators and the headlights.
- **Decode the facelift charger layout.** `ICCU:22E002` differs for 2025 and later. A 63 kWh or
  84 kWh capture is needed.
- **Name the module at `0x730`** with an identity read, and check whether `MFC` and `RDR` answer on
  the OBD-II port.

If you have an Ioniq 5, [contribute your captures
back](../bring-your-own-car/08-share.md#contribute-your-profile-back).

## Sources & attribution

We built this profile without a device, from the public work of the community. The `source_links`
of each signal name its origin. Thank you to the authors. Sources often differ by a byte or two
across model years. So we validate everything again against the actual car (see
[Define & verify](../bring-your-own-car/07-define-and-verify.md)).

- [OBDb/Hyundai-Ioniq-5](https://github.com/OBDb/Hyundai-Ioniq-5) — the signal set and the
  recorded replies (model years 2022–2027). canair checks every signal it took from OBDb against
  these replies. Signals keep the OBDb names, without the `IONIQ5_` prefix.
- [Open Vehicle Monitoring System v3](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3)
  (`vehicle_hyundai_ioniq5`) — the polled addresses and several bit positions.
- [meatpiHQ/wican-fw#478](https://github.com/meatpiHQ/wican-fw/issues/478) (timwelchnz) — the
  odometer reading that verified `CLU:22B002`.
- [commaai/opendbc](https://github.com/commaai/opendbc) — the identity strings of the front
  camera and the front radar (`HYUNDAI_IONIQ_5` fingerprints).
- The Hyundai service manual and the Hyundai technical service bulletins — module names, wiring
  and the part numbers of the BMU.
- [dragz/explorationsincarhacking](https://github.com/dragz/explorationsincarhacking) and
  [tylerharvey/Ioniq5_CAN](https://github.com/tylerharvey/Ioniq5_CAN) — the CAN segments of the
  gateway.
- The [Ioniq Electric profile](hyundai-ioniq-ae-ev.md) of this repository, for the cluster and the
  body module layouts that the Ioniq 5 shares.

If your work is referenced here and you want different attribution, please
[open a pull request or issue](https://github.com/philipkocanda/canair). See
[Contributing](../contributing/index.md).
