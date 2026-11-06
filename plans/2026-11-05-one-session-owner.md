# Diagnostic sessions: one owner on every transport

Status: **PROPOSED** (2026-11-05; checked against the tree on 2026-11-06). No code changed. This
is the proposal that `plans/2026-11-03-transport-review-follow-ups.md` asked for under
"Deliberately out of scope" ("Unifying the two session rituals … propose it to the user before
starting"). The three decisions at the end are agreed.

Opening a diagnostic session (`10 xx`), waking a sleeping ECU and keeping the session alive with
TesterPresent (`3E 00`) are one concern. Today four pieces of code do it, with different timeouts,
retries and validation, and the monitor on the default transport keeps no session alive at all.
This plan makes `transport/session_manager.py::SessionManager` the only owner of that concern on
every transport, fixes its own defects first, and removes the copies.

Paths are relative to `src/canair/`. Point at code by file and symbol: line numbers drift.

## Today

| Path | Used by | Keepalive |
|---|---|---|
| `SessionManager` | `read` pipeline (`live/read/multi.py`), its REPL (`live/read/repl.py`), ELM327 `monitor` (`live/monitor/poll_elm.py`), `read` signal mode (`live/read/param.py`), `action` (`live/actuate/action.py`) | Background task over every tracked ECU, validated, inside a transaction |
| `Terminal.enter_extended_session`, ELM327 (`transport/elm327/session.py`) | `identity`, `routines`, `scan` range and discovery, `raw`, `io`, `dtc read`/`clear`, `memory` and `scan memory` (`live/memory/reads.py::maybe_session`), the IOControl and routines TUIs | One task per call, sent to whatever header is current when it fires |
| `Terminal.enter_extended_session`, raw (`RawTerminal.enter_extended_session`) | The same commands on `slcan-tcp` | Unvalidated, every error suppressed, sent to the ECU current at entry |
| Raw monitor (`live/monitor/sessions.py::MonitorSessions.open_raw` and `.switch`) | `monitor` on `slcan-tcp` | **None**. `10 xx` goes through `RawUdsClient.read` with errors suppressed, `--wake` is ignored, and the mode is kept in `MonitorSessions.raw_modes` |

`live/elm/skm_wakeup.py` (the SKM relay wake) opens its own session with unvalidated
`send_command` and then registers it by writing `sm._sessions[0x7A5]`, in
`live/read/exec.py::_exec_skm_wake` and in `live/read/repl.py`. `live/elm/tester.py` (the REPL's
`!tester`) is a manual tool and stays as it is. A bare `10 01` sent as a ping
(`live/discover/physical.py`, `live/dtc/sweep.py`) or as a probe (`live/scan/sessions.py`) opens
no session and is out of scope.

The three rituals disagree:

| | `SessionManager` | ELM327 copy | Raw copy |
|---|---|---|---|
| Wake | `10 01`, 15 s, SID-validated, 0.5 s pause; or the profile `wake:` ritual | `ATST10`, `10 01` up to twice (3 s, validated), `ATST` restored | `10 01`, 3 s, unvalidated, 0.3 s pause |
| Session request | 5 s, SID-validated, no retry | 5 s, SID-validated, one retry on a non-answer | 3 s, not validated, no retry |
| Keepalive target | each tracked ECU on its own header | the current header | the ECU current at entry |
| Keepalive rate | only when idle (`mark_active` counts real traffic) | every 2 s | every 2 s |
| A refusal is reported as | `[session] 0x…: NRC … -- continuing anyway` | `warning: …` (`render.messages`) | `warning: …` |
| Exit | `close_all` sends `10 01` | task cancelled, no `10 01` | task cancelled, no `10 01` |

Only a `session` step honours an ECU's `wake:` ritual (`live/read/exec.py::_exec_session`, used by
the pipeline and the ELM327 monitor). `canair identity SKM --wake`, `read` signal mode with
`--wake`, the raw monitor and every other `--wake` send a single `10 01` or nothing. The Ioniq
SKM's `wake:` notes say a single `10 01` "races the sleep timer". Six commands describe `--wake`
as `Wake ECUs from deep sleep (10 01)`, each with its own copy of the flag.

### Defects

1. **Open, wake and close are not atomic.** `SessionManager.open_session`, `rapid_read_wake` and
   `close_session` call `set_header` and then send, outside `terminal.transaction()`. A
   background keepalive that runs between the two re-points the ELM327 header, so `10 xx` goes
   to another ECU and `_session_modes` records it against the wrong one. Two callers run the
   background keepalive at that moment: `live/read/param.py` (it starts the background keepalive
   before it opens the sessions) and the monitor's live session switch
   (`MonitorSessions.switch`). The blocklist still judges the real target, so a refused mode
   stays refused.
