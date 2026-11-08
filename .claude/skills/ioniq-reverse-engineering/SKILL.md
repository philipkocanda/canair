---
name: ioniq-reverse-engineering
description: Facts about the bundled 2017 Hyundai Ioniq Electric that no other skill holds — ignition lines and how they map to canair's vehicle states, per-ECU gotchas, factory module names versus the profile's ECUs, the research findings documents, wake behaviour. Load this when working on the Ioniq reverse engineering project, together with reverse-engineer-signal.
---

# Ioniq CAN Reverse Engineering Skill

**This skill is the Ioniq-specific *context*; `reverse-engineer-signal` is the generic
*procedure*: load both for Ioniq reverse-engineering work.** This one carries the vehicle's facts
(ignition lines, per-ECU gotchas, factory module names). `reverse-engineer-signal` is the
vehicle-agnostic decode lifecycle, byte-index and expression syntax, and signal-analysis
reasoning. Decoding, adding, fixing or verifying any PID/DID on the Ioniq needs both. For a
different car, load that generic skill plus that car's profile; this skill is then an example of
a finished profile. Other skills own the rest:

- `wican-hardware-and-protocol`: the dongle, transports, the device API, sleep and power.
- `contributing-profiles`, `profile-variants`, `pid-cleanup`: profile data and its upkeep.
- `AGENTS.md`, `docs/reference/cli/` and `canair <cmd> --help`: commands, flags, config.

## Safety (non-negotiable)

The rules are in `AGENTS.md` (section "The car") and in the `reverse-engineer-signal` skill
(section "Safety first"). In short:

- Never touch the car, the dongle or the bus without the user's explicit "yes" for that exact
  action. Propose the exact commands, then wait.
- Never open a programming session (`10 02`, `10 05`) or write firmware. This car cannot be
  un-bricked.
- One canair connection at a time. Never reboot the WiCAN without asking.

## Backlog and research documents

Backlog: `canair research --summary` (per-ECU `research:` sections), plus `canair research
--gaps` for actuator DID families never read with `22` (PSM `B4xx`, SKM `B1xx`, DDM `B1xx`,
VESS `F0xx` as of 2026-10-30).

Findings documents in `profiles/hyundai-ioniq-ae-ev/research/` collect what firmware,
third-party logs and Hyundai/Kia's own specs say, for triage before a capture session. Each opens with YAML
frontmatter: dates, status, per-section status (integrated, superseded, invalidated, open),
relations to the other documents and plans, and sources:

- `IGPM_FINDINGS.md` — the gateway (IGPM): hardware, gateway behaviour, diagnostics.
- `IONIQ_AVN_FW_FINDINGS.md` — what the head-unit/telematics firmware says about other ECUs.
- `FUNCTIONAL_ADDRESSING_FINDINGS.md` — `0x7DF` broadcast requests: routing, ECU behaviour,
  timing.
- `HKMC_UDS_SPEC_FINDINGS.md` — Hyundai/Kia's UDS spec (ES95486): session aliases, power-state
  rules behind NRC 0x22, identification DIDs, record-DID families and support maps, IOControl
  and routine id ranges, NRC and failure-type tables. The DID conventions for scanning are in the
  `reverse-engineer-signal` skill's `hyundai-kia-uds-conventions.md`.
- `VCU_FIRMWARE_FINDINGS.md`, `OBC_FIRMWARE_FINDINGS.md` — static reads of the VCU and OBC flash
  images: service sets and session matrices, every record byte's source, CAN frame layouts, and
  (OBC section 9) requests that must never be sent.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh, Premium trim, NL market).
  Not the HEV/PHEV. The 2017 model year (2016–2019) has a different CAN layout
  and fewer PIDs than the 2020+ facelift. The 28 kWh is air-cooled (fan); the
  38 kWh has a liquid-cooled battery, different BMS, and more cell-voltage PIDs. The profile
  describes both as variants (`variants.yaml`: `28kwh`, `38kwh`); this car is the 28 kWh.
