---
title: "DTC meanings for the generic Hyundai units: ESC, BCM (IBU), IGPM, smart key, TPMS"
subject: >-
  What Hyundai's own 2017 Ioniq Electric service manual says each stored DTC means for the units
  that carry no `dtcs:` section yet, with detection thresholds, plus where the manual is silent
  or inconsistent
created: 2026-11-01
updated: 2026-11-01
status: active
verification: >-
  Text transcribed from the manual's per-DTC pages (DTC Description and DTC Detecting Condition),
  not from the code lists of other sites. Nothing here has been seen on this car: the logged scans
  contain only AMP B2915-B2921 and CCM C182C. Every entry added to `ecus/` is `verified: false`
  until a scan returns it.
sections:
  - section: "1"
    status: open
    note: Source and how far to trust it.
  - section: "2"
    status: open
    note: >-
      What the "-NN" suffix is. The manual never says; the match with the standard failure-type
      table is an inference.
  - section: "3-7"
    status: integrated
    date: 2026-11-01
    note: >-
      Every code whose own page was read is in `esc.yaml`, `bcm.yaml` (IBU and TPMS), `igpm.yaml`
      and `skm.yaml` as `verified: false`. Codes known only from the index (sections 3 and 8) are
      not. `eps.yaml` was corrected against the EV MDPS index: C1604, C1705, C2412, C2413,
      C1692, C1102 and C1628 renamed, C1687 and C1688 removed (ESC codes), the rest flagged.
    target: profiles/hyundai-ioniq-ae-ev/ecus/
relations:
  related:
    - path: docs/concepts/dtcs.md
    - path: profiles/hyundai-ioniq-ae-ev/ecus/esc.yaml
    - path: profiles/hyundai-ioniq-ae-ev/ecus/bcm.yaml
    - path: profiles/hyundai-ioniq-ae-ev/ecus/igpm.yaml
    - path: profiles/hyundai-ioniq-ae-ev/ecus/skm.yaml
sources:
  - name: >-
      Hyundai 2017 Ioniq Electric service manual, "DTC Index (EV)" and the per-system diagnosis
      pages
    kind: factory service manual, mirrored by a third party; copyright Hyundai
    url:
      - https://lemon-manuals.org.ua/Hyundai/2017/Ioniq%20Electric/Repair%20and%20Diagnosis/Quick%20Lookups/DTC%20Index/DTC%20Index%20%28EV%29/
  - name: Hyundai 2025 Kona N Line service manual, Brake ABS/ESP DTC index
    kind: factory service manual, mirrored; used only to corroborate names
    url: https://alloemmanuals.com/manual/Hyundai/2025/Kona%20N%20Line%20S%2C%204WD/Repair%20and%20Diagnosis/Quick%20Lookups/DTC%20Index/Brake%20Abs%2FEsp%20DTCS
  - name: Hyundai 2016 Azera and 2009 Veracruz ESC DTC index
    kind: factory service manuals, mirrored; used only to corroborate names
    url: https://lemon-manuals.org.ua/Hyundai/2016/Azera%20Limited/Repair%20and%20Diagnosis/Brakes/Traction%20Control/Electronic%20Stability%20Control%20%28ESC%29%20System%20Diagnosis/DTC%20Index/
---

# DTC meanings for the generic Hyundai units

## 1. Source and trust

The text below comes from the 2017 Ioniq Electric manual's own pages for each code. Each code has a
"DTC Description" and a "DTC Detecting Condition" table (strategy, enable condition, threshold,
diagnostic time, possible cause). Thresholds are quoted as the manual prints them.

- It is the right model, year and drivetrain, which a generic Hyundai code list is not.
- It is a US-market manual. The TPMS section gives separate EU and NA timings, so it covers both.
- The mirror is third-party and was slow and intermittently blocked. The manual has typos, noted
  per unit below. Where the manual contradicts itself, both readings are listed.
- Names for `C1200`-`C1211`, `C1235`, `C1260`, `C1261`, `C1283`, `C1604`, `C1616` and `C2112`
  agree with the Azera (2016) and Kona (2025) manuals. That only confirms the names; the
  thresholds are Ioniq-specific.
- The local offline manuals (`references/manuals/Ioniq_2017_US`) only carry codes for steering
  (already in `eps.yaml`), brake pedal and stop lamp, blind-spot, and the battery, so they cannot
  cross-check any unit below.

## 2. The "-NN" suffix

