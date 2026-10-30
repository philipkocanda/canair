---
# Paths are relative to the repository root; references/can/ is gitignored (local only).
title: "IGPM findings: hardware, gateway behaviour, diagnostics"
subject: >-
  The 2017 Ioniq Electric's IGPM (Integrated Gateway & Power control Module, UDS 0x770/0x778):
  hardware, signal routing between the five buses, network management, the 22BCxx status DIDs
  and the 2F BCxx actuators
created: 2026-10-30
updated: 2026-10-30
status: active
verification: >-
  Mostly sibling-vehicle (Kona, Niro, i30) and first-party document evidence; each claim is tagged
  confirmed, probable or speculative. The section 6.2 verdicts were cross-checked against this
  car's captures on 2026-10-30.
sections:
  - section: "6.2"
    status: integrated
    date: 2026-10-30
    note: >-
      Renames folded into signal names, notes and status_param readbacks. The bits marked "new"
      are not defined as signals yet.
    target: profiles/hyundai-ioniq-ae-ev/ecus/igpm.yaml
  - section: "7.2"
    status: integrated
    date: 2026-10-30
    note: IOControl labels folded in, with warnings on BC13 (deadlock) and BC44 (IG3 relay).
    target: profiles/hyundai-ioniq-ae-ev/ecus/igpm.yaml
  - section: "7.1"
    status: open
    note: Actuator-support map prediction not yet checked on the car.
  - section: "11"
    status: open
    note: No on-car action from the list has been run, apart from item 1.
relations:
  expands_on:
    - path: profiles/hyundai-ioniq-ae-ev/research/IONIQ_AVN_FW_FINDINGS.md
      note: Section 9's gateway frame translation, generalised in section 3.2.
    - path: profiles/hyundai-ioniq-ae-ev/ecus/igpm.yaml
      note: The research entry on the support bitmap (section 6.1) and on gateway frame forwarding.
  expanded_by:
    - path: profiles/hyundai-ioniq-ae-ev/research/FUNCTIONAL_ADDRESSING_FINDINGS.md
      note: How functional (0x7DF) requests are routed and handled, beyond section 4.
  invalidates:
    - path: profiles/hyundai-ioniq-ae-ev/references/kona/
      note: >-
        "BMU capture No HV connected.csv" is from a 2020 Ioniq EV pack, not a Kona (section 8).
  related:
    - path: plans/2026-10-29-support-bitmap-coverage.md
    - path: profiles/hyundai-ioniq-ae-ev/can_buses.yaml
sources:
  - name: igpm-research bundle
    kind: downloaded third-party material, mixed licences, never committed
    location: references/can/igpm-research/
  - name: projectgus Kona EV CAN logs
    kind: 2019/2021 Kona EV bus logs (CC0-1.0)
    url: https://github.com/projectgus/hyundai-kona-ev-can-logs
  - name: projectgus kona-ev-dbc and fakon
    kind: Kona EV DBC (MIT) and IGPM emulator firmware (MPL-2.0)
    url:
      - https://github.com/projectgus/kona-ev-dbc
      - https://github.com/projectgus/fakon
  - name: Pelland-Coupe EV_Hacking
    kind: isolated 2021 Kona EV IGPM bench sweeps; ScanDoc D-CAN session on the IGPM
    url: https://github.com/Pelland-Coupe/EV_Hacking
  - name: openinverter topic 1195 (konalogs1.zip)
    kind: Kona EV P-CAN logs
    url: https://openinverter.org/forum/viewtopic.php?t=1195
  - name: comma commaCarSegments
    kind: C-CAN segments (MIT)
    url: https://huggingface.co/datasets/commaai/commaCarSegments
  - name: OBDb test cases
    kind: raw 62BCxx responses across Hyundai/Kia models (CC BY-SA 4.0, facts only)
    url: https://github.com/OBDb
  - name: uhi22 Ioniq28Investigations
    kind: 2018 EU Ioniq 28 P-CAN logs
    url: https://github.com/uhi22/Ioniq28Investigations
    location: references/can/uhi22
  - name: Hyundai/Kia first-party documents
    kind: >-
      Ioniq AE, Niro DE and Genesis DH service-manual mirrors (hioniqae.com for the AE), owner's
      manuals, TSBs 24-01-034H, 20-01-018H, 21-01-006H and Kia ELE253, KSAE17-S0264
  - name: Patents
    kind: design intent only
    url:
      - https://patents.google.com/patent/KR101439050B1
      - https://patents.google.com/patent/KR101393539B1
      - https://patents.google.com/patent/KR101582531B1
---

# IGPM findings: hardware, gateway behaviour, diagnostics (for canair to triage)

This document collects what public sources, sibling-vehicle captures and a cross-check against
this profile's own captures say about the 2017 Ioniq Electric's IGPM (Integrated Gateway & Power
control Module, UDS `0x770`/`0x778`). Items are folded into `ecus/igpm.yaml` one at a time, with
canair's own tooling, once confirmed on the car, the same way as `IONIQ_AVN_FW_FINDINGS.md`.

**Status (2026-10-30).** The renames in section 6.2 and the IOControl labels in 7.2 are folded into
`ecus/igpm.yaml` (signal names, notes, `status_param` readbacks, research text). The bits marked
"new" are not yet defined as signals, and nothing from sections 7.1 and 11 that needs the car has
been run. The "Profile before" columns show the old names.

## Provenance and how much to trust it

Evidence classes, strongest first:

- **This car's captures** (`captures/`), re-analysed on 2026-10-30 with `canair align`. Where they
  disagree with anything below, the captures win.
- **Hyundai/Kia first-party documents**: the Ioniq AE service-manual mirror (hioniqae.com, which
  covers the hybrid and plug-in hybrid, not the EV), including two GDS screenshots of the IGPM's
  current-data and actuation-test lists; the Kia Niro DE and Genesis DH manual mirrors; EU and US
  owner's manuals; NHTSA-filed service bulletins (TSB 24-01-034H, 20-01-018H, 21-01-006H, Kia
  ELE253); a Hyundai-authored KSAE 2017 paper on gateway routing; Hyundai/Kia/Yura/Kyungshin
  patents.
- **Hardware photos**: a drive2.ru teardown of a 2017 Ioniq Electric IGPM (part G7510), parts
  listings with label close-ups, and projectgus's teardown of a Kona OS EV IGPM.
- **Sibling-vehicle captures and code**: projectgus's 2019/2021 Kona EV logs (CC0), bench code
  and IGPM emulator; Pelland-Coupe's bench work on an isolated 2021 Kona EV IGPM with all five
  buses wired out; OVMS, evDash and Battery-Emulator source; OBDb test cases (about 3,200 raw
  `62BCxx` responses across Hyundai/Kia models).
- **Commercial-tool name lists**: ScanDoc's IGPM data-stream and actuator names for a 2022 i30 PDE,
  reassembled from search-engine extracts (the site blocks automated access). The Ioniq's own GDS
  screenshots confirm their order wherever the two overlap.

