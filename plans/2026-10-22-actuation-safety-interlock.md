# Actuation safety interlock: no actuator command unless the car is verifiably parked

Status: **PROPOSED** (2026-10-22). The §1 classifier exists as `canlib/actuation.py::classify`
(2026-10-30), with the KWP2000 `0x30` table below; `canair io` uses it to refuse an ON request
with no known release. The funnel, the interlock model and everything after are not implemented.

canair can drive physical outputs: input/output control (IOControl, UDS `0x2F` / KWP2000 `0x30`),
routine control (UDS `0x31`, KWP2000 `0x31`/`0x32`), and the Ioniq smart key module (SKM) relay
wake. Nothing checks what the car is doing before it sends one. Toggling a lamp, a lock or a
solenoid on a moving car is a safety hazard, and so is an ECU reset or a communication-control
request to a chassis ECU.

This plan adds an **interlock**: canair refuses every state-changing request unless it has, *just
now*, read the car's gear and speed live from at least two ECUs and found it parked and stationary.
The interlock is **fail-closed**, **hard to bypass** (for a person *and* for an agent), and
**loud**: every step of the way the user sees what is being checked, what was found and why the
request is or is not allowed.

## Today

- **The command blocklist** (`canlib/safety.py`) is the one transport-independent guard.
  `enforce_command_safety` runs in every terminal's outbound path
  (`canlib/transport/elm327_terminal.py` `send_command`, `canlib/transport/raw_terminal.py`
  `send_uds`/`send_command`), so every transport gets it. It knows nothing about vehicle state, and
  `--unsafe` turns a refusal into a typed `YES` prompt.
- **The service registry** (`canlib/uds_services.py`) already tags services with `actuates=True`
  (`0x04`, `0x11`, `0x2E`, `0x2F`, `0x30`, `0x31`, `0x32`, `0x34`–`0x38`, `0x3B`). Nothing
  enforces that tag.
- **Actuation paths**, each sending directly with no state check:
  - `canair io ECU --did DID` → `canlib/modes/iocontrol.py::mode_iocontrol_execute`, which can hold
    until Ctrl+C.
  - The `canair io` text user interface (TUI) → `canlib/modes/_iocontrol_actuate.py::
    IOControlActuator` (`send_on`, `send_off`, `send_adjust`).
  - `canair routines --sf start` → `canlib/modes/dispatch/actuators.py::handle_routines` (a `[y/N]`
    prompt), then `canlib/modes/routines.py::mode_routines_execute`. The routines TUI's `!` key
    asks for a typed `yes`.
  - The `io` step of the `read`/`monitor` query language → `canlib/modes/multi_exec.py`.
  - `skm-wake` → `canlib/modes/skm_wakeup.py` (`2F{DID}03{magic}` over `send_command`). It prints
    "Only proceed if the car is in Park" and asks for `YES`, but checks nothing.
  - `canair raw` and `canair repl` accept any hex.
  - `canair wican autopid write/upload`: the device then polls each request **by itself**, with no
    canair process involved. An actuating request in an AutoPID profile would fire on every cycle.
- **The vehicle-state vocabulary** (`vehicle_states.yaml`) already says `PARKED` when
  `VCU.GEAR_P == 1 or ESC.GEAR_SELECTOR == 1`. It is the wrong tool for safety. Its predicates are
  tuned for *suggesting a capture label*, so they use `or` across sources and are edited for
  analysis reasons. An `or` passes when one source says Park and the other says Drive.

## Goals

1. No state-changing request reaches the bus unless a **fresh, passing interlock verdict** exists
   for the live connection.
2. The verdict comes only from **live reads made by the interlock itself**: never from captures,
   imports, a caller-supplied value or a cached decode.
3. **Fail closed.** An unpolled, silent, malformed or contradictory signal means *not safe*.
4. **Releasing is never blocked.** Switching an output off, returning control to the ECU and
   stopping a routine always go through, interlock or not. They are the safe direction.
5. **No easy bypass.** No flag, environment variable, config key or `--yes` skips the interlock. A
   car that reads as *unsafe* cannot be overridden at all.
6. **Notices at every step.** Help text, start-up banner, each pre-flight check, each actuation,
   each hold, each refusal and each edit to the interlock definition say what is going on.

