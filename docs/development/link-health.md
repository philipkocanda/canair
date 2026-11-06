# Link health: fallback, reconnect and desync recovery

How canair decides which device to use, keeps a session alive when the link drops, and notices
when a link is up but useless. Read this page before you change a transport backend, add a
request path or change the monitor's reconnect behaviour. The user-facing view is
[Remote & cellular links](../concepts/remote-and-cellular.md).

## Choosing a device

- **Auto-fallback.** When the selected device is unreachable at connect time, canair tries the
  other configured devices. Settings: `transport.fallback` (default true, `--no-fallback` per
  command), `transport.connect_timeout` (default 5.0 s, the per-device liveness probe) and
  `transport.fallback_order`. The selected device is always tried first.
- **Candidates and selection.** `transport/config.py::resolve_transport_candidates` builds the
  ordered list. `transport/fallback.py::select_reachable_transport` picks the first one that
  answers a liveness probe. It runs before the raw-or-ELM branch, so a fallback **can cross
  transports**. A `wican-ws` device is skipped on a `classic` WiCAN. The probe checks port 80
  for a WiCAN HTTP transport and the data port for any other transport.
- **`canair status` runs the same probe**, so its diagnosis matches what a live command will do.
  Never resolve only the selected device there (`resolve_transport_candidates(args)[0]` discards
  the fallback and reports a working setup as dead).
- **`--wait`** blocks the initial connect and retries until a candidate appears
  (`fallback.py::wait_for_reachable`). It also makes a mid-session drop reconnect forever.

## Reconnect after a drop (`monitor`)

A session that drops mid-run is **re-homed** instead of abandoned. The monitor re-probes the
reachable devices of the **same transport** (raw to raw, ws to ws; only the initial connect
crosses transports), rebuilds the client, re-opens the sessions and resumes. A `--save`
recording continues on the same journal.

- The window is `transport.reconnect_max_wait` (default 60 s). `--wait` removes the bound.
- The mechanism is `live/monitor/reconnect.py`. The per-transport "connect to this candidate"
  closures are `cli/live/connect.py::build_elm_reconnector` and
  `live/monitor/raw_run.py::build_raw_reconnector`.
- The gap goes into the session's `link_events`
  (`captures/link_events.py`, written by `live/monitor/link_events.py`). The recorder also
  records pauses, silent ECUs, unpolled PIDs and backoff. **Every link-failure path must go
  through `MonitorController.mark_disconnected`.** Never set `disconnected` directly, or the
  gap is not recorded.
- Recorded gaps bound the forward fill of the analysis joins
  (`analysis/fill.py::next_gap_starts`). Holes that no event explains are inferred for display
  only (`captures/inferred_gaps.py`).
- Plan: `plans/2026-08-03-monitor-reconnect-and-wait.md`.

## Correctness-triggered reconnect

A session can stop being usable while **nothing raises**. A half-open socket keeps accepting
writes. A desynchronised ELM327 pipe keeps returning well-formed replies, each one to the
**previous** request, forever. Three mechanisms sit under the reconnect, and a new request path
must not bypass them.

1. **Every request slot is echo-validated**, so that a one-slot offset can be detected at all.
   Pass `expected_sid` **and** `expected_echo` (`uds/echo.py::request_echo`) on any new
   `send_uds` call. An unvalidated request ignores a desync and returns the wrong PID's bytes as
   the right PID's value. Keepalives are validated too (`3E00` with `expected_sid=0x3E`). An
   unvalidated keepalive consumes the orphaned reply and clears the dirty-pipe flag, which is how
   the offset used to become permanent.
2. **The ELM327 engine repairs itself.** A stale response (`CAT_STALE`) triggers
   `Elm327Terminal._resync()`. It drains for a window that comes from the adapter's own `ATSThh`
   budget plus the measured link latency, never from a hardcoded value. A drain that is shorter
   than the adapter's ECU wait cannot discard the reply it chases. Then it probes with `ATI` and
   raises `ConnectionError` if the adapter itself is silent. Each repair counts as `resyncs` on
   `TransportStats`. The prompt ledger that normally prevents the need for a resync is
   `transport/elm327/pipe.py::ResponsePipe`.
3. **Hold `terminal.transaction()` across `set_header` and `send_uds`.** The command lock is per
   command. Without the transaction, a concurrent keepalive can retarget `ATSH` between a read's
   header and its request. The transaction is re-entrant per task. `RawTerminal`'s is a no-op,
   because the raw path addresses every frame explicitly.

Two config keys back these mechanisms:

- `transport.ws_ping_interval` (default 20 s, `0` disables) makes a dead `wican-ws` link raise.
- `transport.stale_cycles_before_reconnect` (default 3, `0` disables) is the backstop.
  `MonitorHealth.check_liveness` (`live/monitor/health.py`) hands the session to the reconnector
  after N poll cycles in which no live ECU answered coherently. **An NRC counts as an answer**,
  because the reply landed in the right slot.

Plan: `plans/2026-08-08-elm327-pipe-desync-recovery.md`.

## The link is measured, not configured

`transport/link_latency.py::LinkLatency` is an RFC 6298 smoother (`srtt + 4*rttvar`). Both data
domains share it. A plain mean underestimates about half of the time, and every underestimate
orphans a reply.

- `seed()` takes one unambiguous measurement: the TCP handshake, timed in
  `SlcanTcpBus.__init__`. It exists before any protocol traffic, which is when the ISO-TP budgets
  must be chosen.
- `observe()` needs `_MIN_SAMPLES` samples. It is fed **only** by exchanges that the adapter
  answers alone (`_LINK_PROBE_CMDS` in the ELM327 terminal: `ATI` and the addressing commands).
  A UDS read mixes link and ECU time and cannot be split afterwards.
- `allowance(floor)` keeps the caller's default as a floor.
- Consumers **add** the allowance to the configured value. The configured value is the car's
  share, the measurement is the network's, and the delays are sequential.
    - `isotp_params.build_isotp_params(config, link_budget)` scales `rx_flowcontrol_timeout` and
      `rx_consecutive_frame_timeout`.
    - `RawUdsClient._budget` scales the per-request deadline. A caller-forced `--timeout` is an
      instruction, not a budget, so it stays untouched.
    - `Elm327Terminal._resync` scales its quiet window.
- Two once-per-session INFO hints come from the measurement and not from a hostname heuristic:
  `isotp_params._warn_blocksize_cost` and `raw/uds_client.py::_hint_transport_choice`.

Plan: `plans/2026-08-08-high-latency-link-hardening.md`.

## Related

- [Response frame counts](frame-counts.md): an undercount creates exactly the desync described
  here.
- The `wican-hardware-and-protocol` skill: device ground truth (the co-processor, the firmware
  modes and the WebSocket and HTTP API).