The manual never defines it. Every ESC heading reads "DTC C1101-01"; every body-side heading
reads "-00". Some descriptions write the code with the suffix glued on (`C168708`, `B160200`).
The observed values fit the standard failure-type byte (FTB) table that canair already knows:

| Suffix | Where | Standard FTB reading |
|---|---|---|
| `01` | ESC voltage, wheel sensor open/short, pressure-sensor electrical, valve, motor | general electrical failure |
| `02` | ESC wheel sensor range/no-signal, steering and G sensors, fluid low | general signal failure |
| `04` | ESC not calibrated, ECU hardware, variant coding | system internal failure |
| `07` | ESC brake disc temperature | mechanical failure |
| `08` | ESC CAN time-outs and bus-off | bus signal/message failure |
| `87`, `88` | MDPS CAN time-out, bus-off | missing message, bus off (J2012-DA) |
| `00` | BCM, IGPM, smart key, TPMS | no sub-type information |

So a scan of the ESC should print `C1616-08` and a body-side unit `B1602-00`. That is an
inference from the pattern, not a statement in the manual. It also means the same base code can
carry a different suffix on another unit (`C1616` is `-08` on the ESC, `-88` on the MDPS, `-00`
on the IGPM and IBU). Always key `dtcs:` on the base code without the suffix, as the schema does.

## 3. ESC (0x7D1): the manual calls it the IBAU

The manual names the unit **IBAU** (Integrated Brake Actuation Unit) and also "ABS ECU". The
profile's `AHB` alias is the active hydraulic booster that the same unit contains. Warning-lamp
behaviour for almost every code below: EBD, RBS, ABS, ESP(ESC) and DBC lamps on, plus the AVH lamp
if fitted; exceptions are noted.

