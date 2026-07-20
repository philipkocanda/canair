# TODOs

- Captures:
  - [ ] Fix BMS cell voltages (CELL_03_VOLTAGE, CELL_11_VOLTAGE, CELL_19_VOLTAGE, CELL_27_VOLTAGE are hitting PCI bytes!)
  - [ ] ESC PIDs while driving
  - [ ] VCU/MCU while driving (canreq --multi "query VCU 2101" "query MCU 2102" --monitor 1 --keep-all --save --wican vpn)
  - [ ] More HVAC PIDs in various states
  - [ ] Drive-mode button (Eco/Normal/Sport) + regen paddle (0-3) toggle test while monitoring VCU 2101 (see "Drive mode + regen" section below)
- [x] Fix incorrect VCU VEHICLE_SPEED param, cluster value vs decoded PID value
- Canreq:
  - [x] display byte index(es) for each mapped PID
  - [ ] simplify usage by making the "multi" mode the default, removing the complexity of maintaining both single and multi modes
- Various:
  - [x] In query-captures, implement a "step through" feature so I can use the arrow keys (l/r) to step through the captures and see the decoded values for each capture, while also still showing the capture diff (against previous capture) underneath, just only for the current capture and not for all at the time. This is useful for debugging and understanding how the values change over time.
  - [x] unified query syntax (rather than having --ecu and --pid flags)
  - [ ] The project should clarify that this is intended for UDS and OBD-II diagnostic queries, not for handling raw CAN bus traffic (that may be a future follow-up). We should note that "ioniq-can" is not an ideal name, given it is primarily UDS/KWP2000 focused.
  - [ ] Web UI for viewing and querying captures (similar to https://github.com/deanlee/openpilot-cabana used by https://www.projectgus.com/2023/10/kona-can-decoding/, but focused on UDS/KWP2000)
  - [ ] Store captures in CAN log files in the "gvret/SavvyCAN" CSV format, as supported by SavvyCAN? Or DBC? Not sure what is best here.

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
./query-captures.py --ecu VCU --pid 2101
./query-captures.py --diff VCU 2101

# Above actually broken on local machine, but works on agent VM. Use this on Mac instead (we should streamline and document a setup/install process!):
uv run ./query-captures.py --diff VCU 2101

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