2. **The raw monitor sends no keepalive.** A session ECU stays in its session only while one of
   its PIDs is polled within the ECU's session timeout (the S3 timer, nominally 5 s). The default
   `--interval` is 5 s, and the slow-PID backoff stretches a PID's period up to ×32. On the
   bundled Ioniq only the SKM (`session: true`) reads inside a session, so the exposure there is
   small; a profile with more session ECUs has more. Not observed on the car yet.
   `MonitorRawPoller.setup` says so ("There is no keepalive on the raw backend"), but
   `live/monitor/run.py::mode_monitor` says sessions are "kept alive with background
   keepalives", which is true only on ELM327.
3. **`RawTerminal.transaction()` is a no-op, but the raw terminal has header state.**
   `set_header` sets `_cur`, and `send_uds` reads `_cur` again after an `await`: for the retry
   (`retries=1` on every pipeline read), the frame-count ledger key, the transport tally and the
   protocol it parses with. The `--unsafe` confirmation prompt in `enforce_command_safety` also
   yields between the safety check (judged on `_cur`) and the send (sent to `_cur`).
   `docs/development/link-health.md` (item 3 under "Correctness-triggered reconnect") states the
   no-op as the design.
4. **A dead link kills the background keepalive silently.** `send_keepalive` re-raises
   `ConnectionError` so "the caller's reconnect path runs", but in the background task there is
   no caller: `_loop` catches only `CancelledError`, the task ends with an unretrieved exception
   and keepalives stop. The monitor notices later through `MonitorHealth.check_liveness`;
   `live/read/param.py` and the REPL never do.
5. **Smaller defects in `SessionManager`:**
   - `close_session` sends an unvalidated `send_command` `10 01`, against the rule that every
     request slot is echo-validated.
   - `_session_modes` records a mode the ECU refused with a negative response, so the monitor
     status bar shows a session that is not open.
   - `stop_background_keepalive` cancels without awaiting, so `close_all` can race the unwinding
     keepalive.
   - The verbose keepalive line is printed with `end=""`, so the next line runs on.
   - A refusal or a failed open is reported in a third format (see the table) instead of through
     `render.messages`, and the class decides what the user reads, which the live layer should.
   - `open_session`'s docstring says it returns True when a session was "at least attempted"; it
     returns the reply's `ok`.
   - The mode text is normalised (`upper().removeprefix("0X").zfill(2)`) in five places
     (`SessionManager.open_session`, both `enter_extended_session` copies, twice in
     `live/monitor/sessions.py`), and `_exec_session` prints it without `removeprefix`.
     `uds/requests.py::session_request` already takes either an `int` or the hex text.
6. **`rapid_read_wake` on ELM327 (inferred, not tested).** It lowers canair's own wait per prime
   to `interval_ms`, but not the adapter's `ATST` wait (about 614 ms). The ELM327 engine then
   waits for each prime's owed prompt, so the primes are probably not dense. The ELM327 `_wake`
   and `skm_wakeup` send `ATST10` for this reason.
7. **The multi REPL** (`live/read/repl.py`) stops and restarts the background keepalive around
   every command, which cancels keepalives in the middle of an exchange and predates
   transactions. It also saves the SIGINT handler in `_old_handler` and never restores it.
8. **Private names across packages.** `live/monitor/poll_elm.py` calls `multi._exec_session` and
   `multi._exec_skm_wake` from `live/read/`. `AGENTS.md` forbids a cross-package import of a
   `_name`; the attribute access keeps `tests/contracts/test_structure.py` from seeing it.

### Tests and typing today

- `ty check` is clean, and the full suite passes. `SessionManager` is typed against `Terminal`.
  Its callers are not: `sm` has no annotation in `live/read/exec_read.py`
  (`timed_exchange`, `_read_single`, `_read_batch`, `_single_result`), `ecu_index` is a bare
  `dict` in `live/read/exec.py`, and the session mode is a bare string.