Rule of thumb: Kona/Niro/i30 behaviour is a strong lead for this car (same supplier, same
generation, same DID scheme), never proof. Every claim below carries **confirmed** (first-party
document or this car's data), **probable** (consistent sibling evidence) or **speculative**.

The downloaded material is kept locally in the gitignored `references/can/igpm-research/` (see its
`README.md`). Its licences are mixed (CC BY-SA, GPL, proprietary images, unlicensed repos), so cite
facts from it and never commit it.

## 1. What the IGPM is

- **Hyundai's definition (confirmed).** The AE manual calls it "IGPM | Intergrated [sic] Gateway &
  Power control Module" and, in its body text, the SJB (Smart Junction Block): "Interior Junction
  Block + some functions of BCM. It controls loads with CAN communication and IPS", where "IPS
  stands for Intelligent Power Switch, which uses the semiconductor technology to replace the
  current role of fuse and relay". The Niro manual captions its network diagram "IGPM (SJB+CENTRAL
  GATEWAY)".
- **Three jobs in one box:** the central CAN gateway between all five buses; the interior fuse and
  relay box; and an I/O module that reads hard-wired switches (doors, belts, hood, tailgate, IGN1
  and IGN2, hazard, light stalk, key cylinder) and drives lamps through IPS/ARISU solid-state
  outputs and relays.
- **What it is not (confirmed).** Convenience logic and its timers live in the separate BCM
  (`0x7A0`): the 20 min rear-defogger timer, the 30 s power-window run-on, auto door lock above
  15 km/h, crash unlock, headlamp escort and welcome lamps, alarm arming. The SMK (`0x7A5`) owns the
  ACC/IG1/IG2 relays and the steering lock. The IGPM executes outputs the BCM requests over CAN and
  exposes its switch inputs as `CGW1`/`CGW2`/... broadcast frames.
- **Supplier: YURA Corporation (confirmed).** Two labelled G7510 units, the PCB silkscreens and the
  housing moulding all say YURA; the Kona OS IGPM is also YURA. A Hyundai Mobis blog credits Yura
  with developing the IGPM and its successor, the ICU (Integrated Central control Unit, which adds
  the BCM) used on E-GMP cars.

## 2. Hardware

### 2.1 Identity and part-number lineage

The label reads `LOT NO : G75101606170103  H/W : 1.00  S/W : 1.07`, `91950-G7510`,
`13-16617-012`, `V12`, `YURA Corporation QC`. The lot number is part suffix + YYMMDD + serial. Units
built in 2016 shipped with S/W 1.07; this car's identity reads SW `109`, so it has been updated or
was built later. The application software field `160205` reads like a build date (2016-02-05):
the 2021 Kona IGPM's same field is `200901`. **Probable.**

| Vehicle | IGPM part numbers (`91950-…`) | ROM IDs from TSBs |
|---|---|---|
| Ioniq AE EV, EU (this car) | G7510 (also listed: G7511, G7512) | not published; by pattern probably `G109` |
| Ioniq AE EV, US 2017–19 | G7530, G7531 | — |
| Ioniq AE EV facelift 2020–21 | G7532 | G201/G202 → G206 (event 704, TSB 24-01-034H) |
| Ioniq AE HEV/PHEV | G25xx (2017–19), G2056/G2106/G2156/G2166/G2320/G2370 (2020–22) | — |
| Kona OS EV | K4010…K4140, K4011…K4141 | K401–K405 → K406, K601/K602 → K603 (event 592) |
| Kona OS ICE | J9010…J9151 | — |
| Niro DE EV / PHEV | Q4010, Q4070 / G5050…G5122; Kia `91951-G58xx` for the 2022 PHEV | PHEV 200–204 → 205 (SA382); 2.06.0 → 2.06.1 etc. (ELE253) |
| Soul EV PS (an SJB at `0x771`, not an IGPM) | E4510, E4511 | — |

Hyundai ROM IDs are the part suffix's letter plus the software version digits (Kona K4xxx with
SW 401 is ROM K401), hence the `G109` guess. `22BCFF` reading `1090` fits a full version
`1.09.0`. **Speculative.**

### 2.2 Board construction (confirmed from photos)

- **Two double-sided multilayer boards in a soldered sandwich**, joined by current-carrying pins
  round the edge: a control board silkscreened `YURA AE IGPM C/B EV 1.00 PHEV 1.02 16.03.17` and a
  power board `YURA AE IGPM P/B EV 1.00 PHEV 1.02 16.03.17`. The EV and plug-in hybrid share the
  design. The Kona OS IGPM is a single board (`YURA OS IGPM 1.00 16.12.01`).
- Board-to-board headers `MAIN_E`, `MAIN_F` (40-pin) and `MAIN_G`; front faces `FRONT A`,
  `FRONT B`, `FLOOR C`, `FLOOR D`; a `FUSE SW` (the transport-mode fuse switch).
- An unpopulated through-hole footprint labelled **`JTAG2`** on both the Ioniq and Kona boards.

### 2.3 Chips

No chip marking is fully legible in any photo found (conformal coating and glare).

| Part | What is visible | Best identification | Confidence |
|---|---|---|---|
| MCU | LQFP-144, ~20 mm body, 0.5 mm pitch, centre of the control board; same package on the Kona | a 32-bit part with at least 5 CAN controllers; the JTAG footprint argues against S12/S12X (BDM debug). Candidates: MPC5606B/5607B (Bolero), RH850/F1L or F1K, Infineon XC23xx | speculative |
| U2, upper right | SOICW-32, Freescale logo, `MC3?97?` | fits the MC33972 multiple-switch-detection interface (22 switch inputs over SPI), a standard body-module part | speculative |
| U32, U19, U20 + U4, U1, beside `MAIN_G` | 3× SO-8 + 2× SO-14 in one row with common-mode chokes; the Kona board has the same row with matching designators | five CAN transceivers, one per bus. The 3+2 split fits three high-speed parts (P-, C-, D-CAN) and two fault-tolerant low-speed parts such as TJA1055 (B- and M-CAN; see 2.5) | probable (count), speculative (types) |
| Power board relays | Fujitsu FBR53ND12-Y-HW ×2 (silkscreen `P/WDW LH RLY`, `P/WDW RH RLY`), NEC/TOKIN EX2-2U1 twin (`DR LOCK/UNLOCK RLY`), EX1-2U1 ×2, Fujitsu FTR-P3CN012W1 (25 A) | the manual's six: leak-current autocut (LCAD), burglar alarm, power window RH, power window LH, door lock/unlock, tailgate | confirmed (parts), probable (which is which) |
| Lamp drivers | not identified on the photos | 6 IPS devices plus 2 four-channel "ARISU" drivers per the AE fuse tables; the Kona lists IPS0–IPS8. ARISU is Hyundai's name for a 4-channel IPS; its maker is unknown | confirmed (counts) |

