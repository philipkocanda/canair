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

This skill covers the Hyundai Ioniq 2017 EV CAN bus reverse engineering project, including OBD-II PID definitions, WiCAN Pro vehicle profile configuration, and MQTT data publishing.

Dedicated TODOs for this project are located in "docs/TODO.md"

### Goals

1. **Complete vehicle profile** — build a full Ioniq EV vehicle profile and submit a PR to the [wican-fw repo](https://github.com/meatpiHQ/wican-fw) to include it upstream. Currently close but still some PIDs missing or broken.
2. **Remote control** — enable remote pre-heating, door locks, etc. This will most likely require direct CAN bus write access (not just OBD-II reads). Additional technical details are in the Obsidian vault.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh battery, Premium trim - NL market). Not to be confused with the Hybrid (HEV) or Plug-in Hybrid (PHEV) variants. The 2017 model year (produced from 2016-2019) has a different CAN bus layout and fewer PIDs than the 2020+ facelift models. The 28 kWh version has a different BMS and fewer cell voltage PIDs than the 38 kWh version. The battery of the 28 kWh is air-cooled using a fan, while the 38 kWh has a liquid-cooled battery with a separate pump (EWP ECU?).
- **OBD-II dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`
- **CAN protocol:** ISO 15765-4 (CAN 11-bit, 500 kbps) — ELM327 protocol `6`

## Project Structure

```
├── pids/                               # SOURCE OF TRUTH — per-ECU PID definitions (split by ECU)
│   ├── _meta.yaml                      # Car model and AT init string
│   ├── _schema.yaml                    # Schema documentation
│   ├── bms.yaml, bcm.yaml, vcu.yaml... # One file per ECU
├── validate-pids.py                     # Schema validation for pids/ YAML files
├── query-captures.py                    # Query captures: --ecu+--pid (combinable), --summary, --latest, --diff
├── generate-profile.py                  # Generate JSON profiles, upload/download/diff against WiCAN device
├── canreq.py                            # CLI tool: custom CAN/UDS requests via WiCAN WebSocket terminal
├── decode.py                            # Decode captured payloads using PID expressions (historical analysis)
├── bix.py                               # Byte index converter: WiCAN ↔ ISO-TP ↔ Torque ↔ bix
├── canlib/                              # Extracted library package (elm827, terminal, pids, captures, modes/, byteindex)
├── config.yaml                          # Local WiCAN device addresses (gitignored, user-specific)
├── config.example.yaml                  # Template for config.yaml (committed)
├── ecus.yaml                            # ECU TX ID → name/description lookup (15 entries)
├── captures/                            # UDS response captures, split by date
│   ├── SCHEMA.yaml                      # Capture file schema definition
│   ├── 2025-08-04.yaml ... 2026-04-16.yaml  # Per-date capture files
├── validate-captures.py                 # Validate capture files against SCHEMA.yaml
├── tests/                               # Unit tests (47 tests: elm827, expression, pids, formatting)
├── AGENTS.md                            # Project-specific instructions
├── docs/                                # Tool documentation (gitignored, local only)
├── vehicle-profiles/
│   ├── ioniq-2017.json                  # GENERATED vehicle profile (do not hand-edit; run generate-profile.py)
├── configs/                             # WiCAN device config snapshots (full JSON dumps)
├── wican-fw/                            # WiCAN firmware checkout (gitignored)
└── research/                            # Reference data (Kona, Kia Soul, spreadsheets)
```

## WiCAN Configuration

### Device Access

WiCAN device addresses are configured in `config.yaml` (gitignored, user-specific). Copy from `config.example.yaml` to get started. All CLI tools (`canreq.py`, `generate-profile.py`) read addresses from this file via `canlib.constants`. For device management (config, sleep, protocol, logs, reboot), use the separate [`wican-cli`](https://github.com/philipkocanda/wican-cli) package.

```yaml
# config.yaml
wican_addresses:
  home: "10.0.2.86"       # Device on local LAN
  vpn: "192.168.3.2"      # Device via WireGuard VPN (iPhone hotspot)
default_wican: home
```

Without `config.yaml`, tools fall back to `192.168.80.1` (WiCAN factory AP address).

- **CLI usage:** `--wican home`, `--wican vpn`, or `--wican <arbitrary-ip>`
- **Firmware:** [github.com/meatpiHQ/wican-fw](https://github.com/meatpiHQ/wican-fw)
- **Docs:** [meatpihq.github.io/wican-fw](https://meatpihq.github.io/wican-fw/)

### Live Data

When WiCAN is in AutoPID/Automate mode, the latest PID values can be read directly: `http://<wican-ip>/autopid_data`. AutoPID caches last received data, so querying it might return stale values if the car is off or the ECU is asleep. For real-time data, use the script `canreq.py` to send direct CAN/UDS requests via the WebSocket terminal mode.

