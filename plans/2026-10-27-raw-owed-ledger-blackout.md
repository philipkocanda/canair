# slcan-tcp: owed-response ledger blacks out an ECU after one lost reply

Status: **PROPOSED** (2026-10-27). Reproduced against the real `can-isotp` stack; no code changed.

`RawUdsClient` (the `monitor` client on `slcan-tcp`) keeps a per-ECU count of answers it is
still owed, so a late reply to an abandoned request is not served as the next request's answer.
The count assumes every abandoned answer is late. When an answer is **lost** instead (a silent
ECU, or a multi-frame reply that `can-isotp` aborted on a missing frame), the ledger never
recovers: every later reply from that ECU is discarded as the debt, the request times out, and
the timeout re-creates the debt. The ECU shows no data for the rest of the session.

The fix settles a debt with a TesterPresent probe, whose reply cannot be mistaken for data,
instead of guessing which reply is which.

## Background

The ledger came in with `bc45bd1` (2026-08-08, "correlate raw-CAN replies to their requests").
Echo validation (`uds_parse.payload_echo_mismatch`) rejects a reply for a different identifier;
the ledger covers what echo validation cannot see, a late reply to the **same** PID polled
again. Its regression test is `test_the_same_pid_recovers_via_the_ledger` in
`tests/test_uds_raw_correlation.py`.

Mechanics today (`canlib/transport/uds_raw.py`):

- `_abandon` adds one to `_owed[ecu]` at every deadline with no answer, and resets the count to
  0 once it reaches `_MAX_OWED_RESPONSES` (4).
- `_stale_reason` (`:184`) discards **any** message while `_owed[ecu] > 0`, whatever it
  contains, and the harvest then decrements the count (`:295`, `:408`).
- `_drain` discards messages queued before a send and decrements the count for each.
- A served reply sets the count to 0.

`read()` and `poll()` each carry their own copy of this loop.

## The bug

### Reproduction

Real `RawUdsClient` and real `can-isotp` 2.0.7 over python-can's `virtual` bus, with a scripted
ECU answering `22C101` with a 42-byte reply (First Frame plus six Consecutive Frames):

| Script | Request 1 | Request 2 | Requests 3-7 |
|---|---|---|---|
| healthy | reply | reply | reply |
| Consecutive Frame SN 3 dropped on request 2 | reply | timeout | **all timeout** |
| ECU silent for request 2 only | reply | timeout | **all timeout** |

`owed` stays at 1 and `stale` rises by one per request: each healthy reply is discarded as the
debt.

With the repo's `QueuedStack` fixture, an ECU silent for N poll cycles that then answers ten in
a row:

| Silent cycles | Healthy cycles answered afterwards |
|---|---|
| 1, 2, 3 | 0 / 10 |
| 4 | 10 / 10 |
| 5, 6, 7 | 0 / 10 |
| 8 | 10 / 10 |

An ECU polled for two PIDs (`2101`, `2102`) with one `2101` reply lost blacks out on both,
because the ledger discards even a reply whose echo proves it is not the debt.

### Mechanism

A lost answer leaves `owed = 1`. The next request's genuine reply is discarded (`owed` 0), the
request times out, and `_abandon` sets `owed` back to 1. The cap never fires because the count
never climbs past 1. After N silent cycles the count settles at N mod 4 and the same loop holds
it there.

### Exposure

- `monitor` on `slcan-tcp` only: `RawUdsClient` is built only for it (`raw_ops.py:113-116`).
  Other commands run over `RawTerminal`, which has no ledger.
- Every `RawUdsClient.read()` counts too. `MonitorController.setup` opens sessions (`10 03`,
  1 s, `monitor.py:635`) and sends one warm-up read per ECU (3 s, `:652`). An ECU asleep at
  monitor start begins the session owing an answer and never shows data once it wakes.
- The liveness reconnect (`_check_liveness`, `monitor.py:784`) fires only when nothing at all
  answered in a cycle, so one blacked-out ECU among healthy ones is never rebuilt. A one-ECU
  monitor recovers through it after `stale_cycles_before_reconnect` cycles.
- No recorded `slcan-tcp` session postdates `bc45bd1` (the twelve in `profiles/ioniq-2017` are
  from 2026-07-29/30), so there is no field evidence either way.

### Related defects on the same path

1. **Echo validation covers two services.** `request_echo` (`uds_parse.py:203-243`) validates
   only `21 xx` and `22 xxxx`; every other request skips validation, and any `7F` negative
   response passes for any request. A late `62…` landing in a `10 03` session switch's window is
   accepted, and the switch reported as done.
2. **ResponsePending clears the debt.** `read()` returns a `7F xx 78` when the final answer
   never arrives in time (`uds_raw.py:271-276`), then sets the count to 0 (`:297`) although that
   answer is still coming. The warm-up read uses the first polled PID, so its late answer can be
   served as the first poll's value.