Each turn lamp has its own output line and is diagnosed separately (teardown author, and the AE
manual's open-lamp detection "applied to the turn signal lamp 4 channel").

### 2.4 Power, protection and connectors (confirmed unless marked)

- **12 V feeds** (2017 EV owner's manual): B+1 40 A (brake switch, module 1, door lock, PDM1/2,
  fuse switch, LCAD), B+2 60 A (rear seat heater, ARISU 4CH, IPS 2CH), B+3 60 A (ARISU 4CH, IPS
  1CH/2CH), B+4 50 A (windows, tailgate, sunroof, amp, charger, OBC, front seat heaters, driver
  power seat). B+5 60 A feeds the engine-bay PCB block (EPCU1, horn, burglar horn, BMS, **Main
  relay**). The `IG3 2` fuse feeds the "IPS Control Module", i.e. the IGPM's output stage is itself
  a load on the IG3 rail.
- **Protection**: programmable current limit cuts any lamp load within 300 ms; open-lamp
  detection on the four turn-lamp channels; both log DTCs. **Fail-safe**: with a dead processor or
  both CAN lines failed, low beam and tail lamps are forced on when IGN2 and the low-beam switch
  are on.
- **Dark current** 200 µA asleep; the LCAD relay cuts the interior-lamp, memory and multimedia
  fuse feeds when the module powers down (timers in section 5).
- **Connectors**: I/P-A (~48 pins, front), I/P-B (4 blades, +12 V 60 A), I/P-C (~51) and I/P-D
  (~32, floor side), I/P-E (~38), I/P-F (40) and **I/P-G (16 pins, the CAN connector)**.
- **I/P-G pinout** (Kona OS EV, projectgus; the Ioniq rows come from a 2018 Ioniq Electric
  remote-start install guide):

| Pin | Kona OS EV | Ioniq AE EV |
|---|---|---|
| 1 / 2 | P-CAN L / H | P-CAN Low / High (confirmed) |
| 3 / 4 | D-CAN L / H | — |
| 5 / 6 | B-CAN L / H | B-CAN **High / Low** (the two sources disagree on polarity; measure first) |
| 7 / 8 | M-CAN H / L | — |
| 9 | charging-connector lock sensor | — |
| 10 | hazard switch | — |
| 11 | **IG3 relay control** | — |
| 12 / 13 | C-CAN L / H | — |
| 14 | **charging-connector lock drive** | — |
| 15 / 16 | lock relay / alarm horn relay | — |

  One T-harness on I/P-G therefore exposes all five buses; projectgus built exactly that from an
  Australian telematics T-cable.

### 2.5 Physical layer per bus

- P-, C- and D-CAN: high-speed CAN (ISO 11898-2), 500 kbit/s. **Confirmed.**
- B- and M-CAN: "B-CAN and M-CAN are connected to the low-speed CAN, C-CAN and P-CAN to the
  high-speed CAN while D-CAN is connected for inspection" (Genesis DH manual). **Confirmed for that
  model's architecture.** On the 2021 Kona, M-CAN is 100 kbit/s fault-tolerant CAN (ISO 11898-3):
  it has no termination resistors, idles at H ≈ 2.0 V and L ≈ 2.9 V rather than high-speed levels,
  and Pelland could read it only through a discrete transistor interface or a logic analyser.
  **Confirmed for the Kona M-CAN; probable for the Ioniq M-CAN; open for the Ioniq B-CAN** (Kona
  B-CAN was logged with ordinary interfaces, which receive fault-tolerant frames as long as both
  wires are intact).
- Consequence: tapping M-CAN (and probably B-CAN) needs an ISO 11898-3 transceiver (TJA1055 class)
  in listen-only mode; the WiCAN's high-speed transceiver is not suited.

## 3. The gateway: how frames cross buses ("ID conversion")

### 3.1 Five channels, OBD port behind the gateway

The IGPM terminates B-, P-, C-, M- and D-CAN as five separate physical channels (Kona pinout,
five transceivers on both boards). The OBD-II port's CAN pins are wired only to the gateway:
"진단 커넥터(OBD-Ⅱ 커넥터)의 CAN 통신 단자가 중앙게이트웨이에 직접 연결" ("the CAN terminals of
the OBD-II connector are connected directly to the central gateway", KR101439050B1, co-assigned
to junction-box supplier Kyungshin). **Confirmed.** On the AE hybrid, H-CAN (engine, DCT, BMS,
HPCU) is also wired straight to the OBD connector; the EV has no H-CAN.

The only AE network diagram found is the hybrid's: C-CAN AEB, MDPS, LDWS, ACU, AHB, BSD L/R, rear
camera; B-CAN BCM, WPC, SMK; M-CAN audio, amp, cluster, AVN; P-CAN VESS, DATC, OCS, AAF, water
pumps; with BCM and cluster also on C-CAN and SMK/ACU/AHB also on P-CAN. No EV diagram was found;
`can_buses.yaml` follows US EV wiring.

### 3.2 It routes signals, not frames (confirmed mechanism)

Hyundai engineers (KSAE 2017, KSAE17-S0264) define it: "게이트웨이는… 다른 망 송신 제어기로부터
1개의 CAN Signal을 받아서 다른 망 수신 메시지 1개의 CAN Signal로 보내는 역할… 신호를 주고 받는
관계를 Routing DB라 한다" — the gateway takes one CAN signal from a sender on one network and
sends it as one signal of a receiving message on another network; that mapping is the **Routing
DB**. The paper lists 13 error types for mismatches between the per-bus CAN DBs and the Routing
DB. The gateway is therefore built from Hyundai's per-bus message databases (each bus has its own
versioned DB; the IGPM reports a CAN DB version per bus and an "RDB version", and the head unit
asks for the B-CAN and C-CAN DB versions). There is no runtime "ID conversion protocol": the
mapping is compiled into the firmware from those databases.

What that produces on the wire:

| Kind | Examples | Evidence |
|---|---|---|
| Same ID, relayed as it arrives (P ↔ C) | `0x2B0` steering angle C→P (0.2–0.8 ms), `0x592` P→C (+0.3 ms), `0x593`, `0x220`, `0x507` on the Kona; `0x515`/`0x51B` C→P in the bench sweep | probable |
| New ID, same layout | head unit `0x54F` → P-CAN `0x567` (clock), `0x57B` → `0x55C` (departure schedule), `0x57A` → `0x588` (off-peak window) on the Ioniq; opendbc M-CAN → C-CAN pairs `GW_DDM_PE` `0x1EB` → `0x521`, `GW_IPM_PE_1` `0x16A` → `0x522`, `HU_MON_PE_01` `0x100` → `0x57F`, `TMU_GW_E_01` `0x043` → `0x53A` | confirmed (Ioniq, Kona), probable (opendbc) |
| Repacked | `HU_DATC_PE_00` `0x1C0` → `0x5D3` keeps only byte 4 bits 0–5, moved to byte 0; Kona C-CAN `0x5B0` (4-byte cluster odometer) → P-CAN `0x5D0` (8 bytes, 14 ms later); P `0x590` byte 4 → C `0x596` byte 4 | confirmed (Kona timing) |
| Composed | `CGW1`/`CGW2`/`CGW4`/`CGW5` combine the IGPM's own switch inputs with B-CAN states; MM-CAN `0x56A` mixes LDC, MCU, DATC, VCU and BMS signals for the head unit | confirmed |

Byte order changes with the bus: Hyundai's MM-CAN DBs are big-endian and the P/C-CAN DBs
little-endian, so multi-byte signals are swapped when they cross while single-byte fields keep
their bits. That is why the clock and schedule frames look copied. The Ioniq-specific evidence is
in the IGPM `research:` entry "gateway frame forwarding between MM-CAN, P-CAN and C-CAN" and
`IONIQ_AVN_FW_FINDINGS.md` §9; it is not repeated here.

The ID plan is corporate rather than IGPM-specific: the same converted IDs (`0x55C`, `0x567`,
`0x588`, `0x57F`) already exist on the 2014–19 Soul EV's C-CAN, where the cluster bridged MM-CAN.