- **Dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`.
- **CAN:** ISO 15765-4 (11-bit, 500 kbps) — ELM327 protocol `6`.
- **AT init** (`profile.yaml`): `ATSP6;ATS0;ATAL;ATST96;`, plus
  `response_timeout_ms: 614` (ECUs respond slowly; the first request after idle
  can time out — retry once; see the Gotchas in `profile.yaml`).

## Ignition lines (what "ACC", "IGN1", "IGN2", "IG3" mean on this car)

These lines come from US-market factory wiring. The profile's car is an EU model (Premium, full
options), and the two differ in places (the EU head unit is on MM-CAN only, for example), so treat
fuse names, connector pins and bus membership here as hypotheses until seen on the EU car; the
captures in the profile are the evidence.

The start button (SSB) is driven by the Smart Key Module (SKM, which self-identifies as "SMK"),
which switches three relays; IG3 is a separate EV-only supply. Connector pins below are on the
SKM unless noted.

| Line | Relay in the E/R junction block (fed by) | Hot in | SKM output / feedback input | Feeds |
|---|---|---|---|---|
| ACC | Button Start #1 (ACC), E48 (IG1 40 A) | ACC or ON | B12 / C18 "ACC/ON input" | Audio, navigation, USB and wireless charger, BCM, SKM, SBW switch (IGPM MODULE2) |
| IGN1 | Button Start #2 (IG1), E43 (IG1 40 A) | ON or START | B2 / C9 "ON/START input" | Cluster, SRS, MDPS, door modules, stop-lamp switch, EPCU pin 46 "ON/START input" (IGPM PDM3, CLUSTER, MODULE3-5, IG1 25 A) |
| IGN2 | Button Start #3 (IG2), E46 (IG2 40 A) | ON only | B1 / C8 "ON input" | Wipers, washer, A/C control module, seat warmers and ventilation, heated steering, BCM and SKM (IGPM MODULE6, MODULE7) |
| START | none (IGPM START 7.5 A fuse) | START only | C10 "Start relay output" / C16 feedback | EPCU pin 42 "Start Switch" and the front-wiper FRT2 fuse. Not a starter on an EV |
| IG3 | PCB-block Main relay, ICM-box IG3 #1/#2 relays, a quick-charge relay | HV system awake or charging | not the SKM | EPCU pin 47, OBC, BMS, LDC, cooling fans, blower, e-compressor signal, cluster, AVN, CCM |

Factory labels are inconsistent: E46 is called both "#3 (IG2)" and "#2 (IG2)". The E-numbers and
the SKM wiring above are consistent.

**Driver-visible states** (owner's manual names): OFF (locked or unlocked, no terminal), ACC
(ACC), ON (ACC + IGN1 + IGN2), START (IGN1 + START, momentary), ON with the system running (ACC +
IGN1 + IGN2, READY). Without the brake each press steps OFF, ACC, ON, OFF; with the brake in P or N
one press goes to start. The button LED is amber in ACC and blue in ON (some factory text says
green; the car shows blue). ACC and IGN2 drop out during START; only IGN1 stays on.

**IG3:** IG3 #1 is closed while the HV controller operates, except during charging, and its coil is
driven by the OBC. IG3 #2 is meant for AC charging and another relay for DC, but the #2 coil line
runs to the head unit's "IG3 relay control" pins, so who closes it is unresolved. The PCB-block Main
relay is commanded by the IGPM's IPS control module. "IGN3" in pin tables means IG3.

**Mapping to canair names.** The `OFF` state is no terminal on, with the car awake or asleep
(`SLEEP` implies it); the `ACC` state is the ACC relay alone; `ON` (IGPM `IGN1_SWITCH`,
the GDS "IGN1 Switch") is ACC + IGN1 + IGN2 (called `ACC2` until 2026-10-30); `READY` also needs
IG3. The `IG3` state (`VCU_IG3_ON == 1`) is the rail
itself: READY, CHARGING and TOPUP imply it, and it alone names the plugged-in idle window after a
charge. VCU 2101 `VCU_IG3_ON` (B11:4) is the IG3 rail: the IG3-fed ECUs (OBC, BMS, MCU, AAF, HVAC)
answer only while it is 1. `VCU_IGN_ON` (B11:0|B11:1) is the
ignition as the VCU sees it, and `VCU_POWER_STATE` (B25) the VCU's operating mode. Which of IG3
#1/#2/quick-charge is closed is not decoded: IGPM 22BC07 `IG3_RELAY_OUTPUT` (B12:3) and OBC 2101
`OBC_PROXIMITY_V` are the candidates (hypotheses; see the IGPM research entry);
`IG3_RELAY_OUTPUT` reads 1 whenever IGN1 is on, so it is not a READY signal.

**Telling the power states apart** (narrated cycles, 2026-11-03):

| Signal | OFF | ACC | ON | READY |
|---|---|---|---|---|
| BCM 22B004 `BCM_IGN_STATE_B004` (B10) | 0 | 2 | 14 | 14 |
| SKM 22B008 `SKM_POWER_MODE` (B13) | 0 | 2 | 7 | 9 |
| SKM 22B007 `SSB_LED_ACC` / `SSB_LED_ON` (B11:3 / B11:4) | 0 / 0 | 1 / 0 | 0 / 1 | 0 / 0 |
| BCM 22B003 `BCM_12V_BATTERY` (LDC runs only in READY or charging) | ~12.3 V | ~12.3 V | ~12.3 V | ~14.6 V |

The BCM answers in every state, so the `ACC` and `ON` predicates read it first; no BCM bit
separates ON from READY, so the BCM `READY` reading is the 12 V level with the charge port
unlocked (`vehicle_states.yaml` has the numbers). During START (brake + press, under 1.7 s) IGPM
`IGN2_SWITCH` drops while `IGN1_SWITCH` stays on. SKM 22B006 B17/B18/B19 are the ACC, IGN1 and
IGN2 relay outputs. SKM IOControl B108 to B10B are the ACC, IGN1, IGN2 and START outputs.

## AutoPID staleness

In AutoPID mode the latest values are at `http://<ip>/autopid_data` (cached, so they may be stale
if the car is off). **AutoPID stops polling when the 12 V battery hits the `sleep_volt`
threshold** while the WiCAN may stay WiFi-reachable. So `/autopid_data` and MQTT can go stale
(lights reported "on" after parking) even though the device answers. A direct `canair read` still
works and values self-correct on the next poll. The device API and the sleep logic are in
`wican-hardware-and-protocol`.