| Code | Meaning | Detection (as printed) | Lamps / reset |
|---|---|---|---|
| C1101 | Battery voltage high | VBAT above 17 +/- 0.5 V for 2 s, or above 19 +/- 0.5 V for 0.5 s | all lamps; clears after 2 s normal |
| C1102 | Battery voltage low | below 9.5 +/- 0.5 V for 0.5 s above 7 km/h; below 8.0 +/- 0.5 V below 7 km/h or in ABS/TCS/ESP control | all lamps; clears after 0.5 s normal |
| C1103 | Ignition voltage | hardware ignition state off for 30 s while above 15 km/h | no lamp |
| C1113 | 5 V sensor supply | sensor supply outside 5 +/- 0.25 V for over 1 s | all lamps if both supplies fail |
| C1200 / C1203 / C1206 / C1209 | Wheel speed sensor front-left / front-right / rear-left / rear-right, open/short | sensor current outside 4 mA +/- 10 % to 60 mA +/- 10 % for over 140 ms, or leakage current above a limit | all lamps; ignition cycle clears. More than 2 wheel failures: EBD lamp; more than 4: AHB lamp |
| C1201 | Wheel speed sensor front-left, range/performance/intermittent | speed-jump counters (for example +/-100 g 56 times on one wheel or 5 times on two or more; 70 g 126 or 20 times), a wheel out of step with the others for 20 s to 2 min, or a smart-sensor signal on an active-sensor wheel 200 times in a row; counters clear every 30 min | all lamps; clears on ignition cycle and above 2 km/h |
| C1202 | Wheel speed sensor front-left, invalid/no signal | air gap: slowest wheel below 0.25 km/h while the rest exceed 10 km/h for 160 ms (20 s if two or more wheels below 2 km/h); ABS control longer than 36 s | all lamps; clears on ignition cycle and above 2 km/h. A failure speed under 2 km/h is shown as an air-gap failure |
| C1233 | Front pressure sensor | high-pressure accumulator (HPA) pressure unchanged for over 5 s while charging | all lamps plus buzzer; clears after brake press and release |
| C1235 | Pressure sensor (primary), electrical | sensor signal voltage above 4.8 V or below 0.2 V for over 1 s, or their average off 2.5 +/- 0.1 V | all lamps plus buzzer |
| C1236 | Pressure sensor (secondary), electrical | HPA pressure under 100 bar for over 1.2 s; also set by repeated hard braking | all lamps plus buzzer; clears when pressure recovers |
| C1260 | Steering angle sensor, signal | straight-driving angle over 55 deg, out of sensor spec, estimate vs measured over 50 deg/s for 1.5 s, or under 3 deg in a curve | ESP/DBC lamp |
| C1261 | Steering angle sensor not calibrated | not calibrated after sensor or IBAU replacement | ESP/DBC lamp; clears after calibration with the diagnostic tool |
| C1283 | Lateral G / longitudinal G / yaw rate sensor, signal error | IMU in the airbag control module (SRSCM) disagrees with estimates, or its rolling count stops for over 100 ms; yaw rate over 6 deg/s at standstill for 2 s | ESP/DBC lamp |
| C1285 | Uncalibrated Ax signal | longitudinal G sensor zero not set, or sensor replaced | ESP/DBC lamp; clears after calibration |
| C1286 | Yaw rate and G sensor fail, check SRSCM | IMU rolling count stops or fail flag set | ESP/DBC lamp |
| C1287 | Brake fluid low | fluid-level signal "low" for over 1 s | RBS lamp only |
| C1604 | ECU hardware error | EEPROM write/erase failure, RAM/ROM/timer/ADC fault, or internal power fault | all lamps plus buzzer |
| C160E | P-CAN bus off | more than 10 consecutive bus-off events (70 ms recovery each) | RBS, ESP, DBC lamps |
| C1616 | C-CAN bus off | same rule as C160E on C-CAN | RBS, ABS, ESP, DBC lamps |
| C1623 | CAN time-out, steering angle sensor | no message for over 500 ms, monitored 1.5 s after power-up | RBS, ESP, DBC lamps |
| C1638 | ACC (SCC) communication error | no message for 500 ms in control, 1500 ms otherwise, or alive counter stuck | no lamp |
| C1651 | CAN time-out, EPB | no message for 500 ms with any control active, 1500 ms otherwise | no lamp; AVH lamp if fitted |
| C1687 | CAN time-out, VSM2 (MDPS) | no message for 500 ms / 1500 ms | no lamp |
| C16B4 | CAN time-out, VCU | no message for 0.5 s with control, 1.5 s without | RBS, ESP, DBC lamps |
| C1702 | Variant coding error | variant not set, or a 4WD, Ax or EPB message arrives on a car coded without it | all lamps; clears once coding is set |
| C1806 | CAN time-out, MCU | no message for 500 ms / 1500 ms | RBS, ESP, DBC lamps |
| C2112 | Valve relay error | relay voltage under 6 V for 0.2 s while on, or over 5 V for 0.2 s while off, or power/load side differ by over 1 +/- 0.15 V | all lamps |
| C2227 | Excessive brake disc temperature | calculated disc above 500 C (932 F); DBC above 650 C (1202 F) | no lamp; clears below 400 C |
| C2380 | ABS/TCS/ESP valve error | ABS valve under 2.0 V for 0.06 s, AHB valve voltage difference over 5 V for 1 s, or valve current over 8.5 +/- 2.5 A for 0.06 s | all lamps; AHB control lost and buzzer |
| C2402 | Motor electrical | battery under 4 V for 1 s, relay voltage above 4 +/- 0.5 V while off for 500 ms, motor relay voltage below spec for 1 s, or ground loss above 5 V / below 1 V for 100 ms | all lamps; buzzer if accumulator pressure is low |

The wheel-sensor siblings (`C1204`/`C1207`/`C1210` and `C1205`/`C1208`/`C1211`) were read
afterwards: identical to `C1201` and `C1202` apart from the wheel. Listed in the ESC index but not
read in detail: `C1234` (rear pressure sensor), `C1237` (pressure sensor other), `C1268`
(circuit leak), `C1353`-`C1356` (wheel 1 and 2 pressure sensors), `C1358` (AVH unavailable, EPB in
emergency release), `C135C` (VCU torque control unavailable for SCC), `C1378`-`C1380`/`C1384`
(pedal and pressure sensor signal and calibration), `C1503` (TCS switch), `C1542` (brake switch),
`C1643`, `C1650`, `C1652`, `C1688`, `C1824`. The wheel-sensor families are consistent with the
Azera and Kona lists, where only the wheel position changes.

**`C1625` is not an ESC code.** It reads "CAN time-out ABS/ESP" on the IGPM, TPMS, EPB and BSD
units, i.e. other modules reporting the ESC gone silent. The ESC cannot time out on itself.

Manual quirks: the unit is called IBAU in text but "ABS ECU" in descriptions; the cause lists of
`C1200`-`C1209` all print "(FL)"; `C1260` shows the strategy "Pressure sensor offset monitoring";
`C1287` prints "Isecond" for 1 second; `C1103` repeats the low-voltage description.