**AutoPID stops polling when 12V battery is at or below `sleep_volt` threshold.** The WiCAN may remain WiFi-connected and reachable (not sleeping) but stop sending CAN requests. Current config: `sleep_volt=12.0V`, `sleep_time=5min`. At 12.0V the device is in an ambiguous state — connected but not polling. Stale MQTT values (e.g. lights showing "on" when off) after parking are a symptom of this. Direct `canreq.py` queries still work because they use the WebSocket terminal mode, bypassing AutoPID. Values self-correct on next successful poll cycle (wakeup interval 120min or next drive).

### Connection

- **WiFi SSIDs:**  <redacted — see .secrets.json>
- **MQTT broker:** configured in device config (user-specific)
- **MQTT topic:** `wican/ioniq/pids` (publishes all PID results as single JSON)
- **Sleep:** enabled, voltage threshold 12.9V, sleep time 5 min, wakeup interval 120 min
- **Logging:** SD card, FAT filesystem, 60s period, IMU threshold 8

### Two Profile Formats

WiCAN supports two profile formats — be careful not to confuse them:

1. **Vehicle Profile format** (`vehicle-profiles/ioniq-2017.json`) — grouped parameters per PID, used for upstream PRs. Parameters are key-value pairs: `"PARAM_NAME": "expression"`. **Generated** by `generate-profile.py` from `pids/` — never hand-edit; edit `pids/` and regenerate (see the `generate-profile.py` tool section below).

2. **Device format** — what the firmware actually parses. Parameters as array of objects: `[{"name": "SOC_BMS", "expression": "B09/2", "unit": "%", "class": "battery", "period": "2500", ...}]`. Wrapped in `{"cars": [{"car_model": "...", "init": "...", "pids": [...]}]}`. The `generate-profile.py --upload` command converts format 1 → device format automatically.

The **active device config** uses AutoPID format with destination set to `wican/ioniq/pids` and `Default` type (all PIDs published as a single JSON payload to one topic).

**Important:** The firmware's `load_all_pids()` in `autopid.c` requires `parameters` as an **array of objects** — if you POST a dict (Vehicle Profile format) to `/store_car_data`, cJSON iterates children but `cJSON_GetObjectItem(param, "name")` returns NULL, producing empty entries. The upstream build system (`cars.js process_profile()`) converts grouped→array format during the firmware build; the device never sees the grouped format directly.

### YAML Source of Truth

PID definitions are split into per-ECU YAML files under `pids/` (e.g. `pids/bms.yaml`, `pids/bcm.yaml`). Each file contains one ECU with its `tx_id` and PIDs. `pids/_meta.yaml` has `car_model` and `init`. Each parameter has:

```yaml
PARAM_NAME:
  expression: "B09/2"        # WiCAN formula
  unit: "%"                  # Display unit
  ha_class: battery          # device_class (for downstream consumers)
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

Generates the WiCAN vehicle profile from the `pids/` directory.

**`vehicle-profiles/ioniq-2017.json` is a GENERATED artifact — never hand-edit it.** It is produced entirely from `pids/` (the source of truth). Any manual edit will be silently overwritten on the next run, and hand-edits drift out of sync with `pids/` (e.g. a stale `VEHICLE_SPEED_ALT` lingered in the profile long after it was removed from `pids/`).

**Workflow after changing any PID definition:**

```bash
python3 validate-pids.py        # 1. Validate pids/ against the schema
python3 generate-profile.py     # 2. Regenerate vehicle-profiles/ioniq-2017.json (local write only)
```

Only `pids/` is edited by hand; the profile is always regenerated. `generate-profile.py` reads every `pids/*.yaml`, emits the Vehicle Profile format (grouped params per PID) to the single output file `vehicle-profiles/ioniq-2017.json` (`PROFILE_OUT` in the script). Parameters with `enabled: false` are excluded. Other flags:

- `--verified-only` — include only `verified: true` params
- `--no-write` — dry run (generate without writing the file)
- `--stats` — print the PID statistics table
- `--download` / `--diff` — fetch the device's live config and (optionally) diff it against the freshly generated profile
- `--upload [--reboot]` — convert grouped→device format and POST to the WiCAN (mutative; **ask the user first**, and reboot only when explicitly requested)

Full CLI docs here: `docs/generate-profile.py.md`

#### canreq.py

CLI tool for sending custom CAN/UDS requests to the Ioniq via WiCAN's WebSocket ELM327 terminal mode. Connects to `ws://<ip>/ws`, sends `{"ws_mode": "terminal", "terminal_type": "elm327"}` to enter terminal mode. The firmware handles ISO-TP internally — no Python ISO-TP implementation needed. Core logic lives in `canlib/` package (elm827, terminal, pids, modes/).

**CRITICAL: Only one connection at a time.** The WiCAN has a single WebSocket endpoint. Never run multiple `canreq.py` commands in parallel — the second connection will either fail or lock up the device, requiring a power cycle to recover. Always wait for one command to finish before starting the next.

```bash
# Preferred: --multi "query ..." for decoded output with session management
canreq.py --multi "query BMS 2101"                # Query single PID, decoded parameters
canreq.py --multi "query BMS 2101" "query VCU 2101"  # Multi-ECU in one session
canreq.py --multi "session IGPM --wake" "query IGPM BC03 BC06"  # Wake + query
canreq.py --multi "query BMS 2101" --monitor      # Live-refresh every 5s

# Single-ECU shortcuts (simpler syntax, same decoded output)
canreq.py --param SOC_BMS SOC_DISP         # Query specific named parameters
canreq.py --ecu BMS                        # Query all BMS parameters
canreq.py --ecu BMS --pid 2101             # Query BMS PID 2101 only

# Discovery and scanning
canreq.py --scan --tx 7E4 --service 21 --range 01-FF  # Scan PID range
canreq.py --discover                       # Sweep 0x700-0x7EF for responding ECUs
canreq.py --identity --tx 7A0 --session    # Query UDS identity DIDs

# Raw mode — last resort (hex dump only, no parameter decoding)
canreq.py --raw 7E4:2101                   # Raw UDS request
canreq.py --raw 770:2FBC0103 --wake --hold # IOControl with session held open

# IOControl (dedicated mode with TUI)
canreq.py --iocontrol IGPM                 # Interactive TUI
canreq.py --iocontrol IGPM --did BC01      # Turn on low beam

# Other
canreq.py                                  # Interactive REPL
canreq.py --wican vpn --param SOC_BMS      # Use VPN address
canreq.py --json --param SOC_BMS           # JSON output
```

> **Mode guidance:** Use `--multi "query ..."` or `--param`/`--ecu` for decoded, readable output. Use `--raw` only when no PID definition exists yet, or for ad-hoc UDS commands (IOControl without YAML, exploratory reads). `--verbose` is for debugging canreq itself — not useful for normal operation.

##### `--multi` flag (multi-ECU pipeline)

Executes a sequence of sub-commands within a single WebSocket session, managing extended diagnostic sessions across multiple ECUs with interleaved TesterPresent keepalives. After the pipeline completes, exits by default. Use `--repl` to drop into an interactive REPL with all sessions still active, or include an explicit `repl` step in the pipeline.

```bash
# Wake SKM, query IGPM, exit
canreq.py --multi "skm-wake acc" "query IGPM BC03 BC06"

# Wake SKM + BCM, raw query charge port, exit
canreq.py --multi "skm-wake acc" "session BCM --wake" "raw 7A0:22B00E"

# Wake IGPM, query all PIDs, drop into REPL
canreq.py --multi "session IGPM --wake" "query IGPM" --repl

# Pipeline with explicit sleep between steps
canreq.py --multi "skm-wake acc" "sleep 1" "query BCM B00E" "repl"
```

**Sub-commands:**

| Sub-command                      | Description                                         |
|----------------------------------|-----------------------------------------------------|
| `skm-wake [level]`              | Wake SKM + activate relay (acc/ign1/ign2/start)     |
| `session <ECU\|TX_ID> [--wake]` | Enter extended session on ECU (add to session table) |
| `query <ECU> [PID ...]`         | Query ECU parameters (like `--ecu`/`--param`)       |
| `raw <TX:PID>`                  | Raw UDS request                                      |
| `scan <TX> <SVC> <RANGE> [APP]` | Scan PID range                                       |
| `security <ECU> [algo ...]`     | Try UDS Security Access (27 01/02) with key algorithms |
| `iocontrol <ECU> <DID> [--off]` | Execute IOControl ON/OFF from pids/ YAML             |
| `sleep <seconds>`               | Pause between steps                                  |
| `repl`                          | Drop into interactive REPL (explicit)                |

ECU names are resolved from YAML definitions (e.g., `IGPM`, `BCM`, `SKM`) or can be hex TX IDs (`770`, `7A0`).

##### `security` sub-command (in `--multi` pipeline)

Attempts UDS Security Access (service `27`) on an ECU by requesting a seed (`27 01`) and computing a key (`27 02`) using common Hyundai/Kia key algorithms. Requires an active extended session on the target ECU. Tries all built-in algorithms by default, or a filtered subset if algorithm names are given.

```bash
# Try all algorithms on BCM (session must be open)
canreq.py --multi "session BCM --wake" "security BCM"

# Try specific algorithms only
canreq.py --multi "session BCM --wake" "security BCM not xor-0d0b0507 ki203-30bacd45"

# In REPL after opening a session
security BCM ki221-std
```

**Built-in algorithms** (~40 total): simple transforms (`not`, `swap`, `plus1`, `minus1`, `same`, `zero`), XOR with known constants (`xor-0d0b0507`, `xor-5a`, `xor-a5`, `xor-dead`, etc.), rotations (`ror4`, `ror8`, `rol4`, `rol8`, `ror16`), compound transforms (`swap-not`, `not-swap`, `not-plus1`, `mul3plus1`), Kia-specific (`static-6fd5`, `xor-6fd5`, `add-6fd5`, `sub-6fd5`), and parameterized Hyundai/Kia algorithms (`ki203-*`, `ki221a1-*`, `ki221-std`).

**Output:** Tabular display showing each algorithm attempted, the seed received, key computed, and result (accepted, invalid key, or lockout). Automatically handles NRC 0x36 (lockout — stops immediately) and NRC 0x37 (time delay — waits 11s and re-establishes session before retrying).

**Safety:** Security access is a prerequisite for write operations (`2E`) but does NOT itself modify anything. The algorithms are read-only seed-key computations. However, once security access is granted, be careful with subsequent commands.

##### `--monitor` flag (live refresh)

Turns a `--multi` pipeline into a live-refreshing monitor. Non-query steps (session, skm-wake, sleep) run once as setup; all `query` steps are then polled repeatedly, with Rich Live updating the display in-place. Sessions are kept alive with background TesterPresent keepalives.

```bash
# Monitor BMS every 5s (default interval)
canreq.py --multi "query BMS 2101" --monitor

# Monitor BCM (all known PIDs in bcm.yaml), keep unique payloads per PID
canreq.py --monitor --keep-unique --multi "query BCM"

# Monitor IGPM status with 2s interval, wake from deep sleep
canreq.py --multi "session IGPM --wake" "query IGPM BC03 BC06" --monitor 2

# Monitor BCM voltage ADCs with full payload history (every cycle)
canreq.py --monitor 2 --keep-all --multi "session BCM --wake" "query BCM B003 B004"
```

**Hex display features:**

- **Byte-level change highlighting:** Changed bytes get a highlighted background adapted from their verification color (green -> dark green bg, yellow -> dark goldenrod bg, grey -> grey37 bg). A green dot appears next to PIDs with changed payloads.
- **Verification coloring:** Bytes covered by verified parameters are green, unverified are yellow, uncovered bytes are dim grey.
- **Unmapped PIDs:** Shown with ASCII representation alongside the hex dump.

**`--keep-unique` flag:** Retains only distinct payloads seen for each PID, displayed as a flat chronological list (oldest at top, newest at bottom). Each row highlights bytes that changed from its predecessor. A count is shown next to the PID header (e.g. `22B003 (3 entries)`). Without either `--keep` flag, only the current payload is displayed.

**`--keep-all` flag:** Retains every payload from every poll cycle (including duplicates), with timestamps. Useful for logging all responses over time, even when values don't change.

**`--save` flag:** Prompts for session metadata (label, state, notes) and saves results to `captures/YYYY-MM-DD.yaml`. Works with `--scan`, `--raw`, `--discover`, and `--monitor --keep-unique/--keep-all`. Labels are auto-suggested based on the command (press Enter to accept). Examples:

```bash
canreq.py --scan --tx 7E4 --service 22 --range BC01-BC0B --save
# -> auto-suggests: "Scan BMS 22 BC01-BC0B"

canreq.py --raw 7E4:2101 --save
# -> auto-suggests: "Raw BMS 2101"

canreq.py --discover --save
# -> auto-suggests: "Discovery scan 700-7EF"

canreq.py --multi "query BCM C00B B003 B004" --monitor 5 --keep-unique --save
# ... monitor runs, Ctrl+C ...
# -> Saved 6 capture(s) to 2026-04-18.yaml
```

Press Ctrl+C to stop monitoring.

**Session management:** The SessionManager tracks all ECUs with active extended sessions and sends TesterPresent (`3E00`) keepalives to stale sessions before each foreground command. In the REPL, a background task sends keepalives every 2s. This allows querying one ECU while keeping sessions alive on others (e.g., keeping SKM ACC relay active while reading BCM charge port data).

**Multi-ECU REPL commands** (via `--repl` or `repl` step): same sub-commands as `--multi` pipeline steps (`session`, `query`, `raw`, `skm-wake`, `scan`, `sleep`, `quit`). The `!` prefix is optional.

##### `--identity` flag

Queries standard UDS identity DIDs from an ECU and prints decoded results. Covers the common Hyundai/Kia identity DID set. Requires `--tx`. Use `--session` for most ECUs; use `--wake` for deep-sleeping ECUs (IGPM). Silently skips unsupported DIDs (NRC responses). Use `!identity` in interactive mode after setting a header with `ATSH`.

```sh
canreq.py --identity --tx 7A0 --wake --wican home
```

Known results (deep sleep, no ACC):
- **BCM (0x7A0):** F18C=`1705310070`, F18B=`2017-05-31`, F100=`180`, F194=`100`, F195=`0880`, F196=`220`, F1A4=`620`
- **IGPM (0x770):** F18B=`2017-06-06`, F100=`20`, F101=`160205`, F110=`(empty)`, F194=`100`, F196=`109`

##### `--iocontrol` flag

Executes IOControl (service `2F`) commands defined in the `iocontrol:` section of pids/ YAML files. Session and hold behavior are auto-applied from the YAML metadata.

```bash
# List all IOControl DIDs for an ECU (no CAN connection needed)
canreq.py --iocontrol IGPM
canreq.py --iocontrol BCM --json

# Execute ON command (auto-session, hold until Ctrl+C if hold: true)
canreq.py --iocontrol IGPM --did BC01

# Execute OFF command
canreq.py --iocontrol IGPM --did BC01 --off

# In multi pipeline (session managed by pipeline)
canreq.py --multi "iocontrol IGPM BC01" "sleep 3" "iocontrol IGPM BC01 --off"
```

**Behavior:**
- Without `--did`: lists all IOControl DIDs in a table (DID, label, ON/OFF commands, verified, hold). Works offline — no WiCAN connection.
- With `--did`: sends the ON command (or OFF with `--off`). Auto-enters extended diagnostic session if `session: true` in YAML.
- If `hold: true` in YAML (default): keeps TesterPresent alive until Ctrl+C, then auto-sends the OFF command on release.
- If `hold: false` (e.g. SKM relays): sends command and exits immediately.

ECUs with IOControl DIDs: IGPM, BCM, SKM, PSM, VESS (see respective `pids/*.yaml` files).

##### `--scan` flag

Iterates through a range of PIDs or DIDs, sending each as a UDS request, and reports which ones respond positively. Standard way to discover what data an ECU exposes.

Requires `--tx` (ECU TX ID). Optional arguments:

| Argument    | Default | Description                                                                 |
|-------------|---------|-----------------------------------------------------------------------------|
| `--service` | `21`    | UDS service ID (hex). Common: `21` (live data), `22` (DID read), `2F` (IOControl), `31` (routine) |
| `--range`   | `01-FF` | PID/DID range (hex). Auto-widens to 4-digit for services 22/2F/31          |
| `--append`  | —       | Hex bytes appended after each DID (e.g. `03` for IOControl ShortTermAdjustment) |
| `--session` | off     | Enter extended diagnostic session (`10 03`) before scanning                 |
| `--wake`    | off     | Wake ECU from deep sleep first (implies `--session`)                        |
| `--save`    | off     | Save results to `captures/YYYY-MM-DD.yaml` (prompts for label)             |
| `--verbose` | off     | Show NRC codes and errors for non-responding DIDs                           |
| `--json`    | off     | Output full results as JSON                                                 |

```bash
# Scan all service 21 PIDs on BMS (0x7E4)
canreq.py --scan --tx 7E4 --service 21 --range 01-FF

# Scan service 22 DIDs on IGPM (needs extended session + wake)
canreq.py --scan --tx 770 --service 22 --range BC00-BCFF --session --wake

# IOControl scan with ShortTermAdjustment suffix (2F{DID}03)
canreq.py --scan --tx 7E4 --service 2F --range E000-E0FF --append 03 --session

# Scan and auto-save results to captures/
canreq.py --scan --tx 7A0 --service 22 --range B000-B0FF --session --save

# In a --multi pipeline
canreq.py --multi "session IGPM --wake" "scan 770 22 BC00-BCFF"
```

**Safety notes:**
- Only run ONE scan at a time — parallel scans lock up the WiCAN device.
- Use small ranges first to gauge ECU response time, then expand.
- IOControl scans (`--service 2F`) may actuate physical hardware — ensure the car is in a safe state.

##### `--discover` flag

Sweeps a range of CAN TX addresses to find responding ECUs. Sends `10 01` (default session request) to each address and reports which ones respond (positive or NRC — both indicate a live ECU).

```bash
canreq.py --discover                       # Sweep 0x700-0x7EF (default)
canreq.py --discover --range 600-6FF       # Custom range
canreq.py --discover --delay 0.5           # Slower pacing (default: 0.2s)
```

##### `--session` flag

Enters extended diagnostic session (`10 03`) before sending requests. Required for ECUs like IGPM (0x770) that only respond to `22BCxx` reads and `2FBCxx` IOControl in extended session. Starts a background TesterPresent (`3E 00`) keepalive every 2s. Works with all modes.

##### `--wake` flag

Wakes ECUs from deep sleep before entering extended session. Sends `10 01` as a CAN wake-up frame — triggers the CAN transceiver even when the ECU is in deep sleep. The first attempt may return NO DATA while the transceiver powers up; a 0.5s delay allows the ECU to initialize. Implies `--session`.

Currently the IGPM (0x770) and BCM (0x7A0) are known to wake from deep sleep via this method. Other ECUs (BMS, VCU, MCU) require the ACC relay to be powered.

##### `--hold` flag

Keeps the extended diagnostic session alive after the command completes, until Ctrl+C. Useful for IOControl commands (`2FBCxx03`) where the actuator releases as soon as the session drops. Implies `--session`. Only works with `--raw` mode.

**Interactive mode built-in commands:** `!decode` (decode last response), `!hexdump` (hex dump), `!info <ECU>` (show ECU info), `!list` (list ECUs), `!identity` (query identity DIDs for current header ECU), `!reboot` (reboot WiCAN), `!quit`.

**Dependencies:** `websockets`, `pyyaml`. Optional: `requests` (for `--reboot`).

**ALWAYS use `canreq.py` for any CAN/UDS communication with the vehicle. Never write custom Python code to open a WebSocket, send ELM327 commands, or talk to the WiCAN device. If `canreq.py` doesn't support a particular operation, discuss with the user before working around it.**

**IMPORTANT:** Using the WebSocket terminal overrides AutoPID mode. The WiCAN must be rebooted after a terminal session for AutoPID (MQTT data feed) to resume (though user must be asked first).

**Never reboot the WiCAN without asking the user first.** Always ask whether they are done probing the CAN bus before suggesting or triggering a reboot.

**CRITICAL: Only one connection at a time.** Never run multiple `canreq.py` commands in parallel — the second connection will either fail or lock up the device, requiring a power cycle to recover.

#### wican-cli (separate package)

WiCAN device management is handled by the standalone [`wican-cli`](https://github.com/philipkocanda/wican-cli) package (`pip install wican-cli`). Manages device configuration, sleep/power saving, protocol switching, status, OBD log queries, and reboots via the REST API.

```bash
wican config                          # View full device config
wican config --section sleep          # View sleep settings only
wican config --save                   # Save config snapshot to configs/
wican sleep                           # Show current sleep status
wican sleep --disable                 # Disable sleep
wican sleep --enable                  # Re-enable sleep
wican sleep --voltage 12.5 --time 10  # Adjust thresholds
wican status                          # Device status summary
wican protocol                        # Show current protocol + options
wican protocol --set slcan            # Switch to SLCAN (reboots device)
wican protocol --set auto_pid         # Switch back to AutoPID
wican logs                            # List SD card log databases
wican logs --download                 # Download all log DBs
wican logs --params                   # List all logged parameters
wican logs --query SOC_BMS --limit 20 # Query parameter time series
wican reboot                          # Reboot device
wican --wican vpn sleep               # Use VPN address
```

**Protocol modes** are mutually exclusive: `auto_pid` (normal MQTT polling), `slcan` (for SavvyCAN/candump), `elm327` (OBD-II apps), `savvycan` (native SavvyCAN), `realdash66`. Switching stops the current mode, applies the new one, and reboots the device.

**Important:** `POST /store_config` replaces the entire config on flash and auto-reboots the device. The tool handles this by doing a GET first, modifying only the changed fields, and POSTing the full config back.

**Tip: Disable sleep during reverse engineering sessions.** When probing ECUs with `canreq.py`, the WiCAN may go to sleep mid-session if the 12V battery voltage drops below the threshold (especially with engine off). Disable sleep before starting a session and re-enable it when done:

```bash
wican sleep --disable    # Before RE session
# ... do your CAN bus work ...
wican sleep --enable     # After RE session
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

### Conversion Table (WiCAN ↔ ISO-TP ↔ Torque ↔ bix)

Each CAN frame has 8 data bytes. PCI bytes (at WiCAN indices 0, 8, 16, 24, ...) are consumed by ISO-TP framing and have no ISO-TP/Torque/bix equivalent. Torque 1/bix 1 are for 1-byte subfunctions (service `21xx`), Torque 2/bix 2 for 2-byte subfunctions (service `22xxxx`).

| WiCAN | ISO-TP | Torque 1 | bix 1 | Torque 2 | bix 2 |
| ----- | ------ | -------- | ----- | -------- | ----- |
| 0     |        |          |       |          |       |
| 1     |        |          |       |          |       |
| 2     | 0x00   |          |       |          |       |
| 3     | 0x01   |          |       |          |       |
| 4     | 0x02   | A        | 0     |          |       |
| 5     | 0x03   | B        | 8     | A        | 0     |
| 6     | 0x04   | C        | 16    | B        | 8     |
| 7     | 0x05   | D        | 24    | C        | 16    |
| 8     |        |          |       |          |       |
| 9     | 0x06   | E        | 32    | D        | 24    |
| 10    | 0x07   | F        | 40    | E        | 32    |
| 11    | 0x08   | G        | 48    | F        | 40    |
| 12    | 0x09   | H        | 56    | G        | 48    |
| 13    | 0x0A   | I        | 64    | H        | 56    |
| 14    | 0x0B   | J        | 72    | I        | 64    |
| 15    | 0x0C   | K        | 80    | J        | 72    |
| 16    |        |          |       |          |       |
| 17    | 0x0D   | L        | 88    | K        | 80    |
| 18    | 0x0E   | M        | 96    | L        | 88    |
| 19    | 0x0F   | N        | 104   | M        | 96    |
| 20    | 0x10   | O        | 112   | N        | 104   |
| 21    | 0x11   | P        | 120   | O        | 112   |
| 22    | 0x12   | Q        | 128   | P        | 120   |
| 23    | 0x13   | R        | 136   | Q        | 128   |
| 24    |        |          |       |          |       |
| 25    | 0x14   | S        | 144   | R        | 136   |
| 26    | 0x15   | T        | 152   | S        | 144   |
| 27    | 0x16   | U        | 160   | T        | 152   |
| 28    | 0x17   | V        | 168   | U        | 160   |
| 29    | 0x18   | W        | 176   | V        | 168   |
| 30    | 0x19   | X        | 184   | W        | 176   |
| 31    | 0x1A   | Y        | 192   | X        | 184   |
| 32    |        |          |       |          |       |
| 33    | 0x1B   | Z        | 200   | Y        | 192   |
| 34    | 0x1C   | AA       | 208   | Z        | 200   |
| 35    | 0x1D   | AB       | 216   | AA       | 208   |
| 36    | 0x1E   | AC       | 224   | AB       | 216   |
| 37    | 0x1F   | AD       | 232   | AC       | 224   |
| 38    | 0x20   | AE       | 240   | AD       | 232   |
| 39    | 0x21   | AF       | 248   | AE       | 240   |
| 40    |        |          |       |          |       |
| 41    | 0x22   | AG       | 256   | AF       | 248   |
| 42    | 0x23   | AH       | 264   | AG       | 256   |
| 43    | 0x24   | AI       | 272   | AH       | 264   |
| 44    | 0x25   | AJ       | 280   | AI       | 272   |
| 45    | 0x26   | AK       | 288   | AJ       | 280   |
| 46    | 0x27   | AL       | 296   | AK       | 288   |
| 47    | 0x28   | AM       | 304   | AL       | 296   |
| 48    |        |          |       |          |       |
| 49    | 0x29   | AN       | 312   | AM       | 304   |
| 50    | 0x2A   | AO       | 320   | AN       | 312   |
| 51    | 0x2B   | AP       | 328   | AO       | 320   |
| 52    | 0x2C   | AQ       | 336   | AP       | 328   |
| 53    | 0x2D   | AR       | 344   | AQ       | 336   |
| 54    | 0x2E   | AS       | 352   | AR       | 344   |
| 55    | 0x2F   | AT       | 360   | AS       | 352   |
| 56    |        |          |       |          |       |
| 57    | 0x30   | AU       | 368   | AT       | 360   |
| 58    | 0x31   | AV       | 376   | AU       | 368   |
| 59    | 0x32   | AW       | 384   | AV       | 376   |
| 60    | 0x33   | AX       | 392   | AW       | 384   |
| 61    | 0x34   | AY       | 400   | AX       | 392   |
| 62    | 0x35   | AZ       | 408   | AY       | 400   |
| 63    | 0x36   | BA       | 416   | AZ       | 408   |
| 64    |        |          |       |          |       |
| 65    | 0x37   | BB       | 424   | BA       | 416   |
| 66    | 0x38   | BC       | 432   | BB       | 424   |
| 67    | 0x39   | BD       | 440   | BC       | 432   |
| 68    | 0x3A   | BE       | 448   | BD       | 440   |
| 69    | 0x3B   | BF       | 456   | BE       | 448   |
| 70    | 0x3C   | BG       | 464   | BF       | 456   |
| 71    | 0x3D   | BH       | 472   | BG       | 464   |

## Memory

### 2026-04-19 — AutoPID stops at sleep_volt threshold even when WiCAN stays connected

When 12V battery drops to `sleep_volt` (currently 12.0V), the WiCAN stops AutoPID polling but remains WiFi-connected. This creates a deceptive state: device is reachable, `/autopid_data` returns data, but values are stale from the last successful poll. Observed: after parking, DRL/low beam/tail lights showed "on" via MQTT because the WiCAN polled during a drive with lights on, then stopped polling when voltage dropped to 12.0V. Direct `canreq.py` queries confirmed lights were actually off. Fix: values self-correct on next successful poll. Consider raising `sleep_volt` slightly (e.g. 12.2V) to create a clearer gap vs. the actual sleep trigger.

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
| VCU       | 0x7E2  | 🔶 Partial   | 2101 working (gear, vehicle state, speed). Speed is in MPH — `((S20*256)+B19)*1.609344/100` (high byte S20 signed, low byte B19 unsigned, MPH×100→km/h); reads 0 at park (verified live) and ~+4% vs dashboard, GPS/multi-point still pending. (Do NOT use B21 as low byte — it's a separate signal, non-zero/jittery at standstill.) 2102: inverter DC-link voltage `B17*2` (verified vs BMS pack ~369V) + 12V aux `B18/10`; the Ioniq puts motor RPM/torque/temps/phase-currents in the separate MCU ECU (0x7E3), NOT VCU 2102 (Kia Soul VMCU cross-ref, 2026-07-20). Regen mode and ECO/Sport/Normal TODO. |
| MCU       | 0x7E3  | 🔶 Partial   | Part 36600-0E250 (inverter). **Motor RPM decoded: 2102 `[S10:S11]`** (signed BE) — verified vs VCU speed (rpm/km-h ≈ 62.8, gear 7.412), 0 at park (2026-07-20). Still TODO: torque command/estimated torque (signed 16-bit, tracks accel not speed), phase-current RMS, temps (short cool drive didn't warm them — 2101 candidate bytes were bimodal/constant), HV DC-link. 2102 B27-B47 is a static calibration/offset block (matches Soul resolver/phase offsets + 21F2 `EHEL-MS2` cal ID). Ioniq5 refs `22E001`, `22E009`. |
| HVAC      | 0x7B3  | 🔶 Partial   | PID `220100` (was `2201006` — fixed in YAML). Byte offsets partially verified. IAT/AAT/evaporator temps, not entirely correctly mapped and some values still unknown. More PIDs (`220101`, `220102`) available but not yet explored. |
| CLU       | 0x7C6  | ✅ Working    | `22B002` → odometer (UINT24 big-endian at byte 9). Also has imperial odometer. e-Niro sheet has more (range, time driven, speed limit, cruise) — TODO. |
| IGPM      | 0x770  | ✅ Working   | Full IOControl map complete (BC00-BCFF scanned). 27 actuator DIDs, 11 status registers. Confirmed: lights, horn, turn signals, DRL, CHMSL, brake lights, trunk, door lock/unlock, charge cable lock/unlock. Wakes from deep sleep via `1001` — **status reads (BC03/BC04/BC06) confirmed working during deep sleep** (doors, locks, lights, ignition all readable). See `docs/IOControl CLI commands.md`. |
| SKM/SMK   | 0x7A5  | ✅ Working   | ACC relay IOControl (`2FB108030A0A05`) — UDS positive response confirmed but **relay only physically closes with fob nearby**. Without fob, `6FB10803` returned but IGPM BC03 ignition byte stays `0x00`. `skm-wake` command now verifies via IGPM BC03 (step 4/4). **Wakes from rapid-fire `1001` without fob** (2 attempts at 64ms timeout). ACC/IGN1/IGN2 IOControl accepted but powertrain ECUs stay dead. ACC releases when session drops. See `docs/wakeup-research.md`. |
| LDC       | 0x7E5  | 🔶 Partial   | Confirmed LDC/OBC combined (ecu_id `AEEOBC51`). **Available in ACC2/IGN**. 2101 (48-byte): 2026-07-20 verified via live READY-vs-charging + BMS cross-check — LDC_HV_INPUT_V=`[B45:B46]/100` (386.9V=BMS pack), LDC_OUTPUT_V=14.15V (=BMS aux), LDC_TEMP=`B19-100` (21→65°C over 3h charge, Soul -100 offset), OBC_CHARGE_V `[B10:B11]`=397V, OBC_OUTPUT_V `[B12:B13]`=386V (idle in READY), OBC_DC_A=5.2A (=BMS charge current), pilot 10% at 10A AC. Removed bogus OBC temps (S15/S17=current bytes, S16=PCI 0x22) + OBC_AC_INPUT_V(=B15 current). **OBC internal temps are NOT broadcast** — full charging capture confirms no heatsink/inside/water temp (candidate bytes are dead constants). 2102 = static calibration. 2103: NRC 0x12. |
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
- [ ] **VCU speed** — unit resolved as MPH (converted to km/h in expression), low byte = B19 unsigned (reads 0 at park); still verify with GPS across full range, and validate ESC `22C101` `REAL_SPEED_KMH` (B12) as the true dashboard speed source (only captured at standstill so far)
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan/EWP control DID** — Kingbolen scanner can actuate fan via UDS, specific DID unknown. Scan BMS/MCU `2F E0xx 03` or sniff Kingbolen
- [ ] **IOControl testing** — BCM `2f b0 xx` untested on Ioniq. IGPM fully scanned (BC00-BCFF). SKM B108 ACC confirmed. Remaining: BC0A, BC0C, BC1B, BC1C untested; BC25/BC42/BC43/BC44 accepted but no visible effect
- [ ] **Remote BMS read** — SKM wakes from rapid-fire `1001` without fob (2-17 attempts). ACC/IGN1 IOControl accepted. BCM wakes (TPMS/charge port work). But powertrain ECUs (BMS/VCU/MCU) remain dead — relay doesn't latch. Workarounds: spare fob, direct relay wiring, or reads only during charging.
- [ ] **Verify unverified PIDs** — 44 params from Kia Niro PRs. Most ECUs (IGPM, BCM, ESC) require ACC/ignition on