### 3.3 Timing, timeouts and default values

- Gateway frames are periodic on the gateway's own schedule, at a fixed phase to `CGW1` (`0x541`).
  On the Ioniq P-CAN, 19 IDs hold that phase (see the research entry above). **Confirmed.**
- Hyundai's spec for parking-assist data through the gateway: "maximum gateway time is 50ms"
  (Niro, Tucson, i40, Azera, Stinger manuals). **Confirmed.**
- When a source goes quiet, the gateway keeps sending with default (zero) payloads rather than
  stopping. On a 2019 Kona `0x414`, `0x55C`, `0x567`, `0x588` and `0x5D0` dropped to zero together
  and recovered one by one (`0x567` after 0.7 s, `0x5D0` after 1.0 s, the rest after about 3 s); at
  power-down the 2021 Kona's `0x55C`/`0x55F`/`0x561`/`0x567`/`0x588` go to zeros and `0x5B3`
  reports power state Off while send periods stay unchanged. **Confirmed (Kona).**
- The gateway sets a DTC "only when it fails to receive the signal for more than 10 seconds with
  KEY ON" (Genesis DH). Receivers set their own: on a Kona bench the VCU, OBC and e-shifter log
  `U0146` (lost communication with gateway A) when `0x541`, `0x553`, `0x567`, `0x5D0` or `0x5DF`
  are missing. **Confirmed (Kona).**

### 3.4 One ID can name different frames on different buses

`0x541` and `0x553` differ between P-CAN and C-CAN; `0x57F` is the OBC's frame on P-CAN but the
gateway's on C-CAN; `0x588` is the off-peak window on P-CAN and a different gateway frame on
B-CAN; `0x55C` is the departure schedule on P-CAN and `GW_EV_P_02` to the head unit on MM-CAN;
`0x5D0` is the odometer on P-CAN and the cluster's `CLU_HU_P_00` on MM-CAN. A frame definition in
`signals/` is only valid for the bus it was captured on. **Confirmed.**

### 3.5 Frames an isolated IGPM emits by itself

Pelland powered a 2021 Kona EV IGPM with nothing else connected and logged every bus:

| Bus | IDs |
|---|---|
| P-CAN (29) | `07F 412 450 45D 45E 462 471 479 49A 4A9 4FE 50E 520 541 553 559 55C 55F 561 567 578 588 5B3 5B4 5BE 5D0 5D3 5DF 5EC` |
| C-CAN (29) | `07F 371 372 410 412 436 470 479 49F 4A9 4C9 4CB 50B 50E 520 53B 53F 541 544 54B 553 559 57F 595 596 5A6 5BE 5C1 5FF` |
| B-CAN (16) | `168 169 16A 188–18F 1F7 1FA 416 588 589` |
| M-CAN (41) | `126 12A 12B 131–135 157 158 15D 15E 161 164 166 167 169 16A 17A 17B 17D 17F 18A 18B 19D 19F 1AB 1BA 1BB 1BC 455 531 55C 55D 55E 56A–56F` |

Isolated, the frames whose content comes from elsewhere read zero (`0x567`, `0x55C`, `0x588`,
`0x5D0`, `0x5EC`, `0x559`) and `0x541` reads `00 44 00 00 00 08 00 00`. On the Ioniq the comma
openpilot route of a 2019 Ioniq Electric (28 kWh, US) shows `0x541`, `0x553`, `0x559`, `0x57F`,
`0x5B0`, `0x5BE`, `0x595`, `0x596`, `0x7F`, `0x50E`, `0x544`, `0x54B` and `0x5FF` on C-CAN, and
no `0x567`, `0x5D0`, `0x55C`, `0x588` or `0x5EC` (those are P-CAN-only). **Confirmed (both).**

## 4. Diagnostics through the gateway

- **Relayed frame by frame, IDs and bytes unchanged, no ISO-TP reassembly.** First, consecutive and
  flow-control frames are each forwarded on their own, about 0.3 ms later (13 ms worst case
  towards a loaded P-CAN). On a 2019 Kona the OBD side carried 13,331 frames, all at IDs ≥ `0x700`,
  against 716,581 on P-CAN in the same period. **Confirmed (Kona); matches this car's
  observation that the OBD port sees no broadcast traffic.**
- **An explicit per-bus ID table, both directions, not a range.** Pelland swept every 11-bit ID into
  each bus of the isolated Kona IGPM (one frame per ID, so a missing entry may be a dropped frame):

| Route | Request IDs (tester → bus) | Response IDs (bus → tester) |
|---|---|---|
| D → P | `610 612 620 62C 630 632 634 636 697 6AC 6CA 6E0 703 733 736 7B3 7B6 7C3 7C7 7D1 7D2 7DF 7E2–7E6 7F1` | `611 613 628 62D 631 633 635 637 698 6AD 6CB 6E1 70B 73B 73E 7BB 7BE 7CB 7CF 7D9 7DA 7EA–7EE 7F9` |
| D → C | `680–684 6C0–6C4 6EC 6FD 755 776 795 796 7B7 7C4 7C6 7D0 7D4 7DF` | `685–689 6C5–6C9 6ED–6FC 6FE 75D 77E 79D 79E 7BF 7CC 7CE 7D8 7DC` |
| D → B | `700 701 71E 74C 7A0 7A1 7DF` | `708 70A 71D 72D 7A8 7AD` |
| P ↔ C, P ↔ B | P→C `7C4 7D0 7D4`, P→B `700` | C→P `7CC 7D8 7DC`, B→P `7A8` |

  The functional request `0x7DF` goes to P-, C- and B-CAN, and the IGPM answers it itself on
  `0x778`. The `0x6xx` pairs with a +1 or +5 response offset are unexplained (supplier or
  end-of-line addresses). **Confirmed (Kona).** How the ECUs handle a functional request, and
  the evidence that `0x7DF` also reaches MM-CAN, are in `FUNCTIONAL_ADDRESSING_FINDINGS.md`.
- **Routes are per bus.** On the Kona, requests to `0x720`, `0x737`, `0x760`, `0x7E0` and `0x7E1`
  are not forwarded to P-CAN, and P-CAN responses from `0x71D` and `0x74E` never reach the OBD port,
  while B-CAN `0x71D` does. A module can live on an internal bus and still be invisible to
  `canair discover`. **Confirmed (Kona).**
- **Some routes start on P-CAN.** Requests to the C-CAN camera, radar and steering (`0x7C4`,
  `0x7D0`, `0x7D4`) and to B-CAN (`0x700`) are accepted from P-CAN, and BCM and C-CAN responses are
  copied to P-CAN as well as to the OBD port. That fits an in-car diagnostic client on P-CAN: the
  Kona head unit sits on P-CAN, and the US AE head unit's telematics runs a whole-vehicle DTC poll
  for BlueLink. **Speculative.**
- **Wake.** OBD traffic wakes the IGPM; B-CAN modules (BCM, SMK) then answer because they wake on
  bus activity, while P-CAN ECUs answer only with IG3 on. Matches this profile's `vehicle_states`.
- **No security gateway on this generation.** Hyundai/Kia fitted a security gateway from 2018 on
  newer models (Sonata DN8, Elantra CN7, Tucson NX4, IONIQ 5, EV6): an unauthenticated tester gets
  NRC `0x33` to `2F` there (a 2020 Sonata IGPM answered `7F 2F 33`). Ioniq AE, Kona OS and Niro DE
  are not on the list, and `2F BCxx` works without service `0x27`. **Confirmed.**