### Non-goals

- Replacing the ECU's own preconditions. Many ECUs already refuse with negative response code (NRC)
  `0x22` conditionsNotCorrect. The interlock is defence in depth on top of that.
- Protecting against someone who edits canair's Python source. Tests pin the policy (see Tests), but
  a modified checkout is out of scope.
- Gating ordinary reads or diagnostic-session entry. `monitor @driving` while driving stays
  supported.

## Design

### 1. What counts as state-changing: an allowlist, not a blocklist

A new pure classifier, `canlib/actuation.py::classify(request, protocol) -> RequestClass`, returns
one of `read`, `release` or `actuate`. It is built on the `canlib/uds_services.py` registry, so
service facts stay in one place, plus a small table of sub-function exceptions:

| request | class |
|---|---|
| UDS `2F {DID} 00` returnControlToECU | `release` |
| UDS `2F {DID} 01/02/03` (resetToDefault, freezeCurrentState, shortTermAdjustment) | `actuate` |
| UDS `31 01` startRoutine | `actuate` |
| UDS `31 02` stopRoutine | `release` |
| UDS `31 03` requestRoutineResults | `read` |
| KWP2000 `30 {LID} …` declared as an IOControl entry's `release:` | `release` |
| KWP2000 `30 {LID} …` otherwise, including `30 {LID} 00` | `actuate` |
| KWP2000 `31 {LID}` startRoutineByLocalIdentifier | `actuate` |
| KWP2000 `32 {LID}` stop / `33 {LID}` results | `release` / `read` |
| `11` ECUReset, `28` CommunicationControl, `04`/`14` clear diagnostic trouble codes (DTCs) | `actuate` |
| `10`, `18`, `19`, `1A`, `21`, `22`, `23`, `27`, `3E`, OBD-II read modes, `AT…` | `read` |
| **any service not listed** | `actuate` |

- **Unknown services are `actuate`.** An allowlist means a manufacturer service nobody has
  classified fails closed instead of slipping through.
- **The protocol comes from the target ECU** (`id_protocol`), resolved from the terminal's current
  header through the address book. `0x31` means start on KWP2000 but stop/results on UDS
  (sub-functions 02/03), so when the protocol cannot be resolved the stricter reading wins: `31 02`
  to an unknown ECU is `actuate`.
- KWP2000 `0x30` has no standard release: ISO 14230-3 leaves the control-option byte to the
  manufacturer, Nissan's `00` is a one-shot command, and the OVMS Kia Soul EV firmware sends
  `30 {led} 00` to switch a charger LED *on*. So no option byte is a release by itself; only a
  request the profile declares as an entry's `release:` is.
- The already-blocked flash/write services stay blocked by `check_command_safety` as before. The
  interlock is an *additional* gate, so `--unsafe` unblocking `0x2E` still also needs a passing
  interlock.

### 2. The interlock definition: `interlock.yaml`

A new optional bundle member, registered once in `canlib/profile.py::BUNDLE_MEMBERS` (role
`definition`, so `contribute` ships it), with a tool-owned schema in `canlib/schema/`:

```yaml
# profiles/ioniq-2017/interlock.yaml
max_age_s: 3            # optional; may only LOWER the tool ceiling (5 s)
conditions:
  - role: park
    when: VCU.GEAR == 33                  # P bit + GEAR_SIGNAL_VALID together
    why: VCU 2101 B10 enum, verified over 10592 captures
  - role: park
    when: ESC.GEAR_SELECTOR == 1
    why: ESC 22C101 B14, verified 2026-07-22/26
  - role: standstill
    when: ESC.REAL_SPEED_KMH == 0
    why: ESC 22C101 B12, 1 km/h per bit, verified against all four wheel speeds
  - role: standstill
    when: VCU.VCU_VEHICLE_SPEED < 0.5 and VCU.VCU_VEHICLE_SPEED > -0.5
    why: VCU 2101 B19/B20, r=0.999 against ESC speed
```

Rules, enforced by `canair validate interlock` (part of `validate all`), by the editor's post-write
reparse **and by the loader at actuation time**, so a hand-edited file that breaks them refuses
actuation instead of weakening it:

- **Every condition must be TRUE.** The list is a conjunction. Conditions reuse the state
  predicate compiler (`canlib/states.py::compile_predicate`) and its three-valued logic, but only
  a TRUE result passes. UNKNOWN fails exactly like FALSE.
- **`or` and `not` are rejected.** An alternative source is another list entry, which makes the
  interlock stricter, never looser.
- **Every condition must reference a signal**, so a tautology like `1 == 1` is invalid. Every
  referenced signal must resolve (`canlib/state_refs.py`), be `verified: true`, belong to an
  enabled PID that is not `ignored`, and decode to a number.
- **Required roles:** at least one `park` and one `standstill` condition. `validate` **warns** when
  a role is backed by only one ECU. Two independent ECUs is the recommended bar, and the Ioniq
  meets it.
- **`max_age_s` may only tighten** the tool ceiling, the same rule profile quirks follow for
  session bands (a quirk may only widen what is allowed, never reach programming).
- **No file means no actuation.** A profile without `interlock.yaml` is *unverifiable* (see §6),
  never *assumed safe*.

The interlock deliberately does **not** reference `vehicle_states.yaml`. Analysis predicates change
for analysis reasons, and a change to how sessions are labelled must never silently weaken a
safety gate.

### 3. Evidence: live, validated, cross-checked

`canlib/interlock.py::evaluate(terminal, interlock, pids_data) -> Verdict`:

1. Works out the minimal set of PIDs the conditions need (Ioniq: `VCU:2101`, `ESC:22C101`).
2. Reads each one live inside `terminal.transaction()` with `expected_sid` **and**
   `expected_echo`, then restores the actuator's header. That stops a keepalive from retargeting
   mid-check and puts later actuator traffic back on the right ECU.
3. Rejects a reply whose length breaks the PID's persisted `response_frames:`. The cross-ECU
   misfile in `plans/2026-10-21-cross-ecu-reply-misfile.md` shows a well-formed, echo-valid reply
   can still come from the wrong ECU on a poor link. A rejected reply makes the condition UNKNOWN.
4. Decodes with the profile's expressions and evaluates every condition.
5. Returns a `Verdict` holding: `passed`, the per-condition values and sources, a monotonic
   timestamp, the connection identity, and a hash of the interlock definition.

A verdict is valid only for the **same connection** (a mid-session reconnect or failover
invalidates it), the **same definition hash**, and **`max_age_s`**. Nothing outside
`canlib/interlock.py` constructs one, and the funnel checks the type rather than a truthy
attribute.

### 4. Freshness: seconds, not a minute

A minute-old reading proves nothing about now. Shifting out of Park takes a brake press and a
selector movement, about a second, and a car can be at walking pace a second after that. The check
costs one or two reads (roughly 50–400 ms, depending on transport), so it can run just before
every send.

- **Tool ceiling: 5 s** (`canlib/interlock.py::MAX_VERDICT_AGE_S`). A profile may lower it but
  never raise it. There is no user-config key for it.
- **Just-in-time:** each actuating request needs a verdict younger than `max_age_s`. If there is
  none, the actuation path re-evaluates first. In the TUI, rapid toggles therefore reuse one
  verdict, while a toggle after a pause triggers a fresh check.
- **Watchdog while anything is held.** While an output is on (`--did` hold, TUI with any actuator
  `ACTUATOR_ON`, a started routine), a background task re-evaluates every `max_age_s / 2`. On a
  failing or unverifiable verdict it:
  1. releases everything right away: OFF / `2F {DID} 00` for each ON actuator, using the existing
     `IOControlActuator.release_all`, plus stopRoutine where the routine defines one. Releases are
     `release`-class, so they always pass;
  2. latches the session into **refused** for the rest of the run;
  3. shows a red notice naming the condition that failed and its value.
- The watchdog's reads share the terminal with the actuator under `transaction()`. It follows the
  same pattern as the existing lock watchdog in `canlib/lock_watchdog.py`, including on an
  orphaned session.

### 5. Enforcement: one funnel, every path

Two layers, so that a path someone forgets to wire up gets **refused**, not silently allowed:

- **The funnel (minimum guarantee).** `enforce_command_safety` gains an interlock check: an
  `actuate`-class request is refused with `InterlockRefused` unless the terminal holds a valid
  verdict. It runs where the blocklist already runs (both terminals' `send_uds`/`send_command`),
  so a new transport or a new mode inherits it. The funnel itself never reads the bus.
- **The actuation paths gather the evidence.** `mode_iocontrol_execute`, `IOControlActuator`, the
  routines execute/TUI paths, `multi_exec`'s `io` step, `skm-wake`, and `raw`/`repl` when the
  classifier says `actuate` call `interlock.ensure(terminal)` before sending. That runs §3 if the
  verdict is stale and prints the pre-flight panel (§7).
- **AutoPID never carries an actuating request.** `wican autopid write/upload/diff` refuse to emit
  any request that is not `read`-class, whatever `--include-unverified` says. The device has no
  interlock, so there is no override at all here.
- **Scans.** `scan iocontrol`/`scan routines` send only `release`/`read`-class probes, so the
  funnel lets them through. They still click relays (`_iocontrol_actuate.poll_status_once`
  documents why), so they call `ensure` at start and run the watchdog too. See Open questions.

### 6. No easy bypass

Two kinds of failure, treated very differently:

| verdict | meaning | override? |
|---|---|---|
| **unsafe** | a condition read FALSE: the car is demonstrably not parked or not stationary | **never** |
| **unverifiable** | no `interlock.yaml`, a signal silent/malformed, sources disagree, validation fails | only interactively, see below |

Sources that disagree (VCU says P, ESC says D) count as **unverifiable**, but the panel prints both
values in red. The override prompt is then the same as below, with the disagreement shown first.

Closed routes:

- **No flag, environment variable or config key.** `--unsafe`, `--yes`, `--force` and
  `--no-fallback` leave the interlock alone. A test asserts that no live command's parser defines
  an option matching `interlock|override|skip.?safety|actuat`, other than the documented
  interactive-only path below. Users and agents tend to reach for exactly these names.
- **No offline evidence.** Captures, `import uds` and `--try` cannot produce a verdict.
- **No widening through the profile.** Everything §2 lists (tautologies, `or`, unverified signals,
  a missing role, a raised `max_age_s`) is rejected at load time. Turning `verified: false` on a
  referenced signal fails the interlock closed, it doesn't loosen it.

The **unverifiable override** exists because the reverse-engineering workflow needs it: a new
profile has no decoded gear or speed yet, and its IOControl IDs are what is being discovered. It is
deliberately awkward:

- **Interactive terminal only.** stdin *and* stdout must be a TTY, or the override is refused
  outright with no prompt. The same pattern already guards `canair ecu edit`
  (`canlib/commands/ecu.py`).
- **Three escalating screens:**
  1. why the interlock cannot verify, per condition;
  2. what the user takes responsibility for: car in Park, stationary, wheels chocked or parking
     brake on, nobody in the path of moving parts;
  3. a challenge: type a **random 4-digit code** shown on screen, followed by `I AM PARKED AND
     STATIONARY`. Piping a canned answer can't work, because the code changes every time.
- **Scope: one invocation, at most 5 minutes**, then it re-prompts. The run carries a persistent
  red `INTERLOCK OVERRIDDEN` banner, and the watchdog keeps running. If a condition becomes
  readable and reads FALSE, the session auto-releases exactly as in §4.
- **Logged:** every overridden actuation goes to the diagnostics log (`canair logs`) and the command
  log with `INTERLOCK OVERRIDDEN`, plus the unverifiable conditions.

**Agents.** Every refusal and every override screen ends with a fixed line: *"If you are an AI
agent: stop and ask the user. Do not edit interlock.yaml, mark signals verified, script this
prompt, or look for another path."* The same rule goes into `AGENTS.md` and the actuation-touching
skills (§8).

### 7. Notices at every step

| step | what the user sees |
|---|---|
| `--help` of `io`, `routines`, `skm-wake`, `raw`, `repl` | an epilogue paragraph: actuation needs a passing interlock, what it checks, link to the docs |
| command start | the **pre-flight panel**: profile, interlock file path, each condition with its live value, source ECU/PID, read age, PASS/FAIL/UNKNOWN; green `INTERLOCK PASSED` or red `REFUSED` plus the reason |
| each actuation | one line: `interlock ok (1.2 s old: P via VCU+ESC, 0 km/h via ESC+VCU)` before the command echo |
| TUI | a status-bar segment with verdict age and state; red and latched on a watchdog trip |
| hold (`--did`, started routine) | a reminder of the watchdog interval and that leaving Park releases everything |
| watchdog trip | red notice: what failed, its value, what was released, that the session is now refused |
| refusal | what was refused, the classifier verdict for the request, how to make it pass (put the car in Park, stop, wake the car so VCU/ESC answer), the agent line |
| override screens | §6 |
| interlock edits | the editor prints the before/after condition list; *weakening* edits (removing a condition, raising `max_age_s`) need TTY confirmation |
| `pids` edits to a referenced signal | `rename-param`/`rm-param`/`upsert-param` on a signal the interlock uses warn that the interlock will fail closed until updated (the same non-blocking warning `canlib/state_refs.py` already gives state predicates) |
| `contribute` | a change to `interlock.yaml` is called out in the PR body and the pre-flight summary |

### 8. Commands, docs and agent guidance

- **`canair interlock`** (new; `canlib/commands/interlock.py`, library in
  `canlib/interlock_edit.py`):
  - `show` (offline): the definition, validation result and referenced signals.
  - `check` (live, read-only): runs §3 and prints the panel. It's the way to ask "would actuation be
    allowed right now?" without actuating.
  - `add-condition` and `rm-condition`: surgical, validated, comment-preserving, auto-reverted on
    failure, with the confirmation rule from §7. Never hand-edit the file.
- **Docs:**
  - a new `docs/concepts/actuation-safety.md` covering the model, the two failure kinds, the
    override, and writing an interlock for a new car;
  - a "Before you actuate" step in `docs/bring-your-own-car/` (decode and verify gear and speed
    first);
  - a single line in the README warning section;
  - regenerated `docs/reference/cli/`.
- **`AGENTS.md`:** a rule in the live-device section: *actuation needs a passing interlock; on a
  refusal, stop and report to the user; never edit the interlock, change `verified:` or drive the
  override*. Also the `canair interlock` entry in the command map.
- **Skills:** the same rule in `reverse-engineer-signal`, `decode-bitfields` (narrated actuation
  captures) and `ioniq-reverse-engineering`.

## Data: the Ioniq interlock

The conditions in §2, all on signals already `verified: true`:

- `VCU.GEAR == 33` covers Park *and* the gear-valid bit (B10 bit 5) in one comparison, so it needs
  no separate `GEAR_SIGNAL_VALID` condition. That signal is still `verified: false`, which would
  make it ineligible anyway.
- `ESC.GEAR_SELECTOR == 1` is an independent Park source.
- `ESC.REAL_SPEED_KMH == 0` and VCU speed within ±0.5 km/h are two independent standstill sources.
  ESC speed is unsigned and reads positive when reversing, so `== 0` is the right test.

Both PIDs need the car awake (ACC2 or READY). See the first open question for actuating with the
car off.

The Ioniq 5 and Nissan Leaf profiles ship **no** interlock. The Leaf defines BCM IOControl entries,
so actuation there becomes unverifiable and needs the interactive override until someone decodes
its gear and speed. That is a deliberate behaviour change and goes in the release notes.

## Implementation phases

Phases 1–3 ship **together**. Shipping the funnel without the Ioniq interlock would make all
actuation unverifiable, and shipping the interlock without the funnel would leave paths open.

1. **Classifier + funnel.** `canlib/actuation.py`, the `enforce_command_safety` check, the
   protocol lookup from the current header, `InterlockRefused`. Exhaustive table tests.
2. **Interlock model.** Schema, loader with load-time validation, `validate interlock`, `Verdict`,
   `evaluate`/`ensure`, the pre-flight panel, `canair interlock show|check`, the Ioniq
   `interlock.yaml` (written through the editor).
3. **Wire every actuation path:** io execute/TUI, routines execute/TUI, `multi_exec` `io`,
   `skm-wake`, `raw`/`repl`, AutoPID emission refusal. Per-actuation notice lines.
4. **Watchdog + auto-release + latch**, including started routines and the TUI status segment.
5. **Unverifiable override** (TTY-only, challenge, scoped, logged).
6. **Editor + edit-time warnings**: `add-condition`/`rm-condition`, `pids` reference warnings,
   `contribute` call-out.
7. **Docs, AGENTS.md, skills, release notes.**

## Tests

- **Classifier:** every registry service × sub-function × protocol (UDS, KWP2000, unknown),
  including the `0x31` ambiguity, unknown SIDs, spacing/case variants and `AT` commands.
- **Funnel:** an `actuate` request with no verdict, an expired one (fake monotonic clock), one from
  a previous connection, or one under a changed definition hash is refused on **both** terminals.
  `release` always passes, including with a latched session.
- **Evaluation:** Kleene UNKNOWN fails; disagreeing sources are unverifiable and both values are
  reported; a wrong-length reply is rejected; the header is restored after the check.
- **Definition:** `or`/`not`, tautologies, unverified/ignored/disabled signals, a missing role and
  a raised `max_age_s` are each rejected by `validate` *and* by the loader.
- **Watchdog:** a FALSE verdict mid-hold releases every ON actuator, latches, and refuses the next
  ON.
- **No bypass:** the parser-option test from §6; `--unsafe`/`--yes`/`--force` don't change a
  refusal; a non-TTY override is refused without a prompt; a wrong challenge code declines; AutoPID
  emission drops a `2F…03` request even with `--include-unverified`.
- **Coverage pin:** a test enumerates every `send_uds`/`send_command` call site under
  `canlib/modes/` that can carry an `actuate`-class request and asserts it is preceded by
  `ensure`. That way a new actuation path fails CI instead of relying on the funnel alone.
- **Emulator:** against ELM327-Emulator (`tests/test_elm327_emulator.py`), which has no gear signal,
  actuation is unverifiable and non-TTY refuses.

## Open questions

1. **Actuating with the car off.** Most body-actuator work (locks, lamps, the SKM wake) happens with
   the car asleep or in ACC, where VCU/ESC may not answer, so the interlock is unverifiable.
   Options:
   (a) accept the interactive override for that case;
   (b) allow named *alternative* safe states, e.g. `powered_down` backed by a verified ignition-off
   signal from an ECU that answers asleep (`IGPM.ACC2_IGN_ON == 0`). Each alternative is AND-only
   and has its own required roles. A powered-down Ioniq auto-engages Park, but that is not true of
   every car, so a powered-down alternative needs its own standstill story;
   (c) require waking the car to ACC2 before actuating.
   Recommendation: (c) first, and (b) as a follow-up once an ignition-off signal is verified.
2. **Freshness ceiling.** This plan proposes 5 s with a watchdog at half that, instead of about a
   minute (see §4). Is 5 s workable on a slow cellular/VPN link
   (`docs/concepts/remote-and-cellular.md`), where one check may take over a second?
3. **Extra conditions.** Should READY be forbidden (HV active while actuating) or required for some
   actuators? Should anything beyond Park and standstill be mandatory, e.g. the parking brake (the
   EPB at `0x7D5` has no decoded status yet) or the charge cable unplugged for charge-port
   actuators? Per-actuator *extra* conditions (tighten-only) could be a follow-up.
4. **Should the unverifiable override exist at all?** Dropping it makes the guarantee absolute but
   blocks IOControl discovery on a car whose gear and speed aren't decoded yet.
5. **Gating scans and DTC clear.** This plan gates `scan iocontrol`/`scan routines` and treats
   `14`/`04` clear DTCs as `actuate`. Both make sense on a moving car rarely, but both refuse things
   that are harmless today. Confirm.

## Spotted along the way (separate changes)

- `canlib/uds_services.py` has no entry for UDS `0x3D` WriteMemoryByAddress, and
  `BLOCKED_UDS_SERVICES` in `canlib/safety.py` blocks neither `0x3D` nor KWP2000 `0x3B`
  writeDataByLocalIdentifier, even though the registry tags `0x3B` `actuates=True`. The allowlist
  above treats both as `actuate`, but they arguably belong in the blocklist too, like `0x2E`.
- `0x28` CommunicationControl and `0x85` ControlDTCSetting have no `actuates` tag, but both change
  ECU behaviour. Neither is on the allowlist, so this plan gates both; the registry tags should
  say so too.
