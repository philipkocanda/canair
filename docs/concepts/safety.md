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
  when the session ends, so a toggled output doesn't stay stuck.

## Hard limits — what canair refuses

canair maintains a command blocklist that it enforces on **every** transport. It
will not:

- Open a UDS **programming session** (`10 02`).
- Perform firmware **write/upload** or flash services.

These exist to make it very hard to brick a real car by accident. The block is
enforced centrally, not per-command, so it can't be quietly bypassed by a
different code path.

## Discovery vs. actuation

Note the difference between *discovering* a capability and *using* it:

- `canair scan iocontrol` / `scan routines` are **safe probes** — they ask
  *whether* an actuator or routine exists (using the read-only variants), they
  don't trigger anything.
- Actually actuating (`canair io`) or starting a routine is a separate,
  confirm-first action.

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
