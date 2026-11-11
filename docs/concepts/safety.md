# Safety

Interacting with the Controller Area Network (CAN) bus of a vehicle can damage the car, trigger
faults, or leave it in an unsafe state. canair is **safe by default**, but you stay responsible.
This page explains what canair does and does not do.

## Safe by default

- **Reads are free.** Querying, scanning, capturing and decoding only *read* from the bus. You can
  explore freely.
- **Mutations confirm first.** Anything that changes the state of an electronic control unit (ECU)
  or of the device asks for confirmation before it acts. Examples are clearing diagnostic trouble
  codes (DTCs), IOControl actuation, starting routines, config writes and reboots. A flag such as
  `--yes` skips the prompt for scripts.
- **Actuators auto-release.** IOControl actions hand control back to the ECU when the session
  ends, so a toggled output does not stay stuck. On a Unified Diagnostic Services (UDS) ECU, the
  release is `returnControlToECU` (`2F {DID} 00`). The service `0x30` of Keyword Protocol 2000
  (KWP2000) has no standard release. So canair switches a `0x30` actuator on only when its profile
  entry declares a release (see below).
- **Packeted DIDs carry their mask.** One UDS data identifier (DID) can hold several outputs. Some
  makers then require a controlEnableMaskRecord on every request, including the release. They
  refuse a release without it, which would leave the output driven. Declare the mask with
  `canair pids set-iocontrol ECU DID --enable-mask HEX`. The value keys of the `canair io` TUI
  then append the mask. The release becomes `2F {DID} 00` with `FF` for each mask byte. ISO
  14229-1 requires every ECU to accept this as "hand every output back".

## Hard limits — what canair refuses

canair keeps a command blocklist and enforces it on **every** transport. canair will not:

- Open a UDS **programming session** (`10 02`, or `10 82`, the same session with the
  suppress-positive-response bit). It also will not open the KWP2000 one (`10 85`) or the alias
  of Hyundai/Kia for it (`10 05`, "ECU programming mode" in their ES95486 spec). ISO reserves the
  value, so blocking it costs nothing elsewhere.
- Open the UDS **safety-system** session (`10 04`), used for airbag and pyrotechnic deployment
  tests.
- Run firmware **write/upload** or flash services, write ECU memory (`2E`, `3B`, `3D`), or start
  or stop a KWP2000 routine at a raw memory address (`38`, `39`). Reading the results with `3A`
  stays allowed.