- **Flashing disrupts routing.** Kia ELE253 tells technicians to clear the EPS, ESC and TPMS codes
  set while the IGPM is reflashed. **Confirmed.**

## 5. Network management, sleep and wake

- **OSEK direct network management (a logical token ring) on the low-speed buses only.**
  - M-CAN: opendbc `hyundai_2015_mcan.dbc` defines `NM_<node>` at `0x440 + node`; **`NM_CGW` is
    `0x455`** (node `0x15`), with `Destination` in byte 0, `NMCommandCode` in byte 1 bits 0–2 and
    `NMSleepFlag` in bits 4–5. A Kona M-CAN shows `0x440`, `0x44D` and `0x455`. **Confirmed.**
  - B-CAN: on 2019 and 2021 Konas the ring is `0x400` → `0x401` → `0x416` → `0x41E` (payload
    `NN 02 00 00 00 00 FF FF`, byte 0 the next node, `0x12` in byte 1 once sleep is indicated),
    90–100 ms per hop and about 375 ms per round. `0x400` is the BCM, `0x401` the SMK and **`0x416`
    the IGPM** (the isolated IGPM sends `0x416`). **Confirmed (Kona).**
  - P- and C-CAN carry no NM frames: an awake controller is recognised by its application frames
    (KR101439050B1, and the Kona logs). **Confirmed.**
- **Wake and sleep ordering.** Remote wake-up messages routed into another network keep it awake;
  they must flow one way, which yields a sleep order such as B-CAN → M-CAN → P-CAN → C-CAN
  (KR101439050B1). A body-bus wake also wakes the other buses (prior art in KR101393539B1).
  **Confirmed (patents; Hyundai's own architecture description).**
- **IGPM sleep (AE manual, confirmed).** It sleeps with "IG OFF, constant input switch, CAN network
  doesn't activate" and wakes on "Any switch inputs, CAN network activates, KEY ON, IGN ON". It
  powers down (LCAD relay off, 200 µA) 20 min after sleeping; 35–65 s after a remote-key lock with
  the alarm armed ("Waiting time of other unit: 30 to 60s + SJB sleep counts 5s"); 5 min with the
  fuse switch off (pre-delivery transport mode). For dark-current measurement GDS sends a forced
  sleep message to every network and the gateway sleeps too.
- **Parked HV-battery monitoring.** Kona campaign 960 and the Ioniq facelift's event 704 reflashed
  the IGPM with BMS, MCU and VCU "for high voltage battery monitoring logic". Hyundai Australia's
  description, as quoted on insideevsforum: "Before : Check the condition of the vehicle once 24
  hours after starting off … After : Monitor the vehicle condition for 2 hours in a row … (Total of
  2.5 days)". projectgus's Kona bench also shows the IGPM sending the clock (`0x567`). Together with
  this car's IG3-relay bit being set during the parked 12 V top-up (section 6), the IGPM is the
  likely owner of the parked wake timer behind the 24 h 12 V top-up. **Speculative.**

## 6. The status DIDs `22BC01`–`22BC07`

### 6.1 The support bitmap

The four bytes before the data (B05–B07, B09) are a per-vehicle map of which data bits exist:
bit 7−k of map byte n marks bit k of data byte n (B10–B13), as the existing IGPM research entry
already states. OBDb's ~3,200 responses confirm it physically: an LHD Veloster (one rear door, on
the right) has `3F` in BC03's first map byte (left rear bits absent), an RHD Veloster `CF` (right
rear absent), and EPB cars lack the parking-brake-switch bit that hand-brake cars have. Of 9,409
data bits seen set, 96.9 % lie inside their map. **Confirmed.** This car's BC03 map
`FD EE 3C 73` equals a 2021 Kona EV's in OBDb's data.

The official names below come from the AE manual's GDS current-data screenshots (which list the
items in DID bit order and confirm BC03 and BC04 bytes 0–2) and ScanDoc's i30 PDE list for the
rest (BC05–BC07). "×" marks a bit the map says this car does not have.

### 6.2 Bit map for this car