## 4. BCM (0x7A0): on this car it is the IBU

The manual's "Body Control Module" page states that the BCM is the **IBU** (Integrated Body
control Unit). The TPMS receiver is integrated in it ("BCM (TPMS module integrated)"), which
matches the profile note that TPMS shares `0x7A0`.

| Code | Meaning | Detection (as printed) |
|---|---|---|
| B1210-B1217 | Parking assist sensor fault: front left, front centre-left, front centre-right, front right, rear left, rear centre-left, rear centre-right, rear right | LIN data from that ultrasonic sensor not received, or abnormal; checked with ignition on in "D" range; reported immediately. Causes: dirt on the sensor, trapped sensor rubber, LIN fault, open or poor connection, faulty sensor |
| B1602 | CAN error (B-CAN) | B-CAN high or low line failure while the bus still communicates; 9-16 V, 2 s |
| B1603 | CAN bus off (B-CAN) | B-CAN communication impossible; high and low shorted to battery or to ground together; 2 s |
| B1609 | CAN time-out SJB | no B-CAN message from the SJB for 10 s. The manual's footnote says SJB is the IGPM's power-control half |
| B168A | CAN time-out smart key | no B-CAN message from the smart key unit for 10 s |
| C1616 | CAN bus off (C-CAN) | C-CAN communication impossible; **700 ms** (the B-CAN rules use 2 s). The description prints "B161600", an apparent typo |

The IBU's parking sensors are LIN devices, not CAN, so `B1210`-`B1217` mean a sensor or its LIN
wire, not a bus fault.

## 5. IGPM (0x770): gateway plus power control

Footnotes split the unit: "IGPM_GATEWAY" (the central gateway, CGW) and "IGPM_IPS CONTROL
MODULE" (the power half, also called the SJB). All codes are `-00`.

| Code | Meaning | Detection (as printed) |
|---|---|---|
| B1101 / B1102 | Battery voltage high / low | IG power voltage above 19 V / below 6.5 V for 3 s, checked 5 s after ignition on |
| B1581 | Ignition switch signal error | IG key signal seen by the gateway differs from the one in the IBU's B-CAN data for 5 s |
| B1602 / B1603 | B-CAN line error / bus off | as the IBU rules, 2 s |
| B1604 / B1605 / B1606 / B1607 | CAN time-out: BCM / driver door module / assist door module / power seat module | no B-CAN message for 10 s |
| B164D | CAN time-out wireless charger | same rule, B-CAN |
| B1621 | ECU failure | EEPROM read/write error inside the gateway; reported immediately |
| B2211 | CAN time-out BMS | no P-CAN message from the BMS for 10 s |
| B2800 / B2801 | M-CAN bus off / single-line error | 2 s |
| C160E / C160F / C1616 | bus off on P-CAN / D-CAN / C-CAN | 2 s |
| C1625, C1628, C1651, C1667, C1687, C1804, C1818, C1819, C1827 | CAN time-out: ABS/ESP (IBAU), cluster, EPB, SCC (AEB unit), MDPS, LKAS, blind-spot radars, TPMS (the BCM), iBAU | no C-CAN message for 10 s |
| C1614, C1806, C1808, C1829, C182D, C1830, C181A, C181C, B1644, B1648, B2804 | CAN time-out: VCU, MCU, LDC, OBC, SCU, VESS, AAF, SBW switch, SRS control module, passenger occupant module, climate control | no P-CAN message for 10 s |
| B1699, B2805 | CAN time-out AV/AVN, I-BOX | M-CAN, 10 s |
| B2510-B2513 | Head lamp high RH/LH, low RH/LH short to ground | over 12.5 A at the output, lamp switched on |
| B2352, B2353, B2517, B2522-B2525 | Daytime running lamp LH/RH, internal tail lamp, turn signals short to ground | over 7.2 A |
| B251D-B251F, B258A-B258C | Stop lamps and external tail lamps short to ground | over 6.2 A |
| B258D | External tail lamp rear-left short to ground | over 7.8 A |
| C220B | Charge inlet actuator failure | the IGPM failed the OBC's charge-connector lock/unlock command twice; needs IG3 relay on, 9-16 V and a connected charge connector |

Every CAN time-out description ends with the same remedy: if the code persists after the fault is
fixed, run the diagnostic tool's "Erase Network Configuration" and clear it.