- Reset an ECU (`11`), stop its normal CAN traffic (`28`) or change its bit rate (`87`). Each can
  take a system off the bus while the car is in use. `canair reset` is the one way to send a
  reset (see [Resetting an ECU](#resetting-an-ecu)).
- Send a request that it cannot judge, or that changes what the ECU lets through:
    - SecuredDataTransmission (`84`) carries another request inside it.
    - Authentication (`29`) changes the access rights of the tester.
    - DynamicallyDefineDataIdentifier (`2C`) defines data identifiers in the ECU. By memory
      address, it would read memory past the `23` rules.
- Change how the ECU reports and times itself. This applies in these services only to the requests
  that change state:
    - ControlDTCSetting off (`85 02`): the ECU stops recording DTCs until DTC setting is on again
      or the session ends. `85 01` (on) stays allowed.
    - AccessTimingParameter writes (`83 02`, `83 04`). The reads (`83 01`, `83 03`) stay allowed.
    - Starting periodic or event-driven replies (ReadDataByPeriodicIdentifier `2A 01`–`2A 03`,
      ResponseOnEvent `86 01`–`86 07`). The ECU then sends replies on its own, which can desync
      the link. Stopping them (`2A 04`, `86 00`) and listing the active events (`86 04`) stay
      allowed.
- Start or stop a **reprogramming routine** (`31 01`/`31 02`). These routines are:
    - ISO's eraseMemory (`FF00`), checkProgrammingDependencies (`FF01`) and, in the 2006 edition,
      eraseMirrorMemoryDTCs (`FF02`).
    - The firmware bank swap, delta patch, update-condition and features-on-demand routines of
      Hyundai/Kia (`0211`–`0213`, `0220`, `0301`, `0410`–`0440`).

    Reading the results of a routine (`31 03`) stays allowed. The Hyundai/Kia ids are
    manufacturer-range values. On another make they may be unrelated routines. canair still refuses
    them there, but `--unsafe` can turn the refusal into a prompt (see below).

- Start or stop a **safety-system routine** (`31 01`/`31 02` with `E200`–`E2FF`). ISO 14229-1
  reserves these ids for ISO 26021, which deploys or disables airbags and seatbelt pretensioners
  when a car is scrapped. A wrong request can injure a person, so `--unsafe` does not lift this
  refusal. canair does not judge a KWP2000 ECU by this rule, because `31` takes a one-byte local
  identifier there.
- Start a **KWP2000 routine that harms the Hyundai/Kia OBC or VCU** (on an ECU that the profile
  says speaks KWP2000):
    - `31 00` and `31 01` leave the application for the bootloader and wait for a reflash.
    - `31 F1` hangs the VCU in a bench loader with the watchdog off, even while the car is READY.
    - `31 F3` starts a board self-test loop on the VCU. The loop runs until the VCU is reset.

    All four need only the `10 90` session, which asks for no key. On another make they may be
    unrelated routines. canair still refuses them there, but `--unsafe` can turn the refusal into
    a prompt (see below).

- Send the manufacturer-specific service `99`. The Hyundai/Kia OBC erases its stored VIN on it, in
  any diagnostic session.

canair judges a session request by the protocol of the ECU it goes to (its `identity.id_protocol`),
because the same byte means different things. `10 82` is a KWP2000 diagnostic session but a UDS
programming session. When canair does not know the protocol of an ECU, it applies whichever
reading is stricter.

- **UDS:** canair refuses the manufacturer- and supplier-specific session types (`0x40`–`0x7E`).
  It also refuses the suppress-bit form of anything but the default and extended sessions (`81`,
  `83`).
- **KWP2000:** canair refuses session types in the manufacturer-defined `0x80`–`0xFF` range,
  except the well-known diagnostic sessions `81`, `82` and `83`. canair cannot tell which of the
  other types in that range is a development or programming mode, so it refuses all of them.

A profile can permit the diagnostic session of its own make through a quirk.

- The bundled Nissan Leaf profile declares `nissan_c0_session`. It permits exactly `10 C0`, the
  session that Nissan ECUs require for actuator tests. It applies on its KWP2000 ECUs only. On a
  UDS ECU, `C0` is an OEM session with the suppress bit.
- The Hyundai/Kia profiles declare `hk_90_session`. It permits `10 90` on their KWP2000 ECUs in
  the same way. The Kia Soul EV opens it for actuator tests. A commercial scan tool opens it on
  every Kona EV powertrain ECU.

No quirk can permit a programming or safety-system session. `canair validate` checks the
`session_mode` of every actuator against the same rule.

A refused request stops the command with `error: …` (exit code 2). `--unsafe` ([once you enable
it](#enabling-unsafe)) turns the refusal into a prompt. You must answer `YES`. Any other answer
ends the run (exit code 1). With no terminal on stdin, canair refuses the request (exit code 2).
The same prompt covers every refusal above except the safety-system routines. It does not cover
the broadcast rule below.

canair also refuses anything that is neither hex bytes nor an AT command, because the blocklist
cannot tell what it sends. For example, the `STPX` command of an STN adapter puts any frame on the
bus. canair ignores whitespace inside hex (spaces, tabs, newlines), as the transports do.

### Broadcasts

A request on the functional id (`0x7DF`, or `0x18DB33F1` in a 29-bit mode) reaches, all at once,
every ECU that the gateway routes it to. So it gets a stricter rule. It must be one of the reads
that `canair discover --functional` lists:

- TesterPresent
- one DID
- ReadEcuIdentification
- the DTC-by-status reads
- one On-Board Diagnostics II (OBD-II) mode 01 parameter identifier (PID)

This holds whichever command sends the request, including `canair raw 7DF:…` and the REPL.
`--unsafe` does not change it. The rule exists because a clear-DTCs, a session change or an
IOControl to every ECU at once is not something to do by accident. A profile can register an ECU
on the functional id (a car whose ECUs take their requests there). canair addresses that ECU under
the ordinary rules.

These limits make it hard to brick a real car by accident. canair enforces the block centrally,
not per command. A different code path cannot bypass it quietly.

## Enabling `--unsafe`

`--unsafe` is off until you turn it on in your own config file. Until then, a live command that
gets `--unsafe` stops with `error: …` (exit code 2) before it connects. `--help` lists the flag
only on a terminal. Piped help (what a script or an agent reads) leaves it out. The flag still
works there once the key is on. To turn it on, run:

```bash
canair config set safety.allow_unsafe true
```

This command asks you to type `YES`. No flag skips the prompt. With no terminal on stdin, canair
refuses (exit code 2). canair reads the key only from the user config file (`canair config path`),
not from a legacy `config.yaml` in the repo root. Turn it off again with
`canair config unset safety.allow_unsafe`. With the key on, every refused request still asks for
its own `YES`. Every canair command also starts with a yellow banner on stderr that says unsafe
mode is on, so you do not forget to turn it off.

!!! note "What this guard is for"
    These steps do not stop a malicious user or a malicious agent. Anyone who wants to send a
    refused request can edit the config file or change the code of canair. The guard protects
    against *accidental* use. The main case is an AI agent that means well, wants to finish its
    task, and sees `--unsafe` as the way to get a refused request through. The key, the typed
    consent and the hidden flag make sure that a person decides to turn it on.

## Resetting an ECU

**Reset an ECU only with the car parked and not in use.** canair does not check the state of the
car for you. `canair reset ECU TYPE` restarts one ECU with ECUReset (`0x11`). While an ECU
restarts, it does not do its job:

- An ESC stops its ABS and stability control.
- An EPS stops helping the steering.
- A BMS or VCU may open the high-voltage contactors.
- A gateway takes a whole bus with it.

The command is off until you turn it on in your own config file, the same way as `--unsafe`:

```bash
canair config set safety.allow_reset true
```

This command asks you to type `YES`, and no flag skips it. After that, every reset asks you to
type the name of the ECU. `--yes` skips that question, for scripts, but not the config key.

| Type | Request | What the ECU does |
|---|---|---|
| `hard` | `11 01` | hardReset: restarts as if its power were cycled (KWP2000: powerOn) |
| `key-off-on` | `11 02` | keyOffOnReset: restarts as if the key went off and on |
| `soft` | `11 03` | softReset: restarts its application |

The rules:

- **One ECU at a time.** canair refuses a reset on the functional (broadcast) id.
- **Only these three types.** `11 04`/`11 05` change how the ECU powers down and do not restart
  it. `11 40`–`11 7E` belong to the manufacturer or supplier. canair refuses all of them. It also
  refuses the suppress-positive-response form (`11 81`), because the reply is how you learn that
  the ECU took the reset.
- **A KWP2000 ECU, or one whose protocol the profile does not record, takes only `hard`.** ISO
  14230-3 calls `01` powerOn. canair has not checked what the other values do there.
- `canair raw` and `canair read` still refuse `11 …`. The command sends its one request under an
  exact grant that ends with the send.

What to expect:

- The ECU answers `51 {type}` and then restarts, or it restarts before it answers. No reply does
  not prove that nothing happened.
- Many ECUs refuse a reset in the default session (`--session` opens the extended session first),
  without security access, or unless their own conditions hold. Hyundai/Kia ECUs, for one, reset
  only with the ignition on and the drive system off. Otherwise they answer with the negative
  response code (NRC) `0x22`.
- After the restart, the ECU is in its default session. Any security access or IOControl override
  is gone. canair sends TesterPresent until the ECU answers again (`--restart-timeout`, default
  10 s). It reports how long that took.
- A reset is not a battery disconnect. Stored DTCs, adaptations and coding survive it. Other ECUs
  may store lost-communication DTCs while this one restarts. Check with `canair dtc --all`.

## Discovery vs. actuation

Discovering a capability is different from using it:

- `canair scan iocontrol` and `canair scan routines` ask *whether* an actuator or routine exists.
  They use the least actuating request that each service has. Which request that is depends on the
  make:
    - UDS IOControl uses the standard `returnControlToECU`, a safe probe.
    - KWP2000 IOControl (`0x30`) has no standard control-option byte. ISO 14230-3 leaves it to the
      manufacturer. The scan sends `30 {LID} 00` (LID: local identifier), which SSF 14230-3 calls
      `returnControlToECU`. No make is known to follow that. On Nissan it begins a one-shot
      command. What it does on Hyundai/Kia is unknown. So the sweep asks for confirmation first
      (`--yes` skips the prompt). Without a terminal, it refuses unless you pass `--yes`.
    - On a profile with the `nissan_c0_session` quirk, canair sends the "is this value
      supported?" query of Nissan instead. This query drives nothing, so there is no prompt.
      Nissan does not document its behaviour for the KWP2000 routine-results service. So
      `scan routines` asks for confirmation there (`--yes` skips the prompt). Without a terminal,
      it refuses unless you pass `--yes`.
- Actuating (`canair io`) or starting a routine is a separate action, and it confirms first.
    - canair switches on a KWP2000 `0x30` actuator only when its profile entry declares a
      `release:`. This is the request that hands the output back. canair sends it on exit and on
      Ctrl-C.
    - Find that request for each ECU (test it with `canair raw`). Record it with
      `canair pids set-iocontrol ECU KEY --release HEX`.
    - The request must be a `0x30` request on the same local identifier, or StopDiagnosticSession
      (`20`), which hands every output back. canair refuses anything else, because anything else
      would leave the output driven.
    - The status poll of the `canair io` TUI (`--poll`) sends the release of each entry. So it
      skips a `0x30` entry without one.
    - The value keys of the TUI build UDS `0x2F` requests, so canair refuses them for a KWP2000
      ECU.

### Actuating from the monitor

`canair monitor` can switch outputs and run routines while it polls, so you can watch what an
output does. It does so only when you name the ECUs with `--actuate`. Without the flag, a monitor
run never actuates. This matters for a recording made while driving.

```bash
canair monitor IGPM:BC03 --actuate IGPM --save --label "rear defogger toggle"
```

- `A` shows the actuator pane: the IOControl entries and routines of the named ECUs. The pane sits
  beside the values on a wide terminal and below them otherwise. `tab` moves between the values
  and the pane. `?` lists the keys of the pane. A routine start asks first.
- canair sends each request between two poll requests, not after the poll cycle. On `slcan-tcp`,
  it waits only for the poll request to the same ECU. The other ECUs keep polling.
- A request opens the session mode that its entry declares. The monitor opens it with its own
  session manager, the one that keeps the other sessions of the monitor alive. When that mode
  differs from the current mode of the ECU, the ECU switches. Once nothing on that ECU is held
  (no output on, no routine started and not stopped), the previous mode comes back. If the ECU
  had no session before, canair closes the session. The session-type picker (`t`) refuses an ECU
  that holds something.
- Hiding the pane releases nothing. The status line shows how many outputs are on. The monitor
  releases every output that is still on when it exits, and again after a reconnect. A dropped
  link may or may not have ended the session of the ECU, and a release is safe to repeat.
- `--actuate` needs a terminal. In a script, a `canair read` pipeline drives an output with an
  `iocontrol` step and releases it when the pipeline ends
  ([pipelines](query-mini-language.md#pipelines)).
- With `--save`, canair records every request in the `actuations` list of the session
  ([captures](capture-format.md#what-the-recording-switched-actuations)).

## Profile actions: guarded, make-specific requests

Some things a car can be told to do are specific to its make, so canair has no command for them.
Putting a Hyundai/Kia to sleep is one example. Its diagnostic spec defines `10 D1`, "Sleep Mode
Start Request", sent on the functional id so that the gateway passes it to every network. A
profile defines such a request as an *action* in `actions.yaml` (`canair action add`).
`canair action run NAME` sends it. A profile cannot use an action to get around the rules above:

- **Every request still goes through the blocklist.** `10 D1` is an OEM session sent as a
  broadcast, so the blocklist refuses it twice over. An action may send a refused request only
  when a code-owned quirk grants exactly that request on that id. (`hk_forced_sleep` grants
  `10 D1` on `0x7DF` and nothing else.) The grant lasts only while the action sends its request.
  The same quirk does not make `canair read "raw 7DF:10D1"` work. `--unsafe` has no effect on an
  action.
- **Every action is guarded.** It must name PIDs to read (`reads:`) and a condition on them
  (`require:`). canair reads them live, and only a TRUE result passes. A signal that did not read
  leaves the condition UNKNOWN, which refuses after one re-read. The condition may not use `or` or
  `not`, so adding a clause can only make it stricter. It may test only ECUs that the action
  reads.
- **It confirms, then checks again.** You type the name of the action to send it. `--yes` skips
  this. With no terminal to ask on, `--yes` is required. canair then repeats the reads, so the
  request never goes out on data from before a long pause at the prompt. `--dry-run` reads and
  checks without sending.
- **Each reply must answer its request.** A request to one ECU must draw a reply to its own
  service (and, on a UDS ECU, its own sub-function and identifier). So canair never takes a late
  answer to one of the guard reads for the answer to the action. A broadcast that expects replies
  collects every responder. The action counts as answered only when every responder accepted it,
  and the report lists each one. A refusal or a silence stops the action before its next request.
  canair never repeats the request of an action on its own.

An action can also drive an output, read while it is driven, and hand it back:

- **`hold:`** names the ECUs whose diagnostic session canair opens before the requests and keeps
  alive to the end, as an IOControl needs. The session rules above check a hold, so a hold can
  never be a programming session.
- **`then:`** waits (`wait:`, 0–60 s). It then reads its selectors through the same path as
  `canair read`. They run only when every request was answered (or, for a request that expects no
  reply, was sent). The reads never open the REPL that `canair read --repl` opens. So no prompt
  waits while the output is driven. `canair action run NAME --save` keeps the reads in
  `captures/`, labelled `action NAME` unless you pass `--label`.
- **`release:`** is sent at the end whatever happened: after a refused request, after an error,
  and on Ctrl-C. Only then does canair close the held sessions. canair refuses at load time an
  IOControl request that drives an output, unless a release on the same ECU and identifier hands
  it back, as `canair io` requires.

The `ig3-read` action of the Ioniq profile closes the IG3 relay of the IGPM (`2F BC44 03`) with
the ignition off. It waits 3 s, reads the VCU, BMS, OBC, MCU, AAF and HVAC, and releases the relay
(`2F BC44 00`). It requires the same state as `sleep` below, because IG3 also powers the OBC. In
the test, the rail still held 75 s after the release and was off about 110 s after it. A
follow-up `canair read` within about 75 s still reached those ECUs.

The `sleep` action of the Ioniq profile requires IGN1 and IGN2 off, the IG3 relay of the IGPM off
(no 12 V top-up in progress), and the charge port unlocked (not charging). The request expects no
reply, so canair can only report that it sent the request.

### Injecting a broadcast frame

An action can also put a crafted **non-diagnostic broadcast CAN frame** on the bus (`frames:`). It
is for a make-specific function that no diagnostic service exposes, for instance a
remote-preconditioning command that a telematics unit normally sends. This is the most dangerous
thing canair can do. The frame is unauthenticated and reaches ECUs directly, and no reply confirms
it. Four independent guards must all pass:

- **The profile declares the exact frame**, never a free-form id and payload:
    - an arbitration `id`
    - an 8-byte `data` template
    - a `mask` of the bits that the frame may drive
    - an optional free-running `counter`
    - an optional repeat (`rate_ms` with `count` or `duration_s`)

    canair refuses at load time if `data` (or the counter) sets a bit outside `mask`. So every bit
    that the author did not name stays 0. This is how canair provably holds a dangerous bit on the
    same frame low.

- **`safety.allow_frame_injection` must be on.** It is a fail-closed config key like
  `safety.allow_unsafe`. canair reads it only from this user config file, and only a YAML `true`
  enables it. `canair config set safety.allow_frame_injection true` asks you to type YES at a
  terminal (no flag answers for it). `canair action run` refuses a frame action before it
  connects when the key is off.
- **The `reads`/`require` of the action still gate it**, exactly as above.
- **It is `slcan-tcp` only**, and the `id` must be below `0x700`, so a frame can never forge a
  diagnostic response. canair refuses an ELM327 transport before the confirm.

When the stream stops, the bus returns to normal. But any behaviour that the frame *started* (a
timed climate session, say) runs on until it ends on its own. A frame action cannot always undo
what it set in motion.

## Be gentle

ECUs vary in how quickly and reliably they respond. Some are slow or finicky, especially on the
first request after they have been idle. canair holds a single connection at a time. A mutex
prevents concurrent sessions that could lock up the dongle. Never hammer an ECU with concurrent
requests. When a first read looks unresponsive, retry once before you conclude that the PID or ECU
is dead.

If another canair session is already running, a new one refuses to start and names the holder
(process ID, command line, how long it has been running).

## One session at a time — and how to clear a stuck one

The device serves a single connection, so canair takes a mutex for the duration of a live
session. canair releases it when the session ends, cleanly *or* by crashing. A dead session never
leaves the lock behind.

- **`--force` asks that session to release the connection**, waits for it, and then proceeds. It
  never signals or kills anything. The holder notices the request itself and shuts down
  gracefully, reconciling its `--save` data.
- **A session that loses its terminal stands down on its own** once another run asks for the
  connection. This is the orphan case, for example an SSH drop or a closed window. No hangup is
  ever delivered there, and the session would otherwise hold the device forever.
- **If the holder does not respond** within the wait window (an older canair, or a genuinely
  wedged process), `--force` gives up promptly and prints the exact `kill <pid>` to run. It never
  hangs, and it never leaves you hunting for the process ID.

Inspect and clear the mutex without starting a session:

```bash
canair lock          # who holds the device connection?
canair lock steal    # ask them to release it, then wait
canair lock kill     # signal a holder that won't release (confirms first)
```

`canair status` also shows whether the lock is free or held.

Any other command that does not talk to the device itself (`decode`, `captures`, `pids`, …) prints
a yellow `warning:` on stderr while another session holds the connection. The warning names the
holder and says how to free it. Your output on stdout does not change, so pipes and `--json` stay
clean. `canair lock` and `canair status` report the lock themselves, so they do not repeat the
warning.

Sessions shut down gracefully on **Ctrl-C**, on **`SIGTERM`** (`kill`, `pkill`), and on
**`SIGHUP`** (your terminal went away). Each one reconciles any `--save` data and releases the
connection on the way out. If something killed a session outright (`kill -9`), its write-ahead
journal still holds the data that it recorded. Recover that data with
`canair captures uds --recover`.


## The bottom line

> Interacting with your vehicle's CAN bus and ECUs can damage your car, trigger
> faults, or leave it in an unsafe state. **Use this software entirely at your own
> risk.** You are solely responsible for any consequences.
