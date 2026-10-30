---
name: ioniq-reverse-engineering
description: Working with WiCAN OBD-II, Ioniq CAN bus, PID decoding, vehicle profiles, CAN request CLI tool, UDS protocol, expression evaluator. Load this skill when working on the Ioniq reverse engineering project, CAN bus analysis, or WiCAN device configuration.
---

# Ioniq CAN Reverse Engineering Skill

General project/device/tool context for the Hyundai Ioniq 2017 EV CAN
reverse-engineering project.

**This skill is the Ioniq-specific *context*; `reverse-engineer-signal` is the
generic *procedure* — load both for Ioniq RE work.** This one carries the
vehicle-specific facts (ECU status table, safety, device/transport details, and
the `canair`/`wican-cli` command reference — "what am I working on, with what
tools"). **`reverse-engineer-signal`** is the **vehicle-agnostic** decoding
procedure and reference — the discover→capture→analyze→define→verify lifecycle,
byte-index/expression syntax, UDS conventions, and the signal-analysis reasoning
(EE/power-electronics/physics/statistics) — written to apply to *any* car, using
the Ioniq only as its worked example. **Decoding, adding, fixing, or verifying
any PID/DID on the Ioniq? Load `reverse-engineer-signal` too** for the method, and
use this skill for the Ioniq facts it needs. (Working a *different* car? That
generic skill plus that car's profile is what you want; this Ioniq skill is then
just an example of a finished profile.) The full `canair` subcommand + flag
reference lives in **AGENTS.md**, `docs/reference/cli/`, and `canair <cmd>
--help` — this skill covers project-specific facts and gotchas, not help text.

## Safety (non-negotiable)

- **NEVER** use the UDS programming session (`10 02`) or any firmware
  write/upload command. This is a real, un-brickable car — a bad flash bricks an
  ECU or the whole vehicle.
- **Never `2E` write** unless you know exactly what it does. Reads (`22`) are safe in
  any DID range; canair's guard also refuses `10 05` (Hyundai/Kia's programming-mode
  alias), ECU resets and reprogramming routines (`docs/concepts/safety.md`).
- **IOControl (`2F`) actuates real hardware** — only with the car stationary and
  in a safe state.
