---
name: ioniq-reverse-engineering
description: Working with WiCAN OBD-II, Ioniq CAN bus, PID decoding, vehicle profiles, CAN request CLI tool, UDS protocol, expression evaluator. Load this skill when working on the Ioniq reverse engineering project, CAN bus analysis, or WiCAN device configuration.
---

# Ioniq CAN Reverse Engineering Skill

General project/device/tool context for the Hyundai Ioniq 2017 EV CAN
reverse-engineering project.

**This skill is the Ioniq-specific *context*; `reverse-engineer-pid` is the
generic *procedure* — load both for Ioniq RE work.** This one carries the
vehicle-specific facts (ECU status table, safety, device/transport details, and
the `canair`/`wican-cli` command reference — "what am I working on, with what
tools"). **`reverse-engineer-pid`** is the **vehicle-agnostic** decoding
procedure and reference — the discover→capture→analyze→define→verify lifecycle,
byte-index/expression syntax, UDS conventions, and the signal-analysis reasoning
(EE/power-electronics/physics/statistics) — written to apply to *any* car, using
the Ioniq only as its worked example. **Decoding, adding, fixing, or verifying
any PID/DID on the Ioniq? Load `reverse-engineer-pid` too** for the method, and
use this skill for the Ioniq facts it needs. (Working a *different* car? That
generic skill plus that car's profile is what you want; this Ioniq skill is then
just an example of a finished profile.) The full `canair` subcommand + flag
reference lives in **AGENTS.md**, `docs/reference/cli/`, and `canair <cmd>
--help` — this skill covers project-specific facts and gotchas, not help text.

## Safety (non-negotiable)

- **NEVER** use the UDS programming session (`10 02`) or any firmware
  write/upload command. This is a real, un-brickable car — a bad flash bricks an
  ECU or the whole vehicle.
- **Never `2E` write** unless you know exactly what it does. Treat `0x22Fxxx`
  (flash/calibration) DIDs as read-only.
- **IOControl (`2F`) actuates real hardware** — only with the car stationary and
  in a safe state.
- Be gentle: the ECUs are old and slow. **No concurrent requests to one ECU.**
- **One `canair` connection at a time, any transport.** canair enforces this with
  a `flock` mutex (`/tmp/wican-connection.lock`, `canlib/lock.py`); a second
  concurrent command fails fast. The WiCAN serves a single client per protocol —
  a second `slcan-tcp` client hangs unserved until the first disconnects, and a
  second `wican-ws` WebSocket can lock up the device (power-cycle to recover).
  Use `--force` only to steal a stale lock from a killed session.
- **Never reboot the WiCAN without asking.** A bus reset via ELM327 is usually
  enough. Using the WebSocket (`wican-ws`) terminal overrides AutoPID; a reboot
  is needed to resume the MQTT feed afterward — ask first.
- **ALWAYS talk to the car through `canair`** (`uv run canair …` from the repo
  root). Never hand-roll a WebSocket/socket or send raw ELM327 yourself. If
  canair can't do something, discuss with the user first.

## Goals

1. **Complete vehicle profile** — build a full Ioniq EV profile and submit a PR
   to the [wican-fw repo](https://github.com/meatpiHQ/wican-fw). Close, but some
   PIDs are still missing/broken.
2. **Remote control** — remote pre-heat, door locks, etc. Likely needs direct CAN
   write access, not just OBD-II reads.

Backlog: `canair research --summary` (per-ECU `research:` sections) and
`docs-ignored/TODO.md`.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh, Premium trim, NL market).
  Not the HEV/PHEV. The 2017 model year (2016–2019) has a different CAN layout
  and fewer PIDs than the 2020+ facelift. The 28 kWh is air-cooled (fan); the
  38 kWh has a liquid-cooled battery, different BMS, and more cell-voltage PIDs.
- **Dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`.
- **CAN:** ISO 15765-4 (11-bit, 500 kbps) — ELM327 protocol `6`.
- **AT init** (`profile.yaml`): `ATSP6;ATS0;ATAL;ATST96;`, plus
  `response_timeout_ms: 614` (ECUs respond slowly; the first request after idle
  can time out — retry once; see the RECON GOTCHA banner in `profile.yaml`).

## Profiles

Vehicle data lives in a *profile* bundle: `ecus/` (one file per ECU — the
**source of truth**, each carrying identity/scan_log/dtcs/pids), `profile.yaml`
(car_model/init/failure_types), `captures/` (dated UDS payloads), `states.yaml`
(canonical operating states + auto-suggest predicates), `references/` (reference
data), and **generated** `out/` (never hand-edit — run `canair wican autopid write`). The repo ships
`profiles/ioniq-2017/` as the default. Local (uncommitted) profiles live in
`~/.config/canair/profiles/` and shadow bundled ones by name; precedence:
`--profile NAME|PATH` > `CANAIR_PROFILE` > `default_profile` > single discovered.
Inspect with `canair profile list|show|path`, `canair ecu`, and
`canair validate pids --stats` (current counts).

## WiCAN device

### Access & config

Device addresses live in `~/.config/canair/config.yaml` (a legacy repo-root
`config.yaml` is still read for back-compat; both gitignored). Copy from
`config.example.yaml`. Manage with `canair config`. Keys: `default_profile`,
`profiles_dir`, `wican_addresses`, `default_wican`, `transport`.

```yaml
wican_addresses:
  home: "10.0.2.86"       # local LAN
  vpn: "192.168.3.2"      # via WireGuard VPN
default_wican: home
```

Select with `--wican home|vpn|<ip>`. Without a config, tools fall back to
`192.168.80.1` (WiCAN factory AP). Live device state (transport, protocol/mode,
sleep, battery, IP, active profile) is one command away: **`canair status`**.

### AutoPID / live data

In AutoPID mode the latest values are at `http://<ip>/autopid_data` (cached — may
be stale if the car is off). For real-time reads use `canair query` (direct
CAN/UDS), which bypasses AutoPID. **AutoPID stops polling when the 12V battery
hits the `sleep_volt` threshold** while the WiCAN may stay WiFi-reachable — so
`/autopid_data` (and MQTT) can go stale (e.g. lights reported "on" after
parking) even though the device answers. Direct `canair query` still works and
values self-correct on the next poll. See Memory below. Check live sleep/voltage
settings with `canair status` / `wican config`; don't rely on hardcoded numbers.