Manual quirks: the smart-key time-out `B168A` is a P-CAN message on the IGPM page and a B-CAN
message on the IBU page; `B1648` says "B-CAN bus is normal" but monitors a P-CAN message; the
descriptions of `C181C` and `C1829` print `B181C00` and `B182900`; the footnotes of `B2511` and
`B2513` say RH for the left lamp; the `C1651` description page is missing from the mirror.
These time-outs are the codes most likely to appear on a healthy car after a battery
disconnect: they name the module that was silent, not the module that is broken.

## 6. Smart key (0x7A5)

| Code | Meaning | Detection (as printed) |
|---|---|---|
| B1602 | CAN error | CAN high or low short to battery, ground, open, or shorted together; 0.3 s |
| B1603 | CAN bus off | high and low shorted together; 2 s. "CAN error: lamp on, CAN bus error: no lamp when hazard on" |
| B16A8 / B16A9 / B16AA | Interior antenna 1 / 2 / 3 failure | antenna open, ignition on, below 3 km/h |
| B16AC | Driver door handle antenna failure | same |
| B19AD | Passenger door handle antenna | same |

For the antenna codes the manual says the current DTC "is only displayed without storing in
memory", so a stored-DTC read may not return them. `B1602`/`B1603` history is erased
automatically after 30 ignition cycles.

## 7. TPMS (in the BCM, 0x7A0)

All codes are `-00`. Per-wheel families repeat for FL, FR, RL, RR with consecutive numbers
(`C1121`-`C1124`, `C1312`-`C1315`, `C1322`-`C1325`, `C1332`-`C1335`).

| Code | Meaning | Detection (as printed) | Lamp |
|---|---|---|---|
| C1121-C1124 | Sensor battery low | sensor battery below 2.2 V, 3 consecutive bursts, then 180 s | off |
| C1126 | TPMS ECU battery low | vehicle battery below 9 V for 2 s, above 25 km/h | on (60 s flashing, then steady) |
| C1127 | TPMS ECU battery high | 12 V battery above 16 V for 2 s, ignition on | on (60 s flashing, then steady) |
| C1212 | Vehicle speed signal | EMS speed invalid for 2.5 s, or wheel-acceleration plausibility fails; 30 s of bad ABS/ESC values | on |
| C1312-C1315 | Sensor RF channel failure | no RF signal for 540 s (EU) or 1140 s (NA) after exceeding 25 km/h | on |
| C1322-C1325 | Sensor over temperature | last temperature at or above 115 C (239 F) | off |
| C1332-C1335 | Sensor fault | sensor reports pressure, temperature or acceleration as 00h, or fails plausibility; 180 s | on |
| C1611 / C1625 / C1642 | CAN time-out EMS / ABS-ESC / brake message failure | listed in the index; not read in detail | |
| C1660 | Receiver RF circuit fail | no telegrams from all four sensors for 540 s (EU) / 1140 s (NA) | on |
| C1662 | Auto learning failure | under 10 min above 25 km/h and a sensor is still missing | off |
| C1663 | Auto location failure | new ID found while learning and fewer than three positions located | off |

## 8. Also in the index (not read beyond the list)

Driver and assist door modules: `B1602`, `B1603` only. Cluster: `B1000` (EOL not programmed),
and `B1001`, `B1002`, `B1003`, `B1005`, `B1008`, `B1010`, `B1020`, `B1040`, each with a `-52`
"missing" and `-55` "present, coding error" form for SCC, EPB, ABS, airbag, MDPS, ESC, TPMS and
LKAS/LDWS. Electric parking brake: 21 codes (`C1100`-`C1103`, `C1501`, `C1513`, `C1604`,
`C1614`-`C1628`, `C1642`, `C1646`, `C1656`, `C1710`, `C1812`, `C2220`, `C2224`, `C2416`,
`C2417`). Airbag: about 100 `B13xx`-`B27xx` codes, all `-00`. The cluster list is useful: it
shows which unit is *expected* by coding, so a `B1010-52` on the cluster means the cluster did not
hear the ESC.

## 9. Next steps

- Run `canair dtc --all` with the car awake and compare the printed suffixes with section 2.
- Read the remaining ESC pages (section 3 list) and the EPB, cluster, door-module and airbag pages
  (section 8) before adding them; and the EV MDPS pages, whose codes `eps.yaml` lacks (`C1101`,
  `C1109`, `C1262`, `C1603`, `C1614`, `C1711`, `C1802`, `C1804`, `C1817`).
- TPMS `C1611`, `C1625` and `C1642` are index-only too.
