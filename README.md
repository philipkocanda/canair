# ioniq-can

**CAN bus reverse engineering toolkit for the 2017 Hyundai Ioniq Electric (28kWh)**

> Work in progress — actively mapping ECUs, decoding parameters, and building tooling.

This project uses a [WiCAN Pro](https://www.meatpi.com/products/wican-pro) OBD-II WiFi dongle to communicate with the vehicle's Electronic Control Units (ECUs) via Unified Diagnostic Services (UDS) and Keyword Protocol 2000 (KWP2000). The goal is to decode and document the car's internal diagnostic data and publish it via MQTT to Home Assistant for remote monitoring.

## What's been mapped so far

- **30 ECUs** discovered on the CAN bus
- **220+ parameters** defined (192 verified), including:
  - Battery SOC (State of Charge), voltage, current, power
  - All 96 individual cell voltages
  - State of Health (SOH) and cumulative lifetime energy
  - Tyre pressures and temperatures
  - HVAC / climate control state
  - Gear position, vehicle speed, steering angle
  - Charging state (AC / DC / CCS)
  - Ambient temperature

## Project structure

The primary working directory is **`wican-pro/`**:

```
wican-pro/
├── canreq.py              # Main CLI — send CAN/UDS requests via WebSocket
├── wican.py               # WiCAN device management (config, sleep, reboot)
├── generate-profile.py    # Generate WiCAN vehicle profiles from YAML definitions
├── decode.py              # Decode captured payloads using PID definitions
├── query-captures.py      # Query raw UDS payloads across capture dates
├── validate-pids.py       # Schema validator for pids/ YAML files
├── validate-captures.py   # Schema validator for captures/ YAML files
├── bix.py                 # Byte index notation converter (4 systems)
│
├── canlib/                # Shared Python library
│   ├── elm327.py          #   ELM327 / ISO-TP protocol parsing
│   ├── expression.py      #   WiCAN expression evaluator (Bnn, Snn notation)
│   ├── session_manager.py #   UDS session management
│   ├── terminal.py        #   WebSocket terminal interface
│   └── modes/             #   canreq.py sub-modes (scan, interactive, IOControl, etc.)
│
├── pids/                  # PID definitions per ECU (source of truth)
│   ├── bms.yaml           #   Battery Management System (largest — 220+ params)
│   ├── vcu.yaml           #   Vehicle Control Unit
│   ├── mcu.yaml           #   Motor Control Unit
│   ├── bcm.yaml           #   Body Control Module
│   ├── hvac.yaml          #   Climate Control
│   └── ... (25+ ECU files)
│
├── captures/              # Raw UDS response payloads by date
├── vehicle-profiles/      # Generated WiCAN JSON profiles
├── ecus.yaml              # Master ECU address registry (30 ECUs)
├── configs/               # WiCAN device config backups
├── docs/                  # Documentation (CLI reference, research notes)
├── logs/                  # Command/response logs (gitignored)
└── tests/                 # pytest test suite
```

The top-level directory also contains earlier CarScanner captures (by date), reference spreadsheets, and cross-reference material from related vehicles (Kona, Kia Soul EV).

## Key tools

| Script | Purpose |
|--------|---------|
| `canreq.py` | Send UDS/KWP2000 requests to ECUs via the WiCAN WebSocket terminal. Supports interactive mode, parameter queries, DID scanning, IOControl actuation, and Smart Key Module wake-up. |
| `wican.py` | Manage the WiCAN Pro device — view/save config, toggle sleep, switch protocol modes, query SD card logs, reboot. |
| `generate-profile.py` | Read all `pids/*.yaml` definitions and produce a WiCAN-compatible JSON vehicle profile. Can upload directly to the device or diff against the current config. |
| `decode.py` | Apply byte-level expressions from PID definitions to historical captures, showing decoded values and spotting anomalies. |
| `query-captures.py` | Search across all capture files — show summaries, diffs between dates, or latest values per ECU/PID. |

## IOControl — what can be remotely controlled

Beyond reading diagnostic data, the toolkit can **actuate** vehicle hardware via UDS IOControlByIdentifier (service `0x2F`). All actuators auto-release when the diagnostic session ends (Ctrl+C or timeout) — no permanent state changes.

### IGPM (Integrated Power Gate Module, `0x770`)

Works from deep sleep with `--wake`. No ACC/IGN required.

| Category | Actuators |
|----------|-----------|
| Lights | Low beam, high beam, DRL, tail lights, rear fog, left/right indicators, rear brake lights (L/R), CHMSL, luggage lamp |
| Horn | Horn |
| Locks | Door lock all, door unlock all, trunk release |
| Charge cable | Cable lock, cable unlock |

### BCM (Body Control Module, `0x7A0`)

Requires extended session + SKM ACC power.

| Category | Actuators |
|----------|-----------|
| Mirrors | Fold, unfold |
| Interior | Room lamp, puddle lights, heated steering wheel + LED |
| Wipers | Wiper motor (slow/fast) |
| Sensors | Parking sensor buzzer |
| Warnings | Seatbelt warning (driver + 3 passengers) |

### SKM (Smart Key Module, `0x7A5`)

Requires keyfob proximity for physical relay engagement.

| Relay | Effect |
|-------|--------|
| ACC (`B108`) | Turns on accessories, dash, infotainment, unlocks doors |
| IGN1 (`B109`) | Wakes HV system (untested, use with caution) |

### HVAC (`0x7B3`) — work in progress

14+ actuator DIDs discovered but unverified. Goal: remote cabin pre-conditioning (heat/cool before driving). Research ongoing.

### Where IOControl commands are defined

- **PID/DID YAML files:** `wican-pro/pids/igpm.yaml`, `bcm.yaml`, `skm.yaml`, `hvac.yaml`, `vess.yaml`, `psm.yaml` — source of truth for all actuator definitions, parameters, and verification status.
- **IOControl mode implementation:** `wican-pro/canlib/modes/iocontrol.py` — TUI-based interactive actuator control and single-command execution.
- **Quick reference docs:** `wican-pro/docs/IOControl CLI commands.md` — copy-paste command examples.

## How the CLI works

The main tool is `canreq.py` — an async Python CLI that connects to the WiCAN Pro via WebSocket, enters ELM327 terminal mode, and sends UDS/KWP2000 requests over ISO-TP.

**Architecture:**

```
canreq.py (argparse + argcomplete)
  └── canlib/
      ├── terminal.py        # WebSocket connection (WiCANTerminal)
      ├── elm327.py          # ELM327/ISO-TP protocol parsing
      ├── session_manager.py # Multi-ECU sessions + TesterPresent keepalive
      └── modes/             # 17 sub-mode implementations
```

**Key modes:**

| Mode | Flag | Purpose |
|------|------|---------|
| Parameter query | `--param NAME` / `--ecu NAME` | Decode named parameters from YAML definitions |
| IOControl | `--iocontrol ECU [--did DID]` | Interactive TUI or single actuator command |
| Multi-ECU pipeline | `--multi "CMD" "CMD" ...` | Sequenced commands with session management |
| Scan | `--scan --tx ID --service SVC --range START-END` | Probe DID ranges for responses |
| SKM wakeup | `--skm-wakeup [--level acc\|ign1]` | Wake ECUs via Smart Key Module relay |
| Raw | `--raw TX:PAYLOAD` | Direct hex request (no decoding) |
| Routines | `--routines ECU` | RoutineControl (0x31) TUI |
| Monitor | `--multi "..." --monitor [SEC]` | Live-refreshing poll loop |

**Cross-cutting flags:** `--session`, `--wake`, `--hold`, `--timeout`, `--save`, `--json`, `--verbose`, `--reboot`, `--wican home|vpn|IP`, `--unsafe`

## Protocols

| Protocol | Used for |
|----------|----------|
| **UDS** (ISO 14229) | Body/comfort ECUs — session control, ReadDataByIdentifier, IOControl, RoutineControl |
| **KWP2000** (ISO 14230) | Powertrain ECUs (BMS, VCU, MCU, LDC/OBC) — ReadDataByLocalIdentifier |
| **ISO-TP** (ISO 15765-2) | Transport layer for multi-frame CAN messages |
| **ELM327 AT commands** | Communication protocol between tooling and the WiCAN dongle |

## Tech stack

- **Python 3.12** with `uv` for package management
- `websockets` — async WebSocket communication with WiCAN
- `pyyaml` — YAML-based PID definitions and captures
- `rich` — terminal output formatting
- `requests` — WiCAN HTTP API (config upload/download)
- `pytest` + `ruff` — testing and linting

## Getting started

```bash
cd wican-pro
uv sync            # Install dependencies
uv run canreq.py --help
uv run wican.py --help
```

The WiCAN Pro must be powered on and connected to your WiFi network (or you connect to its AP). Default WebSocket endpoint: `ws://192.168.80.1/ws`.

## License

Not yet determined — this is a personal research project.
