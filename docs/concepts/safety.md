# Safety

Interacting with a vehicle's CAN bus can damage the car, trigger faults, or leave
it in an unsafe state. canair is built to be **safe by default**, but the
ultimate responsibility is yours. This page explains what canair will and won't
do.

## Safe by default

- **Reads are free.** Querying, scanning, capturing, and decoding only *read*
  from the bus. You can explore freely.
- **Mutations confirm first.** Anything that changes ECU or device state —
  clearing DTCs, IOControl actuation, starting routines, config writes, reboots —
  prompts for confirmation before acting, with an explicit flag escape hatch
  (e.g. `--yes`) for scripting.
- **Actuators auto-release.** IOControl actions release control back to the ECU
  when the session ends, so a toggled output doesn't stay stuck. On a UDS ECU the
  release is `returnControlToECU` (`2F {DID} 00`). KWP2000's `0x30` has no standard
  release, so canair only switches a `0x30` actuator on when its profile entry
  declares one (see below).
- **Packeted DIDs carry their mask.** One UDS DID can hold several outputs. Some makers
  then require a controlEnableMaskRecord on every request, the release included, and
  refuse a release without it, which would leave the output driven. Declare the mask
  with `canair pids set-iocontrol ECU DID --enable-mask HEX`. The value keys of the
  `canair io` TUI then append it, and the release becomes `2F {DID} 00` with `FF` for
  each mask byte, which ISO 14229-1 requires every ECU to accept as "hand every output
  back".

## Hard limits — what canair refuses

canair maintains a command blocklist that it enforces on **every** transport. It
will not:

- Open a UDS **programming session** (`10 02`, or `10 82` — the same session
  with the suppress-positive-response bit), the KWP2000 one (`10 85`), or
  Hyundai/Kia's alias of it (`10 05`, "ECU programming mode" in their ES95486
  spec; ISO reserves the value, so blocking it costs nothing elsewhere).
- Open the UDS **safety-system** session (`10 04`), used for airbag and
  pyrotechnic deployment tests.
- Perform firmware **write/upload** or flash services, or write ECU memory
  (`2E`, `3B`, `3D`), or start or stop a KWP2000 routine at a raw memory address
  (`38`, `39`; reading its results with `3A` stays allowed).