- Line coverage on the full suite (2026-11-06): `transport/session_manager.py` 89 %,
  `transport/keepalive.py` 100 %, `transport/raw/terminal.py` 91 %,
  `live/monitor/sessions.py` 88 % (the ELM327 switch has no test), `live/memory/reads.py` 77 %
  (`maybe_session`'s exit has no test), `transport/elm327/session.py` 44 %
  (`enter_extended_session` and `_wake` have no test), `live/elm/skm_wakeup.py` 65 %,
  `live/read/repl.py` 9 %.
- `TestMarkActive.test_active_poll_suppresses_redundant_keepalive` in
  `tests/transport/test_session_manager.py` asserts that no `send_command` `3E00` was sent.
  Keepalives go through `send_uds`, so the test passes even when `mark_active` is broken.
- `tests/support/fakes.py::FakeTerminal.transaction` records boundaries but is not a lock, so no
  test can show an interleaving. No test runs the background keepalive against an open or a read.

## Goal

- Every live operation, on every transport, opens, wakes, keeps alive and closes sessions
  through `SessionManager`. `Terminal` loses `enter_extended_session`; session lifecycle is not
  a transport primitive (the docstring of `transport/elm327/session.py` already says so).
- The policy (which ECUs have a session, in which mode, which are stale, which refuse
  TesterPresent) is a pure, I/O-free state object that the raw monitor's poller can use from its
  own thread without the async driver.
- Every `--wake` honours the ECU's `wake:` ritual.
- Each defect above has a regression test that fails before its fix.

## Non-goals

- No change to what may be sent: the blocklist, `SessionPolicy` and the quirks stay the only
  judges of a session mode (`safety/blocklist.py::check_session_mode`).
- No change to the SKM relay sequence in `live/elm/skm_wakeup.py` beyond registering its session
  through a public method. It actuates relays, so it belongs to
  `plans/2026-10-22-actuation-safety-interlock.md`.
- No change to which commands return the ECU to its default session on exit (Decision 2).
- `live/elm/tester.py` stays a separate manual tool.

## Design

### The state: `SessionTable`

A small I/O-free class beside the driver (`transport/session_table.py`, or a `transport/session/`
package if the pair grows past one module each). It holds what `SessionManager` keeps today:

- per ECU: when the last traffic or keepalive went out, the open mode (only after a positive
  reply), and whether the ECU refused TesterPresent with NRC 0x11 (`keepalive.unsupported`);
- `opened(tx_id, mode, outcome)`, `mark_active(tx_id, now)`, `refused_keepalive(tx_id)`,
  `stale(now, threshold) -> list[int]`, `mode(tx_id)`, `active() -> list[int]`, `forget(tx_id)`;
- a clock passed in, so tests need no sleeps.

The mode is an `int`: a `SessionType`/`KwpSessionType` value from `uds/subfunctions.py`, or a
quirk-permitted OEM value. One parser in `uds/subfunctions.py` turns the user's text (`03`,
`0x3`, `81`) into it, and one formatter gives the two-digit form the TUI shows. The five
normalisation copies go, and `session_request` is called with the `int`.

### The driver: `SessionManager`

`SessionManager(terminal, table=None)` keeps its role and most of its API, with these changes:

- `open_session`, `rapid_read_wake`, `send_keepalive` and `close_session` each run as one
  `terminal.transaction()`. A wake burst holds the transaction for the whole burst: interleaving
  a keepalive into a burst is what the burst must avoid.
- `open_session` returns a `SessionOutcome` (ok, NRC code and description, error) instead of a
  bool and reports nothing. The live layer reports it once, through `render.messages`
  (`warning:` for a refusal or a failure, as both terminal copies do today). Verbose tracing
  stays on stderr.
- `close_session` sends `DEFAULT_SESSION_REQUEST` through `send_uds` with `expected_sid`.
- `track(tx_id, mode)` registers a session opened elsewhere (the SKM relay wake), replacing the
  two writes to `_sessions`.
- The background loop catches `ConnectionError`, stores it as `link_error`, logs it and ends.
  `stop_background_keepalive` becomes `async` and awaits the cancelled task. The ELM327 monitor
  poller reads `link_error` and calls `MonitorController.mark_disconnected` (the one entry for a
  link failure); other callers raise it at their next request.
- `held(tx_id, *, mode, wake, wake_plan, close=False)`: an async context manager for the
  single-ECU commands. It opens the session, runs the background keepalive and stops it on exit.
  It sends `10 01` on exit only with `close=True`, which keeps those commands' current exit
  behaviour. `live/memory/reads.py::maybe_session` already has this shape and becomes a call to
  it.

### The wake ritual on ELM327

