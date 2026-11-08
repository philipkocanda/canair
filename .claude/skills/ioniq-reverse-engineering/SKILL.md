---
name: ioniq-reverse-engineering
description: Working with WiCAN OBD-II, Ioniq CAN bus, PID decoding, vehicle profiles, CAN request CLI tool, UDS protocol, expression evaluator. Load this skill when working on the Ioniq reverse engineering project, CAN bus analysis, or WiCAN device configuration.
---

# Ioniq CAN Reverse Engineering Skill

General project/device/tool context for the Hyundai Ioniq 2017 EV CAN
reverse-engineering project.

**This skill is the Ioniq-specific *context*; `reverse-engineer-signal` is the
generic *procedure* — load both for Ioniq RE work.** This one carries the
vehicle-specific facts (safety, ignition lines, per-ECU gotchas, device and
transport details, project-specific tool gotchas — "what am I working on, with
what tools"). **`reverse-engineer-signal`** is the **vehicle-agnostic** decoding
procedure and reference — the discover→capture→analyze→define→verify lifecycle,
byte-index/expression syntax, UDS conventions, and the signal-analysis reasoning
(EE/power-electronics/physics/statistics) — written to apply to *any* car, using
the Ioniq only as its worked example. **Decoding, adding, fixing, or verifying
any PID/DID on the Ioniq? Load `reverse-engineer-signal` too** for the method, and
use this skill for the Ioniq facts it needs. (Working a *different* car? That
generic skill plus that car's profile is what you want; this Ioniq skill is then
just an example of a finished profile.) The full `canair` subcommand + flag
reference lives in **AGENTS.md**, `docs/reference/cli/`, and `canair <cmd>
--help`; this skill does not repeat it.

## Safety (non-negotiable)

- **NEVER interact with the car without the user's explicit approval** (`AGENTS.md` → The
  car). Approval is never implied by the task or by a skill. This covers every `canair` live
  command, `canair status`, every `wican-cli` command and the device's HTTP API. Propose the
  exact commands, then wait for an explicit "yes".
- **NEVER** use the UDS programming session (`10 02`) or any firmware
  write/upload command. This is a real car that cannot be un-bricked — a bad flash bricks an
  ECU or the whole vehicle.
- **Never `2E` write** unless you know exactly what it does. A read (`22`) cannot harm an ECU in
  any DID range (it still needs approval); canair's guard also refuses `10 05` (Hyundai/Kia's
  programming-mode alias), ECU resets (except `canair reset`, which is off until the user sets
  `safety.allow_reset`) and reprogramming routines (`docs/concepts/safety.md`).
- **IOControl (`2F`) actuates real hardware** — only with the car stationary and
  in a safe state, and only for an actuation the user approved by name.
- Be gentle: the ECUs are old and slow. **No concurrent requests to one ECU.**
- **One `canair` connection at a time, any transport.** canair enforces this with
  a `flock` mutex (`/tmp/wican-connection.lock`, `src/canair/device/lock.py`); a second
  concurrent command fails fast. The WiCAN serves a single client per protocol —
  a second `slcan-tcp` client hangs unserved until the first disconnects, and a
  second `wican-ws` WebSocket can lock up the device (power-cycle to recover).
  `--force` asks the current holder to release the connection and waits for it —
  it never kills anything, and it reports the PID + `kill` command if the holder
  won't go. Inspect/clear a stuck one with `canair lock` (`lock steal`/`lock
  kill`); a session whose terminal vanished stands down by itself once another run
  asks for the connection.
- **Never reboot the WiCAN without the user's explicit approval.** A bus reset via ELM327 is
  usually enough (that needs approval too). Using the WebSocket (`wican-ws`) terminal overrides
  AutoPID; a reboot is needed to resume the MQTT feed afterward — ask first.
- **When the user approves car access, ALWAYS talk to the car through `canair`** (`uv run
  canair …` from the repo root). Never hand-roll a WebSocket/socket or send raw ELM327 yourself.
  If canair can't do something, discuss with the user first.

## Backlog and research documents

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
- `VCU_FIRMWARE_FINDINGS.md`, `OBC_FIRMWARE_FINDINGS.md` — static reads of the VCU and OBC flash
  images: service sets and session matrices, every record byte's source, CAN frame layouts, and
  (OBC section 9) requests that must never be sent.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh, Premium trim, NL market).
  Not the HEV/PHEV. The 2017 model year (2016–2019) has a different CAN layout
  and fewer PIDs than the 2020+ facelift. The 28 kWh is air-cooled (fan); the
  38 kWh has a liquid-cooled battery, different BMS, and more cell-voltage PIDs. The profile
  describes both as variants (`variants.yaml`: `28kwh`, `38kwh`); this car is the 28 kWh.
- **Dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`.
- **CAN:** ISO 15765-4 (11-bit, 500 kbps) — ELM327 protocol `6`.
- **AT init** (`profile.yaml`): `ATSP6;ATS0;ATAL;ATST96;`, plus
  `response_timeout_ms: 614` (ECUs respond slowly; the first request after idle
  can time out — retry once; see the Gotchas in `profile.yaml`).

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

