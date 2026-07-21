# canair

**CLI for reverse engineering CAN/OBD diagnostics over-the-air using the WiCAN Pro**

This project interfaces with a [WiCAN Pro](https://www.meatpi.com/products/wican-pro) OBD-II WiFi dongle to communicate with the vehicle's ECUs via diagnostic protocols (UDS and KWP2000). It comes with tools for discovering, decoding, analyzing and documenting the car's internal diagnostic data so it can be turned into a [WiCAN vehicle profile](https://meatpihq.github.io/wican-fw/config/automate/new_vehicle_profiles) or for general purpose sharing and documentation.

Everything ships as a single installable CLI, **`canair`**. Vehicle data lives in a *profile* bundle; the repo ships `profiles/ioniq-2017/` as the default/example profile.
Originally this project was built for reverse engineering a 2017 Hyundai Ioniq AE EV (28kWh), but it now supports multiple vehicle profiles and is no longer tied to a single vehicle.

Some highlights of this project's features:

<img width="838" height="497" alt="Screenshot 2026-07-21 at 11 45 49" src="https://github.com/user-attachments/assets/7cab4e56-550a-4443-83dd-2f96bb5eedc7" />

Example screenshot of analyzing/decoding a captured signal using `canair decode <query> --plot`


<img width="952" height="254" alt="Screenshot 2026-07-21 at 11 49 19" src="https://github.com/user-attachments/assets/f44a53f9-3849-46ec-9934-ba5802ae0f27" />

Example screenshot of stepping through captures one by one using `canair captures <query> --step`


<img width="960" height="977" alt="Screenshot 2026-07-21 at 11 47 24" src="https://github.com/user-attachments/assets/791010b8-0f8d-44d5-8cfd-16c5e04a7305" />

Example screenshot of viewing capture diffs using `canair captures <query> --diff`. Green/yellow represents PID verification state, changed bytes are highlighted between frames. This byte diff view is the default then using `canair query` on a live vehicle.

-----

### What's in the box

- 🔌 **`canair query`** — query ECUs live over WiFi or VPN: read battery stats, decode parameters, scan for unknown DIDs, and actuate hardware (lights, locks, horn, trunk) via IOControl (`canair scan`/`discover`/`io`/`routines`/`raw`)
- 📦 **`canair wican`** — turn YAML PID definitions into a WiCAN vehicle profile and upload it to the device in one command
- 🔬 **`canair decode`** — replay captured UDS payloads against PID definitions to validate expressions and spot anomalies
- 🗂️ **`canair captures`** — search and diff historical captures across dates and vehicle states
- 🧮 **`canair bix`** — convert between the four byte-index notations used by WiCAN, ISO-TP, Torque, and OBDb
- 📐 **`profiles/ioniq-2017/pids/`** — 25+ YAML files defining every known parameter per ECU (the single source of truth for the bundled example profile)

## Hyundai Ioniq 2017 Electric (28 kWh) — what `canair` reads & controls

While the tooling is vehicle-agnostic, the bundled `ioniq-2017` profile makes this
a practical, ready-to-use **OBD-II / UDS diagnostics toolkit for the 2017 Hyundai
Ioniq Electric (28 kWh, `AE` platform)**. Plug in a WiCAN Pro and you can read live
battery, motor, charging, climate, and body data over WiFi — no dealer tools
required. If you own an Ioniq 28 kWh and want deeper telemetry than a generic
OBD app provides, this profile is for you.

### What's been mapped so far

- **30 ECUs** discovered on the CAN bus
- **220+ parameters** defined (192 verified), including:
  - Battery SOC (State of Charge), voltage, current, power
  - All 96 individual cell voltages
  - State of Health (SOH) and cumulative lifetime energy
  - Tyre pressures and temperatures
  - HVAC / climate control state and interior temperatures
  - Gear, vehicle speed, motor speed, torque, and temperatures
  - Charging state (AC / DC / CCS) & charge port lock
  - Door locks, trunk, lights, indicators
  - Ambient temperature

## Project structure

```
├── canlib/                     # The canair CLI + shared library
│   ├── cli.py                  #   argparse entrypoint — the `canair` command
│   ├── commands/               #   one module per subcommand (query, scan, decode, captures, wican, bix, …)
│   ├── elm327.py               #   ELM327 / ISO-TP protocol parsing
│   ├── expression.py           #   WiCAN expression evaluator (Bnn, Snn notation)
│   ├── session_manager.py      #   UDS session management
│   ├── terminal.py             #   WebSocket terminal interface
│   ├── modes/                  #   query sub-modes (scan, interactive, IOControl, etc.)
│   └── schema/                 #   tool-owned schemas: pids_schema.yaml, captures_schema.json
│
├── profiles/                   # Vehicle profile bundles (each = one car's data)
│   └── ioniq-2017/             #   bundled default/example profile
│       ├── pids/               #     PID definitions per ECU (source of truth)
│       │   ├── _meta.yaml      #       car model + AT init string
│       │   ├── bms.yaml        #       Battery Management System (largest — 220+ params)
│       │   ├── vcu.yaml        #       Vehicle Control Unit
│       │   ├── mcu.yaml        #       Motor Control Unit
│       │   ├── bcm.yaml        #       Body Control Module
│       │   ├── hvac.yaml       #       Climate Control
│       │   └── ... (25+ ECU files)
│       ├── captures/           #     Raw UDS response payloads by date
│       ├── ecus.yaml           #     Master ECU address registry (30 ECUs)
│       └── out/                #     Generated WiCAN JSON profiles
│
├── config.example.yaml         # Template for ~/.config/canair/config.yaml
├── configs/                    # WiCAN device config backups
├── logs/                       # Command/response logs (gitignored)
└── tests/                      # pytest test suite
```

Local (uncommitted) profiles live in `~/.config/canair/profiles/` and shadow bundled ones by name.

The `research/` directory contains earlier CarScanner captures, reference spreadsheets, and cross-reference material from related vehicles (Kona, Kia Soul EV).

## Key tools

All functionality is exposed as `canair <subcommand>`.

| Subcommand | Purpose |
|--------|---------|
| `canair query` | Send UDS/KWP2000 requests to ECUs via the WiCAN WebSocket terminal. Supports parameter queries, positional query steps (multi-ECU pipeline), monitoring, and more. Companions: `canair scan` (DID scanning), `canair discover`, `canair io` (IOControl actuation), `canair routines`, `canair raw`, `canair repl` (interactive). |
| `canair wican` | Read all `pids/*.yaml` definitions and produce a WiCAN-compatible JSON vehicle profile. Can upload directly to the device or diff against the current config. |
| `canair decode` | Parameter/value-centric decoding: shows each PID parameter's value range across all captures (default), plus statistics (`--stats`), correlation vs a reference signal (`--corr`), an interactive signal explorer (`--plot` — sweep ImHex-style byte interpretations and transforms, plot across captures), and candidate-expression testing without editing YAML (`--try`). |
| `canair captures` | Search across all capture files — show summaries, diffs between dates, or latest values per ECU/PID. Scope any mode by date with `--since`/`--until`/`--date`. |
| `canair research` | Report the open reverse-engineering backlog from the per-ECU `research:` sections (by type/status/priority/prerequisite). The "what should I decode next?" entry point. |
| `canair coverage` | Audit PID definitions for decoding gaps — unmapped data bytes, partial bitfields, and PIDs with no captures yet. |
| `canair pids` | Safely add/update `pids/` parameters and research entries from the CLI (comment-preserving, schema-validated, auto-reverted on failure). |
| [`wican-cli`](https://github.com/philipkocanda/wican-cli) | Separate package for WiCAN device management — config, sleep/power, protocol switching, status, OBD log queries, and reboots. Install with `pip install wican-cli`. |

## Profiles

A *profile* is a directory bundling one vehicle's data — `pids/`, `ecus.yaml`, `captures/`, and generated `out/`. The repo ships `profiles/ioniq-2017/` as the default/example profile. Inspect profiles with `canair profile list`, `canair profile show [NAME]`, and `canair profile path [NAME]`.

Start a new vehicle from scratch with `canair profile create <name> --car-model "..."`, which scaffolds an empty bundle (`pids/_meta.yaml`, an empty `ecus.yaml`, `captures/`, `out/`) under `~/.config/canair/profiles/<name>` (or `--path DIR`). Add `--set-default` to make it the default. Validate the ECU registry any time with `canair validate ecus`.

**Selection precedence** (first match wins): `--profile NAME|PATH` (global flag, before the subcommand) → `CANAIR_PROFILE` env var → `default_profile` in config → the single discovered profile (auto).

**Discovery** searches, in order: `--profiles-dir`, `$CANAIR_PROFILES_DIR`, `profiles_dir` in config, `~/.config/canair/profiles/` (user, uncommitted), and the repo's bundled `profiles/`. User profiles shadow bundled ones by name. Local profiles live in `~/.config/canair/profiles/` and are **not** committed.

## Querying captures

`canair captures` searches across all saved UDS response captures (in the profile's `captures/`) and displays them with context — timestamps, vehicle state, notes, and decoded parameter values (computed on the fly from the PID definitions, not stored in the capture files) where those definitions exist.

```bash
canair captures BMS                 # All captures for the BMS ECU
canair captures IGPM 22BC03         # Specific ECU+PID (most useful)
canair captures "BMS:2102,2103"     # Several PIDs (query mini-language)
canair captures --summary           # Overview: captures per ECU, per date
canair captures --latest BMS        # Most recent payload per BMS PID
canair captures IGPM 22BC03 --diff  # Byte-level diff (highlights changed bytes)
canair captures BMS 2102 --step     # Interactively step through captures
```

**Example output** (`canair captures BMS` shows 38 captures across multiple dates):

```
BMS — 38 captures

2026-04-17 16:43:21  (ready)
  PID: 2101
  Payload: 6101FFFFFFFF8C264826480300080E720F0E0E0E0F0F0E0010C00DC001000091...
  SOC_BMS: 70.0 %
  BATTERY_POWER: 0.3 kW
  BATTERY_VOLTAGE: 369.8 V

2026-04-17  (ready)
  PID: scan
  Scan: 0 responding, BC01-BC0B (11 DIDs): all NRC 0x31.
  Notes: No service 22 DIDs found beyond existing 21xx PIDs

2026-04-17  (ready)
  PID: 1A90
  Response: AEEV__ BMS
  Notes: ECU name
```

Captures are saved by `canair query --save` during scanning, raw queries, and monitor sessions. You will be prompted to provide context on the scan when done. Use `canair captures` and `canair decode` after collecting new data to spot patterns not obvious during the live session (byte-level changes between vehicle states, new ECU/PID combinations, payload length differences).

## Generating WiCAN vehicle profile

`canair wican` reads all PID definitions from the profile's `pids/*.yaml` and produces a WiCAN-compatible JSON vehicle profile. It can also upload directly to the device or diff against the currently loaded config.

```bash
canair wican                    # Generate JSON to the profile's out/ioniq-2017.json
canair wican --verified-only    # Only include verified parameters (105 vs 138 total)
canair wican --no-write         # Dry run — show what would be generated without writing
canair wican --stats            # Show per-ECU/PID statistics table
canair wican --download         # Download current config from WiCAN device
canair wican --diff             # Download + diff against locally generated profile
canair wican --upload           # Generate + upload to WiCAN device
canair wican --upload --reboot  # Upload + reboot device to apply changes
```

**Example output** (default mode):

```
Loading profiles/ioniq-2017/pids/

Generating profile...
  17 PID groups, 138 parameters

Writing output...
  Written: profiles/ioniq-2017/out/ioniq-2017.json (6222 bytes)
```

**`--stats` mode** shows a detailed breakdown per ECU and PID — parameter counts, verification status, polling period, and data source:

```
ECU        TX ID    PID        Period   Params   Verified   Source Summary
────────────────────────────────────────────────────────────────────────────────
BMS        0x7E4    2101       2500     31       29/31      AutoPID config; CSS Electron...
BMS        0x7E4    2105       5000     19       19/19      Original WiCAN config
BMS        0x7E4    2102       10000    32       32/32      ImHex pattern
IGPM       0x770    22BC03     2500     15       13/15      Decoded from live captures
HVAC       0x7B3    220100     5000     12       7/12       Fan speed test 2026-04-19
VCU        0x7E2    2101       2500     20       12/20      AutoPID config; ImHex pattern
...
```

**Device interaction flags** (`--download`, `--diff`, `--upload`) require the WiCAN to be reachable on the network. Use `--wican home`, `--wican vpn`, or `--wican <ip>` to select the device address (defaults to `home` from `~/.config/canair/config.yaml`).

The generated profile uses the **Vehicle Profile format** (grouped parameters per PID) — the format accepted by the WiCAN web UI and `POST /store_car_data`. The tool handles conversion to the device's internal array format automatically during upload.

## IOControl — what can be remotely controlled

Beyond reading diagnostic data, the toolkit can **actuate** vehicle hardware via UDS IOControlByIdentifier (service `0x2F`). All actuators auto-release when the diagnostic session ends (Ctrl+C or timeout) — no permanent state changes.

<img width="1211" height="698" alt="Screenshot 2026-05-21 at 11 17 53" src="https://github.com/user-attachments/assets/70c7af9f-d356-4a76-ba4f-a9529008e505" />

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

- **PID/DID YAML files:** `profiles/ioniq-2017/pids/igpm.yaml`, `bcm.yaml`, `skm.yaml`, `hvac.yaml`, `vess.yaml`, `psm.yaml` — source of truth for all actuator definitions, parameters, and verification status.
- **IOControl mode implementation:** `canlib/modes/iocontrol.py` — TUI-based interactive actuator control and single-command execution.
- **Quick reference docs:** `docs/IOControl CLI commands.md` — copy-paste command examples.

## How the CLI works

`canair` is an async Python CLI (argparse subcommands) that connects to the WiCAN Pro via WebSocket, enters ELM327 terminal mode, and sends UDS/KWP2000 requests over ISO-TP. `canair query` (and its sibling subcommands) drive the live communication.

**Architecture:**

```
canair (canlib/cli.py — argparse + argcomplete)
  └── canlib/
      ├── commands/          # one module per subcommand (query, scan, io, wican, …)
      ├── terminal.py        # WebSocket connection (WiCANTerminal)
      ├── elm327.py          # ELM327/ISO-TP protocol parsing
      ├── session_manager.py # Multi-ECU sessions + TesterPresent keepalive
      └── modes/             # 17 sub-mode implementations
```

**Key modes:**

| Mode | Command | Purpose |
|------|------|---------|
| Parameter query | `canair query --param NAME` / `canair query ECU` | Decode named parameters from YAML definitions |
| IOControl | `canair io ECU [--did DID]` | Interactive TUI or single actuator command |
| Query pipeline | `canair query "CMD" "CMD" ...` | Sequenced query steps (multi mini-language) with session management |
| Scan | `canair scan --tx ID --service SVC --range START-END` | Probe DID ranges for responses |
| Raw | `canair raw TX:PAYLOAD` | Direct hex request (no decoding) |
| Routines | `canair routines ECU` | RoutineControl (0x31) TUI |
| Monitor | `canair query "..." --monitor [SEC]` | Live-refreshing poll loop |

**Cross-cutting flags:** `--session`, `--wake`, `--hold`, `--timeout`, `--save`, `--json`, `--verbose`, `--reboot`, `--wican home|vpn|IP`, `--unsafe`

## Usage examples

```bash
# Read specific parameters (decoded output)
canair query --param SOC_BMS BATTERY_VOLTAGE BATTERY_POWER

# Query all parameters for an ECU (or a single PID)
canair query BMS
canair query BMS:2101

# Live monitor — refresh every 5 seconds and highlight changes
canair query BMS:2101 --monitor

# Wake a sleeping ECU and query it
canair query "session IGPM --wake" "query IGPM:BC03,BC06"

# IOControl — interactive TUI for actuators
canair io IGPM
# Or single command: turn on low beam (hold until Ctrl+C)
canair io IGPM --did BC01

# Scan for unknown DIDs on an ECU
canair scan --tx 7E4 --service 22 --range BC00-BCFF

# Discover all responding ECUs on the bus
canair discover

# Discover and auto-register new ECUs into ecus.yaml (--dry-run to preview)
canair discover --register

# Raw UDS request (hex in, hex out)
canair raw 7E4:2101

# Monitor + capture unique payloads, save on exit
canair query BCM:C00B --monitor --keep-unique --save

# Multi-ECU pipeline: wake SKM, query IGPM and BCM
canair query "skm-wake acc" "query IGPM:BC03" "query BCM:C00B"
```

All commands support `--wican home|vpn|<ip>` to select the target device, `--json` for machine-readable output, and `--reboot` to restore AutoPID mode after a session.

### Query mini-language

`canair query` (and the capture/decode tools) select ECUs and PIDs with a small
selection syntax. A **selector** is `ECU[:PIDLIST]`:

| Selector | Meaning |
|----------|---------|
| `BMS` | all known PIDs for BMS |
| `BMS:2101` | BMS PID `2101` only |
| `IGPM:BC03,BC06` | two IGPM DIDs (comma-separated PID list) |
| `VCU:2101 BMS:2101` | cross-ECU — a **space separates independent selectors** |

> **Bind each PID to its ECU with a colon, never a space.** In a query a space
> separates independent ECU selectors, so `IGPM 22BC07` means "all of IGPM **plus**
> a (bogus) ECU named `22BC07`" — not IGPM's PID `22BC07`. Write `IGPM:22BC07`.
> `canair query` rejects a bare PID/DID in the ECU slot with a hint to the colon form.

`canair query` also accepts a **pipeline** of steps (each a quoted string), run in
order over one session. A bare selector is shorthand for a `query` step, so
`canair query BMS:2101` == `canair query "query BMS:2101"`. Step verbs:

| Step | Purpose |
|------|---------|
| `query <SELECTORS>` | read ECU parameters/PIDs |
| `session <ECU> [--wake]` | enter an extended diagnostic session |
| `skm-wake [acc\|ign1\|ign2]` | wake the SKM and activate a relay |
| `raw <TX:PID> [--hold]` | raw UDS request |
| `scan <TX> <SVC> <RANGE>` | scan a PID range |
| `iocontrol <ECU> <DID> [--off]` | InputOutputControl |
| `security <ECU>` / `sleep <s>` / `repl` | security access / pause / drop into REPL |

```bash
# Pipeline: wake IGPM, then read two of its DIDs
canair query "session IGPM --wake" "query IGPM:BC03,BC06"
```

### Live monitoring of responses and auto-highlighting changes 

```
canair query IGPM:22BC07 --monitor 2 --keep-unique
```

<img width="1206" height="448" alt="image" src="https://github.com/user-attachments/assets/53e2d063-0aae-4089-903b-b2fa8a213c91" />

## Protocols

| Protocol | Used for |
|----------|----------|
| **UDS** (ISO 14229) | Body/comfort ECUs — session control, ReadDataByIdentifier, IOControl, RoutineControl |
| **KWP2000** (ISO 14230) | Powertrain ECUs (BMS, VCU, MCU, LDC/OBC) — ReadDataByLocalIdentifier |
| **ISO-TP** (ISO 15765-2) | Transport layer for multi-frame CAN messages |
| **ELM327 AT commands** | Communication protocol between tooling and the WiCAN dongle |

## Getting started

```bash
uv tool install .    # Install the canair CLI (or: uv sync for dev)
canair --help        # First run auto-creates ~/.config/canair/ + a starter config.yaml
```

Then edit `~/.config/canair/config.yaml` (created automatically on first run) to set
your WiCAN device address(es). `config.example.yaml` in the repo documents every key.

In the repo, `uv run canair ...` also works without installing. Enable tab-completion
(subcommands, flags, and ECU/PID names from the active profile) with one command:

```bash
canair completion --install    # auto-detects your shell; open a new shell afterwards
```

This writes the completion script into your shell's autoload directory (fish/bash need
no further setup; zsh loads it from a directory on `$fpath`). To wire it up manually
instead, add `eval "$(canair completion zsh)"` to your shell startup file.

**For development** (`uv run canair`, no global install): completion hooks the `canair`
command word, so it won't fire through the `uv run` prefix. Activate the project venv so
`canair` is on your `PATH` directly, then install as above:

```bash
uv sync && source .venv/bin/activate
canair completion --install
```

The WiCAN Pro must be powered on and connected to your WiFi network (or you connect to its AP). Device addresses are configured in `~/.config/canair/config.yaml` (a legacy repo-root `config.yaml` is still read for back-compat) — the `--wican` flag selects which address to use (e.g. `--wican home`, `--wican vpn`, or `--wican 192.168.80.1`). Without a config file, tools default to `192.168.80.1` (WiCAN's built-in AP).

## License

Public domain — see [LICENSE](LICENSE) (Unlicense).
