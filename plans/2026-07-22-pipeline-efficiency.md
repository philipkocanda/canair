# Pipeline Efficiency & Timeout Robustness — Implementation Plan

Reduce **false timeouts**, **wasted round trips**, and **event-loop stalls** in
the query/monitor pipeline — independent of transport choice. Motivated by a
2026-07-22 cellular drive where VCU/MCU saw an elevated timeout rate; the code
audit found several transport-agnostic inefficiencies (see companion plan
`2026-07-22-cellular-transport-timeouts.md` for the transport side).

Approach: **measure first (Tranche 0), then fix (1-3)**, validating each change
against recorded per-ECU/PID RTT stats.

## Decisions (locked)

- **Sequencing:** Tranche 0 -> 1 -> 2 -> 3. Each tranche lands with unit tests +
  `pytest`/`ruff` green, and an on-device A/B vs Tranche 0 stats where relevant.
- **Journal durability (2.1):** **per-cycle fsync** — buffered `write()` + explicit
  `flush()` (flush+fsync); monitor flushes once per cycle. Worst-case hard-crash
  loss = last (~1 s) cycle; reconcile-on-exit / `--recover` unchanged.
- **Timeout config surface (1.4):** **CLI `--timeout` flag + per-ECU
  `response_timeout_ms`** in `ecus/*.yaml` (+ schema + validator), plus threading
  the global `profile.response_timeout_ms` into the raw path. Precedence:
  `--timeout` > per-ECU > profile > client default.

## Confirmed facts (grounding, file:line)

- Three request clients: `WiCANTerminal` (ELM/ws, `canlib/terminal.py`),
  `RawTerminal` (raw one-shot, `canlib/transport/raw_terminal.py`), `RawUdsClient`
  (raw pipelined monitor, `canlib/transport/uds_raw.py`).
- Profile layout is `profile.yaml` + `ecus/*.yaml`. Docs referencing
  `pids/_meta.yaml` / `pids/*.yaml` are **stale** (fix in a doc pass).
- Session timestamps update only in `SessionManager.open_session`
  (`session_manager.py:92`) and `send_keepalive` (`:100`); reads never refresh
  them, so `keepalive_stale(1.5)` (`:106-115`) fires on actively-polled ECUs.
- RTT is already measured (`terminal.py:130,212`; `raw_terminal.py:197,202`) then
  discarded; surfaced only as a monitor status aggregate.
- Profile `response_timeout_ms` is applied **only** on the ELM path
  (`_live.py:431-440`); the raw path builds clients with no timeout
  (`raw_ops.py:50-52`) and defaults to 2.0 s / 1.0 s.
- `args.timeout` is hardwired to 3.0 (`_live.py:114`); no `--timeout` flag, so the
  `max(1.0, args.timeout)` monitor floor (`raw_monitor.py:61`) is dead code.

---

## Tranche 0 — Instrumentation (do first)

**0.1 Per-(ECU,PID) RTT capture.**
- Add a `TimingRecorder` (dict `(ecu,pid) -> {n, mean, max, last}`) on each client,
  stamped in `send_command` (`terminal.py`), `_exchange_tx` (`raw_terminal.py`),
  and `RawUdsClient.poll` (`uds_raw.py`). Timing is already computed — just key +
  keep it.
- Surface via `canair query … --timings` (sorted "slowest PID" table on exit) and
  in `--json`.
- **Test:** feed fake elapsed values -> correct aggregation; render snapshot.

**Rationale:** confirms which PIDs/ECUs are slow and proves Tranches 1-3 help.

---

## Tranche 1 — False timeouts & wasted round trips (targets the symptom)

**1.1 Retry-on-timeout for user-facing one-shot reads.**
- Add `retries: int = 0` to `WiCANTerminal.send_uds` (`terminal.py:271`) and
  `RawTerminal.send_uds`. Retry **only** on timeout / NO DATA / no-response —
  **never** on a valid NRC (definitive answer).