## Ioniq-specific gotchas

- **Passive sniffing sees almost nothing.** The OBD-II port is gateway-isolated: only diagnostic
  request/response reaches it. The raw-CAN value here is pipelined UDS, not `canair sniff`.
- **`--session` and `--wake`.** `--session` (extended session `10 03`) is required for IGPM
  `2FBCxx`; its `22BCxx` reads need none. `--wake` rouses a deep-sleeping ECU first. IGPM (0x770)
  and BCM (0x7A0) wake from CAN activity alone. BMS and MCU answer only while the IG3 rail is on
  (ignition ON, charging, the 12 V top-up), and the VCU a few minutes beyond, so ACC alone is not
  enough.
- **`canair io --poll` can actuate hardware.** The IOControl TUI's background status poll
  (opt-in) sends each DID's release (`2F{DID}00`, returnControlToECU) every 3 s. On relay and
  solenoid DIDs this can re-assert defaults and cause an audible click. A KWP2000 `0x30` entry is
  polled, and switched on, only once it declares a `release:`. None of the Ioniq's KWP ECUs has
  one yet (see the OBC `iocontrol_scan` research entry).

## ECU status

Live status: `canair ecu` (registry, per-ECU stats, identity confidence) and `canair research`
(open backlog). The registry has 30 ECUs. Decode findings are recorded in each `ecus/<ecu>.yaml`;
the table below carries only gotchas that a signal list does not show.

| ECU | TX ID | Gotcha |
|-----|-------|--------|
| IGPM | 0x770 | IOControl map BC00–BCFF (lights, horn, signals, DRL, brake, trunk, locks, charge-cable lock). Status reads (BC03/04/06) need no session; `2FBCxx` does. |
| BCM | 0x7A0 | Part 95400-G7470 (Ioniq AE BEV only). TPMS and body state share it: `22C00B`. IOControl B000–B072 scanned; B061 answers NRC 0x31 (charge door not supported here). |
| VCU | 0x7E2 | Speed in 2101 is MPH (`((S20*256)+B19)*1.609344/100`), about 3.6 % above true speed. 2102 B18 is the coolant temperature (`B18-100`), not the 12 V rail: that is `VCU_LDC_OUTPUT_VOLTAGE` (`((B20<<8)|B19)/1000`). Motor RPM and torque live in the MCU. The 12 V DC-DC converter (LDC) is integrated here (ecu_id `AEVLDC53`, "VLDC"); there is no separate LDC ECU. |
| MCU | 0x7E3 | Inverter (36600-0E250). Motor RPM = 2102 `(S10<<8)|B09` (signed, verified). Most other 2101/2102 signals are unverified. |
| OBC | 0x7E5 | On-board charger only; no DC-DC control. Every 2101 byte is mapped from the firmware. Never send `99`, or `30 06/07` / `31 00/01` in session 0x90 (OBC_FIRMWARE_FINDINGS.md §9). |
| SKM | 0x7A5 | ACC relay IOControl is accepted, but **the relay only closes with the fob nearby**. `skm-wake` verifies via IGPM BC03. The powertrain stays dead without the fob. |
| PSM, VESS | 0x7A3, 0x736 | No readable live data. Both are driven by IOControl only (the seat motors run until OFF is sent). |

