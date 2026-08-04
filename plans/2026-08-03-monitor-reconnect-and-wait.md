# Auto-failover during monitoring + `--wait`

Status: **DONE** (2026-08-03) — both features shipped. Verified 2026-08-04:
`transport/fallback.py::wait_for_reachable` (:92), `config.py::reconnect_max_wait`
(:405) + `transport.reconnect_max_wait` in `config.example.yaml`, `--wait` in
`add_connection_args` (`_live.py:312`), `modes/monitor_reconnect.py`
(`ReconnectPolicy` / `MonitorReconnector` / `reconnect_policy`), and
`tests/test_monitor_reconnect.py`. The per-section checklists below were never
ticked during implementation — they are the design record, not open work.

The two **out-of-scope** items still stand as stated: cross-transport mid-session
re-home, and re-homing a mid-session drop for non-monitor commands.

## Motivation

Two related reliability features, both requested together:

1. **Auto-failover / reconnect during monitoring.** Today cross-device fallback
   is **connect-time only** (`transport/fallback.py::select_reachable_transport`,
   invoked once in `async_main`). A mid-session drop sets
   `MonitorController.disconnected`, the poll loop exits, the `--save` journal is
   reconciled in `mode_monitor`'s `finally`, and a `ConnectionError` is raised and
   reported by the outer guard (`run_session_guarded` / the raw-monitor guard).
   The session is *given up*, never re-homed.
2. **`--wait`.** Keep retrying to reach the WiCAN **indefinitely**, and start
   monitoring (and saving) as soon as it comes online — so
   `canair mon @driving --save --wait` blocks until the device appears, then runs.

Both reduce to one primitive: *"keep re-probing candidates until one is
reachable, then (re)connect."*

Builds on `plans/2026-08-01-per-device-transport-and-fallback.md`.

## Decisions (confirmed with the user)

- **Same-transport reconnect + failover** for mid-session re-home (raw↔raw /
  ws↔ws). Cross-transport mid-session re-home is out of scope (would require
  tearing down + rebuilding the whole controller + ELM re-init / AutoPID
  restore). The *initial* connect keeps today's cross-transport fallback.
- **`--wait` is boolean** = retry forever (interruptible with Ctrl-C).
- **Bounded auto-failover is the default** (no `--wait`): on a drop, retry a
  short bounded window then give up exactly as today. `--wait` makes both the
  initial connect *and* mid-session reconnect retry forever.
- **`--save` continuity:** on reconnect, continue the *same* journal/session; the
  gap is visible in the timestamps. (No new segment.)
- **`--wait` lives in the shared connection args** so all live commands inherit
  it (initial-connect wait). Mid-session reconnect is monitor-only (the poll
  loop); for other commands `--wait` only affects the initial connect.

## Architecture

One new primitive + one controller method + one flag + reconnect wiring at the
two poll-loop runners.

### 1. `wait_for_reachable` (transport/fallback.py)

New sync helper next to `select_reachable_transport`, plus a shared
`_first_reachable(candidates, connect_timeout) -> TransportConfig | None`:

```
wait_for_reachable(candidates, *, connect_timeout, poll_interval=1.0,
                   deadline=None, stop=None, notice=None) -> TransportConfig | None
```

