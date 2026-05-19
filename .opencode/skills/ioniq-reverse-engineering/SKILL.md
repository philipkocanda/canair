---
name: ioniq-reverse-engineering
description: Working with WiCAN OBD-II, Ioniq CAN bus, PID decoding, vehicle profiles, CAN request CLI tool, UDS protocol, expression evaluator. Load this skill when working on the Ioniq reverse engineering project, CAN bus analysis, or WiCAN device configuration.
---


# Ioniq CAN Reverse Engineering Skill

IMPORTANT: NEVER use UDS programming session (1002). 
This is a real car with real ECUs and I cannot fix any firmware/config mistakes which might brick the entire vehicle!
Also NEVER use firmware write/upload commands!

Be gentle when querying the car, its ECUs are old and not that fast. 
Do not make multiple concurrent requests to the same ECU.
Never reboot the WiCAN without asking, its often not needed. 
You can also use the ELM327 command to reset the bus if that is what you need.

## Overview

This skill covers the Hyundai Ioniq 2017 EV CAN bus reverse engineering project, including OBD-II PID definitions, WiCAN Pro vehicle profile configuration, and the data pipeline into Home Assistant via MQTT.

Dedicated TODOs for this project are located in "WiCAN Pro/docs/TODO.md"

### Goals

1. **Complete vehicle profile** — build a full Ioniq EV vehicle profile and submit a PR to the [wican-fw repo](https://github.com/meatpiHQ/wican-fw) to include it upstream. Currently close but still some PIDs missing or broken.
2. **Remote control** — enable remote pre-heating, door locks, etc. This will most likely require direct CAN bus write access (not just OBD-II reads). Additional technical details are in the Obsidian vault.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh battery, Premium trim - NL market). Not to be confused with the Hybrid (HEV) or Plug-in Hybrid (PHEV) variants. The 2017 model year (produced from 2016-2019) has a different CAN bus layout and fewer PIDs than the 2020+ facelift models. The 28 kWh version has a different BMS and fewer cell voltage PIDs than the 38 kWh version. The battery of the 28 kWh is air-cooled using a fan, while the 38 kWh has a liquid-cooled battery with a separate pump (EWP ECU?).
- **OBD-II dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`
- **CAN protocol:** ISO 15765-4 (CAN 11-bit, 500 kbps) — ELM327 protocol `6`

## Project Structure

```
├── WiCAN Pro/
│   ├── pids/                               # SOURCE OF TRUTH — per-ECU PID definitions (split by ECU)
│   │   ├── _meta.yaml                      # Car model and AT init string
│   │   ├── _schema.yaml                    # Schema documentation
│   │   ├── bms.yaml, bcm.yaml, vcu.yaml... # One file per ECU
│   ├── validate-pids.py                     # Schema validation for pids/ YAML files
│   ├── query-captures.py                    # Query captures: --ecu+--pid (combinable), --summary, --latest, --diff
│   ├── wican.py                            # WiCAN device management CLI (config upload/download, sleep, protocol, logs, reboot)
│   ├── generate-profile.py                  # Generate JSON profiles, upload/download/diff against WiCAN device
│   ├── canreq.py                       # CLI tool: custom CAN/UDS requests via WiCAN WebSocket terminal
│   ├── decode.py                       # Decode captured payloads using PID expressions (historical analysis)
│   ├── bix.py                               # Byte index converter: WiCAN ↔ ISO-TP ↔ Torque ↔ bix
│   ├── canlib/                              # Extracted library package (elm827, terminal, pids, captures, modes/, byteindex)
│   ├── ecus.yaml                            # ECU TX ID → name/description lookup (15 entries)
│   ├── captures/                            # UDS response captures, split by date
│   │   ├── SCHEMA.yaml                      # Capture file schema definition
│   │   ├── 2025-08-04.yaml ... 2026-04-16.yaml  # Per-date capture files
│   ├── validate-captures.py                 # Validate capture files against SCHEMA.yaml
│   ├── tests/                               # Unit tests (47 tests: elm827, expression, pids, formatting)
│   ├── AGENTS.md                            # Project-specific instructions
│   ├── docs/                                # Tool documentation (canreq, generate-profile, etc.)
│   ├── vehicle-profiles/
│   │   ├── ioniq-2017.json                  # Generated vehicle profile
│   ├── Configs/                             # WiCAN device config snapshots (full JSON dumps)
│   └── wican-fw/                            # WiCAN firmware checkout (git submodule-like)
├── Kona/                                    # Reference data from Kona EV
├── logs-for-jejusoul/                       # Raw CAN log captures
├── Spreadsheet_IoniqEV_BMS_2101_2105.xls    # Reference BMS PID spreadsheet
├── Kia Soul EV CAN Messages.xlsx            # Reference CAN message database
├── Charge-Curve.ods                         # Charging curve analysis
```

## WiCAN Configuration

### Device Access

- **Home network:** `http://10.0.2.86` (when car is parked at home)
- **Remote/driving:** `http://192.168.3.2` (via WireGuard VPN — WiCAN uses iPhone hotspot for internet connectivity)
- **Firmware:** [github.com/meatpiHQ/wican-fw](https://github.com/meatpiHQ/wican-fw)
- **Docs:** [meatpihq.github.io/wican-fw](https://meatpihq.github.io/wican-fw/)

### Live Data

When WiCAN is in AutoPID/Automate mode, the latest PID values can be read directly: `https://10.0.2.86/autopid_data`. AutoPID caches last received data, so querying it might return stale values if the car is off or the ECU is asleep. For real-time data, use the script `canreq.py` to send direct CAN/UDS requests via the WebSocket terminal mode.

**AutoPID stops polling when 12V battery is at or below `sleep_volt` threshold.** The WiCAN may remain WiFi-connected and reachable (not sleeping) but stop sending CAN requests. Current config: `sleep_volt=12.0V`, `sleep_time=5min`. At 12.0V the device is in an ambiguous state — connected but not polling. Stale HA sensor values (e.g. lights showing "on" when off) after parking are a symptom of this. Direct `canreq.py` queries still work because they use the WebSocket terminal mode, bypassing AutoPID. Values self-correct on next successful poll cycle (wakeup interval 120min or next drive).

### Connection

- **WiFi SSIDs:**  <redacted>
- **MQTT broker:** `mqtt://10.0.1.114:1883` (NUC) — user `mqtt`
- **MQTT topic:** `wican/ioniq/pids` (publishes all PID results as single JSON)
- **Sleep:** enabled, voltage threshold 12.9V, sleep time 5 min, wakeup interval 120 min
- **Logging:** SD card, FAT filesystem, 60s period, IMU threshold 8

### Two Profile Formats

WiCAN supports two profile formats — be careful not to confuse them:

1. **Vehicle Profile format** (`Vehicle Profiles/ioniq-2017.json`) — grouped parameters per PID, used for upstream PRs. Parameters are key-value pairs: `"PARAM_NAME": "expression"`. Generated by `generate-profile.py` from `pids/`.

2. **Device format** — what the firmware actually parses. Parameters as array of objects: `[{"name": "SOC_BMS", "expression": "B09/2", "unit": "%", "class": "battery", "period": "2500", ...}]`. Wrapped in `{"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}`. The `generate-profile.py --upload` command converts format 1 → device format automatically.

The **active device config** uses AutoPID format with destination set to `wican/ioniq/pids` and `Default` type (all PIDs published as a single JSON payload to one topic).

**Important:** The firmware's `load_all_pids()` in `autopid.c` requires `parameters` as an **array of objects** — if you POST a dict (Vehicle Profile format) to `/store_car_data`, cJSON iterates children but `cJSON_GetObjectItem(param, "name")` returns NULL, producing empty entries. The upstream build system (`cars.js process_profile()`) converts grouped→array format during the firmware build; the device never sees the grouped format directly.

### YAML Source of Truth

PID definitions are split into per-ECU YAML files under `pids/` (e.g. `pids/bms.yaml`, `pids/bcm.yaml`). Each file contains one ECU with its `tx_id` and PIDs. `pids/_meta.yaml` has `car_model` and `init`. Each parameter has:

```yaml
PARAM_NAME:
  expression: "B09/2"        # WiCAN formula
  unit: "%"                  # Display unit
  ha_class: battery          # HA device_class
  mqtt_topic: soc_bms        # MQTT suffix
  min: "0"                   # Expected range
  max: "100"
  source: "Original WiCAN config"
  source_links: [...]        # URLs
  verified: true             # Tested on Ioniq 2017?
  notes: ""
  enabled: true              # Include in generated profiles
```

Current state: 18 PIDs, 211 parameters, 167 verified, 44 unverified (from Kia Niro EV PRs).

### WiCAN REST API

All endpoints are JSON, no authentication. Device address varies (see Device Access above).

| Method | Endpoint                  | Purpose                                                |
|--------|---------------------------|--------------------------------------------------------|
| GET    | `/load_auto_pid_car_data` | Download vehicle profile (returns `{"cars": [...]}`)   |
| POST   | `/store_car_data`         | Upload vehicle profile (writes raw to flash)           |
| GET    | `/load_auto_pid`          | Download custom AutoPID config                         |
| POST   | `/store_auto_data`        | Upload custom AutoPID config                           |
| GET    | `/autopid_data`           | Read latest live PID values                            |
| GET    | `/load_config`            | Full device configuration                              |
| POST   | `/store_config`           | Store device configuration (full replace, auto-reboot) |
| GET    | `/check_status`           | Device status (WiFi, CAN, MQTT, battery, firmware)     |
| GET    | `/obd_logs`               | SD card log database index (JSON)                      |
| GET    | `/obd_logs/<filename>`    | Download SQLite log database file                      |
| POST   | `/system_reboot`          | Reboot device (body: `"reboot"`)                       |

### Tools

#### generate-profile.py

Generates WiCAN vehicle profiles from `pids/` directory.

Full CLI docs here: `projects/ioniq-can-reverse-engineering/WiCAN Pro/docs/generate-profile.py.md`

#### canreq.py

CLI tool for sending custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket ELM327 terminal mode. Connects to `ws://<ip>/ws`, sends `{"ws_mode": "terminal", "terminal_type": "elm327"}` to enter terminal mode. The firmware handles ISO-TP internally — no Python ISO-TP implementation needed. Core logic lives in `canlib/` package (elm827, terminal, pids, modes/).

**CRITICAL: Only one connection at a time.** The WiCAN has a single WebSocket endpoint. Never run multiple `canreq.py` commands in parallel — the second connection will either fail or lock up the device, requiring a power cycle to recover. Always wait for one command to finish before starting the next.

Full CLI docs here: `projects/ioniq-can-reverse-engineering/WiCAN Pro/docs/canreq.py.md`

**Preferred modes (use these first):**

- **`--multi "query ..."`** — multi-ECU pipeline with decoded output. Handles sessions, wake, and keepalives automatically. Best for querying known PIDs across one or multiple ECUs:
  ```bash
  canreq.py --multi "query BMS 2101"                                  # Query single PID, decoded
  canreq.py --multi "query BMS 2101" "query VCU 2101"                 # Multi-ECU in one session
  canreq.py --multi "session IGPM --wake" "query IGPM BC03 BC06"      # Wake + query
  canreq.py --multi "query BMS 2101" --monitor                        # Live-refresh every 5s
  canreq.py --multi "query BCM C00B" --monitor --keep-unique --save   # Monitor + capture changes
  ```
- **`--param` / `--ecu`** — query named parameters or full ECU. Simpler syntax for single-ECU reads:
  ```bash
  canreq.py --param SOC_BMS SOC_DISP    # Query specific named parameters
  canreq.py --ecu BMS --pid 2101        # All parameters from BMS PID 2101
  ```
- **`--scan`** — discover what PIDs/DIDs an ECU supports. Iterates a range, reports positive responses:
  ```bash
  canreq.py --scan --tx 7E4 --service 21 --range 01-FF           # Scan BMS service 21
  canreq.py --scan --tx 770 --service 22 --range BC00-BCFF --wake # Scan IGPM DIDs
  canreq.py --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session  # IOControl scan
  ```

**`--raw` — last resort only.** Use `--raw` when no PID definition exists yet, or for ad-hoc UDS commands (IOControl, security, identity). It returns a hex dump with no decoding:
```bash
canreq.py --raw 7E4:2101                          # Raw hex dump (no parameter decoding)
canreq.py --raw 770:2FBC0103 --wake --hold         # IOControl (no YAML definition)
canreq.py --multi "session BCM --wake" "raw 7A0:22B00E"  # Raw within a pipeline
```

**`--verbose` — debugging only.** Shows raw WebSocket traffic. Not useful for normal operation — only for debugging canreq itself.

#### wican.py

WiCAN device management CLI. Manages device configuration, sleep/power saving, protocol switching, status, OBD log queries, and reboots via the REST API.

```bash
python3 wican.py config                          # View full device config
python3 wican.py config --section sleep           # View sleep settings only
python3 wican.py config --save                    # Save config snapshot to configs/
python3 wican.py sleep                            # Show current sleep status
python3 wican.py sleep --disable --dry-run        # Preview disabling sleep
python3 wican.py sleep --disable -y               # Disable sleep (skip confirmation)
python3 wican.py sleep --enable -y                # Re-enable sleep
python3 wican.py sleep --voltage 12.5 --time 10   # Adjust thresholds
python3 wican.py status                           # Device status summary
python3 wican.py protocol                         # Show current protocol + options
python3 wican.py protocol --set slcan --dry-run   # Preview switching to SLCAN
python3 wican.py protocol --set slcan -y          # Switch to SLCAN (reboots device)
python3 wican.py protocol --set auto_pid -y       # Switch back to AutoPID
python3 wican.py logs                             # List SD card log databases
python3 wican.py logs --download                  # Download all log DBs to logs/
python3 wican.py logs --params                    # List all logged parameters
python3 wican.py logs --query SOC_BMS --limit 20  # Query parameter time series
python3 wican.py reboot                           # Reboot device
python3 wican.py --wican vpn sleep                # Use VPN address
```

**Protocol modes** are mutually exclusive: `auto_pid` (normal MQTT polling), `slcan` (for SavvyCAN/candump), `elm327` (OBD-II apps), `savvycan` (native SavvyCAN), `realdash66`. Switching stops the current mode, applies the new one, and reboots the device. The `protocol` subcommand shows clear warnings about consequences (e.g. AutoPID stopping MQTT data feed to HA).

**Important:** `POST /store_config` replaces the entire config on flash and auto-reboots the device. The tool handles this by doing a GET first, modifying only the changed fields, and POSTing the full config back.

**Tip: Disable sleep during reverse engineering sessions.** When probing ECUs with `canreq.py`, the WiCAN may go to sleep mid-session if the 12V battery voltage drops below the threshold (especially with engine off). Disable sleep before starting a session and re-enable it when done:

```bash
python3 wican.py sleep --disable -y    # Before RE session
# ... do your CAN bus work ...
python3 wican.py sleep --enable -y     # After RE session
```

### Captures

UDS response payloads are stored in `captures/` as per-date YAML files (e.g. `2026-04-16.yaml`). Schema defined in `captures/SCHEMA.yaml`. Each file contains sessions with `date`, `label`, `state` (optional), and a list of captures. Each capture has `ecu` (name from `ecus.yaml`), `pid`, `notes`, and exactly one of `payload` (hex), `response` (text/NRC), or `scan_results` (structured).

**Saving captures:** Use `--save` with `--scan`, `--raw`, or `--discover` to auto-save results. Labels are auto-suggested (press Enter to accept). Monitor mode also supports `--save` (prompts on Ctrl+C). Shared save logic in `canlib/captures.py`.

**Querying captures:** After adding new captures, always run `query-captures.py` to check for patterns that weren't obvious during the live session (e.g. byte-level changes between states, new ECU/PID combinations, payload length differences).
```bash
python3 query-captures.py --ecu IGPM --pid 22BC03   # ECU+PID combination (most useful)
python3 query-captures.py --summary                  # Overview stats: captures per ECU/date
python3 query-captures.py --ecu BMS                  # All captures for an ECU
python3 query-captures.py --pid 22BC03               # All captures for a PID (across ECUs)
python3 query-captures.py --latest BMS               # Most recent payload per PID
python3 query-captures.py --diff IGPM 22BC03         # Byte-level diff (red=changed, dim=unchanged)
```

**Decoding captures:** Use `decode.py` to apply PID parameter expressions to captured payloads and see decoded values. Essential for validating expressions against real data and spotting anomalies (out-of-range values, wrong offsets, PCI boundary issues).

```bash
python3 decode.py BMS 2101                            # Full table: all params × all captures
python3 decode.py BMS 2101 --param SOC_BMS BATTERY_VOLTAGE  # Filter to specific params
python3 decode.py BMS 2101 --compact                  # One-liner per capture
python3 decode.py BMS 2101 --unverified               # Only unverified params (validation focus)
python3 decode.py BMS 2101 --json                     # JSON output (for further processing)
```

```bash
python3 validate-captures.py              # Validate all capture files against schema
```

### AT Command Init

Per-ECU init: `ATSH{id};ATFCSH{id};` (e.g. `ATSH7E4;ATFCSH7E4;` for BMS). Global init: `ATSP6;ATS0;ATAL;ATST96;` (protocol 6, no spaces, allow long, timeout 600ms).

## WiCAN Byte Index Notation

WiCAN expressions index into the **raw CAN frame data including PCI bytes**. The firmware's ELM327 response parser (`parse_elm327_response()` in `autopid.c`) runs with headers ON and copies ALL 8 CAN data bytes per frame (including ISO-TP PCI bytes) sequentially into a flat byte array.

### Byte layout (AutoPID internal format)

For a multi-frame response to `2101` on BMS (0x7E4):

```
Frame 0 (First Frame):  [10 3B] [61 01 FF FF FF FF]  → B00-B07
Frame 1 (Consecutive):  [21]    [d  d  d  d  d  d  d] → B08-B15
Frame 2 (Consecutive):  [22]    [d  d  d  d  d  d  d] → B16-B23
...
```

- `B00` = PCI high byte (0x10), `B01` = PCI low byte (length)
- `B02` = SID response (0x61), `B03` = PID echo (0x01)
- `B08` = PCI consecutive (0x21), `B09` = first actual data byte of frame 1
- PCI bytes occupy indices 0, 8, 16, 24, 32, 40, 48, 56, ...

### Byte indexing examples

For a `0x21` service request (PID `01`), the response starts `61 01 <data...>`:
- `B0` = `0x61` (service response ID)
- `B1` = `0x01` (PID echo)
- `B2` = first data byte

For a `0x22` service request (DID `C00B`), the response starts `62 C0 0B <data...>`:
- `B0` = `0x62` (service response ID)
- `B1` = `0xC0` (DID high byte)
- `B2` = `0x0B` (DID low byte)
- `B3` = first data byte

### Expression syntax

`Bnn` (unsigned byte), `Snn` (signed), `[Bnn:Bmm]` (multi-byte unsigned), `[Snn:Smm]` (multi-byte signed), `Bnn:k` (bit k, 0=LSB). Operators: `+ - * / << >> & | ^`. See `expression_parser.c` source for full reference.

**CAUTION: `[Bnn:Bmm]` reads consecutive raw bytes — it does NOT skip PCI bytes.** If a multi-byte value spans a CAN frame boundary (B07-B08, B15-B16, etc.), the PCI byte at B08/B16/... will be included in the value, producing garbage. Use manual bit-shifting instead: `(B07 << 8) | B09` to skip the PCI byte at B08. Always use `bix.py` to verify whether your byte range crosses a PCI boundary.

Use `bix.py` to convert between WiCAN, ISO-TP, Torque, and OBDb (bix) byte index notations:

```bash
python3 bix.py w9        # WiCAN B09 → ISO-TP 0x06, Torque E, bix 32
python3 bix.py E         # Torque letter → all notations
python3 bix.py -2 w5     # 2-byte subfunction mode (22xxxx DIDs)
python3 bix.py --table   # Full conversion table

# Annotate a UDS response payload — shows WiCAN Bnn for each byte
python3 bix.py -2 --annotate 62B0047402990C0040A000AAAA
python3 bix.py --annotate 6101FFFF...           # service 21 (1-byte PID)
python3 bix.py -2 -a "62 B0 04 74 02 99"       # spaces OK
```

The `--annotate` (`-a`) flag takes raw UDS response bytes (as seen in `canreq --raw` or monitor output), reconstructs the WiCAN frame with PCI bytes inserted, and prints a table with each byte's WiCAN Bnn, ISO-TP index, Torque letter, bix, and role (PCI/SID/DID/PID). Use `-1` (default) for service 21 or `-2` for service 22 DIDs.

## Data Pipeline: WiCAN -> HA

```
WiCAN Pro (OBD-II port in car)
  → MQTT: wican/ioniq/pids (JSON with all PID results)
    → Node-RED "one topic for every PID" function
      → MQTT: wican/ioniq/filtered/<key_lowercase> (retain=true, per-parameter)
        → HA MQTT sensors (packages/ev/mqtt/wican.yaml)
          → HA template sensors (packages/ev/template/wican.yaml)
          → InfluxDB export (entity glob: sensor.ioniq_*)
```

### HA Configuration Files

| File                                                          | Contents                                        |
|---------------------------------------------------------------|------------------------------------------------|
| `~/VM100/home-automation/homeassistant/packages/ev/mqtt/wican.yaml` | 51 MQTT sensors + 15 binary sensors             |
| `~/VM100/home-automation/homeassistant/packages/ev/template/wican.yaml` | Derived sensors (drive mode, state, power, speed) |
| `~/VM100/home-automation/homeassistant/packages/ev/automation/notify.yaml` | 12V alert, charge start/stop/stale notifications |
| `~/VM100/home-automation/homeassistant/packages/ev/automation/abrp.yaml` | ABRP telemetry upload                           |

### Lovelace Dashboard

Home Assistant WiCAN dashboard lives at path `/lovelace-car/wican`.

## Memory

### 2026-04-19 — AutoPID stops at sleep_volt threshold even when WiCAN stays connected

When 12V battery drops to `sleep_volt` (currently 12.0V), the WiCAN stops AutoPID polling but remains WiFi-connected. This creates a deceptive state: device is reachable, `/autopid_data` returns data, but values are stale from the last successful poll. Observed: after parking, DRL/low beam/tail lights showed "on" in HA because the WiCAN polled during a drive with lights on, then stopped polling when voltage dropped to 12.0V. Direct `canreq.py` queries confirmed lights were actually off. Fix: values self-correct on next successful poll. Consider raising `sleep_volt` slightly (e.g. 12.2V) to create a clearer gap vs. the actual sleep trigger.

### 2026-04-19 — BCM 95400-G7470 is Ioniq AE Electric (BEV) only

The BCM part number `95400-G7470` is **not shared across trims**. The `Gx` code in Hyundai part numbers identifies the vehicle model. The G7 code is exclusive to the Ioniq AE Electric (BEV):

| Code | Vehicle                    | BCM shared? |
|------|----------------------------|-------------|
| G7   | Ioniq AE Electric (BEV)   | Only this car |
| G2   | Ioniq AE Hybrid (HEV)     | Different BCM |
| G5   | Kia Niro (HEV/PHEV)       | Different BCM |
| G6   | Kia Picanto                | Different BCM |

This means IOControl DIDs that accept but produce no visible effect are **not** explained by cross-vehicle sharing. More likely explanation: **market variants** — the same G7 BCM ships across all Ioniq Electric markets (EU, US, Korea, etc.) with features like heated door handles, rain sensor, or auto-dimming mirrors that may not be fitted on every market/trim level.

### 2026-04-16 — IGPM status reads confirmed during deep sleep

Tested querying IGPM (0x770) while car is in deep sleep using `canreq.py --ecu IGPM --wake`. Sequence: wake frame `10 01` (returns NO DATA, expected), then extended session `10 03` (succeeds), then `22BC03`/`22BC04`/`22BC06` all return valid data. Confirmed readable: all 5 door open/close states, trunk, 4 door locks, all lights (DRL/tail/high/low beam), ignition, seatbelts, brake light, turn signals. All values consistent with parked locked car (doors closed, locked, lights off, ignition off). This means **periodic IGPM polling during WiCAN wake cycles is feasible** — no SKM wake or fob needed for IGPM reads.

### 2026-04-14 — Capture decoder + expression evaluator

Created expression evaluator (`canlib/expression.py`) — faithful Python port of `wican-fw/main/expression_parser.c`. Decoder surfaced PID definition issues: BMS 2101 `B62+` exceed 61-byte stationary payload, VCU 2101 `B26` exceeds 22-byte payload (CAR_READY/PARK_BRAKE wrong offset for Ioniq), MODULE_3/5_TEMP read padding bytes as -50°C, cumulative energy values implausibly large.

## ECU Research Status

Derived from `~/obsidian-vault/KB/EV/Hyundai Ioniq/Reverse engineering/PIDs by ECU/`. For untested ECU/PID combinations, see `untested-pids-index.yaml`.

### ECU Status Overview

| ECU       | Arb ID | Status        | Notes                                                                                                  |
|-----------|--------|---------------|--------------------------------------------------------------------------------------------------------|
| BCM/TPMS  | 0x7A0  | ✅ Working    | Shared ECU (part 95400G7470). TPMS pressure on `22C00B` (also has tyre temps at -50 offset). `22C00B` contains full BCM state beyond just TPMS. BCM IOControl `2f b0 xx` (charge door, mirrors, heated handles, room lamp) known from e-Niro, untested on Ioniq. **Wakes from CAN bus activity alone** (no ACC relay needed) — same as IGPM. Full IOControl scan done (24 accepted B000-B072). B061 (charge door) definitively not supported on Ioniq 2017. |
| BMS       | 0x7E4  | ✅ Working    | PIDs 2101 (main), 2105 (temps/SOH), 2102/03/04 (cell voltages). Full ImHex patterns documented.        |
| VCU       | 0x7E2  | 🔶 Partial   | 2101 working (gear, vehicle state, speed). Speed may be in MPH. PIDs 2102 captured but not decoded (motor heat sink temp, inverter voltage, MCU temp, motor RPM). Regen mode and ECO/Sport/Normal TODO. |
| MCU       | 0x7E3  | 🔶 Partial   | Part 36600-0E250 (inverter). 2101/2102 captured, not decoded. Likely has torque and motor RPM. Ioniq5 references `22E001`, `22E009`. |
| HVAC      | 0x7B3  | 🔶 Partial   | PID `220100` (was `2201006` — fixed in YAML). Byte offsets partially verified. IAT/AAT/evaporator temps, not entirely correctly mapped and some values still unknown. More PIDs (`220101`, `220102`) available but not yet explored. |
| CLU       | 0x7C6  | ✅ Working    | `22B002` → odometer (UINT24 big-endian at byte 9). Also has imperial odometer. e-Niro sheet has more (range, time driven, speed limit, cruise) — TODO. |
| IGPM      | 0x770  | ✅ Working   | Full IOControl map complete (BC00-BCFF scanned). 27 actuator DIDs, 11 status registers. Confirmed: lights, horn, turn signals, DRL, CHMSL, brake lights, trunk, door lock/unlock, charge cable lock/unlock. Wakes from deep sleep via `1001` — **status reads (BC03/BC04/BC06) confirmed working during deep sleep** (doors, locks, lights, ignition all readable). See `docs/IOControl CLI commands.md`. |
| SKM/SMK   | 0x7A5  | ✅ Working   | ACC relay IOControl (`2FB108030A0A05`) — UDS positive response confirmed but **relay only physically closes with fob nearby**. Without fob, `6FB10803` returned but IGPM BC03 ignition byte stays `0x00`. `skm-wake` command now verifies via IGPM BC03 (step 4/4). **Wakes from rapid-fire `1001` without fob** (2 attempts at 64ms timeout). ACC/IGN1/IGN2 IOControl accepted but powertrain ECUs stay dead. ACC releases when session drops. See `docs/wakeup-research.md`. |
| LDC       | 0x7E5  | 🔶 Partial   | Confirmed as LDC (CarScanner: `AEV**LDC**53`). **Available in ACC2/IGN** — responds to 2101 even post-charge with ACC2 on (LDC itself may be inactive). PID 2101: 4 verified params (HV input V, output V/A, temp), 3 medium-confidence (OBC charge V, AC A, pilot duty). PID 2102: 34 bytes captured, undecoded. PID 2103: NRC 0x12 (not supported). Niro PR#716 formulas incompatible (shorter response, different offsets). |
| Gateway   | 0x7E6  | ✅ Working    | Ambient temp via `2180`, expression `(B18-80)/2`. Not a discrete ECU — likely gateway-forwarded. |
| Charging  | 0x744  | ❓ Unverified | Cross-platform evidence only, not confirmed for Ioniq 28 kWh. |
| PSM       | 0x7A3  | 🔶 Research  | Power seat IOControl `2f b4 xx` — slide, recline, height. From e-Niro only, not tested. |
| VESS      | 0x736  | 🔶 Research  | Vehicle Exterior Sound System. IOControl commands known, Python script exists. Not yet tested. |

## UDS Protocol Notes

Source: `KB/EV/Hyundai Ioniq/Reverse engineering/Hyundai Kia UDS DID Conventions.md`

### PID Categories

- **`0x21xx` PIDs** — fast live data snapshots; no extended session or security needed; multiple parameters per response; use manufacturer-specific function byte `0x21`
- **`0x22xx` PIDs** — structured, may need extended diagnostic session (`10 03`); use standard UDS ReadDataByIdentifier (`22`); some DIDs are writable via `2E` — handle with care

### DID Paging vs Indexing

- Some ECUs (e.g. BMS) use **paging**: `2101`, `2102`, `2103`, `2104` each return a different block of data (the `xx` is a page number, not a DID)
- Other ECUs use **indexing**: `2101`, `2102` are sub-functions or pages within the same dataset

### DID Range Semantics (Hyundai/Kia convention)

- `0x21xx` — live data, manufacturer-specific
- `0x22Bxxx` — cluster/display data
- `0x22Cxxx` — body/comfort (BCM, TPMS)
- `0x22Exxx` — powertrain (BMS, MCU, VCU, HVAC)
- `0x22Fxxx` — often flash/calibration data — **do not write**

### Hyundai/Kia DID -1 Offset (F1xx Identity DIDs)

Hyundai/Kia ECUs use identity DIDs shifted by **-1** from the standard UDS specification. When reading standard UDS identity DIDs (`22 F1xx`), use the Hyundai/Kia DID instead:

| Standard UDS DID | HK DID | Field            |
|------------------|--------|------------------|
| F188             | F187   | ECU Part Number  |
| F18C             | F18B   | Manufacture Date |
| F192             | F191   | Supplier HW No   |

The `--identity` flag in `canreq.py` queries both standard and HK DIDs. The ECU responds positively to the HK DID (e.g. `22F187` → `62F187 <part number>`) while the standard DID (F188) returns NRC 0x31 during deep sleep.

**Confirmed part numbers via F187:**
- BCM (0x7A0): `95400G7470`
- IGPM (0x770): `91950G7510`

This -1 offset may also apply to other DID ranges — when a DID scan finds data echoing a DID one less than requested, try the -1 DID directly.

### Security Access

- Standard UDS: send `27 01` (seed request), ECU responds `67 01 <seed>`, compute key, send `27 02 <key>`
- Known answer for KIA Soul: `67 02 34` — **may differ on Ioniq**
- Most `0x21` reads do not require security access
- `2E` writes may require Security Access Level 1 or 2

### Safety Warnings

- **Never use `2E F1 xx`** without knowing what it does — risk of bricking ECU
- `0x22 Exxx` DIDs in the write range should be treated as read-only until the range is fully understood
- IOControl (`2F`) commands can actuate physical hardware — use only in safe conditions (car stationary, engine off, doors closed where relevant)

## Key References

### Obsidian Vault

Location: `~/obsidian-vault/KB/EV/Hyundai Ioniq/Reverse engineering/`

Key files: `PIDs by ECU/` (per-ECU research, summarized in ECU Status table above), `Ioniq OBD-II CAN modules.md` (CarScanner ECU dump), `Hyundai Kia UDS DID Conventions.md` (UDS conventions), `Fan control from scan tools (Kingbolen).md` (fan actuation test). Additional unread files: `Ioniq UDS decoding`, `Gen5 head unit`, `Kona teardown`, `OBDb`, `OVMS/`, `Tools/`, `CAN buses/`, `Conversion tables/`, `Tested scenarios/`.

### External

- [WiCAN firmware repo](https://github.com/meatpiHQ/wican-fw) — upstream firmware + vehicle profiles
- [WiCAN docs](https://meatpihq.github.io/wican-fw/)
- [Kia Niro 64 kWh PID database](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M) — good cross-reference
- Local spreadsheets: `Kia Soul EV CAN Messages.xlsx` (Soul PIDs offset by 1), `Spreadsheet_IoniqEV_BMS_2101_2105.xls`

## Open TODOs

For the full untested ECU/PID index with priorities, prerequisites, and scan commands, see `untested-pids-index.yaml`.

**Active investigation items:**
- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan/EWP control DID** — Kingbolen scanner can actuate fan via UDS, specific DID unknown. Scan BMS/MCU `2F E0xx 03` or sniff Kingbolen
- [ ] **IOControl testing** — BCM `2f b0 xx` untested on Ioniq. IGPM fully scanned (BC00-BCFF). SKM B108 ACC confirmed. Remaining: BC0A, BC0C, BC1B, BC1C untested; BC25/BC42/BC43/BC44 accepted but no visible effect
- [ ] **Remote BMS read** — SKM wakes from rapid-fire `1001` without fob (2-17 attempts). ACC/IGN1 IOControl accepted. BCM wakes (TPMS/charge port work). But powertrain ECUs (BMS/VCU/MCU) remain dead — relay doesn't latch. Workarounds: spare fob, direct relay wiring, or reads only during charging.
- [ ] **Verify unverified PIDs** — 44 params from Kia Niro PRs. Most ECUs (IGPM, BCM, ESC) require ACC/ignition on