- Enable `retries=1` at: `query` (`multi.py:367`), `--param` (`param.py:54`),
  `--ecu` (`ecu.py:56`), `raw` (`raw.py:55`), `identity` (`identity.py:105`).
  Leave `scan`/`discover`/`*-scan` at `0` (NO DATA is the expected negative there).
- Matches profile guidance (`profile.yaml:14-16`).
- **Test:** timeout-then-success -> one retry, success surfaced; NRC -> no retry.

**1.2 Per-request deadlines in `RawUdsClient.poll`.** One shared round deadline
(`uds_raw.py:125-127`) lets a slow/silent ECU starve ECUs collected after it
(~0.05 s left) -> spurious timeouts.
- Record `sent_at` per inflight request; collect against each request's own
  `deadline = sent_at + t`, polling stacks in a short-slice loop until each is done
  or individually past deadline.
- **Test:** ECU A responds at 0.8 s, B at 0.9 s, t=1.0 s -> both succeed.

**1.3 Honor NRC 0x78 in `RawUdsClient`.** `read`/`poll` never call the
`is_response_pending` helper (`uds_raw.py:36-43`), unlike ELM (`terminal.py:201`)
and RawTerminal (`raw_terminal.py:190`).
- Loop on `is_response_pending`, re-arming recv with a fresh deadline (cap total,
  like RawTerminal's 20 s).
- **Test:** fake stack emits `7F2278` then `62…` -> final positive returned.

**1.4 Timeout budget model.**
- Thread `profile.response_timeout_ms` into `RawTerminal` (`raw_ops.py:50-52`) and
  `RawUdsClient` (`raw_monitor.py:61`).
- Optional per-ECU `response_timeout_ms` in `ecus/*.yaml`; resolved per request
  (VCU/MCU longer, ESC/EPS snappy). Add to ECU schema + `canair validate`.
- Add a real `--timeout` CLI flag; retire the dead monitor floor
  (`raw_monitor.py:61`). Precedence: `--timeout` > per-ECU > profile > client
  default.
- **Test:** resolver precedence unit test; schema accept/reject of the new field.

**1.5 Active read counts as keepalive.** Reads don't refresh `_sessions[tx_id]`, so
an actively-polled ECU gets a spurious `3E00` (+ possible `ATSH`/`ATFCSH` switch)
every 1.5 s (`session_manager.py:106-115`, `multi.py:365`).
- Add `SessionManager.mark_active(tx_id)` (stamp if present); call after every
  successful read in `_read_single`/`_read_batch`. A UDS request already resets the
  ECU S3 timer, so this is correct.
- **Test:** 3 reads over 2 s on an open session -> zero keepalives emitted.

---

## Tranche 2 — Monitor / save hot-loop (active during the `--save` drive)

**2.1 Batch journal flush/fsync to once per cycle.** `_write` does flush+`os.fsync`
per record (`capture_journal.py:99-106`), called per-PID per-cycle synchronously on
the loop (`monitor.py:433,461`).
- Split into buffered `write()` (no fsync) + explicit `flush()` (flush+fsync);
  `_record` appends all rows then calls `journal.flush()` once per cycle.
- **Test:** monkeypatch `os.fsync` counter -> 1/cycle regardless of PID count;
  reconcile still yields all rows.

**2.2 Bounded / viewport render with `--keep-all`.** TUI rebuilds the whole buffer
every cycle walking all history (`_monitor_tui.py:185`, `monitor.py:178-190`);
history unbounded (`monitor.py:438`) -> ~O(cycles^2 * PIDs) + unbounded memory.
- Render only the visible viewport (or cap displayed rows/PID with "+N more");
  keep full data in the journal, not the render buffer. Optionally cap in-memory
  `hex_history` when journaling.
- **Test:** 10k synthetic cycles -> flat per-cycle render time, bounded memory.

**2.3 Decode memoization + expression compile-once.** Unchanged payloads
re-decoded every cycle (`decoding.py` via `multi.py:332`); `evaluate_expression`
re-tokenizes each call (`expression.py:9`).
- Cache decoded rows keyed by `(pid, payload_hex)`; cache compiled expression ASTs
  keyed by expression string.
- **Test:** decode twice on same payload -> expression evaluated once (spy).

**2.4 Prime the ELM monitor's first cycle (parity with raw).** Raw primes each ECU
(`monitor.py:378-388`); ELM branch (`:391-404`) doesn't.
- One throwaway read per ECU (or `retries=1` on cycle 1) in ELM `setup()`.
- **Test:** ELM monitor setup issues one prime per distinct ECU.

**2.5 Skip dead `save_history` growth when journaling** (`monitor.py:434,690` — not
read on the journal path).

---

## Tranche 3 — Throughput & hygiene (small, independent)

**3.1 Multi-DID batching in the one-shot pipeline.** `mode_multi` passes no
`batch_state`, so the gate is always off (`multi.py:543`); `multi_did` ECUs
(IGPM/BCM) send 1 request/DID vs the monitor's <=3-DID grouping.
- Pass a real `batch_state` in `mode_multi` for `multi_did` ECUs.
- **Test:** `IGPM:BC03,BC06,BC07` builds 1 batched request when `multi_did`.

**3.2 Event-loop hygiene.**
- Replace blocking `time.sleep` in async transport with `await asyncio.sleep`
  (`raw_terminal.py:166`; guard `uds_raw.py:79`, which is in sync `__init__`).
- Drop the redundant second `ATST96` per connect (`_live.py:437`) unless the
  resolved value differs from the init string.
- Add `open_timeout` to the WS connect (`terminal.py:63`) for symmetry with
  SLCAN's 5 s (`slcan_tcp.py:103`).

**3.3 Session-entry cleanups.**
- "Already-active" guard on `open_session` (skip redundant `10 03` if
  `has_session(tx_id)` and fresh).
- Fix `--param` opening a session + keepalive loop **per (tx_id, pid)**
  (`param.py:47-52`) -> group by `tx_id`.

**3.4 Latent bug.** `multi.py:645` calls `sm.ensure_session(...)`, which does not
exist on `SessionManager` (only `open_session`) -> `AttributeError` if the
`iocontrol` pipeline step ever opens a session. Add the method (or fix the call).
- **Test:** `iocontrol` pipeline step needing a session runs without error.

---

## Cross-cutting: verification & rollout

- Per-tranche: `uv run pytest` + `ruff` green; add the unit tests above.
- On-device A/B (via Tranche 0 stats): same multi-ECU query on `home`/LAN, before
  vs after each tranche — compare timeout counts + per-PID RTT. Restore device to
  `auto_pid` after.
- Docs pass: fix stale `pids/_meta.yaml` references in `AGENTS.md` + skills to
  `profile.yaml` + `ecus/*.yaml` (separate small commit).

## Status

- [ ] Tranche 0 — `TimingRecorder` on all 3 clients + `canair query --timings`
      (+ `--json`) + tests.
- [ ] Tranche 1 — retry-on-timeout (1.1); per-request poll deadlines (1.2); raw
      0x78 handling (1.3); timeout budget: `--timeout` flag + raw threading +
      per-ECU `response_timeout_ms` + schema/validator (1.4); active-read keepalive
      (1.5). Tests + on-device A/B.
- [ ] Tranche 2 — per-cycle journal flush (2.1); bounded/viewport render (2.2);
      decode/expression memoization (2.3); ELM monitor prime (2.4); drop dead
      `save_history` when journaling (2.5). Tests.
- [ ] Tranche 3 — one-shot multi-DID batching (3.1); event-loop hygiene (3.2);
      session-entry cleanups (3.3); `ensure_session` fix (3.4). Tests.
- [ ] Docs — profile-layout reference fix (`profile.yaml` + `ecus/*.yaml`).