**Mapping to canair names.** The `OFF` state is no terminal on, with the car awake or asleep
(`SLEEP` implies it); the `ACC` state is the ACC relay alone; `ON` (IGPM `IGN1_SWITCH`,
the GDS "IGN1 Switch") is ACC + IGN1 + IGN2 (called `ACC2` until 2026-10-30); `READY` also needs
IG3. The `IG3` state (`VCU_IG3_ON == 1`) is the rail
itself: READY, CHARGING and TOPUP imply it, and it alone names the plugged-in idle window after a
charge. VCU 2101 `VCU_IG3_ON` (B11:4) is the IG3 rail: the IG3-fed ECUs (OBC, BMS, MCU, AAF, HVAC)
answer only while it is 1. `VCU_IGN_ON` (B11:0|B11:1) is the
ignition as the VCU sees it, and `VCU_POWER_STATE` (B25) the VCU's operating mode. Which of IG3
#1/#2/quick-charge is closed is not decoded: IGPM 22BC07 `IG3_RELAY_OUTPUT` (B12:3) and OBC 2101
`OBC_PROXIMITY_V` are the candidates (hypotheses; see the IGPM research entry);
`IG3_RELAY_OUTPUT` reads 1 whenever IGN1 is on, so it is not a READY signal.

**Telling the power states apart** (narrated cycles, 2026-11-03):

| Signal | OFF | ACC | ON | READY |
|---|---|---|---|---|
| BCM 22B004 `BCM_IGN_STATE_B004` (B10) | 0 | 2 | 14 | 14 |
| SKM 22B008 `SKM_POWER_MODE` (B13) | 0 | 2 | 7 | 9 |
| SKM 22B007 `SSB_LED_ACC` / `SSB_LED_ON` (B11:3 / B11:4) | 0 / 0 | 1 / 0 | 0 / 1 | 0 / 0 |
| BCM 22B003 `BCM_12V_BATTERY` (LDC runs only in READY or charging) | ~12.3 V | ~12.3 V | ~12.3 V | ~14.6 V |

The BCM answers in every state, so the `ACC` and `ON` predicates read it first; no BCM bit
separates ON from READY, so the BCM `READY` reading is the 12 V level with the charge port
unlocked (`vehicle_states.yaml` has the numbers). During START (brake + press, under 1.7 s) IGPM
`IGN2_SWITCH` drops while `IGN1_SWITCH` stays on. SKM 22B006 B17/B18/B19 are the ACC, IGN1 and
IGN2 relay outputs. SKM IOControl B108 to B10B are the ACC, IGN1, IGN2 and START outputs.

## WiCAN device

### AutoPID / live data

In AutoPID mode the latest values are at `http://<ip>/autopid_data` (cached — may
be stale if the car is off). For real-time reads use `canair read` (direct
CAN/UDS), which bypasses AutoPID. **AutoPID stops polling when the 12V battery
hits the `sleep_volt` threshold** while the WiCAN may stay WiFi-reachable — so
`/autopid_data` (and MQTT) can go stale (e.g. lights reported "on" after
parking) even though the device answers. Direct `canair read` still works and
values self-correct on the next poll. Check live sleep/voltage
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

## Transports

canair reaches the bus over an **explicit, config-chosen transport** (never
auto-switched). Registered in `src/canair/transport/config.py`; today there are three:

- **`slcan-tcp`** (default) — raw SLCAN over TCP with client-side ISO-TP.
  Requires the device in `slcan` mode. `monitor` uses the pipelined/batched
  `RawUdsClient`; everything else runs the normal dispatch over a `RawTerminal`
  adapter. **Only transport that supports passive `canair sniff`.**
- **`wican-ws`** — WiCAN Pro WebSocket ELM327 terminal; the *dongle* does ISO-TP.
  Works in any device protocol. No passive sniff.
- **`elm327-tcp`** — a generic ELM327 WiFi adapter over a plain TCP socket (no WiCAN HTTP API).
  No passive sniff.

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
  `canair read/monitor/scan/raw/discover … --save`, once the user approves that read. Agents must
  pass
  `--label` (+ optional `--state`/`--notes`) for non-interactive save; without
  `--label` it prompts. Saves are **journaled** to `captures/.journal/` as they
  stream and reconciled into the dated file on exit, so a killed/disconnected
  session isn't lost — recover leftovers with `canair captures uds --recover`
  (`--discard` to drop). In `canair monitor` press `s` to set/edit the current
  session's label/state/notes live (the modal names the segment it labels), or
  `n` to close the current segment and start a fresh one (several labelled
  sessions per run); a `● REC` blinks while `--save` is recording. The **state is
  auto-suggested** from decoded PID values via the profile's
  `vehicle_states.yaml`. For edits/removals use `canair.captures.session_edit`
  (`set_capture_note`/`delete_capture`). After saving, run `canair captures uds
  --summary` to spot patterns missed live, or `canair captures uds --sessions` for a
  metadata table of contents (date/state/label/notes/ECUs per session; `--json`).