- Be gentle: the ECUs are old and slow. **No concurrent requests to one ECU.**
- **One `canair` connection at a time, any transport.** canair enforces this with
  a `flock` mutex (`/tmp/wican-connection.lock`, `canlib/lock.py`); a second
  concurrent command fails fast. The WiCAN serves a single client per protocol —
  a second `slcan-tcp` client hangs unserved until the first disconnects, and a
  second `wican-ws` WebSocket can lock up the device (power-cycle to recover).
  `--force` asks the current holder to release the connection and waits for it —
  it never kills anything, and it reports the PID + `kill` command if the holder
  won't go. Inspect/clear a stuck one with `canair lock` (`lock steal`/`lock
  kill`); a session whose terminal vanished stands down by itself once another run
  asks for the connection.
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

Backlog: `canair research --summary` (per-ECU `research:` sections), plus `canair research
--gaps` for actuator DID families never read with `22` (PSM `B4xx`, SKM `B1xx`, DDM `B1xx`,
VESS `F0xx` as of 2026-10-30).

Findings documents in `profiles/hyundai-ioniq-ae-ev/research/` collect what firmware,
third-party logs and Hyundai/Kia's own specs say, for triage before a capture session. Each opens with YAML
frontmatter: dates, status, per-section status (integrated, superseded, invalidated, open),
relations to the other documents and plans, and sources:

- `IGPM_FINDINGS.md` — the gateway (IGPM): hardware, gateway behaviour, diagnostics.
- `IONIQ_AVN_FW_FINDINGS.md` — what the head-unit/telematics firmware says about other ECUs.
- `FUNCTIONAL_ADDRESSING_FINDINGS.md` — `0x7DF` broadcast requests: routing, ECU behaviour,
  timing.
- `HKMC_UDS_SPEC_FINDINGS.md` — Hyundai/Kia's UDS spec (ES95486): session aliases, power-state
  rules behind NRC 0x22, identification DIDs, record-DID families and support maps, IOControl
  and routine id ranges, NRC and failure-type tables. The DID conventions for scanning are in the
  `reverse-engineer-signal` skill's `hyundai-kia-uds-conventions.md`.

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

**Mapping to canair names.** The `ACC` state is the ACC relay alone; `ON` (IGPM `ACC2_IGN_ON`,
the GDS "IGN1 Switch") is ACC + IGN1 + IGN2 (called `ACC2` until 2026-10-30); `READY` also needs
IG3. The `IG3` state (`VCU_IG3_ON == 1`) is the rail
itself: READY, CHARGING and TOPUP imply it, and it alone names the plugged-in idle window after a
charge. VCU 2101 `VCU_IG3_ON` (B11:4) is the IG3 rail: the IG3-fed ECUs (OBC, BMS, MCU, AAF, HVAC)
answer only while it is 1. `VCU_IGN_ON` (B11:0|B11:1) is the
ignition as the VCU sees it, and `VCU_POWER_STATE` (B25) the VCU's operating mode. Which of IG3
#1/#2/quick-charge is closed is not decoded: IGPM 22BC07 `IG3_RELAY_OUTPUT` (B12:3) and OBC 2101
`OBC_AC_NOT_CONNECTED` are the candidates (hypotheses; see the IGPM research entry). Nothing decoded
separates ACC from OFF yet; the SKM's `ACC/ON input` is the candidate (SKM research entry). SKM
IOControl B108 to B10B are the ACC, IGN1, IGN2 and START outputs.

## Profiles

Vehicle data lives in a *profile* bundle: `ecus/` (one file per ECU — the
**source of truth**, each carrying identity/scan_log/dtcs/pids), `profile.yaml`
(car_model/init/failure_types), `captures/` (dated UDS payloads), `vehicle_states.yaml`
(canonical operating states + auto-suggest predicates), `references/` (reference
data), and **generated** `out/` (never hand-edit — run `canair wican autopid write`). The repo ships
`profiles/hyundai-ioniq-ae-ev/` as the default. Local (uncommitted) profiles live in
`~/.config/canair/profiles/` and shadow bundled ones by name; precedence:
`--profile NAME|PATH` > `CANAIR_PROFILE` > `default_profile` > single discovered.
Inspect with `canair profile list|show|path`, `canair ecu`, and
`canair validate pids --stats` (current counts). Set the default profile with
`canair profile use NAME` (alias for `canair config set default_profile NAME`).

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
be stale if the car is off). For real-time reads use `canair read` (direct
CAN/UDS), which bypasses AutoPID. **AutoPID stops polling when the 12V battery
hits the `sleep_volt` threshold** while the WiCAN may stay WiFi-reachable — so
`/autopid_data` (and MQTT) can go stale (e.g. lights reported "on" after
parking) even though the device answers. Direct `canair read` still works and
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
  expression: "B09/2"      # WiCAN formula (see reverse-engineer-signal skill)
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
skipped in bare-ECU sweeps and not shipped) · `slow` (live but too slow-changing
to poll every cycle, e.g. the 21F2 last-cycle records; shipped at its own required
`period`, skipped in bare-ECU sweeps) · `ignored` (dead DID, excluded
everywhere). `validate` errors if a PID omits it; set it with
`canair pids set-pid-status ECU PID STATUS` (and `canair pids upsert-param`
seeds `status: active` on a newly-created PID). Power
states in which a PID/ECU responds, and those a research lead needs, use the
shared **`vehicle_states:`** list (UPPERCASE vocabulary from the profile's
`vehicle_states.yaml` — `SLEEP, PLUGGED, ACC, "ON", READY, CHARGING, IG3, TOPUP, PARKED, DRIVING`
— plus the `ALL` meta-token; list/edit it with `canair states`). It's written as an inline flow
list (`[READY, CHARGING]`); input is case-insensitive. Tag an ECU with the *broadest* state it
answers in: `canair states READY` lists an ECU tagged `[IG3]` because READY implies IG3, so the
battery-side ECUs (BMS, OBC, MCU, AAF, VCU) are `[IG3]` and HVAC `["ON", IG3]`. ACC and ON are
the owner's manual names for the Start/Stop button positions; ON must stay quoted (`"ON"`), since
YAML 1.1 reads a bare ON as `true` and `canair validate` rejects it.

Edit with `canair pids upsert-param / add-research / set-status / set-pid-status /
set-pid-notes`
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
  `canair read BMS` / `BMS:2101` and `--param NAME` are single-ECU shortcuts.
- **`--save` discipline.** NEVER hand-write/edit `captures/` YAML — record via
  `canair read/monitor/scan/raw/discover … --save`. Agents must pass
  `--label` (+ optional `--state`/`--notes`) for non-interactive save; without
  `--label` it prompts. Saves are **journaled** to `captures/.journal/` as they
  stream and reconciled into the dated file on exit, so a killed/disconnected
  session isn't lost — recover leftovers with `canair captures uds --recover`
  (`--discard` to drop). In `canair monitor` press `s` to set/edit the current
  session's label/state/notes live (the modal names the segment it labels), or
  `n` to close the current segment and start a fresh one (several labelled
  sessions per run); a `● REC` blinks while `--save` is recording. The **state is
  auto-suggested** from decoded PID values via the profile's
  `vehicle_states.yaml`. For edits/removals use `canlib.captures`
  (`set_capture_note`/`delete_capture`). After saving, run `canair captures uds
  --summary` to spot patterns missed live, or `canair captures uds --sessions` for a
  metadata table of contents (date/state/label/notes/ECUs per session; `--json`).
- **`--session` / `--wake`.** `--session` = extended session (`10 03`) + 2s
  TesterPresent keepalive; required for IGPM `22BCxx`/`2FBCxx`. `--wake` sends
  `10 01` to rouse a deep-sleeping ECU (implies `--session`; first try may return
  NO DATA — allow a retry). **IGPM (0x770) and BCM (0x7A0) wake from CAN activity
  alone; BMS/MCU answer only while the IG3 rail is on (ignition ON, charging, the 12 V
  top-up), and the VCU a few minutes beyond, so ACC alone is not enough.**
- **`canair io --poll` can actuate hardware.** The IOControl TUI's background
  status poll (`--poll`, opt-in) sends each DID's release (`2F{DID}00`,
  returnControlToECU) every 3s; on relay/solenoid DIDs this can re-assert defaults
  and cause an audible click. Off by default. A KWP2000 `0x30` entry is polled, and
  switched on, only once it declares a `release:` — none of the Ioniq's KWP ECUs
  has one yet (see the OBC `iocontrol_scan` research entry).
- **`--verbose`** debugs canair itself; not useful for normal reads.
- **`canair raw`** is a last resort (hex only, no decoding) — use decoded
  `query`/`--param` when a PID definition exists.

### Query pipeline (multi mini-language)

`canair read` runs a sequence of positional STEP strings in one session,
managing extended sessions across ECUs with interleaved TesterPresent. Exits
after the pipeline unless `--repl` (or an explicit `repl` step). Unknown ECU
names are rejected up front.

```bash
canair read "query BMS:2101"                        # single PID, decoded
canair read "query BMS:2101" "query VCU:2101"       # multi-ECU, one session
canair read "session IGPM --wake" "query IGPM:BC03,BC06"   # wake + query
canair read "skm-wake acc" "sleep 1" "query BCM:B00E" "repl"
canair monitor "query BCM" --interval 2   # live refresh (default keep-all)
canair monitor @charging                  # a saved selector group (see `canair groups`)
```

Step verbs: `skm-wake [acc|ign1|ign2|start]`, `session <ECU> [--wake] [--mode XX]`,
`query <ECU>[:PIDLIST] …`, `raw <TX:PID> [--hold]`, `scan <TX> <SVC> <RANGE> [APPEND]`,
`iocontrol <ECU> <DID> [--off]`, `sleep <s>`, `repl`.
ECUs resolve by name (`IGPM`) or hex TX ID (`770`). A **`@group`** token in a
`read`/`monitor` step expands to a saved set of selectors (`canair groups` to
list/edit; e.g. `@charging`, `@driving`) and composes with ad-hoc selectors.

### `canair monitor`

Live-refreshing poll (its own top-level command; positional query steps like
`canair read`). By default each PID refreshes at its own profile `period:` and a
PID without one every `--interval` seconds (5.0); `--schedule fixed` polls every
PID every cycle, `--interval` apart. Non-query steps run once
as setup, `query` steps poll in a
background worker with TesterPresent keepalives. On a TTY it's a scrollable
Textual TUI (`↑↓/j/k`/wheel scroll — the view **never** follows the tail, a
repaint leaves the scroll position put; `space` pause, `=`/`-` poll
faster/slower live (the interval on `--schedule fixed`, a multiplier on every
PID's period otherwise), `m` switch schedule,
`r` toggle the byte ruler + per-signal byte-reference column
(in `display.byte_notation`/`--notation`), `E` open the errors/diagnostics
log overlay, `↑`/`↓` select a signal (`esc` deselects), `e`/`v`/`x`/`F`
edit/verify/en-disable/filter, `s` save payloads with a metadata modal, `i` the
session-info overlay (incl. the `--save` journal path), `?` a
keybinding help modal, `q` quit); piped, it polls silently until
Ctrl+C. Signals are listed in **payload order** (by the byte each reads first),
so a value sits above the hex byte it came from. Hex view highlights bytes changed since the previous cycle, colors bytes
by verification state, and shows ASCII for unmapped PIDs; a changed byte also
highlights the parameter row(s) whose expression decodes it, with the same
coverage-coloured background the byte gets. The status line shows a
**`captured N/uniq M`** frame counter — total fresh payloads received vs distinct
values kept (both differ from the on-screen row count of one row per PID). **Keep-mode default is
`--keep-all`** — every poll is recorded with its timestamp (full time-series, so real
timing/rate, `correlate --transform delta` and dwell all work). The capture file stays
compact because repeated payloads are folded on disk (lossless; see
`docs/concepts/captures-and-states.md`). `--keep-changes` records only value-transitions
per PID (run-length; sessions tagged `keep_mode: changes`, and the analysis tools caveat
rate math on them). Many older sessions are `keep_mode: unique` (retired global dedup):
return-to-previous transitions and durations are lost there, and the tools warn. The live
view collapses each run of identical payloads into one row with a `×N` poll count and
renders only the newest few per PID (`--keep N` sets how many); every poll is in the
`--save` journal.
Throughput is governed by ELM commands/cycle — cut via header caching + service-22
multi-DID batching (IGPM 3 DIDs: 11→5→1 cmds/cycle). `--elm-timeout` overrides
`response_timeout_ms`.

### `canair identity`

Queries standard + Hyundai/Kia identity DIDs (see the F1xx `-1` offset in the
reverse-engineer-signal skill) and prints decoded results. `--session` for most
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
`canair research` (open backlog). Registry = 30 ECUs (18 with live PIDs; 27
files carry a `pids:` section).
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

### Factory module names and the profile's ECUs

Factory wiring (US-market; see the caveat in the ignition-lines section) names the car's modules
differently from the profile. A 0x700-0x7EF address sweep
(2026-04-17, ignition on) found 30 responders, and every one has an `ecus/` file, so a factory
module with no row below either does not answer at a 0x7xx address or was not awake.

| Factory module | Profile ECU | Confidence | Basis |
|---|---|---|---|
| IGPM (gateway, power switches) | IGPM | certain | self-identifies; bridges every bus except the private CLIMATE-CAN |
| SMK, DATC, ACU, AAF, CCM, VESS, AMP, AVN (MM-CAN only on the EU car), WPC, DDM, ADM, BSD LH/RH, rear camera | SKM, HVAC, SRS, AAF, CCM, VESS, AMP, AVN, WPC, DDM, ADM, BSD-L/R, RCAM | certain | same name or alias, same bus |
| EPCU (VCU + MCU + LDC) | VCU, MCU | certain | VCU identifies as "VCU with the LDC integrated" |
| IBAU (integrated brake unit) | ESC | probable | ESC's alias is AHB (active hydraulic boost); the IBAU sits on C-CAN and P-CAN |
| MDPS, EPB unit | EPS, EPB | certain | same name, C-CAN |
| Lane keeping assist unit | MFC | probable | the camera; C-CAN; only the LKAS unit is a camera |
| AEB radar / smart cruise unit | SCC | probable | the one front radar; C-CAN |
| Driver IMS (memory seat) module | PSM | probable | the car's only power-seat module; B-CAN; address next to the door modules |
| Shift button module or SCU (parking actuator control) | GSA | unresolved | both are on P-CAN; GSA is a Kostal shift assembly; which one answers is unknown |
| Cluster, BCM | CLU, BCM | certain | name, buses |
| EWP, ODS, DBL, charge lamp, compressor, PTC heater, the other of SBW switch / SCU | none | no responder found | not in the sweep |

`Unknown-746` (KWP2000) and `Unknown-7C0` match no factory module yet. The HVAC unit also has a
private link to the compressor and PTC heater (`CLIMATE-CAN` in `can_buses.yaml`, marked
`private: true`): neither the IGPM nor the OBD-II port is on it.

## UDS notes

Security Access, PID/DID conventions, byte-index notation, and the F1xx `-1`
offset are in the **reverse-engineer-signal** skill. Quick reference:

- Standard UDS: `27 01` (seed) → compute key → `27 02 <key>`. Most `21`/`22` reads
  need no security; `2E` writes may need Security Access L1/L2.
- One-off byte-index lookups: `canair bix <token>`, `canair bix --table`,
  `canair bix --annotate <hex>`.

## Memory

### 2026-04-19 — AutoPID stops at `sleep_volt` even while WiCAN stays connected

When 12V drops to `sleep_volt`, the WiCAN stops AutoPID polling but stays
WiFi-connected: reachable, `/autopid_data` returns data, but values are stale.
Observed after parking (DRL/beam/tail lights showed "on" via MQTT from an earlier
poll during a lights-on drive); direct `canair read` confirmed they were off.
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

- [wican-fw repo](https://github.com/meatpiHQ/wican-fw) ·
  [WiCAN docs](https://meatpihq.github.io/wican-fw/)
- local `profiles/hyundai-ioniq-ae-ev/references/` spreadsheets (Kia Soul and Kia e-niro
  XLSX-format spreadsheets pulled from Google Docs):
  - [Kia Niro 64 kWh sheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  - [Kia Soul EV sheet](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI)
  - Both contain a lot of raw CAN logs and PID decoding information, SKM commands and ECU mappings. 
- **User-facing docs:** `docs/` (task-first, for humans) — `docs/profiles/hyundai-ioniq-ae-ev.md`
  documents the bundled profile. Keep it and the README current when you change a
  user-facing capability; see the README↔`docs/` policy in `AGENTS.md`.