### REST API (JSON, no auth)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET  | `/load_auto_pid_car_data` | Download vehicle profile (`{"cars":[…]}`) |
| POST | `/store_car_data`         | Upload vehicle profile (raw to flash) |
| GET/POST | `/load_auto_pid` / `/store_auto_data` | Custom AutoPID config |
| GET  | `/autopid_data`           | Latest live PID values |
| GET/POST | `/load_config` / `/store_config` | Full config (POST = full replace + reboot) |
| GET  | `/check_status`           | WiFi/CAN/MQTT/battery/firmware status |
| GET  | `/obd_logs[/<file>]`      | SD-card log index / download SQLite DB |
| POST | `/system_reboot`          | Reboot (body `"reboot"`) |

Most of this is wrapped by `canair` and `wican-cli` — prefer those over raw HTTP.

### Two profile formats (don't confuse them)

1. **Vehicle Profile format** (`out/autopid.json`) — grouped params per PID
   (`"PARAM": "expression"`), used for upstream PRs. **Generated** by
   `canair wican autopid write` from `ecus/`; never hand-edit.
2. **Device format** — what the firmware parses: params as an array of objects
   (`[{"name":…, "expression":…, "unit":…, "period":…}]`) wrapped in
   `{"cars":[{"car_model":…, "init":…, "pids":[…]}]}`. `canair wican autopid upload`
   converts grouped → device format automatically.

**Firmware gotcha:** `load_all_pids()` in `autopid.c` requires `parameters` as an
**array of objects**. POSTing the grouped (Vehicle Profile) dict to
`/store_car_data` yields empty entries (`cJSON_GetObjectItem(param,"name")` is
NULL). The upstream build (`cars.js process_profile()`) converts grouped→array at
build time; the device never sees grouped format directly.

### PID source of truth

Per-ECU YAML under `ecus/` (one ECU per file, with its `tx_id`, `identity:`, and PIDs);
`profile.yaml` holds `car_model`/`init`. A parameter looks like:

```yaml
SOC_BMS:
  expression: "B09/2"      # WiCAN formula (see reverse-engineer-pid skill)
  unit: "%"
  ha_class: battery        # downstream device_class
  mqtt_topic: soc_bms
  min: "0"
  max: "100"
  source: "Original WiCAN config"
  verified: true           # tested on this car? (per-param confidence axis)
  enabled: true            # ship this param to the device (param-level; default true)
```

