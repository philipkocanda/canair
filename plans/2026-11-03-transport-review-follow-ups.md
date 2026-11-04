# Transport and protocol review: follow-ups

Status: **DONE** (items 1–6 landed 2026-11-03 as `491fb01`..`09dca1c`; on-car results below).
Hand-off for the leftovers of the transport and protocol review
whose fixes landed as `987c79b`..`85855b9`. Every item below is independent unless it says
otherwise; do them in the order given, one commit per item.

## Before you start

- Read `AGENTS.md` and load the `contributing-code` skill. Load `wican-hardware-and-protocol`
  before touching anything under `canlib/transport/`.
- Every item is a code-quality or robustness change on paths that already work on the car. None
  may change what goes on the bus unless the item says so; if one turns out to, stop and ask.
- Other sessions edit this working tree at the same time. Commit only the paths you changed
  (`git commit -o -- <paths>`), never `git add -A`/`commit -a`/`stash`, and check `git diff` of
  a file for hunks that are not yours before committing it.
- Each change ships with a regression or pinning test (device-free, fake terminals as in
  `tests/test_terminal.py` and `tests/_fakes.py`). Before finishing: `uv run pytest -q`,
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, and
  `uv run python scripts/gen_cli_reference.py --check` if `--help` changed.
- Point at code by file and symbol; the line numbers below were right when this was written and
  will drift.

## 1. Back off per PID, not per ECU

**Problem.** `canlib/poll_backoff.py::EcuBackoff` slows a whole ECU from its per-tick
`PollCost` (`MonitorController.ecu_costs`). On the dynamic schedule one PID that always times out
makes ticks with `reads=1, answered=0`, so the ECU's healthy PIDs are slowed with it. The
timeout is real cost (it holds up every other ECU on the serial pipe), so the dead PID itself
should still be slowed; its neighbours should not.

**Shape.**
- Track health per `(ecu, pid)` request alongside, or instead of, per ECU, and feed the
  slowdown into `canlib/poll_schedule.py::PollScheduler` per PID. The scheduler already plans per
  PID, but its `_slowdown` (set through `set_slowdown`) is keyed by ECU; keep it a
  dependency-free leaf (`AGENTS.md`, `canair monitor`).
- The raw path already has a cost per request (`canlib/modes/monitor_raw.py::ecu_costs`, from
  each `Submission`). The ELM327 path does not: an error result carries no `elapsed_ms`. Start by
  giving the ELM327 executor's per-request outcome a duration (`canlib/modes/multi_exec.py`
  `_read_single`/`_read_batch`, counted by `_request_outcomes` in `canlib/modes/monitor.py`).
  Keepalive time must stay excluded (`SessionManager.keepalive_seconds`), and a multi-DID batch
  stays one request.
- Keep the two invariants of the reconnect fix: `MonitorController.reset_backoff` clears the
  backoff *and* the scheduler's slowdown together, and a factor the backoff no longer tracks must
  still be restorable (`PollScheduler.slowed()`).
- The monitor header shows the slowdown per ECU (`_monitor_render.py`); decide how a per-PID
  slowdown is shown (the PID row is the natural place) and update the docs that describe the
  backoff (`docs/getting-started/first-read.md`, the `canair monitor` entry in `AGENTS.md`).

**Done when** a test with one always-timing-out PID and two healthy PIDs on one ECU shows only
the dead PID slowed, on both the raw and the ELM327 path.

## 2. One ECU-name lookup on every executor path

**Problem.** `canlib/pids.py::ecu_index_key` resolves a step's ECU name to its index key
(upper-cased name, else the canonical name of an alias). The raw poller uses it; these still
inline their own copy or skip the alias step:
- `canlib/modes/multi_exec.py`: the wake-plan lookup near the top (`ecu_index.get(target.upper())`
  then `canonical_ecu_name_safe`) and `_exec_query` (`upper = ecu_name_str.upper()` …).
- `canlib/modes/monitor.py`: `MonitorController.setup`'s warm-up loops and the loop near the end
  of the class all use `step["ecu"].upper()` with no alias resolution.

**Shape.** Replace each with `ecu_index_key`. Steps are normally canonicalised already by
`normalize_query_steps`, so this is consistency, not a visible fix; the test is that an alias in
a hand-built step resolves the same everywhere (there is one for the raw poller in
`tests/test_raw_monitor.py` to copy).

