# Link health: fallback, reconnect and desync recovery

How canair decides which device to use, keeps a session alive when the link drops, and notices
when a link is up but useless. Read this page before you change a transport backend, add a
request path or change the reconnect behavior of the monitor. The user-facing view is
[Remote & cellular links](../concepts/remote-and-cellular.md).

## Choosing a device

- **Auto-fallback.** When the selected device is unreachable at connect time, canair tries the
  other configured devices. It always tries the selected device first. The settings are:

    - `transport.fallback` (default true, `--no-fallback` per command)
    - `transport.connect_timeout` (default 5.0 s, the per-device liveness probe)
    - `transport.fallback_order`

- **Candidates and selection.** `transport/config.py::resolve_transport_candidates` builds the
  ordered list. `transport/fallback.py::select_reachable_transport` picks the first candidate that
  answers a liveness probe. It runs before the raw-or-ELM branch, so a fallback **can cross
  transports**. canair skips a `wican-ws` device on a `classic` WiCAN. The probe checks port 80
  for a WiCAN HTTP transport and the data port for any other transport.
- **`canair status` runs the same probe**, so its diagnosis matches what a live command will do.
  Never resolve only the selected device there. `resolve_transport_candidates(args)[0]` discards
  the fallback and reports a working setup as dead.
- **Commands outside the live runtime** (`status`, `sniff`, the `wican` device commands) call
  `transport/fallback.py::select_device`, which resolves and probes in one step. A fallback
  device must also suit the command: `sniff` accepts raw devices only, and the HTTP-only `wican`
  commands accept WiCAN devices only. The `wican` commands apply one more rule
  (`cli/commands/wican/common.py::resolve_device_url`). A device named with `--wican` is used as
  given and never swapped, because `mode set` and `autopid upload` rewrite or reboot it. Only the
  implicit default falls back. `tests/contracts/test_device_selection.py` fails when a command
  picks a device without probing it.
- **`--wait`** blocks the initial connect and retries until a candidate appears
  (`fallback.py::wait_for_reachable`). It also makes a mid-session drop reconnect forever.

## Reconnect after a drop (`monitor`)

canair **re-homes** a session that drops mid-run instead of abandoning it. The monitor re-probes
the reachable devices of the **same transport** (raw to raw, ws to ws). Only the initial connect
crosses transports. The monitor then rebuilds the client, re-opens the sessions and resumes. A
`--save` recording continues on the same journal.

- The window is `transport.reconnect_max_wait` (default 60 s). `--wait` removes the bound.
- The mechanism is `live/monitor/reconnect.py`. The per-transport "connect to this candidate"
  closures are `cli/live/connect.py::build_elm_reconnector` and
  `live/monitor/raw_run.py::build_raw_reconnector`.
- canair puts the gap into the `link_events` of the session (`captures/link_events.py`, written
  by `live/monitor/link_events.py`). The recorder also records pauses, silent electronic control
  units (ECUs), unpolled PIDs and backoff.
  **Every link-failure path must go through `MonitorController.mark_disconnected`.** Never set
  `disconnected` directly, or canair does not record the gap.
- Recorded gaps bound the forward fill of the analysis joins (`analysis/fill.py::next_gap_starts`).
  canair infers holes that no event explains for display only (`captures/inferred_gaps.py`).
- Plan: `plans/2026-08-03-monitor-reconnect-and-wait.md`.

## Correctness-triggered reconnect

A session can stop being usable while **nothing raises**. A half-open socket keeps accepting
writes. A desynchronised ELM327 pipe keeps returning well-formed replies, each one to the
**previous** request, forever. Three mechanisms sit under the reconnect. A new request path must
not bypass them.

1. **Every request slot is echo-validated**, so that canair can detect a one-slot offset at all.
   Pass `expected_sid` **and** `expected_echo` (`uds/echo.py::request_echo`) on any new `send_uds`
   call. An unvalidated request ignores a desync. It returns the bytes of the wrong parameter
   identifier (PID) as the value of the right PID. Keepalives are validated too (`3E00` with
   `expected_sid=0x3E`). An unvalidated keepalive consumes the orphaned reply and clears the
   dirty-pipe flag. It leaves its own reply for the next reader, so the offset becomes permanent.
