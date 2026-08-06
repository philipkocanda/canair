# Connection mutex: orphan self-abort, cooperative steal, SIGHUP

Date: 2026-08-05

## The incident

A `canair monitor` session was running over SSH from a phone. The network
switched, the terminal app disconnected — and the session kept running. Nothing
could stop it:

- No `SIGHUP` was ever delivered (a silent network drop; sshd never noticed the
  peer was gone, so the pty stayed open and the process was never hung up).
- `canair … --force`, the documented escape hatch, **hung**.
- The only remaining option was finding the PID by hand — and, in the event,
  rebooting the VM.

## Root causes

1. **`--force` could not steal from a live holder.** It called
   `fcntl.flock(fd, LOCK_EX)` *without* `LOCK_NB` — a blocking acquire. Against a
   dead holder (flock already released) it returned immediately; against a live
   one it blocked forever. The "stole the lock from PID N, which is still
   running" warning was near-unreachable: it required the recorded PID to be
   alive while *not* holding the flock.
2. **Nothing ever re-checked the mutex.** Once a session had the lock it never
   looked again, so a contender had no way to ask it to leave.
3. **`SIGHUP` was unhandled.** Its default disposition terminates the process,
   which does release the flock (the kernel closes the fd) but skips the
   `--save` journal reconcile and the clean device close. And in the *silent*
   drop case it is never delivered at all.
4. **Teardown assumed a working terminal.** The monitor's `finally` printed
   deferred save banners *before* reconciling the journal; an `EIO` on a dead tty
   skipped the reconcile entirely.
5. **No way to see or clear the mutex** short of `ps`-hunting: neither `canair
   status` nor any command reported the holder.

## Design

### Cooperative steal (no signals)

canair does not kill other processes on the user's behalf. Instead the holder is
*asked* to leave, and notices by itself:

- The lock file (`/tmp/wican-connection.lock`) keeps its existing format — a bare
  holder PID — so a mixed-version canair still reads it. No migration.
- A sibling `/tmp/wican-connection.steal` carries a **target-scoped**, TTL'd
  request: `target=<holder pid> by=<pid> at=<epoch>`. Target-scoping means a
  leftover request can never abort an unrelated later session; the TTL and a
  liveness check on `by` expire a request whose requester died.
- `--force`: try `LOCK_NB` first (a dead holder is still instant), else write the
  request and poll for up to `FORCE_WAIT_S` (10s). On success, take it and report
  how long it took. On timeout, withdraw the request and fail with the holder's
  PID, its command line, and the exact `kill` / `kill -9` to run — plus a pointer
  to `canair captures uds --recover` for its recorded data. Bounded either way.

### The holder side: a watchdog

`canlib/lock_watchdog.py::LockWatchdog` is a daemon thread polling
`WiCANLock.still_ours()` every 2s. The mutex counts as lost when:

- a pending request targets our PID,
- the lock file records a different holder PID, or
- the lock file was removed, or replaced (`(st_dev, st_ino)` changed) — which
  would otherwise let a second process hold a *different* inode's flock.

On loss it fires once: prints why, then `SIGTERM`s **itself**, reusing each
command's existing graceful-stop path. A separate thread means it works even
while the main thread is blocked in a socket read (the signal interrupts the
syscall). It is stopped *before* `lock.release()`, so a normal teardown can never
be mistaken for a takeover.

This is why the watchdog, not an external signal, is the fix: the orphan is the
only party that can reliably free the device's single connection, and it is
reachable even when no signal ever arrives from outside.

### Stop signals

`canlib/stop_signals.py` centralizes the policy: `SIGTERM` **and `SIGHUP`** both
route to the same graceful stop as Ctrl-C, with previous handlers restored on
exit (so the monitor can override for its duration and hand back).
`graceful_stop_async` is the event-loop variant used by the monitor TUI, which
routes a stop signal through the app's own quit action — Textual tears down
cleanly instead of a `KeyboardInterrupt` unwinding through its internals.

Every output write in a shutdown path is now failure-tolerant: after a `SIGHUP`
the tty is gone, and a failed banner must not cost the user their captures.

### Surfaces

- `canair status` — read-only `Lock` line + `lock` block in `--json`, probed
  without taking the lock.
- `canair lock` — show the holder; `canair lock steal` — the cooperative steal
  without opening a session; `canair lock kill` — signal a holder that won't
  release (confirms; refuses a PID whose command line shows it isn't canair,
  which is the recycled-PID guard).

## Layering

| Concern | Home |
|---|---|
| Mutex + steal protocol + read-only probe | `canlib/lock.py` |
| Holder self-abort | `canlib/lock_watchdog.py` |
| Signal policy (sync + asyncio) | `canlib/stop_signals.py` |
| Wiring for every live command | `canlib/commands/_live.py::run_live` |
| Wiring for the sniffer | `canlib/commands/sniff.py` |
| User-facing inspection/clearing | `canlib/commands/lock.py`, `commands/status.py` |

The lock's paths resolve per call/instance rather than as argument defaults, so
they are redirectable — which is what makes the protocol testable against a
tmpdir instead of the real `/tmp`.

## Tests

Device-free, using a real child process as the "orphaned session":

- Cooperative steal end-to-end (the holder stands down; no signal sent).
- **Regression:** `--force` against a holder that ignores the request returns
  within the window instead of hanging, names the PID, and withdraws the request.
- `still_ours()` positives (request for us, foreign holder PID, removed/replaced
  file) and negatives (request for another PID, dead requester, expired request).
- Watchdog: fires once, silent while ours, never fires on normal teardown, and a
  failing probe can't break a session.
- `SIGTERM`/`SIGHUP` reach the graceful path in `run_live`, the monitor's piped
  loop, and the TUI; handlers are restored.
- Journal reconcile survives a dead terminal.
- `canair lock` show/steal/kill, including the not-canair refusal and
  `canair status` not taking the lock while probing it.

## Deliberately out of scope

1. **The mutex is global, not per-device.** Two different WiCANs (home + VPN)
   serialize unnecessarily. Keying the lock by device host is a follow-up.
2. **`/tmp` hygiene.** The lock is intentionally shared across users (the device
   is), so it stays in `/tmp`; this change only adds graceful handling when the
   file is owned by someone else.
3. **Autonomous "my terminal vanished" detection** is not possible for a silent
   network drop — sshd keeps the pty open — which is precisely why the mutex flag
   and `canair lock` are the escape hatches.