## 3. Carry the NRC code on a monitor result

**Problem.** A `ResultEntry` has no `nrc` field, so `canlib/modes/monitor.py::_is_nrc` decides
"answered with a negative response" by matching the error text against `"NRC"`. The code is known
where the entry is built (`canlib/modes/multi_batch.py::_error_result`, and the raw path's
`_raw_pid_result` in `canlib/modes/monitor_raw.py`).

**Shape.** Add an optional `nrc: int | None` to the entry, set it at both construction sites, and
make `_is_nrc` read it. While there, split `EcuBackoff.reason` (free text such as
`"0.8s per read"`) into a `Literal["not answering", "slow"]` kind plus the measured value, so the
log and header wording live in one place. Item 1 touches the same code; if you do item 1 first,
fold this in.

## 4. One home for the ResponsePending check and the ELM327 command helpers

**Problem.** The same facts are spelled several times:
- ResponsePending (`7F xx 78`): `canlib/uds/nrc.py` now has `NRC_RESPONSE_PENDING` and
  `is_response_pending(reply, sid)`. Copies remain in `canlib/captures/from_log.py`
  (`_RESPONSE_PENDING`), `canlib/transport/functional.py::is_pending`,
  `canlib/transport/uds_raw.py::is_response_pending` and, as text,
  `canlib/transport/elm327_pipe.py::PENDING_RE`.
- ELM327 command canonicalising and reset commands: `canlib/transport/elm327_eeprom.py` exports
  `canonical()` and `RESET_COMMANDS`; `canlib/transport/elm327_addressing.py` keeps its own
  `_RESET_COMMANDS`, and `elm327_terminal.py` canonicalises inline (`cmd.replace(" ", "").upper()`)
  next to `_BANNER_COMMANDS`.
- `canlib/uds/parse.py::_compact` duplicates `canlib/transport/elm327_pipe.py::compact`.

**Shape.** Make the byte-level checks call `canlib/uds/nrc.py`; keep `PENDING_RE` (it matches
adapter text) but build it from the constant. Move `canonical` and `RESET_COMMANDS` to whichever
ELM327 module the others already import, or a small leaf of their own, and use them everywhere.
Mind the layering: `canlib/uds/` is the I/O-free protocol layer and must not import from
`canlib/transport/`, so `compact` belongs in `canlib/uds/` (or a neutral leaf) with the pipe
importing it, not the other way round. No behaviour change; the existing tests are the guard.

## 5. Keep every ECU's session alive in `canair read` of several ECUs' signals

**Problem.** `canlib/modes/param.py` opens one session and one TesterPresent task per ECU
(`terminal.enter_extended_session`), but the ELM327 keepalive loop
(`canlib/transport/elm327_session.py::_tester_present_loop`) sends `3E00` to whichever ECU the
adapter is addressed at when it fires. With two ECUs, the first one's session lapses while the
second is read.

**Shape.** Use `canlib/session_manager.py::SessionManager`, which keeps a session per ECU and
refreshes each one on its own header (`keepalive_stale`), as `multi_exec` does. Check the raw
terminal's behaviour too (`RawTerminal.enter_extended_session`), since `param.py` runs on both.
Test with a fake terminal that records the header each `3E00` went to.

## 6. Small cleanups

- `canlib/modes/dispatch/scans.py`: the functional discover call passes
  `getattr(args, "window", None) or DEFAULT_WINDOW_S`. `functional_args_error` in
  `canlib/commands/discover.py` already rejects `--window <= 0`, so the `or` fallback is dead and
  would hide a zero if the check ever moved. Pass the parsed value (`None` → default) explicitly.
  The `request` and `repeat` lookups beside it use the same `getattr` pattern; read them off the
  parser's defaults instead.
- `canlib/transport/elm327_frame_count.py`: while a count is being diagnosed (`_diagnosing`), the
  plain retry's `ok` reply is not passed to the ledger's `observe()`, so that evidence is lost.
  Feed it in, keeping the rules in `AGENTS.md` ("An ELM327 request's odd-length final nibble"):
  learn only from a plain request whose reply is `ok`, and never let a retry that disagrees
  rehabilitate a retired key.
- `plans/2026-10-30-functional-addressing-followups.md` still says `resolve_tx_padding` "stays",
  for the transmit side. It was removed (`b4ffa5e`); the transmit side reads
  `_params["tx_padding"]`. Correct the two mentions.
