# ELM327 pipe desync: detection, resync, escalation

Status: **DONE** (2026-08-08). Shipped in one change: `transport/elm327_terminal.py`
(`_resync` / `transaction` / validated keepalive), `transport/channel.py` (WebSocket
ping keepalive + close surfaced from `drain`), `config.py`
(`ws_ping_interval` / `stale_cycles_before_reconnect`), `transport_stats.py`
(`resyncs` + a `stale` property), `modes/monitor.py` (`_check_liveness`),
`modes/_monitor_tui.py` (transport-vs-internal exception split, `R` key, health
line), `modes/multi_exec.py` (echo-validated batch + transaction), and
`session_manager.py` (validated keepalive).

## The report

On a long drive, `canair monitor` over `wican-ws` eventually showed **every** signal
as `stale`. Ctrl-C and rerunning the *same* command fixed it immediately. The link
was WiCAN Pro → iPhone personal hotspot → WireGuard → server, so a transient stall
was expected — but the session never recovered on its own, which is the actual bug.

The log is the whole diagnosis:

```
09:08:07.541  no_data  ecu=0x7E4 pid=2102 :: Empty response
09:08:07.775  stale    ecu=0x7E4 pid=2102 :: Echo mismatch: response id 0x01 != expected 0x02
09:08:07.933  stale    ecu=0x7E4 pid=2103 :: Echo mismatch: response id 0x01 != expected 0x03
09:08:08.070  stale    ecu=0x7E4 pid=2103 :: Echo mismatch: response id 0x02 != expected 0x03
09:08:08.205  stale    ecu=0x7E4 pid=2104 :: Echo mismatch: response id 0x02 != expected 0x04
```

One `no_data`, then a **stable one-slot offset** for the rest of the session: each
request receives the *previous* request's reply. The lag is 2 on a PID's first
attempt and 1 on its retry, so there were two orphaned replies in flight. This is
not packet loss — every reply is intact, well-formed, and merely late by exactly one
transaction. The pipe is off by one and stays that way.

## Root cause

Four defects compound. Only the first is the trigger; the rest are why it is
permanent.

1. **The dirty-pipe drain window was shorter than the adapter's own ECU wait.**
   A read that times out with nothing sets `_pipe_dirty`, and the next command
   drains before sending. But `Channel.drain()`'s defaults are
   `per_recv_timeout=0.2, max_seconds=1.0`, while `ATST96` tells the adapter to wait
   `0x96 × 4.096 ms ≈ 614 ms` for the ECU. The drain therefore gave up **three times
   sooner** than the reply it was meant to discard could arrive — and cleared
   `_pipe_dirty` anyway, declaring success.

2. **The keepalive was unvalidated, and it launders the offset.** The background
   `3E00` fires every 2 s with no `expected_sid`. It reads the orphaned `61 xx`
   reply, sees a prompt, concludes `clean_exit=True` and clears `_pipe_dirty` — then
   leaves its *own* `7E 00` buffered for the next reader. From here every read
   consumes a prompt-terminated **stale** reply, so the only condition that triggers
   a drain (`_pipe_dirty`) never becomes true again. The one recovery mechanism has
   been permanently disarmed.

3. **A desync raises nothing, and only an exception triggered reconnect.**
   `MonitorController.disconnected` — the sole reconnect gate — was set only from an
   `except ConnectionError` branch. The socket is open and replies keep arriving, so
   nothing raises. `_displayify` carried the last good value forward with
   `stale: True` on *every* signal, with no age limit and no counter. The monitor
   was structurally incapable of noticing.

4. **Half the request surface wasn't validated at all**, so on other paths the same
   desync is not even visible. `request_echo` only understands single-identifier
   `0x21`/`0x22`; the multi-DID batch path passed no `expected_sid` whatsoever, so a
   shifted batch response parsed fine and its bytes were split into the **wrong
   DIDs** — silently wrong decoded values, nothing logged.

The failure class was already understood in this codebase: `transport/channel.py`
carries a comment describing exactly this for the *connect banner* — "a permanent
one-command offset for the whole session, which the engine's stale-frame defence
cannot recover because `connect()` also clears the dirty-pipe flag". The same trap
existed mid-session and had no guard.

## Design

Four layers, deliberately ordered from cheapest and most local to most drastic.
Each one alone would paper over the bug; the point is that a desync is *detected*
where it happens, *repaired* in place if possible, and only escalated to a
reconnect when repair fails.

### L1 — Detect and repair in the transport (`elm327_terminal.py`)

`send_uds` now calls `_resync(reason)` whenever a response comes back with
`error_kind == CAT_STALE`, **before** the retry/return decision — so it fires even
at `retries=0`, which is what the validated keepalive uses. `_resync`:

1. Computes the quiet window from the adapter's *own* configured budget:
   `min(self.timeout, _elm_response_budget() + 0.5)`, where the budget is parsed out
   of `elm_timeout_cmd` (`ATSThh` → `hh × 4.096 ms`). A drain that doesn't outlast
   the adapter's ECU wait cannot possibly discard the reply it is chasing — that was
   defect 1, so the window is derived, never hardcoded.
2. Drains with that window (up to `_RESYNC_MAX_SECONDS = 3.0`), clears
   `_pipe_dirty`, then sends `ATI` as a probe.
3. Raises `ConnectionError` if the probe gets no reply.

`ATI` is the probe because it is answered by the **adapter itself** without touching
the CAN bus, so its silence cannot be blamed on the car — it means the link is dead,
which is precisely the condition the existing reconnect handles. It is matched on
*presence of a reply only*, never on text: this firmware answers `ATI` with
`OBDLink MX`, not `ELM327 v…`.

`_resyncing` guards against recursion (the probe itself must not resync).

