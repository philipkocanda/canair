# TODOs

- Captures:
  - [ ] ESC PIDs while driving
  - [ ] VCU/MCU while driving (canreq --multi "query VCU 2101" "query MCU 2102" --monitor 1 --keep-all --save --wican vpn)
  - [ ] More HVAC PIDs in various states
  - [ ] Drive-mode button (Eco/Normal/Sport) + regen paddle (0-3) toggle test while monitoring VCU 2101 (see "Drive mode + regen" section below)
  - [x] Fix BMS cell voltages (CELL_03_VOLTAGE, CELL_11_VOLTAGE, CELL_19_VOLTAGE, CELL_27_VOLTAGE are hitting PCI bytes!)
  - [x] Fix incorrect VCU VEHICLE_SPEED param, cluster value vs decoded PID value
- Canreq:
  - [x] display byte index(es) for each mapped PID
  - [ ] simplify usage by making the "multi" mode the default, removing the complexity of maintaining both single and multi modes
- Various:
  - [x] In query-captures, implement a "step through" feature so I can use the arrow keys (l/r) to step through the captures and see the decoded values for each capture, while also still showing the capture diff (against previous capture) underneath, just only for the current capture and not for all at the time. This is useful for debugging and understanding how the values change over time.
  - [x] unified query syntax (rather than having --ecu and --pid flags)
  - [x] The project should clarify that this is intended for UDS and OBD-II diagnostic queries, not for handling raw CAN bus traffic (that may be a future follow-up). We should note that "ioniq-can" is not an ideal name, given it is primarily UDS/KWP2000 focused.
  - [ ] Web UI for viewing and querying captures (similar to https://github.com/deanlee/openpilot-cabana used by https://www.projectgus.com/2023/10/kona-can-decoding/, but focused on UDS/KWP2000)
  - [ ] Store captures in CAN log files in the "gvret/SavvyCAN" CSV format, as supported by SavvyCAN? Or DBC? Not sure what is best here.

## PID coverage gaps (audit 2026-07-20)

Systematic pass over all ECU PIDs in `pids/`, cross-referenced against the
longest captured payload per PID. Byte indices are WiCAN Bnn (PCI + SID + DID
echo excluded). **Regenerate the authoritative, up-to-date list any time with
`python3 pid-coverage.py`** (`--bitfields`, `--unmapped`, `--no-capture`,
`--json`). The curated notes below add priority/context; the tool output is the
source of truth for exact byte lists.

### A. Incomplete bitfields (status registers with undecoded bits) — highest value

Bytes where only some bits are mapped (no full-byte debug param covering the rest):

- [ ] **IGPM 22BC03** — `B11` maps bits 1,2,5 (SEATBELT_FL/FR, ACC2_IGN_ON); bits 0,3,4,6,7 undecoded. `B12` maps bits 2,3,4,5 (DRL, TAIL, HIGH_BEAM, LOW_BEAM); bits 0,1,6,7 undecoded — likely turn signals / hazards / fog. Diff while toggling indicators.
- [ ] **IGPM 22BC04** — `B10` maps bits 2,3 (DOOR_LOCK_FL/FR); bits 0,1,4-7 undecoded. `B5/B6/B7/B9/B11/B12` fully unmapped.
- [ ] **IGPM 22BC07** — `B11` bit7 (CHARGE_PORT_LOCK), `B12` bits 0,3 (CHARGE_PORT_LOCK_INV, ACC2_RELATED); remaining bits of B11/B12 undecoded. `B9/B10` unmapped.
- [ ] **BCM 22B004** — `B11` only bit6 mapped (BCM_DOORS_LOCKED); other bits + `B5,B6,B7,B9,B10,B12,B13` unmapped.
- [ ] **BCM 22B00E** — `B10` only bit5 mapped (CHARGE_PORT_OPEN); other bits + `B5,B6,B7,B9,B11,B12` unmapped.
- [ ] **VCU 2101** — `B26` maps bit3 (CAR_READY), bit5 (PARK_BRAKE); bits 0,1,2,4,6,7 undecoded. Note both are unverified — confirm offsets for the Ioniq (see 2026-04-14 memo re: B26 offset).

### B. Partially-decoded PIDs with unmapped data bytes

Multi-parameter PIDs that decode well but still have unmapped data bytes worth chasing:

- [ ] **BMS 2101** (63B, 32p) — unmapped `B4,B5,B6,B7,B10,B11,B12,B13,B28,B61`.
- [ ] **BMS 2105** (48B, 19p) — unmapped `B9,B10,B11,B12,B13,B29,B41,B43-B47` (SOH / temps block tail).
- [ ] **BCM 22C00B** (27B, 8p) — TPMS: unmapped `B5,B6,B7,B9,B12,B13,B17,B18,B21,B22,B26` (tyre temp region already at -50 offset).
- [ ] **HVAC 220100** (42B, 12p) — many unmapped: `B9,B10,B14,B18,B19,B21,B23,B25-B31,B34,B35,B39,B41`.
- [ ] **HVAC 2201A0** (62B, 8p, 3 verified) — largely unmapped tail (`B26-B61` mostly). Explore in multiple HVAC states.
- [ ] **LDC 2101** (48B, 9p, 6 verified) — unmapped `B14,B20-B23,B27-B39,B41-B44` (PID 2102 still fully undecoded, see docs/TODO.md).

### C. Barely-decoded PIDs (single param, mostly unmapped) — need decoding effort

- [ ] **MCU 2102** (62B, 4p, 1 verified) — motor temps/RPM/torque suspected; almost entirely unmapped. Capture while driving.
- [ ] **ESC 22C101** (48B, 1p) — only 1 param; needs driving-state captures + decode.
- [ ] **GSA 220100** (27B, 1p) — gear/lever hall sensors (B19/B21/B22 drift noted); decode.
- [ ] **AAF 2180** (27B, 4p) / **AAF 2181** (27B, 1p) — Active Air Flaps controller; exposes thermal readings (ambient/heater/heatsink/compressor), mostly unmapped.
- [ ] **CLU 22B001 / 22B002 / 22B003** — odometer decoded on B002; B001/B003 return live-but-undecoded bytes (drive-mode/regen candidate — see section below).
- [ ] **SKM 22B002** (139B), **22B00B** (55B), **22B009** (34B), **22B003/22B006/22B007/22B008/22B00A/22B005** — all 1 param, essentially undecoded. Large SKM datasets worth a decode pass.
- [ ] **VCU 2102** (27B, 2p) — mostly unmapped; pair with MCU 2102 while driving.

### D. Parameters defined but NO captures yet (capture first)

- [ ] **EPS** 220101, 220102
- [ ] **ESC** 22C102
- [ ] **MFC** 220100, 220101, 220102
- [ ] **SCC** 220100, 220101, 220102, 220103, 220105
- [ ] **WPC** 220100 (2 params)

## MCU / VCU 2102 decode — Phase 2 (warm synced drive)

Phase 1 (2026-07-20) mined the existing 332 MCU / 1347 VCU captures and added
**unverified** candidates (all `verified: false`; speculative ones `enabled: false`):

- **MCU 2102**: `MCU_MOTOR_TORQUE_2` `[S14:S15]/100`, `MCU_PHASE_CURRENT_RMS` `[B17:B18]/10`
  (in addition to existing RPM ✓ / torque / temp1 / temp2). Calibration block B27–B33
  documented as static (resolver + U/V phase offsets), not polled.
- **VCU 2102**: `VCU_TORQUE_REQUEST` `[S12:S13]` (0 at park, ±31k driving; meaning/scale unknown).

Phase 2 needs the car. Goal: confirm scales/meanings and fill the remaining gaps
(re-run `python3 pid-coverage.py MCU 2102` / `VCU 2102` to see current gaps).

- [ ] **Capture** — dense, back-to-back, minimal skew, and long enough to **warm the inverter**:
  `canreq --multi "query VCU 2101" "query VCU 2102" "query MCU 2102" --monitor 1 --keep-all --save --wican vpn`
  Drive profile: hard launches, lift-off/regen, braking, a reverse segment, sustained cruise
  to heat components; plus one charging capture.
- [ ] **MCU torque** — determine which of `[S12:S13]` / `[S14:S15]` is command vs estimated;
  correlate against pedal (VCU 2101 `ACCEL_PEDAL_DEPTH`) + dRPM incl. regen (signed must go negative).
- [ ] **MCU phase current** — confirm `[B17:B18]` scale (/10→A guess); ~0 at park, rises with |torque|.
- [ ] **MCU temps** — B20/B21 raw °C vs `value−40`; separate motor / inverter / heat-sink with a warm
  drive. Check B22/B25 (52..248 raw, track RPM) as a possible 3rd temp or a different quantity.
- [ ] **MCU B52** — identify the highly-dynamic byte (card=180) near the tail.
- [ ] **VCU torque/power** — pin down `VCU_TORQUE_REQUEST` `[S12:S13]` (torque request vs available torque
  vs power) by joining VCU 2101 speed/pedal + MCU 2102 torque.
- [ ] **VCU EWP** — look for electric water pump speed / target RPM (Soul VMCU field; 0..190 byte that
  spins up under load/charge; candidates B22/B25/B26). EPCU coolant loop exists despite air-cooled battery.
- [ ] **VCU temps** — B20/B27 (~51 raw cold ≈ 11°C if `value−40`) confirm with warm drive.

## canreq.py view

  VCU (0x7E2)
    2101  (127 entries)
      VEHICLE_SPEED                0 km/h  ✓
      VEHICLE_SPEED_ALT            0 km/h  ✓
      DEBUG_DRIVE_MODE_FLAGS       33      ✓
      DRIVE_MODE_P                 1       ✓
      DRIVE_MODE_R                 0       ✓
      DRIVE_MODE_N                 0       ✓
      DRIVE_MODE_D                 0       ✓
      DEBUG_VEHICLE_STATE_FLAGS    90      ?
      VEHICLE_STATE_BRAKE_LAMP     0       ✓
      VEHICLE_STATE_NOT_BRAKING    1       ✓
      VEHICLE_STATE_START_KEY      0       ?
      VEHICLE_STATE_EV_READY       1       ✓
      VEHICLE_STATE_VCU_READY      1       ✓
      VEHICLE_STATE_MAIN_RELAY_ON  1       ?
      VEHICLE_STATE_POWER_ENABLE   1       ?
      VEHICLE_STATE_LDC_ENABLED    1       ?
      DEBUG_MCU_STATE_FLAGS        109     ✓
      CAR_READY                    0       ?
      PARK_BRAKE                   1       ?
      ACCEL_PEDAL_DEPTH            17 %    ?

## CLI commands inconsistencies

```sh
# query-captures.py now takes a positional QUERY (mini-language), consistent
# with decode.py. --diff/--step are view modifiers on the query.
uv run ./query-captures.py VCU 2101           # list captures
uv run ./query-captures.py VCU 2101 --diff    # byte-diff view
uv run ./query-captures.py "VCU:2101,2102" --step

canreq --multi "query VCU" --monitor 2 --keep-unique --wican vpn --save
canreq --multi "query MCU" "query VCU" "query LDC" --monitor 7 --keep-unique --save --wican vpn
```

Note: canreq is an alias on my local machine. This is used in many examples and is not very helpful. Let's standardize the examples in the README or make a wrapping CLI that can be installed and made globally available. The wrapping CLI could be called "ioniq-can" and would call the underlying scripts with the correct arguments.

## Drive mode (Eco/Normal/Sport) + regen level — investigation

Status: NOT located in any polled PID as of 2026-07-20.

### Findings (from July 19 drive analysis)
- VCU 2101 `DEBUG_DRIVE_MODE_FLAGS` (B10) = constant `0x20` base + gear nibble only:
  P=0x21, R=0x22, N=0x24, D=0x28. Bits 0-3 = P/R/N/D; **bit5 always 1; bits 4/6/7 always 0**
  across all 577 samples -> no dynamic eco/regen captured.
- No 4-state (regen 0-3) byte in VCU 2101, MCU 2101, or MCU 2102. Low-cardinality bytes only
  track brake / park-vs-drive frame state.
- Kia Soul VMCU sheet maps the drive-mode byte as: bit4="B", bit5=Eco (INVERTED: (bit-1)*-1),
  bit6=Charge Timer (inverted). By that, our constant bit5=1 = "Eco off / Normal" the whole drive
  -- but unconfirmed (never toggled) and the Soul sheet has NO regen-level field either.

### Where it might be
- **CLU cluster 0x7C6** (dash shows mode + regen): only `22B002` (odo) decoded; `22B001`/`22B003`
  return live-but-undecoded bytes -> prime candidate.
- **SWRC-L/R 0x7A1/0x7A2** (steering-wheel controls): regen paddles are steering-wheel mounted ->
  strongest lead for regen level. Not yet scanned for paddle state.
- **GSA 0x7B6** (gear shift assembly): SCANNED 2026-07-20 — responds ONLY to `220100`. Bytes
  B19/B21/B22 drifted ~30 between two P-gear captures (likely lever hall sensors or temp, not gear).
  Holds gear/lever data; drive-mode button is centre-console (could route here or via BCM/IGPM),
  regen paddles are NOT here (steering wheel -> SWRC).
- Unscanned VCU/MCU `21 03-FF` or a 2017-specific 22xxxx PID. (Ioniq 5 `22E006` drive mode /
  `22E007` regen return NRC 0x12 here.)
- May not be exposed via OBD reads at all (only broadcast on internal CAN).

### Controlled capture plan (needs car, safe while parked in Ready)
1. Monitor VCU 2101 while pressing Drive Mode button Eco->Normal->Sport->Eco; watch B10 (e.g. D
   0x28->0x08?) or any bit flip -> confirms/locates the mode bit.
2. Monitor VCU 2101 + MCU 2101/2102 while cycling regen paddles 0->1->2->3; watch for a byte stepping 0-3.
3. If nothing there: scan CLU 0x7C6 `22B000-22B0FF` in each mode/regen state and diff to find the byte.
   Also scan SWRC-L/R (0x7A1/0x7A2) while pulling the regen paddles.
4. Also monitor GSA 0x7B6 `220100` while shifting P/R/N/D (decode lever sensors B19/B21/B22) and while
   pressing the drive-mode button.
5. Broaden: `21 03-FF` scan on VCU (0x7E2) and MCU (0x7E3).