- **`--session` / `--wake`.** `--session` = extended session (`10 03`), kept alive with
  TesterPresent while the ECU is idle; required for IGPM `2FBCxx` (its `22BCxx` reads need
  none). `--wake` rouses a deep-sleeping ECU first (implies `--session`): the ECU's `wake:`
  ritual where the profile declares one (the SKM), else `10 01`, asked twice if the first
  draws no answer. **IGPM (0x770) and BCM (0x7A0) wake from CAN activity
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
`canair read`). By default each PID refreshes twice as often as its profile
`period:` and a PID without one every `--interval` seconds (5.0); `--schedule fixed`
polls every PID every cycle, `--interval` apart. A slow or silent PID (every SKM PID once
the SKM is asleep) is polled up to 32× less often until it recovers, while its ECU's
healthy PIDs keep their rate (`--no-backoff` turns it off).
Non-query steps run once as setup, `query` steps poll in a
background worker with TesterPresent keepalives. On a TTY it's a scrollable
Textual TUI (press `?` for its keys; they are defined once in `src/canair/tui/keys.py`); piped,
it polls silently until Ctrl+C. Signals are listed in **payload order** (by the byte each reads
first), so a value sits above the hex byte it came from. Hex view highlights bytes changed since
the previous cycle, colors bytes by verification state (a declared support map's bytes in a
muted grey-blue), and shows ASCII for unmapped PIDs; a changed byte also
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
ECUs with IOControl DIDs: IGPM, BCM, HVAC, SKM, PSM, VESS.

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

**Tip:** sleep should be off before probing (12V can sag with the car off and put the
WiCAN to sleep mid-session) and on again after. These commands touch the device: ask the user to
run them, or to approve them.

## ECU status

Live status: `canair ecu` (registry, per-ECU stats, identity confidence) and `canair research`
(open backlog). The registry has 30 ECUs. Decode findings are recorded in each `ecus/<ecu>.yaml`;
the table below carries only gotchas that a signal list does not show.

| ECU | TX ID | Gotcha |
|-----|-------|--------|
| IGPM | 0x770 | IOControl map BC00–BCFF (lights, horn, signals, DRL, brake, trunk, locks, charge-cable lock). Status reads (BC03/04/06) need no session; `2FBCxx` does. |
| BCM | 0x7A0 | Part 95400-G7470 (Ioniq AE BEV only). TPMS and body state share it: `22C00B`. IOControl B000–B072 scanned; B061 answers NRC 0x31 (charge door not supported here). |
| VCU | 0x7E2 | Speed in 2101 is MPH (`((S20*256)+B19)*1.609344/100`), about 3.6 % above true speed. 2102 B18 is the coolant temperature (`B18-100`), not the 12 V rail: that is `VCU_LDC_OUTPUT_VOLTAGE` (`((B20<<8)|B19)/1000`). Motor RPM and torque live in the MCU. The 12 V DC-DC converter (LDC) is integrated here (ecu_id `AEVLDC53`, "VLDC"); there is no separate LDC ECU. |
| MCU | 0x7E3 | Inverter (36600-0E250). Motor RPM = 2102 `(S10<<8)|B09` (signed, verified). Most other 2101/2102 signals are unverified. |
| OBC | 0x7E5 | On-board charger only; no DC-DC control. Every 2101 byte is mapped from the firmware. Never send `99`, or `30 06/07` / `31 00/01` in session 0x90 (OBC_FIRMWARE_FINDINGS.md §9). |
| SKM | 0x7A5 | ACC relay IOControl is accepted, but **the relay only closes with the fob nearby**. `skm-wake` verifies via IGPM BC03. The powertrain stays dead without the fob. |
| PSM, VESS | 0x7A3, 0x736 | No readable live data. Both are driven by IOControl only (the seat motors run until OFF is sent). |

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
| EWP (EPCU/motor coolant pump) | EWP (0x746) | probable | Kona bench log: the pump alone answers on 0x74E and sends P-CAN 0x570; was Unknown-746 |
| ODS, DBL, charge lamp, compressor, PTC heater, the other of SBW switch / SCU | none | no responder found | not in the sweep |

`Unknown-7C0` matches no factory module yet. The HVAC unit also has a
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
