# canair

**CLI for reverse engineering CAN/OBD diagnostics over the air with a WiCAN dongle**

[![Docs](https://img.shields.io/badge/docs-philipkocanda.github.io%2Fcanair-blue)](https://philipkocanda.github.io/canair/)
[![CI](https://github.com/philipkocanda/canair/actions/workflows/ci.yml/badge.svg)](https://github.com/philipkocanda/canair/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

<img width="300" alt="Jul-23-2026 00-14-52" src="https://github.com/user-attachments/assets/e74ce18d-5350-4a69-b96e-d3b312b25b2f" />

canair is a command-line tool (CLI). It connects to a
[WiCAN](https://www.meatpi.com/products/wican-pro) OBD-II (On-Board Diagnostics) WiFi dongle. It
works with the data of a vehicle in **two domains**:

- **Diagnostics.** canair sends requests to the electronic control units (ECUs) of the car and
  reads the responses. It uses the UDS (Unified Diagnostic Services) and KWP2000 (Keyword
  Protocol 2000) protocols.
- **Raw broadcast CAN.** The car sends these Controller Area Network (CAN) frames on its own.
  canair listens passively, or imports frames from external logs and DBC (CAN database) files.

canair also has a set of analysis commands. They capture data, decode it, find statistical
correlations, and show which byte carries which signal. You can then turn the results into a
[WiCAN vehicle profile](https://meatpihq.github.io/wican-fw/config/automate/new_vehicle_profiles)
or into documentation that you can share.

The CLI is one command: **`canair`**. Vehicle data lives in a *profile* bundle. The repository
has several profiles in [`profiles/`](profiles/). The 2017 Hyundai Ioniq Electric is the most
complete one. The tool works with any vehicle, so you can
[build a profile for your car](https://philipkocanda.github.io/canair/bring-your-own-car/overview/).

## The monitor

`canair monitor` is the main screen: the ECUs you choose, decoded live while the car drives or
charges. Record the session with `--save`, label it, and check or edit signal definitions on the
spot. Every command is in the menu bar (`f10`) and the command palette (`ctrl+p`), and it works on
a phone over SSH.

![canair monitor — live values on a recorded motorway drive, the menus and the command palette](docs/screenshots/monitor-tour.gif)

[The monitor](https://philipkocanda.github.io/canair/getting-started/monitor/) is the full tour.

## Why canair?

- **Evidence stays with the definition.** Most tools store only the PID, DID and signal
  definitions. canair also stores the captures that support each definition. Anyone can check a
  definition against the data, and the community can decode together. A definition without proof
  has little worth.
- **Two kinds of data in one profile.** canair combines request/response diagnostics (OBD-II,
  UDS, KWP2000) with raw broadcast CAN frames (for example from DBC files). Both kinds use the
  same profile and the same analysis tools. What you know about a signal in one kind of data helps
  you find the same signal in the other.
- **Made for humans and agents.** Helpers import and analyze both kinds of captures. Every
  capability is a subcommand that you can script, with structured output (`--json`). A person or
  an AI coding agent can use it in the same way. The reverse-engineering workflows are available
  as agent skills in `.claude/skills/`.
- **Shareable without copied definitions.** Profiles come from captures of real cars. They do not
  copy the definitions of a dealer scan tool. People can share them without copying the content of
  a vendor tool. This opens vehicle maintenance and diagnostics to a wider community.

**The WiCAN Pro and the classic WiCAN both work** with the default raw-SLCAN transport. Some
features need a Pro: AutoPID device sync, `wican mode set` and the `wican-ws` transport. If you
set `wican_model: classic`, canair refuses these features with a clear message. See
[connect your dongle](https://philipkocanda.github.io/canair/getting-started/connect-device/).

**canair supports WiCAN firmware 4.x.** On the firmware v6 rewrite, only the `elm327-tcp`
transport works today. See
[firmware compatibility](https://philipkocanda.github.io/canair/getting-started/connect-device/#firmware-compatibility).

> **Documentation:** **[philipkocanda.github.io/canair](https://philipkocanda.github.io/canair/)** —
> [Getting started](https://philipkocanda.github.io/canair/getting-started/install/) ·
> [**The monitor**](https://philipkocanda.github.io/canair/getting-started/monitor/) (the main
> screen) ·
> [**Bring your own car**](https://philipkocanda.github.io/canair/bring-your-own-car/overview/)
> (the full walkthrough for a new vehicle) ·
> [**Profile explorer**](https://philipkocanda.github.io/canair/profiles/explorer/) (browse and
> plot real Ioniq data in your browser) ·
> [Concepts](https://philipkocanda.github.io/canair/concepts/architecture/) ·
> [Reference](https://philipkocanda.github.io/canair/reference/config/). The source is in
> [`docs/`](docs/index.md).

| | |
|---|---|
| ![decode plot](https://github.com/user-attachments/assets/7cab4e56-550a-4443-83dd-2f96bb5eedc7) | Decode and analyze a captured signal with `canair decode <query> --plot` |
| ![capture diff](https://github.com/user-attachments/assets/525affb7-836e-4f2d-9064-020858a4a268) | Byte-level capture diffs with `canair captures <query> --diff`. This is also the default view of `canair read` on a live vehicle. |

## How it connects

canair reaches the bus through an adapter. You select one of three transports explicitly:

- **`slcan-tcp`** (default): raw SLCAN over TCP on any WiCAN. canair handles ISO-TP and UDS.
- **`wican-ws`** (Pro only): ELM327 over WebSocket. The dongle handles ISO-TP.
- **`elm327-tcp`**: a plain TCP socket to a generic ELM327 clone (Kiwi, vLinker, OBDLink) or to the
  [ELM327-Emulator](https://philipkocanda.github.io/canair/development/offline-testing/). You do
  not need a WiCAN.

The **WiCAN is the recommended and best-tested adapter.** Generic clones are best-effort. They
are less likely to work on newer vehicles that use long multi-frame ISO-TP or extended addressing.

```mermaid
flowchart LR
    subgraph host["Your computer — canair"]
        cli["canair CLI<br/>transport: wican-ws | slcan-tcp | elm327-tcp"]
        defs["Profile PID/DID defs<br/>+ captures"]
        isotp["client-side ISO-TP + UDS<br/>(pipelined; slcan-tcp only)"]
    end

    subgraph wican["WiCAN dongle (or ELM327 clone) — one protocol at a time"]
        ws["ELM327 terminal<br/>ws://HOST/ws (wican-ws)<br/>or a clone's TCP port (elm327-tcp)"]
        slcan["SLCAN socket<br/>tcp://HOST:PORT"]
    end

    subgraph car["Vehicle (OBD-II port)"]
        bus["CAN bus"]
        ecus["ECUs<br/>BMS · VCU · MCU · IGPM · BCM …"]
    end

    cli -->|"wican-ws, elm327-tcp:<br/>ELM327 AT + UDS/KWP2000 hex"| ws
    cli --> isotp
    isotp <-->|"slcan-tcp:<br/>raw CAN frames"| slcan
    ws <-->|"dongle does ISO-TP"| bus
    slcan <--> bus
    bus <--> ecus
    defs -.->|"decode responses"| cli
```

In words: canair sends a request through the adapter to the CAN bus of the car. An ECU answers,
and canair decodes the response into named signals with the profile definitions.

canair decodes the responses into named signals. It uses the definitions in the active profile.
[Architecture](https://philipkocanda.github.io/canair/concepts/architecture/) describes the
transports, the protocols (UDS, KWP2000, ISO-TP) and the two data domains.

## Commands

Run every function as `canair <subcommand>`. Use `canair <cmd> --help` for details. The
[CLI reference](https://philipkocanda.github.io/canair/reference/cli/) describes every command and
flag. The [cheat sheet](https://philipkocanda.github.io/canair/reference/cheat-sheet/) lists the
ones you use most.

**Live device** (these commands talk to the car)

| Subcommand | Purpose |
|--------|---------|
| `canair status` | Show the transport, device and versions, and whether the device works (alias: `st`). |
| `canair logs` | Show the diagnostics log of transport drops and errors. |
| `canair read` | Send UDS and KWP2000 requests and decode the responses (alias: `query`). |
| **`canair monitor`** | **The main screen:** live, decoded ECU signals with menus and a command palette; record with `--save` (alias: `mon`). |
| `canair scan` | Probe DID, routine, IOControl, session and memory ranges for responses. |
| `canair memory` | Read ECU memory (service `0x23`) into local dumps. |
| `canair dtc` | Read and clear diagnostic trouble codes, and review the log of changes. |
| `canair reset` | Restart one ECU. It stays off until you enable `safety.allow_reset`. |
| `canair action` | Run a guarded, make-specific action from `actions.yaml`. |
| `canair identity` | Decode the identity DIDs of an ECU (alias: `id`). |
| `canair sniff` | Listen passively on the CAN bus (`slcan-tcp` only). |
| `canair lock` | Show or clear the single-session device lock. |

Related commands: `discover` (which ECUs answer), `io` and `routines` (actuators and routines),
`raw` and `repl` (low-level access). See [safety](https://philipkocanda.github.io/canair/concepts/safety/).

**Analysis** (offline, never touches the car)

| Subcommand | Purpose |
|--------|---------|
| `canair captures` | Search, compare and step through saved captures (alias: `cap`). |
| `canair decode` | Decode captures by value, plot them, and test expressions (alias: `dec`). |
| `canair align` | Show several signals side by side in one time-aligned table. |
| `canair correlate` | Rank the strongest relationships between signals, and find mirrored signals. |
| `canair hunt` | Find which byte is a known signal. |
| `canair investigate` | Report on every byte of a PID, an ECU or the whole profile. |
| `canair search` | Find a string, VIN or byte pattern in the captures. |
| `canair coverage` | Find decoding gaps in the PID definitions (alias: `cov`). |
| `canair research` | Show the open reverse-engineering backlog. |

[Which analysis command when](https://philipkocanda.github.io/canair/concepts/analysis-commands/)
helps you choose.

**Authoring** (edits the profile)

| Subcommand | Purpose |
|--------|---------|
| `canair pids` | Add or update signals and research entries in `ecus/`. |
| `canair signals` | Add or update broadcast signal definitions in `signals/`. |
| `canair ecu` | Inspect, add, rename and edit ECUs. |
| `canair bus` | List the CAN bus segments of the profile. |
| `canair states` | List and edit the vehicle-state vocabulary. |
| `canair groups` | List and edit named selector groups, for example `monitor @charging`. |
| `canair variants` | List and edit the variants of the profile car. |
| `canair wican` | Generate the WiCAN AutoPID JSON, and sync it with the device (Pro). |
| `canair validate` | Check the whole profile against its schemas (alias: `val`). |

**Import, export and setup**

| Subcommand | Purpose |
|--------|---------|
| `canair import` | Bring in a UDS payload (`uds`), a raw frame log (`can`) or a DBC file (`dbc`). |
| `canair export` | Export broadcast signal definitions to DBC. |
| `canair profile` | List, show, choose, create, adopt and overlay profiles (alias: `prof`). |
| `canair contribute` | Open a pull request that shares the active profile (alias: `share`). |
| `canair config` | Show and change the user config. |
| `canair update` | Update canair from its git clone. |

Two utilities complete the set: `canair bix` (byte-index converter) and `canair completion`
(tab-completion). The separate package [`wican-cli`](https://github.com/philipkocanda/wican-cli)
manages the WiCAN *device*: config, sleep and power, and reboots. Install it with
`pip install wican-cli`.

## Quick start

You need a **WiCAN dongle** (Pro or classic), a car with an OBD-II port, and
[`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/philipkocanda/canair.git
cd canair
uv tool install .                       # install the `canair` CLI
canair config set devices.home.host 192.168.1.100
canair config set default_wican home
canair status                           # is the device reachable?
canair discover                         # list every ECU on the bus (any car)
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # which car you have (once)
canair read BMS:2101                    # read a PID (Ioniq profile)
canair monitor @charging                # the main screen, on a saved group (see `canair groups`)
```

[Getting started](https://philipkocanda.github.io/canair/getting-started/install/) describes the
full setup: install, connect the dongle (Pro or classic, access point or LAN), tab-completion and
your first read.

canair checks for a newer release once a day. The check works offline and never blocks a command.
Upgrade with `canair update`. See
[staying up to date](https://philipkocanda.github.io/canair/getting-started/install/#staying-up-to-date).

> **You do not need to be a CAN expert to start.** Reading is safe. canair only *reads* unless you
> tell it to actuate something, and every action that changes state asks for confirmation first.
> Working with a vehicle bus still has real risk. Read the [Warning](#warning).

## Bring your own car

The bundled Ioniq profile is an *example*. To reverse engineer *your* car, build your own profile.
The discovery and scan commands fill it as you work:

```mermaid
flowchart LR
    A["profile create"] --> B["discover --register"]
    B --> C["identity"]
    C --> D["scan"]
    D --> E["capture<br/>(--save while driving/charging)"]
    E --> F["decode · correlate · hunt · investigate"]
    F --> G["pids upsert-param"]
    G --> H["coverage · verify"]
    H --> I["wican autopid write<br/>· share the profile"]
```

In words: create a profile, discover the ECUs, read their identity, scan for data, capture it while
you drive or charge, analyze it, define the signals, check the coverage, and then generate the
AutoPID file or share the profile.

Each step has its own page under
[**Bring your own car**](https://philipkocanda.github.io/canair/bring-your-own-car/overview/):
[create](https://philipkocanda.github.io/canair/bring-your-own-car/01-create-profile/) ·
[discover](https://philipkocanda.github.io/canair/bring-your-own-car/02-discover-ecus/) ·
[identity](https://philipkocanda.github.io/canair/bring-your-own-car/03-identity/) ·
[scan](https://philipkocanda.github.io/canair/bring-your-own-car/04-scan/) ·
[capture](https://philipkocanda.github.io/canair/bring-your-own-car/05-capture/) ·
[analyze](https://philipkocanda.github.io/canair/bring-your-own-car/06-analyze/) ·
[define & verify](https://philipkocanda.github.io/canair/bring-your-own-car/07-define-and-verify/) ·
[share](https://philipkocanda.github.io/canair/bring-your-own-car/08-share/).

## Profiles

A *profile* bundles the data of one vehicle: `ecus/` (one file per ECU, the source of truth),
`profile.yaml`, `captures/`, `references/` and a generated `out/`. Manage profiles with
`canair profile list`, `create` and `use`. Select a profile with `--profile`, `CANAIR_PROFILE` or
`default_profile`. [Bundled profiles](https://philipkocanda.github.io/canair/profiles/) lists the
profiles that ship. [Profiles](https://philipkocanda.github.io/canair/concepts/profiles/)
describes the layout, the precedence and the discovery order.

## The bundled Ioniq profile

The `hyundai-ioniq-ae-ev` profile is a ready-to-use diagnostics toolkit for the **Hyundai Ioniq
Electric (`AE`)**. It comes from a 2017 28 kWh car. You read live battery, motor, charging,
climate and body data over WiFi, and you do not need dealer tools. The profile maps **30 ECUs**
and **600+ signals**. More than 250 of them are verified on the car. They include:

- Battery state of charge (SOC), voltage, current and power, all 96 cell voltages, and state of
  health.
- Motor gear, torque and temperatures. Vehicle speed and **individual wheel speeds** (from the ESC
  module).
- Charging state (AC and DC CCS) and charge-port lock.
- On-board charger AC and DC (input and output) voltage and current, and EVSE pilot current.
- Electric power steering, tyre pressures and temperatures, HVAC (climate), and body controls
  (locks, trunk, lights, indicators).
- **IOControl** actuators (UDS `0x2F`) for hardware that is safe to toggle: lights, horn, locks,
  charge-cable lock, mirrors and wipers. All actuators release when the session ends.

[The bundled Ioniq profile](https://philipkocanda.github.io/canair/profiles/hyundai-ioniq-ae-ev/)
gives the full tour. The per-ECU files are in `profiles/hyundai-ioniq-ae-ev/ecus/`.

## Contributing

**Did you reverse engineer your car? Please contribute it back.** A shared profile gives the next
owner of the same vehicle a head start. This is how canair grows beyond one car. Pull requests are
welcome for whole profiles, a few decoded signals, corrected offsets, and fixes to canair itself.
See [CONTRIBUTING.md](CONTRIBUTING.md) and
[Bring your own car → Share](https://philipkocanda.github.io/canair/bring-your-own-car/08-share/#contribute-your-profile-back).

## License

Code: [MIT](LICENSE). Vehicle profiles (`profiles/`): [CC BY 4.0](profiles/LICENSE). Both licenses
require attribution. Keep the copyright notice. When you reuse profile data, credit "The canair
contributors".

## Warning

Work on the CAN bus and the ECUs of your vehicle can damage the car, cause faults, or leave the
car in an unsafe state. **Use this software at your own risk.** You are responsible for all
consequences.