2. **The ELM327 engine repairs itself.** A stale response (`CAT_STALE`) triggers
   `Elm327Terminal._resync()`.

    - It drains until the pipe stays quiet for a window. The window is the `ATSThh` budget of the
      adapter plus the measured link latency. A fixed margin is only a floor, and a fixed maximum
      caps the window.
    - The window must be longer than the ECU wait of the adapter. A shorter window can end before
      the late reply arrives. That reply then survives the drain and keeps the pipe offset.
    - It then probes with `ATI` and raises `ConnectionError` if the adapter itself is silent.
    - Each repair counts as `resyncs` on `TransportStats`.
    - The prompt ledger that normally prevents the need for a resync is
      `transport/elm327/pipe.py::ResponsePipe`.

3. **Hold `terminal.transaction()` across `set_header` and `send_uds`.** The command lock is per
   command. Without the transaction, a concurrent keepalive can retarget `ATSH` between the header
   of a read and its request. The transaction is re-entrant per task. `RawTerminal` holds a real
   lock too. It addresses every frame explicitly, but the ECU that a request goes to is the one
   that its `set_header` named. Another task could otherwise change that or read the reply off the
   same ISO-TP (the CAN transport protocol) stack.

Two config keys back these mechanisms:

- `transport.ws_ping_interval` (default 20 s, `0` disables) makes a dead `wican-ws` link raise.
- `transport.stale_cycles_before_reconnect` (default 3, `0` disables) is the backstop.
  `MonitorHealth.check_liveness` (`live/monitor/health.py`) hands the session to the reconnector
  after N poll cycles in which no live ECU answered coherently. **A negative response code (NRC)
  counts as an answer**, because the reply landed in the right slot.

Plan: `plans/2026-08-08-elm327-pipe-desync-recovery.md`.

## The link is measured, not configured

`transport/link_latency.py::LinkLatency` is a smoother that follows RFC 6298 (`srtt + 4*rttvar`).
Both data domains share it. A plain mean underestimates about half of the time, and every
underestimate orphans a reply.

- `seed()` takes one unambiguous measurement: the Transmission Control Protocol (TCP) handshake,
  timed in `SlcanTcpBus.__init__`. The measurement exists before any protocol traffic, which is
  when canair must choose the ISO-TP budgets.
- `observe()` needs `_MIN_SAMPLES` samples. Only exchanges that the adapter answers alone feed it
  (`_LINK_PROBE_CMDS` in the ELM327 terminal: `ATI` and the addressing commands). A read through
  Unified Diagnostic Services (UDS) mixes link time and ECU time, and canair cannot split them
  afterwards.
- `allowance(floor)` keeps the default of the caller as a floor.
- Consumers **add** the allowance to the configured value. The configured value is the share of the
  car, the measurement is the share of the network, and the delays are sequential.

    - `isotp_params.build_isotp_params(config, link_budget)` scales `rx_flowcontrol_timeout` and
      `rx_consecutive_frame_timeout`.
    - `RawUdsClient._budget` scales the per-request deadline. A caller-forced `--timeout` is an
      instruction, not a budget, so it stays untouched.
    - `Elm327Terminal._resync` scales its quiet window.

- Two once-per-session INFO hints come from the measurement and not from a hostname heuristic:
  `isotp_params._warn_blocksize_cost` and `raw/isotp_stack.py::hint_transport_choice` (both raw
  clients call it).
- On `slcan-tcp`, a reply that stops after its first frame (a late flow-control frame is one
  cause) is a `drop` ("truncated ISO-TP: a first frame came, then …"), not `no_data`:
  `raw/isotp_stack.py::ReceptionFaults` reads can-isotp's reception errors.

Plan: `plans/2026-08-08-high-latency-link-hardening.md`.

## Related

- [Response frame counts](frame-counts.md): an undercount creates exactly the desync described
  here.
- The `wican-hardware-and-protocol` skill: device ground truth (the co-processor, the firmware
  modes and the WebSocket and HTTP API).