Loops `_first_reachable`; returns the first reachable candidate. `deadline=None`
= forever (that's `--wait`); otherwise a `time.monotonic()` deadline bounds it.
`stop()` (a flag predicate) aborts (Ctrl-C). Sleeps `poll_interval` in ≤0.1s
slices honoring `stop`/`deadline`. Emits a one-shot "waiting…" `notice`.

Generalizes `wican_mode.wait_until_ready` (which stays as-is; a later cleanup can
delegate it here).

### 2. Config knob

`config.py::reconnect_max_wait() -> float` reads `transport.reconnect_max_wait`
(default `6.0`) — the bounded no-`--wait` reconnect window. (Kept separate from
`fallback_settings()` so its 3-tuple contract and callers don't change.)

### 3. `--wait` flag

`add_connection_args`: `--wait` (`store_true`); register `"wait": False` in
`CANAIR_DEFAULTS`. Every live subcommand inherits it.

**Initial connect** (`async_main`, before `select_reachable_transport`): if
`args.wait`, `transport = wait_for_reachable(candidates, deadline=None, …)` (loops
forever, prints a waiting notice, Ctrl-C → `KeyboardInterrupt` handled by
`run_live`); else `select_reachable_transport(...)` as today. Raw path benefits
too (selection happens before the raw branch).

### 4. Reconnect infra (new module `modes/monitor_reconnect.py`)

- `ReconnectPolicy(forever, max_wait, connect_timeout, poll_interval=1.0)` with
  `deadline_from(now)`.
- `MonitorReconnector(candidates, connect, policy)`, async `__call__(controller,
  session_steps, *, stop, notice) -> bool`:
  1. best-effort close the dead client (`controller.close_client()`),
  2. `cand = await asyncio.to_thread(wait_for_reachable, …)` (thread keeps the
     TUI/event loop responsive; `stop`/`notice` are thread-safe),
  3. `client = await connect(cand)` — transport-specific async build; on a
     transport error, retry within the same deadline,
  4. `controller.rebind(client)` + `await controller.setup(session_steps)`,
  5. return `True` (resumed) / `False` (gave up or stopped).
- `reconnect_policy(args) -> ReconnectPolicy` from `fallback_settings()` +
  `args.wait` + `reconnect_max_wait()`.

`connect` callables are built where the client-build knowledge already lives, and
reuse the *same* build path as the initial connect (no duplication):

- **ELM:** extract `connect_elm_terminal(host, pids_data, args) -> WiCANTerminal`
  in `_live.py` (construct + `connect()` + `init_elm` + ATST + per-ECU timeouts).
  `async_main` calls it for the initial connect; the reconnect closure (built in
  `dispatch_mode`'s monitor branch) calls it per candidate. Passed to
  `mode_monitor` as a new `reconnect=` kwarg.
- **Raw:** extract `build_raw_client(host, port, bitrate, ecus, args, pids_data)`
  in `raw_monitor.py`; used by both the initial build and the reconnect closure.
  Built in `run_raw_monitor` and passed to `mode_monitor`.

Candidate filter: raw → `[c for c in candidates if c.is_raw]`, elm →
`[c for c in candidates if c.is_elm]`.

### 5. `MonitorController` changes

- New attributes (typed, defaulted in `__init__`): `session_steps: list[dict] |
  None`, `reconnect: Reconnector | None`, `reconnecting: bool`.
- `rebind(new_client)`: swap `terminal`→rebuild `sm`, or `raw_client`→rebuild
  `raw_poller`; clear `disconnected`. Journal/history/counters preserved ⇒ same
  `--save` session continues.
- `close_client()`: best-effort close the current transport client only (no
  journal reconcile), for the reconnector to free the dead socket.

`mode_monitor` sets `controller.session_steps`/`controller.reconnect` and passes
`reconnect` down (raw path passes directly; ELM path threads it through
`dispatch_mode`).

### 6. Poll-loop integration

- **`_monitor_noninteractive`**: on `controller.disconnected`, if `reconnect` is
  set, print "⟳ reconnecting…", `await reconnect(...)`; `True` → `continue`,
  `False` → if user-stopped clear `disconnected` (clean stop) else leave it
  (give-up → `mode_monitor` raises → reported). `None` reconnect = today's exit.
- **TUI (`_monitor_tui.py`)**: on `disconnected`, set `controller.reconnecting`,
  show a reconnect banner in the status line, `await reconnect(...)`; resume in
  place on success. A quit during reconnect sets a stop flag (clears
  `disconnected` for a clean exit).

### 7. Ctrl-C / signals

`_monitor_noninteractive` already installs SIGINT/SIGTERM → `stop_flag`; the
reconnect wait reads it via `stop`. Pre-connect `--wait` relies on the default
`KeyboardInterrupt` path (no custom handler installed yet at that point).

## Tests

- `wait_for_reachable`: reachable-now / reachable-after-N / deadline timeout /
  stop-abort, with a fake `_tcp_open`.
- `MonitorReconnector`: fake client errors after K cycles then recovers → assert
  rebind + resume and a single continuous `--save` session; bounded give-up →
  today's clean classified exit; user-stop during reconnect → clean stop.
- `MonitorController.rebind`/`close_client` unit tests (raw + elm shapes).
- `--wait` initial connect with a fake probe (device appears after N).

## Docs

- `README.md` monitor map line (+ `--wait`).
- `docs/reference/config.md` — correct the "connect-time only; mid-session
  reported, not re-homed" note; document `reconnect_max_wait` + `--wait`.
- `docs/reference/cli/*.md` (regenerated usage) + `docs/reference/cli/monitor.md`.
- `docs/getting-started/connect-device.md` — mention `--wait`.
- `config.example.yaml` — `reconnect_max_wait` under `transport:`.
- `AGENTS.md` — the auto-fallback paragraph + `canair monitor` flags.
- `CHANGELOG.md` — `[Unreleased]` entry.

## Out of scope

- Cross-transport mid-session re-home.
- Re-homing a mid-session drop for non-monitor commands (only initial-connect
  `--wait` applies there).