### L2 — Make every request slot verifiable

- The keepalive is now `send_uds("3E00", expected_sid=0x3E)` in both
  `elm327_terminal._tester_present_loop` and `session_manager.send_keepalive`, so it
  detects the offset instead of laundering it (defect 2), and re-raises
  `ConnectionError` so a failed resync propagates.
- `multi_exec._read_batch` passes `expected_sid=0x22` and
  `expected_echo=<first DID>` (defect 4).
- **`Terminal.transaction()`** — a new re-entrant async context manager on the
  protocol — holds `_cmd_lock` across `set_header` + `send_uds` as one unit.
  `_cmd_lock` was per-*command*, so the keepalive could retarget `ATSH` between a
  read's header and its request; `_read_single`'s docstring already admitted this
  was only probabilistically mitigated. Re-entrancy is by `_lock_owner`
  (`asyncio.current_task()`), because `set_header` itself issues two `send_command`
  calls. `RawTerminal` gets a no-op implementation: the raw path addresses every
  ISO-TP frame explicitly, so it has no shared adapter state to protect.

`keepalive_stale()` stays deliberately *outside* the transaction — it may need to
open a session, and that is not part of this exchange.

`session_manager.send_keepalive` refreshes its timestamp **even when the reply is
stale**: the request went out, so the ECU's S3 timer was reset regardless, and the
timestamp only rate-limits re-sends. Gating it on success would hammer a
non-answering ECU on every poll cycle.

### L3 — Escalate in the monitor (`monitor.py`, `_monitor_tui.py`)

`MonitorController._check_liveness()` counts consecutive poll cycles in which
nothing answered coherently (`_cycle_answered`, set by `_displayify`) and sets
`disconnected = True` at `config.stale_cycles_before_reconnect()` (default 3, `0`
disables). An **NRC counts as answered** — a negative response proves the request
reached the ECU and its reply came back in the right slot. An idle cycle (no
queries) is not a dead cycle.

`_monitor_tui._poll_loop` previously exited on any unexpected `Exception`, despite
`_attempt_reconnect` existing right below it. It now splits: `transport_error_types()`
sets `disconnected` and falls through to reconnect; a genuine internal fault still
exits, because reconnecting cannot fix a bug and would hide it.

Also `R` to force a reconnect (it only sets `disconnected`; reconnecting inside a
key handler would race the in-flight poll for the same terminal), and the health
line now shows `drops` / `stale` / `resync` / `errs` as separate segments.

### L4 — Notice a dead link at all (`channel.py`)

- `WebSocketChannel.connect` enables `ping_interval`/`ping_timeout` from
  `transport.ws_ping_interval` (default 20 s, `0` disables). Pings were previously
  *disabled*; with them off, a half-open socket is indistinguishable from a quiet
  ECU. The WiCAN registers `/ws` without a control-frame handler, so ESP-IDF's HTTP
  server answers pings itself — no firmware change needed.
- `drain()` on both channels no longer swallows a close. Both ended in
  `except (TimeoutError, Exception): break`, so a link that died *during* a drain was
  reported as a completed drain — fatal now that a drain is the first half of a
  resync. `ConnectionClosed` / TCP EOF now raise `ConnectionError`.

### Observability (`transport_stats.py`)

`record_resync(reason)` logs at INFO and tallies `resyncs`, exposed in
`snapshot()` / `quality()` / `diff()`. New `stale` property splits echo/SID failures
out of `drops`, which keeps summing drop+stale for capture-quality back-compat and
is therefore useless for telling reassembly corruption apart from a desync. A rising
`stale`/`resync` pair is what a marginal link looks like while it is still coping.

## Deliberately not done

- **Widening `request_echo` beyond `0x21`/`0x22`.** Six modes pass `expected_sid`
  without an echo (`sessions_scan`, `dtc` ×4, the KWP scans). Those are one-shot
  probes, not polling loops, so they cannot accumulate a persistent offset — and a
  resync now happens on the shared path anyway.
- **Removing the `hk_f1xx_minus_one` echo blessing.** It deliberately accepts a
  genuine off-by-one identity DID on Hyundai/Kia, which is indistinguishable from
  a one-slot desync on those DIDs. Identity reads are one-shot; not worth the
  regression risk.
- **A time-based staleness limit in `_displayify`.** Cycle counting already covers
  it and scales with the poll rate.

## Verification

`tests/test_terminal.py::TestPipeResync` replays the reported failure: a
prompt-terminated stale reply on a *clean* pipe now resyncs and the retry succeeds,
with the send order asserted as `["2102", "ATI", "2102"]`. Sibling cases pin the
quiet window against `ATST96`/`ATSTFF`/an unparseable `ATSTZZ`, the
`ConnectionError` on a silent probe, that a good response and an NRC never resync,
that the resync doesn't recurse, and that `resyncs` is tallied apart from the fault.
`TestTransaction` covers serialisation, re-entrancy, and lock-owner cleanup after a
failure. `TestStaleFrameDrain` is kept but narrowed — its old comment asserted that
a clean pipe *must not* drain, which is exactly the assumption this change bounds.

`tests/test_monitor_reconnect.py::TestStaleEscalation` covers the threshold, the
reset on a good value, an NRC counting as alive, `0` disabling, and an idle cycle
not counting. `tests/test_monitor_diag.py` covers the `last_stale` delta and the
health-line split. `tests/test_ws_channel.py` / `tests/test_elm327_tcp.py` cover the
ping keepalive being *enabled*, configurable and disablable, and a close surfacing
from `drain`. `tests/test_multi_batching.py` covers the echo-validated batch and its
transaction ordering. `tests/test_session_manager.py` covers the validated
keepalive, the timestamp refresh on a stale reply, and `ConnectionError`
propagation.
