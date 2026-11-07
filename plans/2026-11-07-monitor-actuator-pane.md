# Monitor: an actuator pane for IOControl and routines

Status: **IN PROGRESS** (2026-11-07), on branch `monitor-actuator-pane`.

Paths are relative to `src/canair/`. Point at code by file and symbol: line numbers drift.

## Goal

Switch an output and watch what it does in one run. Today you toggle the rear defogger in
`canair io IGPM` and read its feedback in `canair monitor IGPM:22BC03`, but both commands hold the
device lock, so they cannot run at the same time. This plan adds an **actuator pane** to the
monitor: a split pane beside the live values that lists the IOControl DIDs and routines of the ECUs
you name, and sends ON/OFF/value/routine requests while polling continues. Examples: the SKM's
ACC/IGN1/IGN2 relays, the IGPM lamps and the rear defogger, a BCM routine.

The pane is also the first step towards a monitor made of panes. So the plan builds the general
mechanisms (keys scoped to the focused pane, a foreground lane that shares the bus with polling),
not a one-off widget.

## Decisions

Settled with the user.

1. **A split pane, not a modal.** The pane sits in the monitor screen, at the bottom, or on the
   right when the terminal is wide. `tab` moves focus between the values and the pane, so you can
   scroll the values while the pane is open.
2. **Opt-in with a flag that names the ECUs:** `canair monitor … --actuate IGPM --actuate SKM`
   (also `--actuate IGPM,SKM`). Without the flag the monitor cannot actuate, and the pane key only
   says which flag to pass. A recording run, often made while driving, cannot actuate by accident.
3. **A `--save` recording stores every actuation**, in a new session-level `actuations` list.
4. **Routines are in scope** (startRoutine with confirmation, stopRoutine,
   requestRoutineResults). Profile actions, a driving guard and scripted actuation are follow-ups.
5. **A user request goes out between two poll requests**, not after the poll cycle. See
   "Scheduling".
6. **The session mode is restored** after the last actuator on an ECU is released.
7. **Readback PIDs are not added to the poll set.** The pane says `not polled` instead.
8. **The problems found while planning are fixed** in this work (see "Problems found").

## Today

- **`canair io ECU`**: `live/actuate/iocontrol.py::IOControlController` holds the view state, the
  `ecus/` edits and its own `SessionManager`. `live/actuate/iocontrol_actuate.py::
  IOControlActuator` sends ON/OFF/ShortTermAdjustment and the releases, and reads and writes the
  controller through a back-reference. The actuator state literal (`ActuatorState`) is typed
  because `release_all` selects its work with `== ACTUATOR_ON`.
- **`canair routines ECU`**: `live/actuate/routines.py::RoutinesController` holds the state, its
  own `SessionManager`, the requests and the row cells. A KWP2000 ECU is refused
  (`live/actuate/routines_execute.py::check_routines_protocol`).
- **The monitor**: `live/monitor/controller.py::MonitorController` composes collaborators
  (`sessions`, `selectors`, `recorder`, `editor`, …). Sessions have one owner,
  `MonitorSessions` over `controller.sm` (`plans/2026-11-05-one-session-owner.md`).
- **ELM327 polling** (`live/monitor/poll_elm.py`) sends one request at a time. Each exchange is
  one `terminal.transaction()`, an `asyncio.Lock`, which hands the pipe to waiters in FIFO order.
- **Raw polling** (`live/monitor/raw_poller.py`) runs `RawUdsClient.poll()` on an executor thread:
  each round sends one request to every ECU, then harvests the replies. `RawUdsClient.read()` uses
  the same stacks and the same owed-response ledger, with no lock. The raw keepalive therefore runs
  only between polls (`MonitorSessions.keep_alive`).
- **All monitor keys are app-level** (`tui/apps/monitor/app.py::MonitorApp.BINDINGS`). The keymap
  contract (`tests/contracts/test_tui_keymap.py`) and the help overlay (`tui/help.py`) read
  `App`/`Screen` bindings only.
