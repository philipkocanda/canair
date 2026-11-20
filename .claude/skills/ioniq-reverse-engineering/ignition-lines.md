# Ignition lines and power states

Scope: ACC, IGN1, IGN2, START and IG3 on this car, and how they map to canair's vehicle states.

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