- Reset an ECU (`11`), stop its normal CAN traffic (`28`) or change its bit rate
  (`87`). Each can take a system off the bus while the car is in use. `canair reset`
  is the one way to send a reset (see [Resetting an ECU](#resetting-an-ecu)).
- Send a request it cannot judge or that changes what the ECU lets through:
  SecuredDataTransmission (`84`) carries another request inside it, Authentication
  (`29`) changes the tester's access rights, and DynamicallyDefineDataIdentifier
  (`2C`) defines data identifiers in the ECU (by memory address it would read memory
  past the `23` rules).
- Change how the ECU reports and times itself, in these services only for the
  requests that change state:
    - ControlDTCSetting off (`85 02`): the ECU stops recording DTCs until DTC
      setting is on again or the session ends. `85 01` (on) stays allowed.
    - AccessTimingParameter writes (`83 02`, `83 04`). The reads (`83 01`, `83 03`)
      stay allowed.
    - Starting periodic or event-driven replies (ReadDataByPeriodicIdentifier `2A 01`–`2A 03`,
      ResponseOnEvent `86 01`–`86 07`). The ECU then sends replies on its own, which
      can desync the link. Stopping them (`2A 04`, `86 00`) and listing the active
      events (`86 04`) stay allowed.
- Start or stop a **reprogramming routine** (`31 01`/`31 02`): ISO's eraseMemory
  (`FF00`), checkProgrammingDependencies (`FF01`) and the 2006 edition's
  eraseMirrorMemoryDTCs (`FF02`), plus Hyundai/Kia's firmware
  bank swap, delta patch, update-condition and features-on-demand routines
  (`0211`–`0213`, `0220`, `0301`, `0410`–`0440`). Reading a routine's results
  (`31 03`) stays allowed. The Hyundai/Kia ids are manufacturer-range values, so on
  another make they may be unrelated routines; the refusal is then only a prompt.
- Start or stop a **safety-system routine** (`31 01`/`31 02` with `E200`–`E2FF`).
  ISO 14229-1 reserves these ids for ISO 26021, which deploys or disables airbags
  and seatbelt pretensioners when a car is scrapped. A wrong request can injure a
  person, so `--unsafe` does not lift this refusal. A KWP2000 ECU is not judged by
  this rule, because there `31` takes a one-byte local identifier.
- Start a **KWP2000 routine that harms the Hyundai/Kia OBC or VCU** (on an ECU the profile
  says speaks KWP2000): `31 00` and `31 01` leave the application for the bootloader and wait for
  a reflash, `31 F1` hangs the VCU in a bench loader with the watchdog off even while the car is
  READY, and `31 F3` starts a board self-test loop on the VCU that runs until it is reset. All
  four need only the `10 90` session, which asks for no key. On another make they may be
  unrelated routines; the refusal is then only a prompt.
- Send the manufacturer-specific service `99`. The Hyundai/Kia OBC erases its stored
  VIN on it, in any diagnostic session.

Session requests are judged by the protocol of the ECU they go to (its
`identity.id_protocol`), because the same byte means different things: `10 82`
is a KWP2000 diagnostic session but a UDS programming session. When canair does
not know an ECU's protocol, it applies whichever reading is stricter.

- **UDS:** the manufacturer- and supplier-specific session types (`0x40`–`0x7E`)
  are refused, as is the suppress-bit form of anything but the default and
  extended sessions (`81`, `83`).
- **KWP2000:** session types in the manufacturer-defined `0x80`–`0xFF` range are
  refused, except the well-known diagnostic sessions `81`, `82` and `83`, because
  on an unknown car one of them may be a development or programming mode.

A profile can permit its own make's diagnostic session through a quirk. The
bundled Nissan Leaf profile declares `nissan_c0_session`, which permits exactly
`10 C0` — the session Nissan ECUs require for actuator tests — on its KWP2000
ECUs only (on a UDS ECU `C0` is an OEM session with the suppress bit). The
Hyundai/Kia profiles declare `hk_90_session`, which permits `10 90` on their
KWP2000 ECUs the same way: the Kia Soul EV opens it for actuator tests, and a
commercial scan tool opens it on every Kona EV powertrain ECU. No quirk can permit
a programming or safety-system session. `canair validate` checks
every actuator's `session_mode` against the same rule. A refused request stops
the command with `error: …` (exit code 2); `--unsafe` ([once you enable
it](#enabling-unsafe)) turns the refusal into a
prompt you must answer `YES`: anything else ends the run (exit code 1), and with no
terminal on stdin it is refused (exit code 2). The same prompt covers every refusal above
except the safety-system routines, and it does not cover the broadcast rule below.

Anything that is neither hex bytes nor an AT command is refused too, because the
blocklist cannot tell what it sends: an STN adapter's `STPX`, for one, puts any
frame on the bus. Whitespace inside hex (spaces, tabs, newlines) is ignored, as
the transports ignore it.

### Broadcasts

A request on the functional id (`0x7DF`, or `0x18DB33F1` in a 29-bit mode) is
heard by every ECU the gateway routes it to at once, so it gets a stricter rule:
it must be one of the reads `canair discover --functional` lists (TesterPresent,
one DID, ReadEcuIdentification, the DTC-by-status reads, one OBD-II mode 01 PID).
That holds whichever command sends it, `canair raw 7DF:…` and the REPL included,
and `--unsafe` does not change it. A clear-DTCs, a session change or an
IOControl to every ECU at once is not something to do by accident. A profile that
registers an ECU on the functional id (a car whose ECUs take their requests there)
addresses that ECU under the ordinary rules.

These exist to make it very hard to brick a real car by accident. The block is
enforced centrally, not per-command, so it can't be quietly bypassed by a
different code path.

## Enabling `--unsafe`

`--unsafe` is off until you turn it on in your own config file. Until then, a live
command that gets `--unsafe` stops with `error: …` (exit code 2) before it connects.
`--help` lists the flag only on a terminal, so piped help (what a script or an agent
reads) leaves it out. The flag still works there once the key is on. To turn it on:

```bash
canair config set safety.allow_unsafe true
```

This command asks you to type `YES`. No flag skips the prompt, and with no terminal
on stdin it is refused (exit code 2). canair reads the key only from the user config
file (`canair config path`), not from a legacy `config.yaml` in the repo root. Turn
it off again with `canair config unset safety.allow_unsafe`. With the key on, every
refused request still asks for its own `YES`, and every canair command starts with a
yellow banner on stderr that says unsafe mode is on, so you do not forget to turn it off.

!!! note "What this guard is for"
    These steps do not stop a malicious user, or a malicious agent. Anyone who wants to
    send a refused request can edit the config file, or change canair's code. The guard is
    against *accidental* use: most of all, an AI agent that means well, wants to finish its
    task, and sees `--unsafe` as the way to get a refused request through. The key, the
    typed consent and the hidden flag make sure that a person decides to turn it on.

## Resetting an ECU

`canair reset ECU TYPE` restarts one ECU with ECUReset (`0x11`). While an ECU restarts it
does not do its job: an ESC stops its ABS and stability control, an EPS stops helping the
steering, a BMS or VCU may open the high-voltage contactors, and a gateway takes a whole bus
with it.
**Reset an ECU only with the car parked and not in use.** canair does not check the car's
state for you.

The command is off until you turn it on in your own config file, the same way as
`--unsafe`:

```bash
canair config set safety.allow_reset true
```

That asks you to type `YES`, and no flag skips it. Then every reset asks you to type the
ECU's name. `--yes` skips that question, for scripts, but not the config key.

| Type | Request | What the ECU does |
|---|---|---|
| `hard` | `11 01` | hardReset: restarts as if its power were cycled (KWP2000: powerOn) |
| `key-off-on` | `11 02` | keyOffOnReset: restarts as if the key went off and on |
| `soft` | `11 03` | softReset: restarts its application |

The rules:

- **One ECU at a time.** A reset on the functional (broadcast) id is refused.
- **Only these three types.** `11 04`/`11 05` change how the ECU powers down and do not
  restart it, and `11 40`–`11 7E` are the manufacturer's or supplier's, so canair refuses
  them. So does the suppress-positive-response form (`11 81`): the reply is how you learn
  that the ECU took the reset.
- **A KWP2000 ECU, or one whose protocol the profile does not record, takes only `hard`.**
  ISO 14230-3 calls `01` powerOn; canair has not checked what the other values do there.
- `canair raw` and `canair read` still refuse `11 …`. The command sends its one request
  under an exact grant that ends with the send.

What to expect:

- The ECU answers `51 {type}` and then restarts, or restarts before it answers. No reply
  does not prove that nothing happened.
- Many ECUs refuse a reset in the default session (`--session` opens the extended session
  first), without security access, or unless their own conditions hold. Hyundai/Kia ECUs,
  for one, reset only with the ignition on and the drive system off (NRC `0x22` otherwise).
- After the restart the ECU is in its default session. Any security access or IOControl
  override is gone. canair sends TesterPresent until the ECU answers again
  (`--restart-timeout`, default 10 s) and reports how long that took.
- A reset is not a battery disconnect. Stored DTCs, adaptations and coding survive it.
  Other ECUs may store lost-communication DTCs while this one restarts: check with
  `canair dtc --all`.

## Discovery vs. actuation

Note the difference between *discovering* a capability and *using* it:

- `canair scan iocontrol` / `scan routines` ask *whether* an actuator or routine exists, using
  the least actuating request each service has. Which one that is depends on the make:
    - UDS IOControl uses the standard `returnControlToECU`, a safe probe.
    - KWP2000 IOControl (`0x30`) has no standard control-option byte (ISO 14230-3 leaves it to
      the manufacturer). The scan sends `30 {LID} 00`, which SSF 14230-3 calls
      `returnControlToECU`, but no make is known to follow that: on Nissan it begins a one-shot
      command, and what it does on Hyundai/Kia is unknown. So the sweep asks for confirmation
      first (`--yes` to skip; it refuses non-interactively without it).
    - On a profile with the `nissan_c0_session` quirk canair sends Nissan's "is this value
      supported?" query instead, which drives nothing, so there is no prompt. Nissan's behaviour
      for the KWP2000 routine-results service is undocumented, so `scan routines` asks for
      confirmation there (`--yes` to skip; it refuses non-interactively without it).
- Actually actuating (`canair io`) or starting a routine is a separate,
  confirm-first action. A KWP2000 `0x30` actuator is switched on only when its
  profile entry declares a `release:` — the request that hands the output back,
  which canair sends on exit and on Ctrl-C. Find that request per ECU (test it with
  `canair raw`) and record it with `canair pids set-iocontrol ECU KEY --release
  HEX`. It must be a `0x30` request on the same local identifier, or
  StopDiagnosticSession (`20`), which hands every output back; anything else would
  leave the output driven, so it is refused. The `canair io` TUI's status poll
  (`--poll`) sends each entry's release, so it skips a `0x30` entry without one, and
  its value keys build UDS `0x2F` requests, so they are refused for a KWP2000 ECU.

### Actuating from the monitor

`canair monitor` can switch outputs and run routines while it polls, so you can watch what an
output does. It does so only when you name the ECUs with `--actuate`. Without the flag, a
monitor run never actuates, which matters for a recording made while driving.

```bash
canair monitor IGPM:BC03 --actuate IGPM --save --label "rear defogger toggle"
```

- `A` shows the actuator pane: the named ECUs' IOControl entries and routines, beside the
  values on a wide terminal and below them otherwise. `tab` moves between the values and the
  pane, and `?` lists the pane's keys. A routine start asks first.
- Each request goes out between two poll requests, not after the poll cycle. On `slcan-tcp`
  it waits only for the poll's request to the same ECU, and the other ECUs keep polling.
- A request opens the session mode its entry declares on the monitor's own session. When the
  mode differs, the ECU switches, and the previous mode comes back once nothing on that ECU is
  held (no output on, no routine started and not stopped). The session-type picker (`t`)
  refuses an ECU that holds something.
- Hiding the pane releases nothing. The status line shows how many outputs are on. Every
  output still on is released when the monitor exits, and again after a reconnect: a dropped
  link may or may not have ended the ECU's session, and a release is safe to repeat.
- `--actuate` needs a terminal. In a script, a `canair read` pipeline drives an output with an
  `iocontrol` step and releases it when the pipeline ends
  ([pipelines](query-mini-language.md#pipelines)).
- With `--save`, every request is recorded in the session's `actuations` list
  ([captures](captures-and-states.md#what-the-recording-switched-actuations)).

## Profile actions: guarded, make-specific requests

Some things a car can be told to do are specific to its make, so canair has no
command for them. Putting a Hyundai/Kia to sleep is one: its diagnostic spec
defines `10 D1`, "Sleep Mode Start Request", sent on the functional id so the
gateway passes it to every network. A profile defines such a request as an
*action* in `actions.yaml` (`canair action add`), and `canair action run NAME`
sends it. A profile cannot use this to get around the rules above:

- **Every request still goes through the blocklist.** `10 D1` is an OEM session
  sent as a broadcast, so it is refused twice over. An action may send a refused
  request only when a code-owned quirk grants exactly that request on that id
  (`hk_forced_sleep` grants `10 D1` on `0x7DF` and nothing else), and only for the
  length of the action's own send. The same quirk does not make
  `canair read "raw 7DF:10D1"` work, and `--unsafe` has no effect on an action.
- **Every action is guarded.** It must name PIDs to read (`reads:`) and a
  condition on them (`require:`). canair reads them live, and only a TRUE result
  passes: a signal that did not read leaves the condition UNKNOWN, which refuses
  after one re-read. The condition may not use `or` or `not`, so adding a clause
  can only make it stricter, and it may only test ECUs the action reads.
- **It confirms, then checks again.** You type the action's name to send it
  (`--yes` skips this; with no terminal to ask on, `--yes` is required). The
  reads are then repeated, so a long pause at the prompt is never what the
  request goes out on. `--dry-run` reads and checks without sending.
- **Each reply must answer its request.** A request to one ECU must draw a reply
  to its own service (and, on a UDS ECU, its own sub-function and identifier), so
  a late answer to one of the guard reads is never taken for the action's. A
  broadcast that expects replies collects every responder: the action counts as
  answered only when every responder accepted it, and the report lists each one.
  A refusal or a silence stops the action before its next request, and canair
  never repeats an action's request on its own.

An action can also drive an output, read while it is driven, and hand it back:

- **`hold:`** names the ECUs whose diagnostic session canair opens before the
  requests and keeps alive to the end, as an IOControl needs. A hold is checked by
  the session rules above, so it can never be a programming session.
- **`then:`** waits (`wait:`, 0–60 s), then reads its selectors through the same
  path as `canair read`. They run only when every request was answered.
  `canair action run NAME --save` keeps them in `captures/`, labelled `action NAME`
  unless you pass `--label`, so nothing is asked while the output is driven.
- **`release:`** is sent at the end whatever happened: after a refused request,
  after an error, and on Ctrl-C. Only then are the held sessions closed. An
  IOControl request that drives an output is refused at load time unless a
  release on the same ECU and identifier hands it back, as `canair io` requires.

The Ioniq profile's `ig3-read` action closes the IGPM's IG3 relay (`2F BC44 03`)
with the ignition off, waits 3 s, reads the VCU, BMS, OBC, MCU, AAF and HVAC, and
releases the relay (`2F BC44 00`). It requires the same state as `sleep` below,
because IG3 also powers the OBC. The rail stays up for about 75–110 s after the
release, so a follow-up `canair read` in that window still reaches those ECUs.

The Ioniq profile's `sleep` action requires IGN1 and IGN2 off, the IGPM's IG3
relay off (no 12 V top-up in progress) and the charge port unlocked (not
charging). The request expects no reply, so canair can only report that it was
sent. Anything that talks to the car afterwards wakes it again, including the
WiCAN's own AutoPID polling.

### Injecting a broadcast frame

An action can also put a crafted **non-diagnostic broadcast CAN frame** on the bus
(`frames:`), for a make-specific function that no diagnostic service exposes — a
remote-preconditioning command a telematics unit would normally send, for
instance. This is the most dangerous thing canair can do: the frame is
unauthenticated, reaches ECUs directly, and no reply confirms it. Four independent
guards must all pass:

- **The profile declares the exact frame**, never a free-form id and payload: an
  arbitration `id`, an 8-byte `data` template, a `mask` of the bits the frame may
  drive, an optional free-running `counter`, and an optional repeat (`rate_ms` with
  `count` or `duration_s`). canair refuses at load time if `data` (or the counter)
  sets a bit outside `mask`, so every bit the author did not name stays 0 — that is
  how a dangerous bit on the same frame is provably held low.
- **`safety.allow_frame_injection` must be on**, a fail-closed config key like
  `safety.allow_unsafe`: read only from this user config file, only a YAML `true`
  enables it, and `canair config set safety.allow_frame_injection true` asks you to
  type YES at a terminal (no flag answers for it). `canair action run` refuses a
  frame action before it connects when the key is off.
- **The action's `reads`/`require` still gate it**, exactly as above.
- **It is `slcan-tcp` only**, and the `id` must be below `0x700`, so a frame can
  never forge a diagnostic response. An ELM327 transport is refused before the
  confirm.

Stopping the stream returns the bus to normal, but any behaviour the frame
*started* (a timed climate session, say) runs on until it ends on its own — a
frame action cannot always undo what it set in motion.

## Be gentle

ECUs vary in how quickly and reliably they respond — some are slow or finicky,
especially the first request after they've been idle. canair holds a single
connection at a time (a mutex prevents concurrent sessions that could lock up the
dongle), and you should never hammer an ECU with concurrent requests. When a
first read looks unresponsive, retry once before concluding the PID/ECU is dead.

If another canair session is already running, a new one refuses to start and
names the holder (PID, command line, how long it has been running).

## One session at a time — and how to clear a stuck one

The device serves a single connection, so canair takes a mutex for the duration
of a live session. It is released when the session ends — cleanly *or* by
crashing — so a dead session never leaves the lock behind.

- **`--force` asks that session to release the connection**, waits for it, and
  then proceeds. It never signals or kills anything: the holder notices the
  request itself and shuts down gracefully, reconciling its `--save` data.
- **A session that loses its terminal stands down on its own** once another run
  asks for the connection. This is the orphan case — an SSH drop or a closed
  window, where no hangup is ever delivered and the session would otherwise hold
  the device forever.
- **If the holder doesn't respond** within the wait window (an older canair, or a
  genuinely wedged process), `--force` gives up promptly and prints the exact
  `kill <pid>` to run — it never hangs, and never leaves you hunting for the PID.

Inspect and clear the mutex without starting a session:

```bash
canair lock          # who holds the device connection?
canair lock steal    # ask them to release it, then wait
canair lock kill     # signal a holder that won't release (confirms first)
```

`canair status` also shows whether the lock is free or held.

Any other command that does not talk to the device itself (`decode`, `captures`, `pids`, …) prints
a yellow `warning:` on stderr while another session holds the connection, naming the holder and
how to free it. Your output on stdout is unchanged, so pipes and `--json` stay clean. `canair
lock` and `canair status` report the lock themselves, so they do not repeat it.

Sessions shut down gracefully on **Ctrl-C**, on **`SIGTERM`** (`kill`, `pkill`),
and on **`SIGHUP`** (your terminal went away) — each reconciling any `--save`
data and releasing the connection on the way out. If a session was killed
outright (`kill -9`), the data it recorded is still in its write-ahead journal:
recover it with `canair captures uds --recover`.


## The bottom line

> Interacting with your vehicle's CAN bus and ECUs can damage your car, trigger
> faults, or leave it in an unsafe state. **Use this software entirely at your own
> risk.** You are solely responsible for any consequences.
