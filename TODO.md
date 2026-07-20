# TODOs

- Captures:
  - [ ] ESC PIDs while driving
- [x] Fix incorrect VCU VEHICLE_SPEED param, cluster value vs decoded PID value
- Canreq:
  - [ ] display byte index(es) for each mapped PID
  - [ ] simplify usage by making the "multi" mode the default, removing the complexity of maintaining both single and multi modes
- Various:
  - [ ] In query-captures, implement a "step through" feature so I can use the arrow keys (l/r) to step through the captures and see the decoded values for each capture, while also still showing the capture diff (against previous capture) underneath, just only for the current capture and not for all at the time. This is useful for debugging and understanding how the values change over time.
  - [ ] unified query syntax (rather than having --ecu and --pid flags)
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