A dense burst needs the adapter's own ECU wait shortened. `SessionManager` must not send AT
commands, so `Terminal` gains one method: `short_ecu_wait()`, an async context manager. The
ELM327 engine sends `ATST10` on entry and restores `elm_timeout_cmd` on exit; it already tracks
that value and the adapter's actual wait (`Elm327Terminal._observe_timeout`, `_reapply_init`), so
a reset inside the window is handled. The raw terminal yields and does nothing. `rapid_read_wake`
and the default `10 01` wake both run inside it. This replaces
`transport/elm327/session.py::_wake`, and `rapid_read_wake`'s claim that it "runs identically
over `slcan-tcp` and `wican-ws`" then holds.

### The raw terminal's header

`RawTerminal.send_uds`/`send_command` read `_cur` once, on entry, and use that value for the
safety check, the exchange, every retry, the ledger key, the tally and the protocol.
`RawTerminal.transaction()` holds a real lock, re-entrant per task as in `Elm327Terminal`, and
its docstring says what it protects. `set_header` outside a transaction keeps working.

### The raw monitor

The raw monitor has no `Terminal`: it drives `RawUdsClient` (`transport/raw/uds_client.py`),
whose `read()` blocks and whose `poll()` runs in an executor thread.

- `MonitorController` builds a `SessionTable` on both backends, and `MonitorSessions.raw_modes`
  goes. `MonitorSessions.active_modes` reads the table on both.
- Session setup, the wake and the live switch go through `SessionManager` over a thin adapter
  that satisfies the parts of `Terminal` it uses (`set_header`, `send_uds`, `send_command`,
  `transaction`, `short_ecu_wait`) by calling `RawUdsClient.read` off the event loop. It lives
  with the raw client in `transport/raw/`.
- `MonitorRawPoller` adds a `3E 00` to a poll round for each ECU the table reports stale, and
  marks an ECU active when it answers. The keepalive rides the existing pipelined round, so no
  second thread drives the stacks during a poll.
- This depends on `plans/2026-10-27-raw-owed-ledger-blackout.md` (PROPOSED): its §2 (one exchange
  per ECU at a time) must land first, because the live switch already calls `RawUdsClient.read`
  while `poll()` runs. That plan's TesterPresent probe and this keepalive send the same request
  (`uds/services.py::TESTER_PRESENT_REQUEST`) and read the refusal the same way
  (`keepalive.unsupported_bytes`).

## Phases

One commit per phase, in this order. Each phase passes the gate list in the **contributing-code**
skill ("Before you finish"). Phases 1 to 3 change no bytes on the wire.

### Phase 0: test harness and honest tests

- Give `FakeTerminal` an opt-in real transaction lock and a yield point between commands, so a
  test can run a background keepalive against a foreground exchange and assert which header each
  request went to.
- Fix `test_active_poll_suppresses_redundant_keepalive` to assert on `send_uds`, and confirm it
  fails with `mark_active` disabled.
- Pin today's behaviour of both `enter_extended_session` copies and of `maybe_session`
  (requests, order, timeouts, validation), so Phase 4's changes show as test edits.

### Phase 1: `SessionManager` correctness

Defects 1, 4 and the first four items of 5: transactions, validated close, mode recorded only on
a positive reply, `track`, `link_error`, awaited stop, the `end=""` line, the docstring.
`live/read/param.py` opens its sessions before it starts the background keepalive. Regression
tests for each, the interleaving ones on the Phase 0 harness.

### Phase 2: the raw terminal's header

Defect 3, as in Design, and item 3 of `docs/development/link-health.md`. Tests: a concurrent
`set_header` during a retried read leaves the retry, the ledger key and the tally on the original
ECU; an `--unsafe` prompt cannot change the target.

### Phase 3: `SessionTable`, the mode type and the names

- Split the state out of `SessionManager` with no behaviour change.
- Add the mode parser and formatter to `uds/subfunctions.py`; replace the five normalisation
  copies.
- `SessionOutcome` replaces the bool; the live layer reports it through `render.messages`.
- Defect 8: make `_exec_session` and `_exec_skm_wake` public names of `live/read/`, and update
  `live/monitor/poll_elm.py`.
- Type `sm: SessionManager` and the ECU index (`EcuIndexEntry`) in `live/read/exec_read.py` and
  `live/read/exec.py`.

### Phase 4: one ritual (changes the bus)