3. **Two threads share one stack.** The session-switch action calls the blocking `read()` from
   a TUI worker (`_monitor_tui.py:1391`, through `monitor.py:601`) while `poll()` runs in an
   executor thread (`monitor_raw.py:190`) on the same stacks and the same `_owed`. Either can
   take the other's reply.
4. **ISO-TP errors are invisible.** `build_isotp_stack` passes no `error_handler`, and
   `uds_raw.py:36` sets the `isotp` logger to `ERROR`. A reception aborted by
   `WrongSequenceNumberError`, `ConsecutiveFrameTimeoutError` or
   `ReceptionInterruptedWith*Error` is indistinguishable from a silent ECU: a timeout tallied as
   `no_data`. `drop` is only ever produced on the ELM327 path.

`can-isotp` itself never delivers a truncated or misaligned message: it queues a message only
when the reassembly reaches the First Frame's declared length, aborts on a wrong sequence
number, and ignores a Consecutive Frame while idle. Nothing here changes that.

## Why a count cannot be fixed by itself

With only a count, "reply n was late and n+1's is still missing" and "reply n was lost and n+1's
has arrived" look identical: one correctly-echoing message in the window. Any rule that serves
that message guesses wrong in the first case, showing the previous cycle's value as fresh, which
is what the ledger exists to prevent. Any rule that discards it guesses wrong in the second,
which is today's blackout. Under persistent lateness (every reply after its own deadline, the
failure the `_budget` docstring at `uds_raw.py:241` anticipates) the first kind of rule is wrong
every cycle, while today's code times out every cycle, which is safe. Wrong values are worse
than timeouts, so the ambiguity must be removed, not resolved by a heuristic.

## Design

### 1. Settle debts with a probe

While an ECU owes any answer, send it a TesterPresent `3E 00` before anything else. Its reply
(`7E 00`, or a `7F 3E xx` negative response) carries a service no data request uses, so it is
unambiguous. An ECU answers its requests in order, so every reply still owed has either arrived
before the probe's reply or will never arrive.

1. **Drain before every send**, as today. Each drained message pays one debt. A drained probe
   reply settles all debts.
2. **Owed nothing:** send the request; serve the first message that passes validation (§3).
3. **Owed something:** send the probe instead, and hold the real request back.
   - Messages before the probe's reply are debts: discard them, tally `stale`.
   - The probe's reply settles every debt (`owed = 0`), and the real request goes out next.
     The exception is a `7F 3E 21` (busy) or `7F 3E 78` (pending) reply, because the ECU may
     still be working on an owed request. That keeps the debt, and the probe is retried on the
     next attempt.
   - The probe times out: the ECU is not answering. Add it to the debt and do not send the real
     request. In `poll()`, skip that ECU's remaining requests for this call too (§4).
4. **A request times out:** `owed += 1`, as today.
5. **A request gets only ResponsePending before its deadline:** `read()` still returns the
   `7F xx 78` as today, but `owed += 1`, because the final answer is still coming.

`_MAX_OWED_RESPONSES` goes away: the probe settles debts, so no count needs resetting.

Outcomes by scenario. A wrong value is never served, because a reply is served only when
nothing is owed after the drain and the probe:

| Scenario | Outcome |
|---|---|
| Healthy | Unchanged |
| Late reply lands before the next send | Drained; unchanged |
| Late reply lands in the next window | Discarded during the probe; real request then served |
| Lost once (silent, or reception aborted) | Probe answered, one extra round trip, then served |
| Silent N cycles, then healthy | Probe answered on the first cycle the ECU replies |
| Persistent lateness | Timeouts only, as today |
| ResponsePending, then silence | Debt kept; settled by a later probe |
| Several PIDs per ECU | A lost reply costs one probe, not the ECU |
| Multi-DID batch | Validated against SID and first DID, as today |

Cost: one `3E 00` round trip per incident. `3E 00` passes `check_command_safety`, is accepted
in the default session, and is already canair's keepalive for UDS and KWP2000.

The decision procedure lives in a pure module, `canlib/transport/raw_ledger.py`, after the
pattern of `elm327_pipe.py` (policy in a sibling, I/O in `uds_raw.py`). `read()` and `poll()`
both drive it, removing the duplicated loop.

### 2. One exchange per ECU at a time

A per-ECU lock in `RawUdsClient` covers drain, probe, send and harvest. `poll()` takes an ECU's
lock without waiting when it sends to that ECU and releases it when the request resolves; an ECU
whose lock `read()` holds keeps its request queued for the next round. `read()` waits for the
lock. Only `read()` ever blocks on a lock, and it holds no other, so there is no lock cycle.