### Factory module names and the profile's ECUs

Factory wiring (US-market; see the caveat in the ignition-lines section) names the car's modules
differently from the profile. A 0x700-0x7EF address sweep
(2026-04-17, ignition on) found 30 responders, and every one has an `ecus/` file, so a factory
module with no row below either does not answer at a 0x7xx address or was not awake.

| Factory module | Profile ECU | Confidence | Basis |
|---|---|---|---|
| IGPM (gateway, power switches) | IGPM | certain | self-identifies; bridges every bus except the private CLIMATE-CAN |
| SMK, DATC, ACU, AAF, CCM, VESS, AMP, AVN (MM-CAN only on the EU car), WPC, DDM, ADM, BSD LH/RH, rear camera | SKM, HVAC, SRS, AAF, CCM, VESS, AMP, AVN, WPC, DDM, ADM, BSD-L/R, RCAM | certain | same name or alias, same bus |
| EPCU (VCU + MCU + LDC) | VCU, MCU | certain | VCU identifies as "VCU with the LDC integrated" |
| IBAU (integrated brake unit) | ESC | probable | ESC's alias is AHB (active hydraulic boost); the IBAU sits on C-CAN and P-CAN |
| MDPS, EPB unit | EPS, EPB | certain | same name, C-CAN |
| Lane keeping assist unit | MFC | probable | the camera; C-CAN; only the LKAS unit is a camera |
| AEB radar / smart cruise unit | SCC | probable | the one front radar; C-CAN |
| Driver IMS (memory seat) module | PSM | probable | the car's only power-seat module; B-CAN; address next to the door modules |
| Shift button module or SCU (parking actuator control) | GSA | unresolved | both are on P-CAN; GSA is a Kostal shift assembly; which one answers is unknown |
| Cluster, BCM | CLU, BCM | certain | name, buses |
| EWP (EPCU/motor coolant pump) | EWP (0x746) | probable | Kona bench log: the pump alone answers on 0x74E and sends P-CAN 0x570; was Unknown-746 |
| ODS, DBL, charge lamp, compressor, PTC heater, the other of SBW switch / SCU | none | no responder found | not in the sweep |

`Unknown-7C0` matches no factory module yet. The HVAC unit also has a
private link to the compressor and PTC heater (`CLIMATE-CAN` in `can_buses.yaml`, marked
`private: true`): neither the IGPM nor the OBD-II port is on it.

## Part numbers

### BCM 95400-G7470 is Ioniq AE Electric (BEV) only (2026-04-19)

The `Gx` code in Hyundai part numbers identifies the model; `G7` is exclusive to
the Ioniq AE Electric (BEV) — G2=Ioniq HEV, G5=Kia Niro, G6=Picanto all use a
different BCM. So IOControl DIDs that accept but do nothing are **not** explained
by cross-vehicle sharing; more likely **market variants** (the same G7 BCM ships
across EU/US/KR with features like heated handles or auto-dimming mirrors not
fitted on every trim).

## Key references

- [wican-fw repo](https://github.com/meatpiHQ/wican-fw) ·
  [WiCAN docs](https://meatpihq.github.io/wican-fw/)
- local `profiles/hyundai-ioniq-ae-ev/references/` spreadsheets (Kia Soul and Kia e-niro
  XLSX-format spreadsheets pulled from Google Docs):
  - [Kia Niro 64 kWh sheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  - [Kia Soul EV sheet](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI)
  - Both contain a lot of raw CAN logs and PID decoding information, SKM commands and ECU mappings. 
- **User-facing docs:** `docs/` (task-first, for humans) — `docs/profiles/hyundai-ioniq-ae-ev.md`
  documents the bundled profile. Keep it and the README current when you change a
  user-facing capability; see the README↔`docs/` policy in `AGENTS.md`.
