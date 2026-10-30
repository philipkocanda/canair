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
  (`87`). Each can take a system off the bus while the car is in use.
- Start or stop a **reprogramming routine** (`31 01`/`31 02`): ISO's eraseMemory
  (`FF00`) and checkProgrammingDependencies (`FF01`), plus Hyundai/Kia's firmware
  bank swap, delta patch, update-condition and features-on-demand routines
  (`0211`–`0213`, `0220`, `0301`, `0410`–`0440`). Reading a routine's results
  (`31 03`) stays allowed. The Hyundai/Kia ids are manufacturer-range values, so on
  another make they may be unrelated routines; the refusal is then only a prompt.

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
the command with `error: …` (exit code 2); `--unsafe` turns the refusal into a
prompt you must answer `YES`; the same prompt covers every refusal on this page.

These exist to make it very hard to brick a real car by accident. The block is
enforced centrally, not per-command, so it can't be quietly bypassed by a
different code path.

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
  HEX`. The `canair io` TUI's status poll (`--poll`) sends each entry's release, so
  it skips a `0x30` entry without one, and its value keys build UDS `0x2F` requests,
  so they are refused for a KWP2000 ECU.

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

Sessions shut down gracefully on **Ctrl-C**, on **`SIGTERM`** (`kill`, `pkill`),
and on **`SIGHUP`** (your terminal went away) — each reconciling any `--save`
data and releasing the connection on the way out. If a session was killed
outright (`kill -9`), the data it recorded is still in its write-ahead journal:
recover it with `canair captures uds --recover`.


## The bottom line

> Interacting with your vehicle's CAN bus and ECUs can damage your car, trigger
> faults, or leave it in an unsafe state. **Use this software entirely at your own
> risk.** You are solely responsible for any consequences.