- **IOControl entries name their readback signal** in `status_param:` (IGPM `BC01` → `LOW_BEAM`).

### Problems found

1. **The raw client builds ISO-TP stacks only for the ECUs it starts with**
   (`live/monitor/raw_run.py::query_ecu_addresses`) and has no way to add one. An ECU that the PID
   picker adds mid-run, or a `session` step on an ECU that is not polled, has no stack:
   `RawUdsClient.poll()`/`read()` raise `KeyError`, which the monitor takes for a dead link.
2. **The live session switch (`t`) on `slcan-tcp`** calls the blocking `read()` from a TUI worker
   while `poll()` may be running on the same stacks and ledger (defect 3 of
   `plans/2026-10-27-raw-owed-ledger-blackout.md`). Either can take the other's reply.
3. **The `read` pipeline's `iocontrol` step** (`live/read/exec.py::_exec_iocontrol`) tracks
   nothing and releases nothing when the pipeline ends, unlike `canair io`.

## Design

### 1. Scheduling: a foreground lane, arbitrated per request

A user request (an actuation, a session switch) must reach the car quickly, without stopping the
poll and without two requests ever being on one ECU at once. The monitor gets a **foreground
lane**: `live/monitor/foreground.py::ForegroundLane`, one worker task that runs user jobs in the
order they were asked for, one at a time, concurrently with the poll task. The poll task does not
know about it. The transport decides, request by request, who goes next:

| Transport | Arbiter | A foreground request waits for |
|---|---|---|
| ELM327 | `terminal.transaction()` (FIFO `asyncio.Lock`) | the one exchange in flight |
| `slcan-tcp` | a per-ECU lock in `RawUdsClient` | the in-flight request **to the same ECU** only |

On `slcan-tcp`, `poll()` takes an ECU's lock without waiting when it sends to it and releases it
when the request resolves. An ECU whose lock `read()` holds keeps its request queued for the next
round, and the other ECUs keep polling. `read()` waits for the lock. This is §2 of
`plans/2026-10-27-raw-owed-ledger-blackout.md`, and it fixes problem 2: the session switch runs on
the lane and waits for its ECU like any other user request.

Two consequences:

- **The cost of a poll read is the time it held the pipe.** `live/read/exec_read.py::
  timed_exchange` measures from the moment its transaction starts, so the wait for a foreground
  exchange (or a keepalive) never feeds the slow-PID backoff.
- **The lane owns no session state.** Its jobs open sessions through the monitor's
  `SessionManager`, so `MonitorSessions` stays the only session owner on both transports.

### 2. A raw client that can add an ECU (problem 1)

`RawUdsClient.ensure_ecu(name, address)` builds and starts a stack once, under a build lock, and
is a no-op for a known ECU. Two callers make every path work:

- `RawClientTerminal` (the session surface over the raw client) resolves its header to a name and
  an address and ensures the ECU before each read, on the executor thread. Sessions, keepalives
  and foreground jobs on an unpolled ECU then work.
- The raw poller ensures every ECU of the tick's plan before `poll()`, on the executor thread. The
  picker's new ECU then works, and so does a reconnect (the new client learns its ECUs the same
  way).

### 3. One actuation core, shared by `io`, `routines`, the pipeline and the monitor

Lift the request and state logic out of the two view controllers, so the monitor does not get a
third copy.

- **`live/actuate/io_bank.py::IoBank`**: one ECU's IOControl actuators. It holds the per-DID
  state (`ActuatorState`, last reply, last value, status record) and sends ON, OFF, adjust and
  release, with today's refusals (`safety/actuation.py::on_refusal`, `adjust_unsupported`).
  `release_all` stays the exit-time safety net.
- **`live/actuate/routine_bank.py::RoutineBank`**: one ECU's routines.
- **`live/actuate/sessions.py::ActuationSessions`**, a `Protocol` with one call:
  `async ensure(tx_id, mode) -> bool`. The banks open sessions only through it. `canair io` and
  `canair routines` pass an adapter over their own `SessionManager` with the background keepalive,
  which is their behaviour today. The monitor passes an adapter over its own `SessionManager`.