### 3. Validate the service of every reply

Extend the raw path's check, not `request_echo`'s identifier table: a positive reply's first
byte must be the request SID + `0x40`, and a negative reply's second byte must be the request
SID. The identifier check for `21`/`22` stays. The ELM327 path already passes `expected_sid` on
its requests, so this brings the raw path level with it.

### 4. Skip a silent ECU for the rest of a poll

When an ECU's probe times out, its remaining queued requests in that `poll()` are not sent; each
resolves to an `EcuNotAnswering` exception (a `TimeoutError` subclass, so callers keep working).
They are not tallied as exchanges, because nothing was sent. Today a silent ECU costs one full
budget per queued PID per cycle, which slows every cycle.

### 5. Surface ISO-TP errors

1. `build_isotp_stack` takes an `error_handler`, and both raw clients pass one. It runs on the
   stack's own thread, so it only appends to a lock-protected per-stack record.
2. Count `WrongSequenceNumberError`, `ConsecutiveFrameTimeoutError`,
   `ReceptionInterruptedWithFirstFrameError` and `ReceptionInterruptedWithSingleFrameError` as
   `drop` in `TransportStats`, tagged with the ECU only. An abort can belong to a debt's reply,
   or fire after the request's deadline, so tagging it with a PID would sometimes be wrong. Do
   not count `UnexpectedConsecutiveFrameError` (leftover frames after an abort) or the
   malformed-frame errors.
3. `RawTerminal` gets the same tallying, so `canair read --save` on `slcan-tcp` reports drops.
   Its request logic is otherwise unchanged.

The errors are diagnostics only. The probe handles recovery, so nothing depends on attributing
an abort correctly.

## Tests

- **Unit, `raw_ledger.py`:** every branch of §1, including a busy or pending probe reply, a probe
  reply arriving in a drain, and a probe timeout.
- **Regression, `tests/test_uds_raw_correlation.py`:**
  - lost once, then healthy: served after one probe;
  - silent for N = 1..8 cycles, then healthy: recovers on the first answered cycle for every N;
  - persistent one-cycle lateness never serves value n−1;
  - late once, then lost: no wrong value;
  - two PIDs on one ECU, one reply lost: the other PID is unaffected after the probe;
  - a late `62…` in a `10 03` window is rejected;
  - ResponsePending then silence leaves a debt;
  - a session switch during a `poll()` gets its own reply;
  - the existing late-reply tests, updated for the probe, still never serve a stale value.
- **End to end, real `can-isotp`:** promote the virtual-bus harness to a test: healthy control,
  a dropped Consecutive Frame, a silent request, a duplicated frame. It opens no sockets, so it
  passes `tests/conftest.py`'s loopback guard; small timeouts keep it under a few seconds.

## Docs

- `docs/concepts/captures-and-states.md` (`quality`): `drop` now also occurs on `slcan-tcp`.
- `AGENTS.md` Transports section: one line naming `raw_ledger.py` and the probe rule, next to
  the existing raw-path notes.

## Alternatives considered

- **Hold, then serve at the deadline:** wait for `k + 1` messages, otherwise serve the newest.
  Rejected: under persistent lateness, and after a late reply followed by a lost one, it shows
  the previous cycle's value as fresh, where today's code times out.
- **Debts that expire after a grace period:** bounds the blackout but has the same ambiguity
  inside the window, and back-to-back requests keep renewing the debt.
- **Owed requests kept by identity (a FIFO of request bytes):** tells debts for other PIDs apart
  without a probe, but not same-PID debts, and a negative response carries only the service, so
  it cannot be matched to a PID. The probe covers both and keeps a plain count.

## Open questions

1. **Ordering through the gateway.** The design assumes an ECU's replies arrive in request
   order. That holds for a single UDS server; confirm on the Ioniq's gatewayed OBD port that a
   `3E 00` sent while a slow request is pending gets busy (`7F 3E 21`) or waits, rather than
   being answered ahead of the pending reply.
2. **KWP2000 ECUs outside a session** may answer `3E 00` with a negative response. Any reply
   other than busy or pending settles the debt, so this is harmless, but check the Ioniq's
   KWP2000 ECUs once on the bench.
3. **Stray messages on an ECU's response id** would pay a debt they do not belong to. Only
   that ECU's replies to canair's own requests are expected there. Confirm nothing else (a
   second tester, a functional broadcast reply) shares a response id during `monitor`.

## Out of scope

- `RawTerminal` has no ledger: it drains before each send and serves the first message after
  it. A late same-PID reply that lands after the send is served as the new answer there. The
  probe module could serve it too; separate item.
- The ELM327 path (`ResponsePipe`, `_resync`) and the WiCAN's own AutoPID are unaffected.
