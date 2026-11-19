# `--hold-io ECU:DID`: hold an IOControl output on while a memory read runs

Status: **PROPOSED** (2026-11-12). No code changed.

Paths are relative to `src/canair/`. Point at code by file and symbol: line numbers drift.

## Problem

Some ECUs answer only while another ECU drives an output. On the Ioniq the BMS, VCU, OBC, MCU,
AAF and HVAC sit on the IG3 rail. With the car off, the IGPM's IOControl `BC44` ("Ignition 3
relay", `2FBC4403`) closes that rail and wakes them (`ecus/igpm.yaml`, `iocontrol: BC44`). A
memory read of one of them with the car off therefore needs `BC44` held on for the whole read.

Of those ECUs, the VCU and OBC answer `0x23` over their whole flash (`memory:` in `ecus/vcu.yaml`
and `ecus/obc.yaml`). The BMS does not: it answers NRC `0x11` (serviceNotSupported) at address 0
(`canair scan memory BMS --dry-run`, 2026-11-12, software `8D:4604`, KWP2000 default session).
The HVAC returns its transmit buffer, not memory, and the MCU's memory survey stopped an AC charge
(`ecus/mcu.yaml`). So the first users of the flag are the VCU and the OBC. Both were read before
only while the car was AC charging.

canair cannot do that today:

- **One process owns the device.** `canair io IGPM --did BC44` keeps the connection until Ctrl+C
  (`device/lock.py`). A `canair memory dump VCU …` in a second terminal is refused, and with
  `--force` it asks the `io` session to leave, which releases the output.
- **The output is on only while its session is.** `live/actuate/iocontrol_execute.py::
  mode_iocontrol_execute` keeps the IGPM session alive with TesterPresent and sends the release
  when it exits. ISO 14229-1 returns control to the ECU when the session ends. That has not been
  checked on the IGPM.
- **The memory commands hold one session, on the target.** `live/memory/reads.py::maybe_session`
  opens a `SessionManager` for the target ECU only.
- **The `ig3-read` action stops short.** `actions.yaml` holds `IGPM:03`, sends `2FBC4403` and
  releases, but `then:` waits at most 60 s (`actions.MAX_THEN_WAIT_S`) and reads PIDs only.

The workaround is to release and then read: the `BC44` notes record that the rail stays up for
about 75-110 s after the release. At the measured `wican-ws` rate of about 300 B/s
(`plans/2026-11-05-read-memory-by-address.md`), that is roughly 20-30 KiB per wake.

## Goal

1. `canair scan memory`, `canair memory dump` and `canair memory image` take
   `--hold-io ECU:DID`. canair switches the curated IOControl entry on before the first read,
   holds it for the whole run and releases it when the run ends, whatever ends it.
2. A run stops when the hold is lost. It never reads on as if the output were still driven, and
   it never switches the output on again by itself.
3. The dump records which outputs were held, when, and how each hold ended.

## Non-goals

- **Two processes on one device.** A connection broker is a separate design (discussed
  2026-11-12, not planned).
- **Other commands.** `read` has the `iocontrol` pipeline step, and `monitor` has the actuator
  pane (`plans/2026-11-07-monitor-actuator-pane.md`). The context manager below can serve
  `identity`, `dtc` or `scan range` later. This plan adds the flag to the memory commands only.
- **Routines and sessions.** `--hold-io` holds an IOControl output only. A routine needs a stop,
  and a bare session hold is the action's `hold:`. Neither is in scope.
- **Raw requests.** The flag takes a curated `iocontrol:` entry, never a hex request.
- **Re-actuation.** After a lost hold or a dropped link canair does not switch the output on
  again. That would actuate with nobody watching.

## Decisions

Settled with the user (2026-11-12):

1. **The flag is `--hold-io ECU:DID`.** canair already has three `--hold` flags with other
   meanings: `raw --hold` (keep the session alive), `action add --hold ECU[:MODE]` (a session,
   where `IGPM:03` is a session mode) and `pids set-iocontrol --hold` (the entry's `hold:`
   field). `monitor --actuate ECU` means "enable the actuator pane". A bare `--hold IGPM:03`
   would be ambiguous between a session and a DID. The `-io` suffix names the service, so the
   flag is IOControl by definition.

Proposed by this plan, open to review:

2. **The DID is looked up, never parsed as a request.** `ECU:DID` resolves through
   `ecus/pids.py::build_iocontrol_index`. The entry supplies the ON request, the release, the
   session and its mode, the protocol and the readback signal (`status_param`). This is the same
   lookup as `canair io ECU --did DID`, so the same entry means the same bytes everywhere.
3. **Refusals, before anything is sent (exit code 2):**
   - the ECU has no `iocontrol:` entries, or the DID is not one of them;
   - the entry is a discovery (`iocontrol_discoveries:`), not a curated entry;
   - the entry says `hold: false`;
   - `safety/actuation.py::on_refusal` refuses the ON (a KWP2000 `0x30` ON with no declared
     `release:`);
   - the entry is not `verified: true`. A hold runs unattended for minutes, so it takes only an
     entry that someone has seen work on the car;
   - the held ECU is the memory target. Its IOControl session and the memory session would fight
     over one ECU's session mode.
4. **One confirmation names both.** The command asks once, before it connects, as it does today
   (`cli/commands/memory/reads.py::confirm_reads`). The question names the output ("hold IGPM
   BC44 (Ignition 3 relay) on") and the reads, with `danger=True`. `--yes` answers it. With no
   terminal and no `--yes` the command exits with code 2, as today.
5. **`--hold-io` is repeatable.** Outputs are switched on in the order given and released in
   the reverse order. One flag is the expected use.
6. **A lost hold stops the run.** `memory image` saves the job for `--resume` and does not pause.
   A pause waits for a condition that only the user can restore. canair never acts on the car to
   restore a state (`live/memory/liveness.py`).
7. **No reconnect while a hold is on.** A dropped link ends the IGPM session, so the output is
   no longer driven. `memory image` already turns its reconnect off when it holds a session
   (`cli/live/handlers/memory_image.py`: `reconnect … if options.session_mode is None`). A hold
   turns it off the same way.
8. **A time cap.** A hold keeps ECUs awake with the high-voltage main relay open (the `BC44`
   notes: `BMS_MAIN_RELAY` stayed 0), so the 12 V battery supplies them. `--hold-max-minutes N`
   (default 30) ends the run after N minutes. An image job is saved for `--resume`. See open
   question 2.

## Design

### The run

The handler wraps the existing memory mode. The memory modes do not learn about IOControl.

1. **Before connecting:** resolve every `--hold-io` (decision 2), apply the refusals
   (decision 3), and ask (decision 4).
2. **Readback before:** read the entry's `status_param` with `live/status.py::
   query_param_status`, as `mode_iocontrol_execute` does.
3. **Switch on:** open the entry's session, start its keepalive and send the ON through an
   `IoBank` (`live/actuate/iocontrol_actuate.py`). An ON without a positive reply ends the
   command with exit code 1 before any memory request. The bank still sends the release,
   because a lost reply does not prove the ECU ignored the request.
4. **Settle:** wait until the memory target answers a request, polling every 0.5 s, for at most
   `HOLD_SETTLE_S` (10 s). The `BC44` notes record that the IG3 ECUs answered 3 s after the ON.
   A target that does not answer in time ends the run with exit code 1, and the output is
   released.
5. **Read:** run the memory mode. A guard (below) can stop it between two blocks.
6. **Release, always:** in a `finally`, the bank's `release_all` sends each entry's release
   (`2F{DID}00` on UDS, the declared `release:` on KWP2000), on success, on the skip key, on
   Ctrl-C, on an exception and on a lost hold. Then canair reads the readback signal once more and
   prints it.

### One session owner

`plans/2026-11-05-one-session-owner.md` made `SessionManager` the only owner of sessions and
keepalives. The run keeps that rule with **one** `SessionManager` for both ECUs:

- The handler creates the manager and starts its background keepalive. The bank opens the
  IOControl session through `live/actuate/ports.py::SharedSessions`, as the `read` pipeline does.
- `maybe_session` takes that manager instead of creating one. When it is given a manager, it
  opens the target's session (if `--session` asks for one) and forgets it on exit. It does not
  start or stop the keepalive, because the hold still needs it. Today `SessionManager.held`
  stops the background keepalive in its `finally`, which would leave the IGPM session without
  TesterPresent until the release.
- Every request already runs inside one `terminal.transaction()` (`reads.py::read_memory`,
  `SessionManager.send_keepalive`, `IoBank`). A keepalive to the IGPM therefore cannot re-point
  the header between a memory target's `set_header` and its `send_uds`. That holds on every
  transport.

An ECU that answers TesterPresent with NRC `0x11` (`transport/keepalive.py`) is never sent a
second one. A target with no session of its own (a memory read with no `--session`) leaves the
manager keeping the IGPM alive only.

### Checking the hold

A positive TesterPresent reply proves nothing: an ECU answers `3E 00` in the default session as
well. The check reads the entry's readback signal instead.

- **Every `HOLD_CHECK_S` (10 s)** a task on the same manager reads `status_param` (for `BC44`,
  `IG3_RELAY_OUTPUT`, IGPM `22BC07` `B12:3`) in a transaction. It reports **lost** when the
  signal decodes to 0, or when two reads in a row fail.
- **The keepalive's `link_error`** (`SessionManager.link_error`) also reports lost.
- **The guard.** The memory modes take an optional `guard: Callable[[], str | None]`, checked
  where they check `skip` today. It returns the reason to stop, or None. `mode_memory_dump`
  and `mode_memory_image` already take `skip`, and `mode_memory_scan` gets the same check.
  The run ends with that reason: for example, "the IGPM BC44 hold was lost (IG3_RELAY_OUTPUT
  read 0)".
- **No readback signal.** An entry without `status_param` cannot be checked. `memory dump` and
  `scan memory` warn and run with the keepalive check only. `memory image` refuses it (exit
  code 2), because it runs unattended for a long time.

The `canair io` code says that a readback is best effort: an override may drive an output
without changing the status bit. For `BC44` the notes record that `IG3_RELAY_OUTPUT` read 1 three
seconds after the ON, so the check works for this entry. Other entries need the same evidence
before anyone relies on the check.

### `memory image`

- A lost hold, the time cap or a dropped link stops the run and saves the job (decisions 6-8).
- The job's plan records the `--hold-io` specs.
  `cli/commands/memory/image_report.py::resume_command` prints them in the command it suggests.
  A `--resume` without them is allowed, with a warning, because the user may resume in READY,
  where IG3 is on anyway. A `--state` check covers that case.
- A pause for another reason (a silent target, a failed health check) holds the output for at
  most `--wait-minutes`, within the time cap.

### Recording

- The dump manifest (`memory/store.py::Dump`) gets a `held` list, a sibling of `pauses`. Each
  item has the ECU, the DID, the label, the ON and release requests, when each one went out, both
  replies, and how the hold ended (`released`, `lost: <reason>`, `link dropped`, `time cap`). A
  dump written before this change has no `held` key, and readers treat that as an empty list.
- `memory show` prints the held outputs. `--json` output includes them.
- The actuations go to the central log (`core/eventlog.py::log_event`), as other bus events do.
- A memory run saves no captures, so the session-level `actuations` list of the monitor
  (`captures/actuations.py`) does not apply.

### Output

The hold prints on stderr in the house style (the **cli-tui-standards** skill): the output and
its readback before the ON, the ON reply, a note when a hold check fails, and the release with
its reply and readback. With `--json`, stdout holds only the document, as today.

### Interaction with `safety.allow_actuation`

`plans/2026-11-07-allow-actuation-gate.md` (PROPOSED) gates every actuating `0x2F`/`0x30` request
in the shared send path. The ON of a hold goes through that path, so the gate covers it with no
special case, and the release always passes. If that plan lands first, the refusal from the
gate comes before the confirmation (its own rule).

## Where the code goes

| What | Where |
|---|---|
| Parse `ECU:DID`, resolve it against the IOControl index, the refusals of decision 3 (no I/O) | new `ecus/held_output.py`, beside `build_iocontrol_index` |
| The async context manager: readback, ON, settle, the check task, the guard, `release_all` | new `live/actuate/held_output.py`, over `IoBank` and `SharedSessions` |
| `maybe_session` taking a shared manager; `guard` on the three memory modes | `live/memory/reads.py`, `dump.py`, `image.py`, `scan.py` |
| The `held` list in the manifest | `memory/store.py` |
| `--hold-io`, `--hold-max-minutes`, the confirmation text | `cli/commands/memory/reads.py::add_read_args`, `confirm_reads` (shared by all three commands) |
| Composing the hold with the mode, turning reconnect off | `cli/live/handlers/memory.py`, `memory_image.py` |

`live/memory/` does not import `live/actuate/`. The handler composes them. Check that the new
`ecus/` module fits the `ecus` charter. If it does not, the pure part goes into
`live/actuate/held_output.py` too.

## Phases

One commit per phase. Each passes the gate list in the **contributing-code** skill.

1. **Resolution and refusals.** `ecus/held_output.py` and its tests. No flag yet.
2. **One manager for two ECUs.** `maybe_session` takes a shared manager. Tests on
   `tests/support/fakes.py::FakeTerminal` with `serialize=True` show that the keepalive keeps
   going after the memory session ends.
3. **The held-output context manager.** ON, settle, check task, release on every exit path.
4. **The guard.** `guard` on `mode_memory_dump`, `mode_memory_scan` and `mode_memory_image`.
   The image saves its job on a guard stop.
5. **The flags and the handlers.** `--hold-io`, `--hold-max-minutes`, the confirmation, reconnect
   off, the manifest's `held` list, `memory show`, the resume command. Regenerate
   `docs/reference/cli/`.
6. **Docs.** See below.

## Tests

- **Resolution:** every refusal of decision 3, an unknown ECU or DID (with the list of choices,
  as `live/actuate/not_found.py` prints it), a lowercase DID, repeated flags.
- **Order on the bus** (fake terminal): readback, session, ON, settle polls, the reads with
  keepalives and hold checks between them, release, readback. Each `set_header` and its request
  sit in one transaction.
- **Release on every exit:** a finished run, the skip key, `KeyboardInterrupt`, a
  `CancelledError`, an exception from the mode, a target that never settles, an ON with an NRC,
  an ON with no reply.
- **Lost hold:** the readback decodes 0, two failed readbacks, a `link_error`. The dump ends with
  the reason and the rest is `not_read`. The image saves the job.
- **Time cap:** a fake clock past the cap ends the run.
- **No reconnect** while a hold is on (`memory image`).
- **Every transport:** the handler runs over the fake terminal through `run_session_guarded`
  (the `TestDispatchTransportAgnostic` pattern), and the command's row in
  `tests/cli/commands/test_live_operations.py` pins the flags it reads.
- **Manifest:** a dump with `held` round-trips, and a dump without the key still reads.

## Docs

- `docs/concepts/memory-reads.md`: a section on holding an output during a read. Name the
  Ioniq's `BC44` as the example and state the 12 V cost.
- `docs/reference/cli/`: regenerated.
- The **ioniq-reverse-engineering** skill: a VCU or OBC memory read with the car off uses
  `--hold-io IGPM:BC44`, and the BMS answers no memory reads.
- README: no change (no new command).

## Open questions

1. **Does the IGPM return control when its session ends?** The design assumes that a dropped
   link releases `BC44` within the IGPM's session timeout. A car test can show it (see below).
   If the IGPM keeps the output driven, a dropped link leaves IG3 closed until the IGPM sleeps,
   and the docs must say so.
2. **The time cap.** Is 30 minutes right, and should the cap be a 12 V floor instead? The BMS
   reports `AUX_BATTERY_VOLTAGE` in `2101`, and the VCU reports `VCU_AUX_BATTERY_VOLTAGE_FINE`.
   A floor needs a signal per profile, so it would be a profile setting.
3. **`verified: true` only?** Decision 3 refuses an unverified entry. `canair io --did` does not.
4. **The settle time.** Is a fixed 10 s enough, or does it need a flag?
5. **Same-ECU holds.** Decision 3 refuses a hold on the memory target. An ECU whose IOControl
   and memory reads work in the same session mode could allow it later.

## Needs the car, not an agent

Each step needs the user's approval in the conversation, command by command.

1. **Which ECU is the first test target?** The BMS is out (NRC `0x11`, see Problem). The VCU's
   `memory:` section is recorded (`format: kwp`, `max_block: 64`, readable `0x000000-0x3FFFFF`),
   so no scan is needed. Read a small range, for example the first 4 KiB, with the car awake
   first, to have a baseline to compare with the held read.
2. **Open question 1.** Hold `BC44` with `canair io IGPM --did BC44`, then end the process
   without its release (close the device connection) and read `IGPM:22BC07` every 2 s until
   `IG3_RELAY_OUTPUT` reads 0.
3. **The feature.** With the car off, ignition off and the charging cable unplugged (the same
   guard as the `ig3-read` action): `canair memory dump VCU 0-FFF --hold-io IGPM:BC44 --label …`,
   then `canair memory diff` against the baseline of step 1 and `canair dtc read` on the IGPM, VCU,
   BMS and OBC.
