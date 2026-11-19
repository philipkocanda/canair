# `safety.allow_actuation`: no IOControl or routine actuation until the user turns it on

Status: **PROPOSED** (2026-11-07). No code changed.

Paths are relative to `src/canair/`. Builds on the `safety.allow_unsafe` gate (`461047b2`) and on
the `safety.allow_reset` gate for `canair reset`. That work generalises the gate helpers
(`config/file.py::_safety_flag`, the `_GATES` consent table in `cli/commands/config/edit.py`,
`SAFETY_GATE_KEYS` in `cli/commands/config/keys.py`). Implement this plan after it is committed.

## Problem

canair sends a request that drives an output or starts a routine as soon as a command asks for
it. `canair io`, `canair routines --sf start`, the `io` step of `read`/`monitor`, `canair raw`,
`canair repl` and `canair scan range` can each send one. Most of these paths confirm first, but
`--yes` answers that prompt, so a script or an AI agent can pass it with good intentions. The
`AGENTS.md` rule ("never touch the car without approval") is advice to an agent. The code does
not enforce it.

`safety.allow_unsafe` closed the same gap for `--unsafe`: the flag works only after the user
turns a config key on at a terminal. This plan adds a second key with the same rules for
actuation.

## Decisions

These were settled with the user before the plan was written:

1. **Scope: IOControl and RoutineControl requests that actuate, on every path.** A request is
   gated when its service is UDS `0x2F`, KWP2000 `0x30` or `0x31` (UDS RoutineControl, KWP2000
   StartRoutineByLocalIdentifier) and the existing classifier
   (`safety/actuation.py::classify`) calls it `actuate`. The check sits in the shared send
   path, so `raw`, `repl`, the `io` query step, `scan range` and the KWP2000 `0x30` sweep are
   covered as well as `io` and `routines`.
2. **Releases and reads always pass.** UDS `2F {DID} 00` (returnControlToECU), UDS `31 02`
   (stopRoutine), UDS `31 03` (requestRoutineResults), KWP2000 `32` and a `0x30` request that
   the profile declares as an entry's `release:` are never gated. So `scan iocontrol` (UDS),
   `scan routines`, the io TUI's `--poll` and every auto-release work with the key off.
3. **Exempt: profile actions and the SKM wake.** `canair action run` and `skm-wake` (the
   command and the pipeline step) keep their own guards: an action has a live `require:`
   condition and a typed name, and the wake has a typed `YES`. Each gets an exact, scoped grant
   for its own requests. Nothing else becomes exempt.
4. **Exempt: DTC clear.** `04`/`14` are `actuate` to the classifier but outside the scope in
   decision 1. `dtc --clear` keeps its own confirmation.
5. **Enabling: the same rules as `allow_unsafe`.** canair reads the key only from the user
   config file, not from a legacy repo-local `config.yaml`. Only a YAML boolean `true` turns it
   on. `canair config set safety.allow_actuation true` asks for a typed `YES` at a terminal. No
   flag answers the prompt, and a run with no terminal is refused (exit code 2).
6. **Reminder: one note per live session**, not a banner on every command. Actuation is normal
   reverse-engineering work, so a banner on every command would be noise.

Decisions this plan makes, open to review:

- **`--unsafe` does not lift the gate**, and `--yes` does not either. They answer different
  questions. A request that is both blocklisted and gated (a reprogramming routine) needs both
  keys.
- **The gate comes before the `--unsafe` prompt**, so the user is never asked `YES` for a
  request that canair refuses afterwards.
- **An ECU with no protocol fails closed.** `classify` reads `31 03` to an ECU of unknown
  protocol as `actuate`, because on KWP2000 `0x31` starts a routine. With the key off,
  `routines` and `scan routines` refuse such an ECU. That is the classifier's existing rule. A
  profile fixes it by recording `identity.id_protocol`.

## Design

### 1. The config key

`config/file.py`: `ALLOW_ACTUATION_KEY = "safety.allow_actuation"` and `actuation_allowed()`,
read through the shared `_safety_flag` (user config file only, YAML `true` only, read on each
call). Register the key in `SAFETY_GATE_KEYS` and in the `_GATES` consent table with a prompt
that says what the key enables: IOControl and routine requests that can move or switch hardware,
and that only a person at the car should turn it on. `config/__init__.py` lists the key in its
charter.

### 2. The policy carries the permission