| DID bit | Official name | Profile before 2026-10-30 | Verdict |
|---|---|---|---|
| BC03 B10:0–5, 7 | rear LH door open sw, rear LH door actuator position sw, rear RH door open sw, rear RH actuator position sw, assist door open sw, driver door open sw, tailgate door sw | DOOR_RL_OPEN, DOOR_LOCK_RL, DOOR_RR_OPEN, DOOR_LOCK_RR, DOOR_PAS_OPEN, DOOR_DRV_OPEN, TRUNK_OPEN | agree |
| BC03 B10:6 × | parking brake sw | never set | correct: EPB car |
| BC03 B11:0–2 | hood sw, driver belt sw, assist belt sw | HOOD_OPEN, SEATBELT_FL, SEATBELT_FR | agree |
| BC03 B11:4 | brake fluid level sw | — | new (never set) |
| BC03 B11:5 / B11:6 | **IGN1 switch / IGN2 switch** | ACC2_IGN_ON / IGN_STATUS_MIRROR | rename; they should differ while START is held (IGN2 drops, IGN1 stays) |
| BC03 B12:2 | **tail lamp output (interior)** ("AV tail") | DRL | **mislabel**: equals B12:3 in 92 of 93 lamp-on rows |
| BC03 B12:3 / 4 / 5 | tail lamp output (exterior), head lamp high output, head lamp low output | TAIL_LIGHTS, HIGH_BEAM, LOW_BEAM | agree |
| BC03 B13:1 | **rear defogger relay** | REAR_DEFOGGER_RELAY (candidate) | name confirmed |
| BC03 B13:2 / 3 / 6 / 7 | burglar alarm horn relay, start inhibitor relay, tailgate release relay, central door lock sw | — | new |
| BC04 B10:0 | **power window enable relay** | ACC2_IGN_ON_BC04 | rename: at the one closely sampled power-off it was still on at the first poll after IGN off and off at the next, 40 s later, doors shut (the BCM's 30 s run-on, which opening a door cuts short) |
| BC04 B10:2 / 3 | assist / driver door actuator position sw | DOOR_LOCK_FR / DOOR_LOCK_FL | agree |
| BC04 B10:5 / 7 | assist / driver door key-cylinder unlock sw | — | new |
| BC04 B11:2–7 | hazard sw, central unlock sw, **deadlock relay**, driver door unlock relay, door unlock relay, door lock relay | — | new (relays pulse, so polls rarely catch them) |
| BC04 B12:0 | HID option | — | new (reads 0) |
| BC04 B12:1–3 | rear right / left / centre belt sw | SEATBELT_RR / RL / RM | agree |
| BC04 B12:5 | **dedicated DRL output** | VEHICLE_NOT_IN_P_WARNING | **mislabel**: 1 only with ignition on and low beam off; 1 in all 7 such rows in D, 0 in all 43 in P, 0 in all 87 with low beam on |
| BC04 B13:0 / 1 | RHD option, **fuse switch** | — / constant 1 | new: LHD; transport fuse switch in its normal position |
| BC04 B13:2 / 4 | trunk-lid outside handle sw, high beam relay | — | new (high beams were never co-polled with BC04) |
| BC04 B13:6 | **interior lamp autocut relay (LCAD)** | UNLOCKED_OR_IGN_ON | rename: fits the late fall after an armed lock (35–65 s); the 18 s pulse 5 min after locking would be a wake-up |
| BC05 B10:1 / 2 / 3 | key-cylinder lock sw, mirror fold sw, mirror unfold sw | — | new |
| BC05 B10:4 / 5 / 6 / 7 | **LF / RF / LR / RR turn output** | TURN_LAMP_LEFT / RIGHT / LEFT_2 / RIGHT_2 | rename: front and rear lamps are separate outputs, not duplicates |
| BC05 B11:3 / 7 | **luggage compartment lamp**, occupant position detection sensor | TRUNK_INDICATOR, PASSENGER_SEAT_OCCUPIED | rename / agree |
| BC05 B12:2 | low beam switch | IGPM_BC05_B12_2 | agree with OBDb `HL_SW` |
| BC06 B10:0 | **stop (pedal) switch** | part of BRAKE_LIGHT | new: the pedal is bit 0, not BRAKE_PEDAL_BITS (B10:2) |
| BC06 B10:2 / 3 / 5 | stop lamp L, stop lamp R, high-mounted stop lamp output | BRAKE_PEDAL_BITS is B10:2 | rename BRAKE_PEDAL_BITS to the left stop lamp |
| BC06 B11 (field from bit 0) | turn signal switch status (voltage divided) | LEFT/RIGHT_TURN_SIGNAL | agree: this is the stalk, BC05 B10 the lamps |
| BC07 B10:4 | tailgate full-open sw | TRUNK_OPEN_BC07 | agree (equals TRUNK_OPEN) |
| BC07 B11:1 | rear fog lamp output | — | new: not the defogger (stayed 0 in the 2026-07-30 defogger window) |
| BC07 B11:7 / B12:0 | charge inlet control lock / unlock | CHARGE_PORT_LOCK / _INV | agree; B12:0 is an output of its own, not just the complement |
| BC07 B12:1 / 2 | front / **rear charge door lamp output** | — / IGPM_BC07_B12_2 | rename B12:2: set at every recorded plug-in, just before the port locks (2026-08-02 09:52:59, 08-08 11:41:23, 16:31:23, 17:53:19), plus a few episodes with the port unlocked; clears within 1–3 min where sampled |
| BC07 B12:3 | **Ignition 3 relay** | UNKNOWN_B12_3 | rename: the IGPM's own IG3 relay, on in READY and the parked 12 V top-up, off while charging with the ignition off |
| BC07 B12:4 | an option input (ScanDoc: head lamp LED option) | constant 1 | static option |
| BC07 B13:0 / 1 / 4 / 5 | LF / RF / LR / RR turn signal lamp | constant 0 | probably lamp-failure status: stays 0 while the BC05 outputs flash |

The 22BC06 capture with B10 = `0x02` (2026-04-15, untimed) sets a bit outside the map, so it is
almost certainly corrupt. The three `0x2C` captures (stop lamps lit, pedal switch off) were taken
at 43–77 km/h on 2026-08-09; regeneration-triggered brake lamps are a plausible cause, unverified.

### 6.3 What the maps say about this car

Maps are a free option fingerprint: EPB (no parking-brake switch), no front windscreen heater,
no rear wiper (the Ioniq has none), deadlock relay present (EU), no manual-gearbox reverse input,
no static bending lamps or headlamp washers, LHD, mirror folding switch read by the IGPM but the
fold relays elsewhere (the BCM's `2F B05B/B05C`), and the reverse lamps not driven by the IGPM.

### 6.4 Other DIDs

- **22BC01** `40 00 00 00 | 00 02`: map bit for B10:1, plus a trailing value byte (`02` here,
  `01` on an e-Niro). The GDS list's first item is "One Touch Turn Signal: 3 Flashing", and the
  IGPM owns that user option (GDS "User Option → One Touch Turn Signal: 3/5/7 Flashing/DISABLE",
  a data write). On a Kona, ScanDoc's IGPM "Configuration → Edit configuration" page sends
  `22 BC01` and nothing else before being cancelled (Pelland's D-CAN capture
  `ScanDoc session 30-01-25`, around frame 11420), so BC01 is the record that page edits.
  **Probable**; test: change the setting in the cluster and re-read BC01.
- **22BCFD**: constant on this car. All 1,407 reads from 2026-10-22 to 2026-10-30 return
  `FF FF FF FF 0C 0E 16 2B 3F 26 36 00 00 00 00 00 00`, in READY, CHARGING, PARKED and SLEEP, so it
  is stored data, not a live value. It is probably not the battery discharge alarm history: on
  the Kona that function reads `000B`/`000C` (next item). The Kona and e-Niro owner's manuals have
  the driver warning that most likely goes with it ("Battery discharging due to external
  electrical devices"); the Ioniq's 2018 German and 2019 EU manuals do not, so this car may not
  have the function at all.
  That leaves "Erase Network Configuration"'s data as the main candidate, by elimination only.
  **Speculative.**
- **22BCFF** reads ASCII `1090`, the software version (`109`) plus one more digit. The CCM's
  BCFF (`1033`, software version `103`) follows the same pattern.
- **`000A`–`000C` (Kona only so far)**: a 2021 Kona IGPM answers `22 000A`
  (`0C 00 00 00 | 30 00 00 00`), `000B` (`F0 00 00 00` plus 8 zero bytes) and `000C`
  (`E0 00 00 00 | 00 00 00 00`), the same map-plus-data shape; `000D` returns NRC `0x31`. ScanDoc
  reads all three in its IGPM data list, and its "battery discharge test" utility reads `000B` and
  `000C` and then VCU `21 02` (same capture, around frame 11340). On the Kona, `000B`/`000C`
  therefore hold the battery discharge data. **Probable.** This car's sweeps covered
  `0100–01FF`, `B000–B0FF`, `BC00–BCFF`, `C000–C0FF` and `E000–E0FF`, but not `0000–00FF`.
- **Identity**: the Kona answers F100 `07`, F101 `200901`, F102 `28`, F103 `27`, F104 `27` (F105
  NRC `0x31`), F187 `91953K4780`, F18B `2021-05-26`, F193 `100`, F195 `401`. ScanDoc's
  identification page reads exactly F100–F105, F187, F18B, F193 and F195, and names the CAN DB
  versions for B-, M-, C- and P-CAN, the RDB version, part number, manufacture date, hardware
  version and software number. That makes F100–F104 the four CAN DB versions plus the RDB version,
  in an order the capture does not show. **Probable**; read F100–F105 here.

## 7. IOControl `2F BCxx`

### 7.1 Actuator-support bitmap

ScanDoc opens the IGPM's actuator menu with `2F BC00 02`, `2F BC20 02` and `2F BC40 02` (`BC60`
answers NRC `0x31`). Each returns a 4-byte map of supported actuators `BCx1`–`BC(x+20)` in the
same mirrored order as the DID maps. **Probable**, from two cars:

- Soul EV SJB `BC00` → `F8 B1 FC 30` = BC01–05, 09, 0B, 0C, 10–16, 1B, 1C, matching the Soul
  sheet's working commands.
- 2021 Kona `BC00` → `DF D1 FD 30` = BC01, 02, 04–0A, 0C, 10–16, 18, 1B, 1C; `BC20` → `00 38 00 12`
  = BC2B, 2C, 2D, 3C, 3F; `BC40` → `90 00 60 00` = BC41, 44, 52, 53.

This car's discovery scan predicts `FB D1 FD 30` (`DB` without BC03), `08 38 00 02` and
`F0 00 00 00`. Caution: control option `02` is freezeCurrentState; send it only to the `BCx0`
group DIDs, never to a real actuator DID.

### 7.2 Names

The AE manual's GDS actuation list (confirmed) runs: Head Lamp Low, Head Lamp High, Tail Lamp
(Exterior), Tail Lamp (Interior), Burglar Horn Relay, Tailgate/Trunk Release Relay, Start Inhibit
Relay (Option), Rear Defogger Relay, Front Deicer Relay, Door Lock Relay, Door Unlock Relay, Driver
Door Unlock Relay (Option), Power Window Relay, Turn LH, Turn RH, Dedicated DRL Output (Option),
Auto Cut Relay, …. With ScanDoc's numbering for the rest:

| DID | Official name | Label before 2026-10-30 | Note |
|---|---|---|---|
| BC05 | tail lamp output (interior) | Blue instrument LEDs | the illumination circuit; rename |
| BC06 | backup lamps | — | not on this car (no IGPM reverse-lamp output) |
| BC07 | **burglar horn relay** | Horn | the alarm horn, not the main horn |
| BC0A | start inhibit relay (option) | Welcome / approach light | explains "no visible result" |
| BC0B / BC0D | rear wiper relay / front deicer relay | — | not on this car |
| BC12 | driver door unlock relay (option) | Heated mirror? | the "click-clack" |
| BC13 | **deadlock relay** | Unknown (conditional) | ⚠ deadlocked doors cannot be opened from inside |
| BC14 | power window relay | Unknown (only in sleep) | refused with NRC `0x22` in ACC, perhaps because the relay is already commanded on then |
| BC17, BC19, BC1A | flasher sound relay, static bending L/R | — | not on this car |
| BC18 | dedicated DRL output | DRL lights | status bit is BC04 B12:5, not BC03 B12:2 |
| BC1B | **auto cut relay (LCAD)** | Unknown | matches "single click, dome light goes off" |
| BC25 | high beam relay (ScanDoc item 33 with the BC2B anchor) | Unknown | BC04 B13:4 shows this car has one; unverified |
| BC42 / BC43 | front / rear charge door lamp | Unknown | the rear lamp sits inside the closed port door, so "no visible effect" fits |
| BC44 | **Ignition 3 relay** | Unknown | ⚠ NRC `0x22` in ACC; no effect in READY because IG3 is already on |

Unchanged and confirmed: BC01–BC04, BC08–BC09, BC0C, BC10/BC11 (lock/unlock), BC15/BC16, BC1C,
BC2B–BC2D, BC3F/BC41.

**Remote locking caveat (OVMS, six reports across Kona, e-Niro and Ioniq 38 kWh).** `BC11` unlocks
without disarming the alarm, so opening a door sounds it; `BC10` often only clicks and does not
arm; switching the SMK's ACC relay to wake modules turns the head unit on. The factory remote path
is different: the telematics unit sends `TMU_GW_E_01` (MM-CAN `0x043`: `C_ReqDrLock`,
`C_ReqDrUnlock`, `C_ReqHazard`, `C_ReqHorn`, `C_ReqEngineOperate`), which the gateway re-emits as
`0x53A` (`CF_Gway_TeleReq*`, received by the BCM). Whether the EU IGPM, on a car that never had
BlueLink, still routes it is unknown. **Probable (DBC), speculative (EU car).**

## 8. Charging: IGPM, OBC and the charge-port lock

From the 2019 Kona EV captures and bench (projectgus, CC0), unless marked:

- **The OBC's own frames.** Alone on a bench bus, the Kona OBC sends only `0x47C`, `0x4EE`,
  `0x57F`, `0x581` and `0x58E` (10 Hz) plus diagnostics on `0x7ED`. `0x581`: AC current bytes 0–1
  LE × 0.01 A, AC voltage bytes 2–3 × 0.1 V, power bytes 6–7 × 3.90625 W (7.23 A, 228.4 V, 1688 W
  observed). `0x58E`: byte 1 `0x0C` while charging, byte 2 × 0.5 A the current the OBC will accept,
  byte 4 the port-lock and EVSE flags (PortLocked, PortUnlockReq, PortLockReq, EVSEDisconnected,
  EVSEDetected, PortLockSetting), and an alarm byte reading `0x76` when the port will not lock.
  The Ioniq 28 P-CAN carries `0x4EE`, `0x57F`, `0x581` and `0x58E` but no `0x47C`; that the Ioniq
  OBC sends them is not proven.
- **The lock handshake.** The OBC requests lock or unlock in `0x58E`; the IGPM drives the actuator
  (I/P-G pin 14, sensor on pin 9; Fakon drives it 600 ms with a 3 s pause) and reports the result
  in `0x5EC` bit 0. Without that answer the OBC faults after about 30 s. This is the broadcast
  twin of BC07 B11:7/B12:0 and of `2F BC3F`/`2F BC41`.
- **What the OBC needs from the gateway.** DTCs if `0x567`, `0x5D0` or `0x5DF` are missing; it also
  classifies `0x414`, `0x5D3` and `0x5EC` as gateway messages. `0x414` byte 0 is the AC charging
  current setting (0 Max, 1 Reduced, 2 Minimum) on the 2019 car; the 2021 car sends the head
  unit's `0x562` in a short burst instead. `0x588` reads all zeros off, `58 1C 00` with IG3 on but
  not ready (or charging) and `FC FF 03` ready.
- **Who switches IG3.** The Kona OBC can drive the IG3 relay coil itself ("Can be driven either by
  Fakon or by the OBC"), turning the car "half-on" when a charger is connected. On plug-in only OBC
  frames appear for 0.69 s, then VCU, IGPM (+0.73 s), BMS, CCM and climate controller frames
  (mikeselectricstuff's Kona logs). Yura's KR101582531B1 describes the design: parallel IG3 relays
  in the junction box, driven by several controllers. On this car BC07 B12:3 (the IGPM's IG3 relay)
  is 0 while charging with the ignition off, consistent with the OBC's relay supplying IG3 then.
- **Ioniq specifics (uhi22).** Unlocking the doors during AC charging makes the OBC drop to 0 A for
  10 s and release the connector without a C → B pilot transition. The car wakes every 24 h and, if
  the 12 V battery is low, charges it from HV for about 20 min. The BMS needs `0x553` and the OBC's
  `0x57F` to run (Battery-Emulator sends `0x553` = `04 00 80 00 00 00 80 00`); a CCS session needs
  `0x57F` too.
- **OBC part numbers.** Ioniq AE EV 36400-0E150 (this car); Kona EV 36400-0E070 (2019–21),
  36400-0E071 (2022–23); Niro EV 36400-0E195.
- **Correction to `references/kona/`.** "BMU capture No HV connected.csv" is not from a Kona: it was
  posted in openinverter topic 2584 (post 45544, 2022-09-14) from a 2020 Ioniq EV pack
  (37501-G7650), BMU only, with no gateway IDs.

## 9. Software, coding and security

- **Updates.** Reflashed through dealer "ECU Upgrade" events (GDS via the VCI); event files are
  `.NGM`, images `.git`/`.gwm`/`.gwmz`. GDS error codes of the form AAAA-SS-RRRR show the usual
  UDS sequence (session `10`, security `27`, erase/dependency routines `31`, `34`/`36` transfer);
  routine IDs and security levels are not public. Manual-mode passwords (e.g. `7512` for the
  facelift IGPM) are GDS menu passwords, not seed/key data. **Confirmed (codes generic, not
  IGPM-specific).**
- **No variant coding.** G-scan lists only "User Option", "Erase Network Configuration" and
  "Battery Discharge Alarm History" for the IGPM. Options come from hard-wired inputs (the manual's
  "HID head lamp option" input; GDS shows `(Option)` items) and the gateway learns which modules
  are present ("Erase Network Configuration" clears that after a module change). ScanDoc's Kona
  session matches: its IGPM "Edit configuration" page reads only BC01 (the One Touch Turn Signal
  option) and its battery discharge utility reads `000B`/`000C` (section 6.4). The list covers
  IGPMs of every model, so a function on it may be missing on this car: its owner's manuals have
  no battery discharge warning (section 6.4). **Probable.**
- **User settings.** The IGPM stores One Touch Turn Signal; the BCM stores door, lock, escort and
  welcome options. Settings travel as `CLU14` (`0x515`, cluster → gateway requests),
  `CGW_USM1` (`0x410`, current values back), `HU_GW_E_00/01` (`0x526`/`0x527`) and `GW_HU_E_00/01`
  (`0x524`/`0x525`). `0x515` crosses C → P unchanged in the Kona sweep. The 2017 EU car's charge-
  cable lock is a dash button (AUTO/LOCK), not a menu setting. **Confirmed (manual, DBC).**
- **Security access** levels and algorithm: not public. `2F` works in session `10 03` without
  `27` on this generation (OVMS, community sheets, this car). Routine control answers sub-function
  `03` with NRC `0x12`, so canair's safe routine probe cannot enumerate it.
- **Software architecture**: unknown. A KISTI report has Kyungshin and Yura starting AUTOSAR 4.1
  gateway and junction-box stacks in 2017 for 32-bit ECUs commercialised after 2018, which makes a
  2016-designed IGPM likely pre-AUTOSAR. **Speculative.**
- **IGPM DTCs seen on Kona IGPMs** (descriptions not public, except B1602 CAN error and B1603 CAN
  bus-off on earlier Hyundai body modules): with modules missing on a bench, C1614, C1628, C1687,
  C167C, C1806, C1808, C181C, C1829, C182D, B1604, B164C, B168A, B2211, B2804 (all status `08`); on
  a crashed car B1603, B2801, C167C, B2211, C1829.

## 10. Capture and data inventory

| Source | Car | Buses | Notes | Licence |
|---|---|---|---|---|
| github.com/projectgus/hyundai-kona-ev-can-logs | 2019 and 2021 Kona EV (AU) | P + one of B/C/D, tapped at I/P-G | 57 logs, 2.2 GB: AC charging at 6/8/10 A, scheduled charge, DC fast charge, charge door open/closed, power cycles, fuse pulls, OBC alone on a bench | CC0-1.0 |
| github.com/Pelland-Coupe/EV_Hacking | 2021 Kona EV IGPM on a bench | all five, isolated | ID sweeps (the routing table in section 4), per-bus emitted IDs, M-CAN logic-analyser decode, ScanDoc sessions on D-CAN | none stated |
| openinverter topic 1195, `konalogs1.zip` (mikeselectricstuff) | Kona EV (UK) | P-CAN | AC charge start 32 A, failed DC, 12 V top-up, door close → sleep, wake by unlock | none stated |
| comma openpilot CI routes | 2019 Ioniq Electric (28 kWh, US) `610ebb9faaad6b43/2020-06-13--15-28-36`; Kona EV; Niro EV | C-CAN at the camera | `commadataci.blob.core.windows.net/openpilotci/<dongle>/<route>/<seg>/rlog.bz2` | not stated |
| huggingface.co/datasets/commaai/commaCarSegments | Kona EV (505 segments), Ioniq EV 2020 (288), Niro EV (1661), Ioniq PHEV/HEV | C-CAN | no 2019 Ioniq EV segments | MIT (dataset card) |
| github.com/projectgus/kona-ev-dbc, …/fakon | Kona EV | P-CAN | DBC with gateway-dependency comments; IGPM emulator firmware | MIT; MPL-2.0 |
| OBDb test cases | ~60 Hyundai/Kia/Genesis models | OBD | raw `62BCxx` responses by model year | CC BY-SA 4.0 (facts only here) |
| Korea University HCRL datasets | YF Sonata (OBD), Genesis G80 (B-CAN, M-CAN) | various | the G80 B-CAN shows `0x7A0`/`0x7A8` diagnostics on B-CAN itself | per dataset |
| uhi22/Ioniq28Investigations (already in `references/can/uhi22`) | 2018 Ioniq 28 (EU) | P-CAN | the only Ioniq gateway traffic on file | see repo |

No simultaneous MM-CAN + P-CAN capture exists for any of these cars, and no IGPM firmware image is
public.

## 11. Suggested next actions

1. **Done 2026-10-30: renames and IOControl labels** (with warnings on BC13 and BC44). Still to do:
   define the "new" bits of section 6.2 as signals once a narrated capture exercises them.
2. **Cheap read-only probes**: `canair scan range IGPM --range 0000-00FF` (Kona has `000A`–`000C`)
   and identity F100–F105 (probably the CAN DB and RDB versions).
3. **Actuator-support maps** (`2F BC00 02`, `2F BC20 02`, `2F BC40 02`) to check section 7.1's
   prediction. This is IOControl, so only parked, and only after deciding it is acceptable under
   canair's safety rules.
4. **One narrated capture** (`canair monitor IGPM:22BC03,22BC04,22BC05,22BC06,22BC07 --save
   --keep-all --interval 1`), each step marked with `s`:
   - hold START with the brake off and on (IGN1 vs IGN2 at B11:5/B11:6);
   - READY in P then D with the lamps off (DRL at BC04 B12:5);
   - IGN off with doors shut, then again opening the driver door (power-window relay, BC04 B10:0);
   - fob lock with alarm and wait 2 min (LCAD, BC04 B13:6);
   - open and close the charge door (BC07 B12:2);
   - rear fog on (BC07 B11:1), high beams on (BC04 B13:4);
   - change One Touch Turn Signal in the cluster (BC01's last byte);
   - hazard switch, key-cylinder unlock (BC04 B11:2, B10:7).
5. **Tap I/P-G** for the internal buses: listen-only, an ISO 11898-3 transceiver for M-CAN (and
   B-CAN until proven high-speed). First targets: the B-CAN and M-CAN NM rings (expect the IGPM at
   `0x416` and `0x455`), the remaining 19 gateway-clocked P-CAN IDs, and a simultaneous MM-CAN +
   P-CAN capture for the research entry on frame forwarding.
6. **Diagnostic routing on this car**: send one harmless request (e.g. `3E 00`) to each 11-bit ID
   on the OBD port with a tap on P-, C- and B-CAN to build the Ioniq's own version of section 4's
   table.