- Add `Terminal.short_ecu_wait()` to both terminals, the protocol and `FakeTerminal`.
- Move every `enter_extended_session` caller to `SessionManager.held`: `live/identity/read.py`,
  `live/scan/range.py`, `live/scan/discovery.py`, `live/read/raw.py`,
  `live/actuate/iocontrol_execute.py`, `live/actuate/routines_execute.py`, `live/dtc/read.py`,
  `live/dtc/clear.py` and `live/memory/reads.py`. The IOControl and routines TUIs
  (`live/actuate/iocontrol_actuate.py`, `live/actuate/routines.py`) replace their own
  `_tester_task`/`_session_active`/`_session_mode` with a `SessionManager` they own.
- Every `--wake` passes the ECU's `wake:` plan (`ecus/wake.py::resolve_wake`, already in the ECU
  index as `wake`). `live/read/param.py` passes it too.
- Declare `--wake` once in `cli/args/` with one help text ("the ECU's `wake:` ritual, else
  `10 01`"), used by `read`, `monitor`, `identity`, `raw`, `dtc` and `scan`; regenerate
  `docs/reference/cli/`.
- Remove `enter_extended_session` from `Terminal`, `Elm327Terminal`, `RawTerminal` and
  `FakeTerminal`; delete the ritual in `transport/elm327/session.py` (the module keeps
  `short_ecu_wait` or goes) and the raw copy.
- The behaviour changes, each named in the commit body:
  - raw: the session reply is SID-validated, a non-answer is retried once, the keepalive is
    validated and sent only when the ECU is idle;
  - ELM327: the single-ECU keepalive is pinned to the session's ECU rather than the current
    header, and sent only when idle;
  - both: one set of timeouts (Decision 1), and `--wake` uses the profile ritual where one is
    declared.

### Phase 5: the raw monitor (after the ledger plan's §2)

As in Design. Tests with the raw poller's fake client: an ECU whose PIDs are polled every 8 s
gets a `3E 00` between polls; one polled every second gets none; NRC 0x11 stops it for that ECU;
`session SKM --wake` runs the SKM's `wake:` ritual.

### Phase 6: the multi REPL

Remove the stop/start of the background keepalive around each command, and run a typed command
and its header restore as one transaction. Restore the SIGINT handler in the `finally`. Add tests
for the REPL's command dispatch (9 % today).

## Decisions (agreed 2026-11-06)

1. **One set of timeouts:** wake `10 01` 3 s (the 15 s in `SessionManager` has no recorded
   reason), session request 5 s, one retry on a non-answer, keepalive 1.5 s, background interval
   2 s with an idle threshold of 1.5 s, and no fixed pause after a wake inside
   `short_ecu_wait`.
2. **Return to the default session on exit: unchanged.** The pipeline and the monitor send
   `10 01` to every tracked ECU; single-ECU commands let the session time out. Making them all
   close would change what an interrupted `scan` leaves behind.
3. **Single-ECU keepalive only when idle**, as the pipeline does today. A `scan` sweep then sends
   almost no `3E 00`, because every probe resets the S3 timer.

## Docs and docstrings

`AGENTS.md` needs no change: its `canair read` entry ("TesterPresent keepalives are automatic")
still holds, and the design belongs next to the code.

- Docstrings: `transport/__init__.py` and `transport/elm327/__init__.py` (the charters; the
  latter lists `session` as a policy), `transport/elm327/session.py` (its "known duplication"
  note), `transport/protocol.py` (the surface), `live/monitor/sessions.py` (the raw backend "has
  no SessionManager"), `MonitorRawPoller.setup` and `live/monitor/run.py::mode_monitor`.
  Regenerate `docs/development/architecture.md` if a charter changes.
- `docs/development/link-health.md`: item 3 (Phase 2).
- `docs/concepts/query-mini-language.md` ("Sessions and keepalive"): `--wake` uses the ECU's
  `wake:` ritual where the profile declares one; a session is kept alive on every transport.
- The **ioniq-reverse-engineering** skill, where it describes `--session`/`--wake` ("2s
  TesterPresent keepalive", "`--wake` sends `10 01`").
- `docs/reference/cli/` regenerated after the `--wake` help change (Phase 4).

## Needs the car, not an agent

On the Ioniq, after Phase 4 and again after Phase 5, over `slcan-tcp` and `wican-ws`:

- `canair identity SKM --wake` from a sleeping SKM answers on the first run.
- `canair monitor "session SKM --wake" SKM:22B003 --interval 10` keeps answering for five
  minutes, and the verbose trace shows a keepalive between polls.
- The monitor's `t` switch moves the right ECU while other ECUs keep their sessions.
- A `scan` over a session ECU sends `3E 00` only during pauses (verbose trace).