The terminal already holds one `SessionPolicy` (`safety/blocklist.py`), and an action swaps it
for the length of its own send. The gate needs three more facts per run, so they go on the same
object:

- `actuation: bool = False`: the key's value for this run. The default instance knows nothing,
  so a terminal that nobody configured refuses actuation (fail closed).
- `releases: Mapping[int, frozenset[str]]`: the declared `release:` requests per TX id. Without
  them, `classify` cannot tell a declared KWP2000 `0x30` release from an ON request.
- `actuation_grants: frozenset[tuple[int, str]]`: exact `(tx_id, request)` pairs that pass with
  the key off, added through a `granting_actuation(...)` method. This is a separate field
  from `grants` on purpose. `grants` also lifts the functional-id rule, which an actuation
  grant must not do.

`safety` cannot import `ecus`, so the policy cannot read the releases itself. A new
`ecus/policy.py::session_policy(pids_data, *, allow_actuation)` builds the whole policy:
`SessionPolicy.from_profile` plus the releases from `build_iocontrol_index`. It replaces the two
`SessionPolicy.from_profile(pids_data)` calls that build a terminal's policy today
(`cli/live/connect.py::connect_elm_terminal` and `live/read/raw_ops.py::run_raw`). Confirm that
the mid-session reconnect paths go through one of the two, or route them through it too.

`ConnectionOptions` (`transport/config.py`) gains `allow_actuation: bool`, next to `unsafe`.
`cli/live/runtime.py::connection_options` fills it from `actuation_allowed()`. The CLI reads
the config, and the code below takes the value as an argument.

### 3. The check, in the shared send path

- `safety/actuation.py::actuation_block(cmd, policy, tx_id) -> str | None`: why `cmd` to
  `tx_id` is refused, or None. It returns None for an AT command, for a service outside the
  scope, for a request that `classify(cmd, protocol, releases)` does not call `actuate`, for
  an exact actuation grant, and when `policy.actuation` is true. An odd-length request is an
  ELM327 request plus its frame-count digit. The check judges it without the digit, as
  `functional_block` does.
- `enforce_command_safety` moves from `safety/blocklist.py` into a new `safety/enforce.py`,
  because `safety/actuation.py` already imports `blocklist`. The order becomes: the unliftable
  blocks, then `actuation_block`, then the blocklist and its `--unsafe` prompt. `blocklist`
  keeps the policy and the pure checks. `actuation` keeps what a request does. `enforce` is
  the funnel.
- The three call sites (`transport/raw/terminal.py` `send_uds`/`send_command`,
  `transport/elm327/terminal.py` `send_command`) import from `safety.enforce`. The pipelined
  raw client (`transport/raw/uds_client.py`) calls `check_command_safety` itself and raises
  `SafetyBlocked`. It must call `actuation_block` too, or `monitor` on `slcan-tcp` bypasses
  the gate.
- The refusal is a `SafetyBlocked` (exit code 2), worded per the **cli-tui-standards** skill.
  It names the request, the reason (it drives an output, or starts a routine), the key, the
  config file path, and the command the car's owner runs at a terminal to turn it on.

### 4. Early refusal for the commands that only actuate

The funnel refuses after the connection is set up. A command whose only purpose is to actuate
should refuse sooner, before the device lock, like `--unsafe` does
(`cli/live/runtime.py::_require_unsafe_allowed`):

- `canair io ECU --did DID` without `--off` (an ON request).
- `canair routines ECU --rid RID --sf start`.

`LiveOperation` (`cli/live/operation.py`) gains `actuates: bool`. The `io` and `routines`
commands set it from their flags when they pick their operation, and `run_live` refuses with
exit code 2 when it is set and the key is off. The TUIs, `--off`, `--sf stop` and
`--sf results` still connect. The funnel decides for each request they send.

### 5. The exempt paths

- **Actions:** `live/actuate/action.py::_send` already swaps in `plan.py`'s policy. That
  policy also gets `granting_actuation` for exactly the action's planned requests and its
  `release:` requests. The `then:` reads are reads and need no grant.
- **SKM wake:** `live/elm/skm_wakeup.py` swaps the policy for the length of its own
  `2F {DID} 03 {magic}` send, with one exact grant on `SKM_TX_ID`, and restores it in a
  `finally`. The pipeline step (`live/read/exec.py::exec_skm_wake`) goes through the same
  function.

### 6. Notices