- The banks reach the terminal through a callable, because the monitor replaces its terminal on a
  reconnect, and report messages through a `report(text, level)` callable.
- `IOControlController` and `RoutinesController` keep only the view: view mode, edits, rows.
- The pipeline's `iocontrol` step goes through an `IoBank`, and the pipeline releases every
  actuator it switched on when it ends (problem 3).

### 4. The monitor's actuation collaborator

`live/monitor/actuation.py::MonitorActuation` (`controller.actuation`) holds one `IoBank` and one
`RoutineBank` per `--actuate` ECU and submits their requests to the lane. It never imports
Textual.

- **A session for the actuator.** A job opens the entry's `session_mode` through the monitor's
  `SessionManager`. When the mode differs from the one the monitor's steps opened, the ECU
  switches and the header's `sess` item shows it. After the last actuator on that ECU is released,
  the job restores the previous mode (or the default session when the steps opened none).
- **The session switch (`t`) refuses an ECU with an actuator ON** and names the actuators to
  release first.
- **Reconnect.** After `rebind()`, before polling resumes, the monitor sends the release of every
  actuator that was ON and marks it OFF. A release is safe to repeat, so this is correct whether or
  not the ECU's session survived the drop.
- **Exit.** `live/monitor/run.py::mode_monitor` releases every ON actuator before
  `controller.close()` closes the sessions. A stop signal reaches the same `finally`.
- **Hiding the pane releases nothing.** The header shows how many actuators are ON (yellow, with
  `⚠`), so a hidden pane cannot hide a driven output.

### 5. The flag

- `--actuate ECU[,ECU…]`, repeatable. Each ECU must exist and have curated `iocontrol:` or
  `routines:` entries. A KWP2000 ECU's routines are left out with a note
  (`check_routines_protocol`), and its IOControl entries keep their KWP2000 rules.
- The pane needs a terminal: a piped `monitor --actuate` exits with code 2.
- The pane does not offer `canair io --poll` (it sends releases on a timer, which can actuate) or
  the edit keys (label, notes, verify, promote). Those stay in `canair io` and `canair routines`.

### 6. Panes with their own keys

- **The values become a widget**, `ValuesPane` (a `VerticalScroll` subclass in
  `tui/apps/monitor/`). It takes the bindings that act on the values: the signal cursor, edit,
  verify, ship, filter, top and bottom.
- **The app keeps the global keys:** quit, help, save and new segment, pause, poll rate and
  schedule, rulers, view mode, reconnect, the picker, the session type, the event log, session
  info, the pane toggle and the focus keys.
- **`ActuatorPane`** takes the actuator keys, the roles that `canair io` and `canair routines`
  use: `enter` toggle (over a routine: request results), `O` send OFF, `w` set value, `+`/`-` step
  the value, `!` start routine (through `ConfirmModal`, destructive), `S` stop routine.
- **New role `actuators`**, key `A`: show the pane and focus it, or hide it. `tab`/`shift+tab`
  (`block_next`/`block_prev`, with `next pane`/`prev pane` descriptions) move focus between the
  panes. `esc` in the pane moves focus back to the values. It never quits.
- **`space` stays pause everywhere.** `+`/`-` in the values change the poll rate and in the pane
  step the value: the focused pane decides, and the help overlay says so per pane.
- **The keymap contract** discovers every `Widget` subclass that declares `BINDINGS`.
- **The help overlay** lists the global keys, then one section per pane.
- **Layout.** The pane is hidden by default. At 160 columns or more it docks on the right (about
  40 % of the width), below that at the bottom (about 40 % of the height, at least 6 rows).

### 7. What the pane shows

One table, one row per actuator, grouped by ECU (IOControl DIDs first, then routines).

- **Readback:** the `status_param` signal's latest decoded value, from the monitor's last results.
  No bus traffic. If the signal's PID is not polled, the cell says `not polled` (dim).