Every PID carries a **required, explicit** lifecycle **`status:`** — the single
field that replaced the old `ignored`/`static`/`enabled` booleans:
`active` (indexed, swept, queryable, shipped) · `draft` (tracked/queryable but
not shipped — RE placeholders/speculative) · `static` (unchanging cal/identity;
skipped in bare-ECU sweeps and not shipped) · `ignored` (dead DID, excluded
everywhere). `validate` errors if a PID omits it; set it with
`canair pids set-pid-status ECU PID STATUS` (and `canair pids upsert-param`
seeds `status: active` on a newly-created PID). Power
states in which a PID/ECU responds, and those a research lead needs, use the
shared **`vehicle_states:`** list (`sleep, plugged, acc, acc2, ready, charging`;
a profile's states.yaml may add composites like `parked`/`driving`).

Edit with `canair pids upsert-param / add-research / set-status / set-pid-status`
(surgical, comment-preserving, schema-validated, auto-reverted on failure) — prefer it over
hand-editing. After changes: `canair validate pids` then `canair wican autopid write`.

## Transports

canair reaches the bus over an **explicit, config-chosen transport** (never
auto-switched). Registered in `canlib/transport/config.py`; today there are two:

- **`slcan-tcp`** (default) — raw SLCAN over TCP with client-side ISO-TP.
  Requires the device in `slcan` mode. `monitor` uses the pipelined/batched
  `RawUdsClient`; everything else runs the normal dispatch over a `RawTerminal`
  adapter. **Only transport that supports passive `canair sniff`.**
- **`wican-ws`** — WiCAN Pro WebSocket ELM327 terminal; the *dongle* does ISO-TP.
  Works in any device protocol. No passive sniff.

Select via the `transport:` block (`type`/`host`/`port`/`bitrate`) or per-command
`--transport`/`--wican`. **`canair status`** shows the resolved transport and
warns on a mode mismatch with the exact fix. Switch modes explicitly:
`canair wican mode set slcan|auto_pid [--yes]` (never automatic).

**On this car, passive sniffing sees ~nothing:** the OBD-II port is
gateway-isolated — only diagnostic request/response reaches it, no internal
broadcast traffic. So the raw-CAN value here is pipelined UDS (monitor batches a
`multi_did` ECU's `22` DIDs into one ISO-TP request), not sniffing.

## Tool gotchas (project-specific)

Full reference: **AGENTS.md** + `canair <cmd> --help`. Key project behaviors:

- **Selectors bind PID to ECU with a colon, never a space.** In a `query` step a
  space separates *independent* selectors, so `query IGPM BC03` = "all of IGPM
  **plus** a bogus ECU `BC03`" (rejected). Write `query IGPM:BC03,BC06`. Bare
  `canair query BMS` / `BMS:2101` and `--param NAME` are single-ECU shortcuts.
- **`--save` discipline.** NEVER hand-write/edit `captures/` YAML — record via
  `canair query/scan/raw/discover … --save` (and `--monitor`). Agents must pass
  `--label` (+ optional `--state`/`--notes`) for non-interactive save; without
  `--label` it prompts. Saves are **journaled** to `captures/.journal/` as they
  stream and reconciled into the dated file on exit, so a killed/disconnected
  session isn't lost — recover leftovers with `canair captures --recover`
  (`--discard` to drop). In `--monitor` press `s` to set/edit label/state/notes
  live; the **state is auto-suggested** from decoded PID values via the profile's
  `states.yaml`. For edits/removals use `canlib.captures`
  (`set_capture_note`/`delete_capture`). After saving, run `canair captures
  --summary` to spot patterns missed live, or `canair captures --sessions` for a
  metadata table of contents (date/state/label/notes/ECUs per session; `--json`).
- **`--session` / `--wake`.** `--session` = extended session (`10 03`) + 2s
  TesterPresent keepalive; required for IGPM `22BCxx`/`2FBCxx`. `--wake` sends
  `10 01` to rouse a deep-sleeping ECU (implies `--session`; first try may return
  NO DATA — allow a retry). **IGPM (0x770) and BCM (0x7A0) wake from CAN activity
  alone; BMS/VCU/MCU need ACC/ignition or charging.**
- **`canair io --poll` can actuate hardware.** The IOControl TUI's background
  status poll (`--poll`, opt-in) sends `2F{DID}00` (returnControlToECU) to every
  DID every 3s; on relay/solenoid DIDs this can re-assert defaults and cause an
  audible click. Off by default.
- **`--verbose`** debugs canair itself; not useful for normal reads.
- **`canair raw`** is a last resort (hex only, no decoding) — use decoded
  `query`/`--param` when a PID definition exists.

### Query pipeline (multi mini-language)

`canair query` runs a sequence of positional STEP strings in one session,
managing extended sessions across ECUs with interleaved TesterPresent. Exits
after the pipeline unless `--repl` (or an explicit `repl` step). Unknown ECU
names are rejected up front.

```bash
canair query "query BMS:2101"                        # single PID, decoded
canair query "query BMS:2101" "query VCU:2101"       # multi-ECU, one session
canair query "session IGPM --wake" "query IGPM:BC03,BC06"   # wake + query
canair query "skm-wake acc" "sleep 1" "query BCM:B00E" "repl"
canair query "query BCM" --monitor 2 --keep-unique   # live refresh, unique payloads
```

Step verbs: `skm-wake [acc|ign1|ign2|start]`, `session <ECU> [--wake] [--mode XX]`,
`query <ECU>[:PIDLIST] …`, `raw <TX:PID> [--hold]`, `scan <TX> <SVC> <RANGE> [APPEND]`,
`security <ECU> [algo …]`, `iocontrol <ECU> <DID> [--off]`, `sleep <s>`, `repl`.
ECUs resolve by name (`IGPM`) or hex TX ID (`770`).

`security` tries UDS Security Access (`27 01`→`27 02`) with ~40 built-in
Hyundai/Kia key algorithms (needs an open extended session; read-only seed/key
computation — a prerequisite for `2E` writes but doesn't itself change anything).
Handles NRC 0x36 (lockout, stops) / 0x37 (delay, waits + retries).

### `--monitor`

Live-refreshing poll: non-query steps run once as setup, `query` steps poll in a
background worker with TesterPresent keepalives. On a TTY it's a scrollable
Textual TUI (`↑↓/j/k`/wheel scroll, `f` follow-tail, `space` pause, `s` save
payloads with a metadata modal, `q` quit); piped, it polls silently until
Ctrl+C. Hex view highlights bytes changed since the previous cycle, colors bytes
by verification state, and shows ASCII for unmapped PIDs. `--keep-unique` keeps
distinct payloads per PID; `--keep-all` keeps every cycle with timestamps.
`--keep-unique` is ideal for **event captures** (door/lock/hood): each stored row
is a rising-edge transition, so `investigate --events` reconstructs the timeline
cleanly — but return-to-previous states (falling edges) and durations are dropped,
so the session is tagged `keep_mode: unique` and analysis tools (`decode`/
`correlate`/`investigate`) warn and caveat rate/duration math on that scope.
Throughput is governed by ELM commands/cycle — cut via header caching + service-22
multi-DID batching (IGPM 3 DIDs: 11→5→1 cmds/cycle). `--elm-timeout` overrides
`response_timeout_ms`.

### `canair identity`

Queries standard + Hyundai/Kia identity DIDs (see the F1xx `-1` offset in the
reverse-engineer-pid skill) and prints decoded results. `--session` for most
ECUs, `--wake` for deep-sleepers (IGPM). Known deep-sleep results:

- **BCM (0x7A0):** F18C=`1705310070`, F18B=`2017-05-31`, F100=`180`, F194=`100`
- **IGPM (0x770):** F18B=`2017-06-06`, F100=`20`, F101=`160205`, F196=`109`

### `canair io` (IOControl)

Runs `2F` IOControl from the `iocontrol:` sections of `ecus/`. `canair io ECU`
lists DIDs (offline, no connection). `--did BCxx` sends ON (`--off` for OFF);
auto-enters a session if `session: true`; if `hold: true` (default) keeps
TesterPresent until Ctrl+C then auto-sends OFF (SKM relays are `hold: false`).
ECUs with IOControl DIDs: IGPM, BCM, SKM, PSM, VESS.

### `wican-cli` (separate package)

Device management (config, sleep/power, protocol switching, status, OBD-log
queries, reboots) lives in the standalone
[`wican-cli`](https://github.com/philipkocanda/wican-cli) (`pip install
wican-cli`), NOT in canair!

```bash
wican status            # device summary          wican protocol --set slcan
wican sleep --disable   # before an RE session    wican protocol --set auto_pid
wican sleep --enable    # after                    wican logs --query SOC_BMS --limit 20
```

**Tip:** disable sleep before probing (12V can sag with the car off and put the
WiCAN to sleep mid-session), re-enable after.

## ECU status

Live status: `canair ecu` (registry + per-ECU stats + identity confidence) and
`canair research` (open backlog). Registry = 30 ECUs (27 with PID defs).
Decode findings are recorded in each `ecus/<ecu>.yaml` `notes:`. Highlights:

| ECU | TX ID | Status | Key notes |
|-----|-------|--------|-----------|
| BMS | 0x7E4 | ✅ | 2101 (main), 2105 (temps/SOH), 2102–2104 (96 cell voltages). Full ImHex patterns. |
| IGPM | 0x770 | ✅ | Full IOControl map (BC00–BCFF): lights, horn, signals, DRL, brake, trunk, locks, charge-cable lock. Wakes from `1001`; status reads (BC03/04/06) work in deep sleep. |
| BCM | 0x7A0 | ✅ | Part 95400-G7470 (Ioniq AE BEV only). TPMS + full body state on `22C00B`. Wakes from CAN alone. IOControl B000–B072 scanned; B061 charge-door not supported here. |
| CLU | 0x7C6 | ✅ | `22B002` → odometer (UINT24 BE @ byte 9). Also imperial. Range/time/speed-limit TODO. |
| AAF | 0x7E6 | ✅ | Active Air Flaps. `2180`/`2181`: ambient `(B18-80)/2` + heater/heatsink/compressor temps. |
| VCU | 0x7E2 | 🔶 | 2101 gear/state/speed (speed in MPH: `((S20*256)+B19)*1.609344/100`, ~+4% vs dash; do NOT use B21). 2102 DC-link `B17*2` + 12V aux `B18/10`. Regen/drive-mode TODO. Motor RPM/torque live in MCU, not VCU. |
| MCU | 0x7E3 | 🔶 | Inverter (36600-0E250). Motor RPM = 2102 `[S10:S11]` (signed BE, verified). Torque/phase-current/temps TODO. 2102 B27–B47 = static cal block. |
| OBC | 0x7E5 | 🔶 | Combined OBC+LDC (alias LDC). 2101 verified: `LDC_HV_INPUT_V=[B45:B46]/100`, `LDC_TEMP=B19-100`, OBC charge/output V, DC current. OBC internal temps NOT broadcast. 2102 = cal. |
| HVAC | 0x7B3 | 🔶 | `220100` IAT/AAT/evaporator (partially mapped). `220101`/`220102` unexplored. |
| SKM | 0x7A5 | 🔶 | ACC relay IOControl accepted, but **relay only physically closes with the fob nearby**. `skm-wake` verifies via IGPM BC03. Powertrain stays dead without fob. |
| ESC/EPS/PSM/VESS | 0x7D1/0x7D4/0x7A3/0x736 | 🔶 | Research: ESC/EPS partly decoded; PSM (power seat) + VESS (exterior sound) IOControl known from e-Niro, untested here. |

## UDS notes

Security Access, PID/DID conventions, byte-index notation, and the F1xx `-1`
offset are in the **reverse-engineer-pid** skill. Quick reference:

- Standard UDS: `27 01` (seed) → compute key → `27 02 <key>`. Most `21`/`22` reads
  need no security; `2E` writes may need Security Access L1/L2.
- One-off byte-index lookups: `canair bix <token>`, `canair bix --table`,
  `canair bix --annotate <hex>`.

## Memory

### 2026-04-19 — AutoPID stops at `sleep_volt` even while WiCAN stays connected

When 12V drops to `sleep_volt`, the WiCAN stops AutoPID polling but stays
WiFi-connected: reachable, `/autopid_data` returns data, but values are stale.
Observed after parking (DRL/beam/tail lights showed "on" via MQTT from an earlier
poll during a lights-on drive); direct `canair query` confirmed they were off.
Self-corrects on the next successful poll. Consider a small `sleep_volt` bump to
widen the gap vs. the actual sleep trigger.

### 2026-04-19 — BCM 95400-G7470 is Ioniq AE Electric (BEV) only

The `Gx` code in Hyundai part numbers identifies the model; `G7` is exclusive to
the Ioniq AE Electric (BEV) — G2=Ioniq HEV, G5=Kia Niro, G6=Picanto all use a
different BCM. So IOControl DIDs that accept but do nothing are **not** explained
by cross-vehicle sharing; more likely **market variants** (the same G7 BCM ships
across EU/US/KR with features like heated handles or auto-dimming mirrors not
fitted on every trim).

## Key references

- **Obsidian vault:** `~/obsidian-vault/KB/EV/Hyundai Ioniq/Reverse engineering/`
  — `PIDs by ECU/`, `Ioniq OBD-II CAN modules.md`, `Hyundai Kia UDS DID
  Conventions.md`, fan-control tests.
- [wican-fw repo](https://github.com/meatpiHQ/wican-fw) ·
  [WiCAN docs](https://meatpihq.github.io/wican-fw/)
- [Kia Niro 64 kWh PID sheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  and local `profiles/ioniq-2017/references/` spreadsheets (Kia Soul PIDs are
  offset by 1).
- **User-facing docs:** `docs/` (task-first, for humans) — `docs/profiles/ioniq-2017.md`
  documents the bundled profile. Keep it and the README current when you change a
  user-facing capability; see the README↔`docs/` policy in `AGENTS.md`.