- **Key on:** at the start of every live session, one `note:` line on stderr says that
  IOControl and routine requests are enabled and names `canair config unset
  safety.allow_actuation`. It goes in `cli/live/runtime.py::async_main`, next to the
  `--unsafe` warning. stdout stays clean for `--json`.
- **Key off, in a TUI:** the io and routines TUIs open as now. Their status area says that
  actuation is off and names the key. A key press that would actuate shows the refusal in the
  view instead of a stack trace in the TUI log. Check how `IOControlActuator.send_on` and the
  routines controller report a `SafetyBlocked` today.
- **`--help`:** the `io` and `routines` epilogues say that ON requests and `--sf start` need
  `safety.allow_actuation`. Unlike `--unsafe`, there is no flag to hide.

## Docs and agent guidance

- `docs/concepts/safety.md`: a section "Enabling actuation" next to "Enabling `--unsafe`"
  (what is gated, what always passes, the exemptions, how to turn it on and off, and the
  "What this guard is for" note). Update "Safe by default" and "Discovery vs. actuation" to
  point at it.
- `config.example.yaml` (the commented `safety:` block) and `docs/reference/config.md`.
- `docs/reference/cli/`: regenerate (`scripts/gen/gen_cli_reference.py`) for the `config`
  key list and the `io`/`routines` epilogues.
- `AGENTS.md`: one clause on the `canair io` / `canair routines` entry. An agent never turns
  the key on, and a refusal means "stop and ask the user". The file is ratcheted, so the
  clause must fit in the existing lines.
- Skills: the same rule, one line each, where a skill tells the reader to actuate
  (`reverse-engineer-signal`, `decode-bitfields`, `ioniq-reverse-engineering`).
- `plans/2026-10-22-actuation-safety-interlock.md`: record that the §5 funnel now exists for
  IOControl and routines (`safety/enforce.py`), and that the interlock adds its verdict check
  there. The plan's "no config key skips the interlock" rule still holds:
  `safety.allow_actuation` is a precondition, not a bypass.

## Tests

- `tests/safety/`: a table over `actuation_block`. It covers UDS `2F … 01/02/03` (refused),
  `2F {DID} 00` with and without a mask (passes), KWP2000 `30` declared and undeclared, UDS
  `31 01`/`02`/`03`, `31 03` to an ECU of unknown protocol, KWP2000 `31`/`32`, AT commands,
  whitespace and an odd-length ELM327 request. Also: the key on, an exact grant on the right
  and on the wrong TX id, and a grant that does not open the functional id.
- The funnel order: a gated request that is also blocklisted is refused without a prompt,
  with `--unsafe` set, and `--yes` changes nothing.
- Both terminals and the pipelined raw client refuse a gated request when their policy has
  `actuation=False`. Extend the existing transport safety tests.
- `tests/config/`: `actuation_allowed()` reads only the user config file and only a YAML
  `true`. `config set` asks for consent, refuses with no terminal and accepts `YES`.
- `tests/cli/commands/test_live_dispatch.py`: `io --did` and `routines --sf start` exit 2
  before the lock when the key is off. `--off`, `--sf results` and the TUIs connect.
- An action and the SKM wake send their IOControl requests with the key off, and their grant
  is gone after the send, also after an error.
- The note: printed on a live session when the key is on, absent when it is off, never on
  stdout.
- Existing tests that actuate through a real terminal class or the loopback emulator need a
  policy with `actuation=True`. Add a fixture in `tests/support/` for it. Do not change the
  default of `SessionPolicy`.

## Release

The default is off, so `canair io`, `routines --sf start`, and `raw`/`repl`/`scan range`
requests that actuate stop working until the user turns the key on. The release notes must say
so. See the open question on the commit type.

## Open questions

1. **Commit type.** The change refuses something that worked before, and `RELEASING.md` uses
   `!` for an incompatible behaviour change. The `allow_unsafe` commit used plain `feat`.
   `feat(safety)!` is the stricter reading.
2. **One flag for the TUIs.** Should the io and routines TUIs refuse to open at all with the
   key off? This plan keeps them open, because listing, `--poll` and `scan` results are
   useful without actuation.
3. **AutoPID.** `wican autopid write` could still put an actuating request in the device's
   profile, and the device polls it with no canair process in the path. The interlock plan
   (§5) already proposes refusing any request that is not a read there. Fold that in here, or
   leave it to the interlock?