- `docs/getting-started/first-read.md`, where it describes the slow-ECU backoff: add that a
  reconnect restores every ECU's normal rate.
- `AGENTS.md` has four lines over 100 columns (the `--find-mirrors` bullet, the `send_to`
  sentence in the `canair wican` entry, the explorer export entry, the `devices:` migration
  sentence). Re-wrap them.

## Deliberately out of scope

- **Unifying the two session rituals.** `RawTerminal.enter_extended_session` is a second,
  divergent copy of `canlib/transport/elm327_session.py` (no retry, unvalidated keepalive).
  Unifying them changes raw-path behaviour; propose it to the user before starting.
- **CAN-FD in the passive ISO-TP reader.** `canlib/uds/isotp_passive.py` still counts a CAN-FD
  single-frame length escape as malformed. Nothing imports CAN-FD logs yet.
- **ELM327 flow control during a functional sweep.** In flow-control mode 0 the adapter may answer
  any First Frame seen during the window; changing that needs STN-specific commands.
- **`monitor_raw._protocol` through `ecu_protocol`.** Rejected: it already classifies once
  through `classify_protocol`, and `ecu_protocol` needs the active profile's registry rather than
  the controller's own `pids_data`.

## Needs the car, not an agent

These shipped with scripted-channel tests only. Verify them on the Ioniq over `wican-ws` and
record the outcome here:
- `discover --functional` on ELM327 now lists the 0x7xx ECUs outside 0x7E8–0x7EF (the
  `ATCF`/`ATCM` filter in `canlib/transport/elm327_functional.py`), and a following physical read
  still gets its ECU's `ATCRA` filter back.
- An adapter that resets itself mid-session (cranking dip) gets the init again
  (`Elm327Terminal._reapply_init`) and the session continues.
- `canair identity` on a profile without `hk_f1xx_minus_one` reports the one-lower answers with
  the quirk hint; each costs one resync on ELM327, which is expected.

### Outcome (2026-11-03, Ioniq in READY, WiCAN Pro v4.50p over `wican-ws`, canair 4.3.0)

- **Functional discovery: passed.** Two runs listed 23 and 24 responders, most outside
  0x7E8–0x7EF (0x778, 0x7A8–0x7AD, 0x7BB, 0x73B, …). The verbose log shows `ATCF700`/`ATCM700`,
  `ATH1`, the request, then `ATH0` and `ATCRA` in one transaction. The `--identify` reads after
  it reached all 23 ECUs on their own reply ids. Every Ioniq ECU is conventional (`tx + 0x08`),
  so the per-ECU `ATCRA<rx>` path for a non-conventional ECU is not exercised on this car. AMP
  (0x78B) answered in one run and not in the other.
- **Adapter reset mid-session: not verified.** No safe way to make the MIC3624 reset on its own
  was found; an EV has no cranking dip.
- **Identity without the quirk: works as specified, but the hint is a false positive here.**
  DDM gave three one-lower answers (F101→F100, F194→F193, F196→F195) with the quirk hint, one
  resync each. Each one is byte-identical to the reply to the request before it: DDM, ADM (and
  PSM, which answers a bare `62`) repeat their last reply for a DID they do not support, so
  every unsupported DID costs a resync. IGPM and BCM gave no one-lower answer at all. With the
  quirk on, the terminal accepts those repeats, so the DDM and ADM identity shows invented F101,
  F194 and F196 fields.

- **Functional reach, follow-up run** (3 rounds, 1 s window): the same 24 ECUs answered every
  round, AMP included, so the 300 ms default window can miss AMP. CLU answers a physical `3E00`
  but never the functional one, so either the CLU or the gateway ignores functional requests for
  it. RCAM, AVN and AAF did not answer a physical `3E00` either (AAF: HVAC was off).

Found during these runs:

- Fixed in `babc5e3a`: after a resync, `Elm327Terminal._cur_header` was `None`, so every
  request after one was keyed on `(None, request)`, which all ECUs share. ADM and PSM were sent
  `22F1872`, a count learned on DDM.
- Fixed in `e9b594a3`: `hk_f1xx_minus_one` is retired. The "one lower" replies were DDM and ADM
  repeating their previous reply for an unsupported DID.
- Fixed: KWP2000 record `1A80` on the VCU carries the VIN in clear text. It is now a
  `sensitive:` PID like `1A90` (checked offline; the car was asleep, so not re-read).