- **State:** ON, OFF, queued, the NRC with its name (`uds/nrc.py`), and the last reply.
- Only curated entries are shown.

### 8. Recording actuations

A session-level **`actuations`** list, a sibling of `link_events`, in a pure leaf module,
`captures/actuations.py`.

```json
"actuations": [
  {"at": "14:02:11.400", "rx": "0x778", "kind": "iocontrol", "id": "BC0C",
   "action": "on", "request": "2FBC0C03", "outcome": "ok", "response": "6FBC0C03"},
  {"at": "14:02:31.950", "rx": "0x778", "kind": "iocontrol", "id": "BC0C",
   "action": "release", "request": "2FBC0C00", "outcome": "ok", "response": "6FBC0C00"},
  {"at": "14:05:02.010", "rx": "0x7A8", "kind": "routine", "id": "12A1",
   "action": "start", "request": "310112A1", "outcome": "nrc", "nrc": "0x22"}
]
```

- `action`: `on`, `off`, `adjust`, `release`, `start`, `stop`, `results`, or `held`. `held` opens
  a new segment (`n` key) once for each actuator that is still ON, so every segment says which
  outputs were driven at its start.
- Enums, addresses and hex only. No labels or free text.
- Journal record type `actuation`, written durably, reconciled into the session like
  `link_events`. A schema entry, a validation check, one-line items on disk, the merge driver.
- An actuation is not a gap: forward fill does not change.
- **Consumers:** `captures uds --sessions` shows the count, `captures --step` marks the frame
  nearest each actuation, and `investigate --events` puts actuations on its timeline.

## Phases

One commit per phase. Each passes the gates in the **contributing-code** skill.

1. **Raw client: per-ECU lock and `ensure_ecu`** (problems 1 and 2 on the transport side).
2. **`timed_exchange` measures the pipe hold.**
3. **Extract the actuation core** (`IoBank`, `RoutineBank`, `ActuationSessions`).
4. **The pipeline releases on exit** (problem 3).
5. **Pane-scoped keys in the monitor**, with no new feature.
6. **The foreground lane**, with the session switch moved onto it.
7. **`MonitorActuation` and `--actuate`**: sessions, release on reconnect and on exit.
8. **`ActuatorPane`**: the `A` role, focus keys, layout, header count, readback, confirmation.
9. **Recording**: `captures/actuations.py`, journal, schema, validation, merge, `held`.
10. **Consumers**: `captures uds --sessions`, `captures --step`, `investigate --events`.

### Docs

- Regenerate `docs/reference/cli/` for `--actuate`.
- `docs/concepts/safety.md`: actuation from the monitor, the opt-in, release on exit and
  reconnect.
- `docs/concepts/captures-and-states.md`: the `actuations` list.
- `docs/profiles/hyundai-ioniq-ae-ev.md`: one toggle-and-observe example.
- The `live/monitor/`, `live/actuate/` and `tui/apps/monitor/` charters, the `MonitorApp`
  docstring, and the architecture page.

## On-car checks

These need the user's explicit approval, command by command, at the time. Nothing here is
approval.

1. ELM327 transport, car in ACC: `canair monitor IGPM:22BC03 --actuate IGPM --save --label
   'rear defogger toggle'`. Toggle `BC0C` and watch the readback. Quit with the output ON and check
   that it is released.
2. The same on `slcan-tcp`, with one PID polled on another ECU, to exercise the per-ECU lock.
3. A forced reconnect (`R`) with an output ON: the release goes out after the reconnect.
4. SKM relays (`--actuate SKM`), which need the SKM's own session mode: check the switch and the
   restore.

## Follow-ups

- Scripted actuation in a monitor run (`--at 10s 'iocontrol IGPM BC0C'`).
- A driving guard: refuse actuation while the suggested state is one a profile declares unsafe.
- Profile actions (`actions.yaml`) in the pane.
- More panes (a recording pane, a session pane) on the pane-scoped keys of phase 5.
- The rest of `plans/2026-10-27-raw-owed-ledger-blackout.md` (the probe that settles a debt).
