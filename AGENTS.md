# canair — agent guide

canair is a vehicle-agnostic CAN/UDS reverse-engineering CLI. The bundled reference car is a 2017
Hyundai Ioniq (`profiles/ioniq-2017/`). For reference, the WiCAN firmware is checked out in
`wican-fw/` (gitignored; pull if you need the latest).

**How to read this file:** it carries the *rules*, a one-line-per-command *map*, and the *code/data
map* — not a flag reference and not a workflow guide.

- **Flags and TUI keys** live in `canair <cmd> --help` and in `docs/reference/cli/<cmd>.md`, which
  is **generated from `--help` and CI-checked**, so it cannot drift. Never hand-copy a flag list
  here.
- **Workflows and method** live in the **skills** (`.claude/skills/`) — how to reverse-engineer a
  signal, decode a bitfield, contribute code or a profile. Don't restate a skill's method here; a
  fact an agent needs *only while doing that task* belongs in the skill, and one it needs *at all
  times* (a rule, what a command is for, where code lives) belongs here.

## Running the CLI — ALWAYS `uv run canair` from the repo root

**Every invocation MUST be `uv run canair …` from the project root**
(`/Users/philip/projects/canair`). Never a bare `canair` (a globally installed `uv tool install .`
copy), which may run **stale code** and may resolve a **different profile** (wrong
`--profiles-dir`/`default_profile`) than the repo-bundled one. Wherever this file or a skill writes
`canair …`, execute it as `uv run canair …` with the repo root as `workdir` — never `cd`-hop away.

## Formatting — hard-wrap every file at 100 columns

**Max line length is 100 characters, in every file** — Python, Markdown, YAML, JSON, commit
messages, plan docs, skills, this file. It matches `line-length = 100` in `pyproject.toml`
(ruff enforces it for Python; prose is on you).

- **Hard-wrap prose**; do not write one-line paragraphs. A 9,000-character line is unreadable in a
  terminal, unreviewable in a diff (any edit rewrites the whole line), and expensive to patch.
- **Exceptions, only where wrapping breaks meaning:** a Markdown table row, a long URL, a fenced
  code block whose content must stay verbatim, a YAML frontmatter `description:` value, and
  generated files. Never wrap inside a `--help`-derived block.
- Applies when you *edit* a line too: if you touch a long line, re-wrap it (Boy Scout).

## Prefer canair's built-in tooling — do not hand-roll

For **all** querying, capturing, analysis and authoring, use the subcommands below — not raw
sockets, ad-hoc scripts, or hand-edited YAML.

- **Read the device** with `read`/`monitor`/`scan`/`discover`/`io`/`routines` (`raw` only as a last
  resort). **Analyze history** with `captures`/`decode`/`align`/`correlate`/`hunt`/`investigate`/
  `coverage`/`research`. **Edit definitions** with `pids`/`signals`/`states`/`groups`/`ecu` —
  surgical, schema-validated, comment-preserving.
- **Always pass `--save`** (with `--label`, `--state`, `--notes`) when reading from the device, so
  every payload lands in `captures/`. Works with `read`/`monitor`/`scan`/`raw`/`discover`. Saves are
  **journaled** (write-ahead log in `captures/.journal/`) and reconciled on exit, so a
  killed/crashed session is never lost — recover leftovers with `canair captures uds --recover`.
- **Every write prints the capture file it landed in** (`canlib/captures.py::saved_banner`) — read
  it back to confirm which profile got the data. A save made *inside* the monitor TUI has its banner
  deferred to exit, since Textual owns stdout while the app is up.
- **`monitor` records with `--keep-changes` (run-length) by default**, so a stored-row count
  measures **volatility, not sampling**. `--keep-all` for true timing/rate; the keep-mode tradeoffs
  and the joins' forward fill are in the **reverse-engineer-signal** skill.
- **The vehicle state is auto-suggested** from decoded values via the profile's
  `vehicle_states.yaml`; a `--save` segment with no explicit state is back-filled with the union of
  every state observed across its span.
- After adding captures, run `canair captures uds --summary`.

## Vocabulary — say **signal**, not parameter/param

**A decoded quantity read off the bus is a SIGNAL**, in both data domains (a field from a diagnostic
PID/DID response, and a field from a broadcast CAN frame). Prefer it in every new/edited string a
user reads — `--help`, TUI labels, prompts, errors, docs, release notes, commit messages, plans,
skills.

- **Do not introduce new "parameter"/"param" wording.** When you touch a user-visible string that
  still says parameter, change it to signal (Boy Scout).
- **The `parameters:` key in `ecus/` and the `pids upsert-param`/`rm-param`/`rename-param` CLI are
  the compatibility surface** — they keep their names for now (a schema/CLI rename is a separate,
  migration-bearing change). Their *help text* says signal.
- **`canair signals` remains domain-B-specific** (the broadcast `signals/<bus>.yaml` editor).
  "Signal" as a *word* is domain-neutral; the *command* is not.
- **Python identifiers still say `param`** (`ParamRow`, `decode_param_rows`, …). That rename is one
  atomic follow-up in `plans/2026-08-06-signal-naming-convention.md` — read it before renaming
  piecemeal, so the tree does not sit half-renamed.

## Boy Scout rule — leave it better than you found it

When you notice something wrong while working — a bug, a typo, a stale comment, a small
inconsistency, a missing validation, dead code, an unwrapped line — **be inclined to just fix it**
as part of your change.

The exception: if the fix is **too much work** (a large refactor, a risky/behaviour-changing edit,
anything sensitive/irreversible, or clearly outside the task's scope), **ask first**. Surface what
you spotted, describe the fix, let the user decide. Don't silently balloon a small task.

## Keep docs & README current — every user-facing change

Docs are part of the change. **Any** change that adds, removes or alters a user-facing capability (a
subcommand, a flag, a default, setup/config steps, a workflow, a profile field) MUST update the
user-facing docs in the same change. If nothing user-facing changed, confirm that rather than
assume.

**Respect the split:**

- **`README.md` stays compact and high-level** — the landing page: intro, connection diagram, the
  command *map* (one line per subcommand), a short quick-start, the bring-your-own-car arc,
  profile highlights, license, warning. **Detail does not belong here**; sections link into `docs/`.
  It was deliberately trimmed (311→143 lines) — keep it lean.
- **`docs/` carries the detail** — task-first, for new-car users and profile contributors:
  `getting-started/`, `bring-your-own-car/`, `concepts/`, `reference/`, `contributing/` (contribute
  a profile) and `development/` (change canair's code). `docs/reference/cli/` is **generated** from
  `--help` (`python3 scripts/gen_cli_reference.py`; CI checks it) — never hand-edit it.
- **This file** carries the agent-facing rules, the command *map*, and the code/data map. Where a
  fact is authoritative elsewhere (`--help`, `config.example.yaml`, `canlib/schema/`, a plan doc),
  **point at it instead of copying it**.
- **Skills** (`.claude/skills/`) carry the workflows.

Verify internal links still resolve — a broken cross-link is a defect. Policy:
`plans/2026-07-24-documentation-strategy.md`.

## Tools

> Workflows: reverse-engineering a signal end-to-end is the **reverse-engineer-signal** skill;
> partially-decoded bitflag bytes are **decode-bitfields**; bundled-car/device context is
> **ioniq-reverse-engineering**; changing canair's code is **contributing-code**. Deep
> protocol/transport/device-API work (the WiCAN Pro hardware, its ELM327 co-processor, the
> firmware's modes and API) is **wican-hardware-and-protocol** — not needed for ordinary work.

All functionality is one CLI, `canair` (invoked as `uv run canair` here). **Flags and subcommand
lists are in `canair <cmd> --help` and `docs/reference/cli/<cmd>.md`** — the entries below give each
command's purpose plus the agent-facing rules and code pointers that `--help` does not carry.

**TUI keys are not in `--help`.** Every Textual view's keymap is assembled from the shared role
registry **`canlib/tui_keys.py`** (`ROLES` + `bind(role, action)`), so one key means one thing in
every view; press `?` in any TUI for its live cheat-sheet. Never hardcode a `Binding` key in a TUI —
add or reuse a role, or `tests/test_tui_keymap.py` fails (it pins that every declared key resolves
to a role, that its description matches the role's accepted wording, and that `escape` never quits).
Two standing conventions: **`escape` backs out one level and never exits** (`q` is the only exit),
and **`g`/`G` address the view's primary axis**.

### Live device

- **`canair read`** (alias `query`) — Custom CAN/UDS requests. **Prefer positional query steps**
  (`canair read BMS:2101`) — the multi mini-language handles sessions, wake and keepalives. **Bind
  each PID to its ECU with a colon (`IGPM:22BC07`), never a space**: in a query step a space
  separates *independent ECU selectors*, so `"query IGPM 22BC07"` means "all of IGPM plus a bogus
  ECU" and is rejected. A **`@group`** token expands to a saved selector set (`canair groups`).
  **TesterPresent keepalives are automatic — there is no `tester-present` command/flag**; opening a
  session keeps it alive with idle-aware `3E00` (shared by UDS and KWP2000). Use `--reboot` to
  restore AutoPID after a WebSocket session. `--verbose` is for debugging canair itself.
- **`canair monitor`** (alias `mon`) — Live refreshing view; same query steps as `read`. Recording
  (`--save`/`--label`/`--state`/`--notes`) and the keep-modes live **here, not on `read`**. On a TTY
  it opens the scrollable Textual monitor (press `?` for keys); piped it polls silently until
  Ctrl+C. **`read` never had a `--monitor` flag.** `--wait` blocks until the device is online and
  makes a mid-session drop reconnect forever (see Mid-session reconnect).
- **`canair scan`** — Kinds: `range` (bare `canair scan <ECU>` is this), `sessions`, `routines`
  (UDS `0x31` SF03 / KWP `0x33`, auto-selected by `id_protocol` so it never blind-sends
  StartRoutine), `iocontrol` (returnControlToECU: UDS `0x2F` / KWP `0x30`). All are **safe probes**;
  hits are written to the ECU's `routines:`/`iocontrol_discoveries:`.
- **`canair discover`** (alias `disc`) — Which ECUs answer right now; `--register` writes a new ECU
  into `ecus/`.
- **`canair identity`** (alias `id`) — Standard UDS identity DIDs (F100/F18x/F190/F19x). Writes
  results into the ECU's `identity:`. Note it can write a **live VIN** there — see PII.
- **`canair io`** / **`canair routines`** — IOControl (`0x2F`) and RoutineControl (`0x31`):
  interactive TUI or a single actuation. **Mutative** — confirm-first, and actuators auto-release
  when the session ends.
- **`canair dtc`** — Read DTCs for one ECU or `--all` (UDS `0x19` / KWP `0x18` by `id_protocol`),
  log each scan to `dtc_log.yaml`, and report what **changed** since the last scan. DTC meanings
  live per-ECU in `ecus/<name>.yaml` `dtcs:`; profile-wide `failure_types:` in `profile.yaml`.
  Read-only unless `--clear` (`0x14`).
- **`canair sniff`** — Passive sniffer, **`slcan-tcp` only** (offers to switch the device). Live
  per-ID table plus optional `.asc`/`.blf`/`.csv` logging via `--save`. The only way to observe
  broadcasts the request/response path can't see.
- **`canair raw`** — Last resort: hex in, hex out, no decoding. **`canair repl`** — interactive
  ELM327 terminal (ELM transports only; `!tester` for a manual keepalive loop).
- **`canair status`** (alias `st`) — "What am I talking to, in what mode, is it usable?" Also prints
  the running canair version (`canlib/build_info.py::full_version` — branch + commit from a
  checkout), the device firmware/hardware version when the WiCAN HTTP API answers, and the
  connection-mutex state (read-only, probed without taking it). It resolves the **whole candidate
  list** and runs the same connect-time fallback probe as a live command, so it reports the device
  `read`/`monitor` would actually use (naming what it fell back from) instead of calling a setup
  dead because the *selected* device is down — a real, reported bug. `--no-fallback` pins the
  selected device. **Never reintroduce `resolve_transport(args)` here** (it is just
  `resolve_transport_candidates(args)[0]`, which is exactly what discarded the fallback).
- **`canair lock`** — Inspect/clear the **device connection mutex** (`flock(2)` on
  `/tmp/wican-connection.lock`, released by the kernel on exit, so a dead session never leaves a
  stale lock). `lock steal` runs the cooperative steal; `lock kill` signals a holder that won't
  release and **refuses a PID whose command line isn't canair** (recycled-PID safety). Exit 0 =
  free/freed, 1 = still held.
  **canair never kills another process on its own:** `--force` on a live command *asks* the holder
  to leave and polls for `FORCE_WAIT_S`, then gives up naming the PID and the exact `kill` to run —
  it never blocks indefinitely. The holder side is the watchdog
  (`canlib/lock_watchdog.py::LockWatchdog`, started by `run_live`/`sniff`): a daemon thread that
  self-`SIGTERM`s into the normal graceful shutdown when the lock is stolen or its file replaced, so
  an **orphaned session** (terminal gone, no `SIGHUP`) stands down instead of holding the device
  hostage. Steal requests are target-scoped and TTL'd, so a stale one can't abort a later session.
  Stop signals are unified in `canlib/stop_signals.py`: `SIGTERM`, `SIGHUP` and Ctrl-C all reconcile
  the `--save` journal and release the connection. A `kill -9`'d session's data is still recoverable
  with `captures uds --recover`.
- **`canair logs`** — The central diagnostics event log (`~/.config/canair/logs/canair.log`,
  size-rotated so it never grows unbounded): dropped/stale ISO-TP frames, timeouts, bus errors,
  decode failures (classified by `canlib/uds_parse.py::classify_response` into
  `drop`/`stale`/`no_data`/`bus`/`decode`) plus unexpected internal exceptions. Written by the
  per-exchange recorder `canlib/transport_stats.py::TransportStats` (attached as `.diag` on both
  terminals and the raw client) — the same tally behind the monitor's live `drops` indicator and a
  recorded session's `quality` provenance.

### Analysis (read-only — never touches the device)

> **Method lives in the skills.** *How* to choose and read these tools — the RE workflow, the
> physics/statistics reasoning, and the mirror/counter/fill/keep-mode semantics with their pitfalls
> — is the **reverse-engineer-signal** skill (plus its `signal-reasoning.md`) and
> `docs/concepts/analysis-commands.md`. Below is only what each command *is*, plus the code map.

- **`canair captures`** (alias `cap`) — Query captured data. **`captures uds`** (domain A) takes a
  QUERY; a bare `canair captures BMS 2102` is shorthand for it. Views: `--diff` (byte-level),
  `--step` (Textual stepper/comparator; a multi-PID QUERY stacks the PIDs time-joined in one frame),
  `--latest`, `--summary`, `--sessions` (the fast "what's in the captures?" index — time-span,
  state, label, notes, ECUs, acquisition `transport`, recording `version`, a `quality` line; no
  payloads).
  **The default list view is capped at `--limit N` (default 50) with a loud footer** for hidden
  history, so a bare query can't blow the context window; widen with `--limit 0` or scope tighter.
  Mutating modes: `--delete` (refuses a bare `--delete`, previews with `--dry-run`, confirms unless
  `--yes`), `--backfill-states` (offline state inference, `canlib/state_infer.py`; fills unset
  states and *reports* contradicted ones unless `--overwrite`), `--set-state` (manual counterpart —
  **requires** a scope filter so it can't blanket-relabel history), `--recover`, and the one-time
  `migrate` (YAML→JSON) / `migrate-rx` (`ecu`→`rx`) / `merge-driver` (see Key Files).
  **`captures can`** (domain B) lists imported raw frame logs from `captures/can/index.yaml`.
- **`canair decode`** (alias `dec`) — Value-centric decoding of all historical captures; the default
  view is each signal's value range. `--try` tests a candidate expression **without editing YAML**
  (even on a not-yet-defined PID), `--plot` is the interactive signal explorer, `--dump-bytes` emits
  a `timestamp × byte` matrix (timestamp-compatible with `align --csv`). The analysis modes need a
  single PID; the range/`--compact`/`--json` views take multi-PID queries.
- **`canair align`** — Time-aligned **wide table** of several cross-ECU signals: one row per
  reference sample, one column per `ECU:PID:PARAM|EXPR`. The first selector sets the row cadence;
  the rest nearest-join onto it. `uds` only (a `can` counterpart is future work).
- **`canair correlate`** — Ranks **every** strong cross-signal relationship in a drive; `--against
  REF` focuses one, `--overlap` reports which ECU:PID pairs actually share time-aligned samples,
  `--promote` writes the top raw-byte hit into `ecus/`. **Domain B: `correlate can FILE`** runs the
  same core over a raw frame log's `0xID:rN` byte series.
- **`canair hunt`** — "Which byte on this PID *is* a signal I already know?" `--promote NAME` writes
  the winner into `ecus/` with the evidence in `notes`; `--physical` needs no reference at all.
  **Domain B: `hunt can`** (`--promote` unsupported there — frames are defined in `signals/`).
- **`canair investigate`** — One-shot per-byte verdict for a PID (mapped?, state-separation F, best
  co-polled anchor, triage class from `canlib/triage.py`, physical band, probable multi-byte words).
  **ECU and PID are both optional** — omit one or both to sweep an ECU or the whole profile
  (`canlib/commands/investigate/sweep.py`). `--events`/`--dwell`/`--bits` are the narrated and
  bit-level views. **`--counters`** finds monotonic counters (odometer, hour meter, cycle count):
  detection is the leaf `canlib/counters.py` (numpy-free, so the raw-CAN domain can reuse it) and
  the capture-model bridge is `canlib/commands/investigate/counters.py`, which aligns a **prefix**
  payload matrix — aligning on the *modal* length would silently discard every capture of a minority
  length. Plan: `plans/2026-08-07-monotonic-counter-detection.md`.
- **`canair coverage`** (alias `cov`) — Audits definitions for decoding gaps: **UNMAPPED** data
  bytes, incomplete **BITS**, **NO CAPTURE** PIDs. Bit coverage counts both models (the
  `Bn:k`/`Sn:k` accessor and a `type: bitmask` param's `bits:` keys) and a whole-byte read does
  **not** suppress the bit-gap report. One known blind spot: an `&`-mask expression contributes no
  bit references, so **author bits as `Bn:k`, never a mask**.
- **`canair research`** — The open RE backlog from the per-ECU `research:` sections. Complements
  `coverage` (undecoded *bytes*) by surfacing *planned* work.

**Shared flags are declared once so their defaults cannot drift** — when adding an analysis command,
reuse these instead of re-declaring a flag:

- `--join-tol` / `--fill` / `--max-hold` — `canlib/commands/_join.py::add_join_args`, used by
  `align`/`correlate`/`hunt`/`investigate`/`decode` (`captures uds --step` deliberately keeps its
  own wider *viewer* tolerance). Fill policy is `canlib/fill.py`, resolved at series-build time so
  the join primitives never learn what a keep mode is; a value is never carried across a session
  boundary, and every fill is reported. Plan: `plans/2026-08-05-run-length-forward-fill-joins.md`.
- `--find-mirrors` / `--mirror-match` / `--allow-offset` — one matcher, `canlib/mirrors.py`, behind
  three sweeps (`decode` intra-PID, `correlate` cross-ECU, `correlate can` cross-ID).
- `--notation {wican,isotp,torque,bix}` — **display only**; `--json`/`--promote` always emit the
  canonical WiCAN expression. A byte is modelled in ISO-TP space (`canlib/notation.py::ByteRef`,
  rendered via `ByteDisplay`); WiCAN/Torque/bix are derived views. Persist a default with
  `config set display.byte_notation`.
- Scope flags (`--since`/`--until`/`--date`/`--today`/`--state`/`--label`/`--last-session`/
  `--first`/`--last`) — `canlib/capture_dates.py::add_scope_args`, shared by `captures` and every
  analysis command.

### Authoring (writes into a profile — pass `--profile`)

- **`canair pids`** — Add/update `ecus/` **parameters** and **research** entries. Every edit is
  YAML-reparsed, schema-validated (`canair validate pids`) and **auto-reverted on failure**;
  comments and field order survive. Prefer it over hand-editing the rich per-ECU YAML. Subcommands
  cover params, PIDs, research entries and per-ECU facts (identity, CAN bus, wake ritual,
  addressing, IOControl ranges) — see `--help`. Two things worth knowing before you author an
  expression, both covered in depth by the **reverse-engineer-signal** skill: **WiCAN `Bnn` includes
  the ISO-TP PCI bytes** (so confirm an offset with `canair bix -a HEX --ecu … --pid …` first), and
  a successful `upsert-param` **echoes the new expression's decoded value range** across existing
  captures — a `constant` where you expected variation usually means the offset landed on a PCI
  byte. Typed decoding (`--type` + `--value`/`--bit`) is a *parallel* decoding in
  `canlib/decode_value.py`; the WiCAN `expression` stays the pure float. Free-text `notes`
  auto-format via `canlib/yaml_rt.py` (short inline, long folded `>-`) — pass plain strings, never
  hand-format block scalars.
- **`canair signals`** — The domain-B analogue: view/edit the broadcast sidecar `signals/<bus>.yaml`
  (arbitration ID → named **linear** signals: start bit/length/byte order/scale/offset/unit).
  Surgical, validated (`canair validate signals`), auto-reverted (`canlib/signals_edit.py`). **Never
  hand-edit `signals/`.** Distinct from `pids` (freeform WiCAN expressions over `Bnn`); this is the
  DBC-compatible linear model. The schema also accepts the typed-decode fields, but CLI flags to
  author them are a follow-up (typed broadcast signals are hand-written in YAML for now).
- **`canair ecu`** — Inspect the ECU registry and per-ECU stats (PIDs/signals/verified counts,
  protocol, addresses, CAN bus, `--states`). `ecu <ECU> pids` lists every PID with its **latest
  decoded state**. **`ecu add TX …`** registers an ECU offline (no device) — the counterpart to
  `discover --register`, and how you seed a known ECU into a blank profile (including a non-standard
  `--rx-id` or a 29-bit/extended addressing block in one valid write). **`ecu <ECU> rename NEW`**
  renames an ECU — its name *is* its top-level YAML key plus its `ecus/<name>.yaml` filename, so the
  rename rewrites the key and moves the file (validated, comment-preserving); use it to promote a
  placeholder like `Unknown-7D5` once identified. **`ecu <ECU> edit`** opens
  `$EDITOR` and is **TTY-only — agents cannot drive it**; use `pids …`/`ecu add` instead.
- **`canair states`** — List/edit the profile's vehicle-state vocabulary (`vehicle_states.yaml`);
  `canair states <STATE>` is the reverse lookup (which ECUs are readable in that state, via
  `canlib/states.py::ecus_in_state`). A state with a `when:` predicate is marked ● — or **`✗` with
  the reason** when the predicate references a signal the registry doesn't define, so a dead
  predicate can't hide behind a healthy ●. `set-implies NAME [STATES]` (and `add --implies`)
  declares the **specificity hierarchy** — see `vehicle_states.yaml` under Key Files; the list view
  shows it as a dim `specializes:` line and `canair states <STATE>` also reports the reverse edges
  (`specialized by:`). **Never hand-edit `vehicle_states.yaml`** (`canlib/states_edit.py`) — its
  editors also retarget/drop sibling `implies:` references on `rename`/`remove`, which a hand-edit
  would leave dangling (and a dangling target fails the next validate).
- **`canair groups`** — List/edit `groups.yaml`, the named selector groups recalled with the `@`
  sigil. Expansion is **textual, before the query parser**
  (`canlib/ecu_groups.py::expand_group_refs`), so a group composes with other groups and with ad-hoc
  selectors. Members are plain selectors (never other groups, never full pipeline steps) and carry
  no PID suffix. `@group` works in `read`/`monitor` today; analysis commands don't expand groups
  yet. **Because a group routinely overlaps a hand-typed selector, `parse_sub_commands` ends by
  coalescing every contiguous run of `query` steps to one step per canonical ECU**
  (`canlib/modes/multi_parse.py::normalize_query_steps`): a bare ECU supersedes its `ECU:PID`
  selectors, distinct PIDs union, aliases resolve to the canonical name, and first-mention position
  is kept. A non-`query` step ends the run, so a deliberate pipeline re-read survives. Overlaps are
  reported (never silently collapsed) by `merged_selector_notes` via
  `canlib/commands/_live/steps.py::report_merged_selectors`. This is load-bearing for `monitor`: a
  duplicated ECU was polled twice per cycle *and* its rows collided on the
  `(ecu_label, pid, param_name)` selection key, snapping the TUI cursor and viewport back to the
  first copy.
- **`canair bus`** — Read-only list of the profile's CAN bus segments from `can_buses.yaml` with ECU
  counts; flags **undeclared** codes and counts **unbussed** ECUs. Companion to `pids set-can-bus`.
- **`canair validate`** (alias `val`) — `validate
  pids|captures|ecus|states|can-buses|signals|groups|can|all` (default `all`); `--stats` for
  counts. Schemas are tool-owned in `canlib/schema/`; the optional targets skip gracefully when
  absent. What matters beyond the schema check: **`validate states` resolves every `when:`
  predicate's `ECU.PARAM` against the registry and ERRORS on one that can't** — the *only* place a
  renamed/typo'd predicate signal is caught, because Kleene evaluation makes it silent (a missing
  signal is UNKNOWN exactly like an unpolled one, so the state just never gets suggested; that is
  how a rename disabled a state with `validate all` green). Each way of missing is reported
  distinctly (unknown ECU, an alias instead of the canonical name, wrong case, a signal on another
  ECU, one under an `ignored` PID, one that doesn't decode to a number). Resolver
  `canlib/state_refs.py`. The same check runs as a **non-blocking warning** from `states add
  --when`/`set-predicate` and `pids rename-param`/`rm-param`. **`validate pids`** also errors on a
  duplicate *shipped* signal name across PIDs (each becomes a distinct device signal) and on an
  `identity:` field outside the schema's `identity_fields`, and warns when two *synonymous* identity
  fields hold the same value (dead mirrored data). **`validate captures`** soft-warns on
  out-of-vocabulary states, SID/DID **echo mismatches** (a misfiled payload), non-hex payloads,
  **untimed payload captures** (excluded from every time-aligned analysis) and **degraded-transport
  sessions** (recorded drops/stale frames). **`--max-untimed N` is the CI ratchet**: fail when the
  count *grows*. The write path now enforces a timestamp so the count only falls — CI gates each
  profile at its baseline (`.github/workflows/ci.yml`), lowered opportunistically. `--strict`
  forbids any untimed row.
- **`canair wican`** — Build/sync the device's AutoPID profile from the active bundle's `ecus/`. A
  bare `canair wican` prints help (writes nothing). `autopid write` generates JSON into `out/`
  — **verified-only by default**, `--include-unverified` also emits in-progress candidates;
  `upload`/`download`/`diff` sync the device (Pro-only, same verified-only default). **Opt in with
  `--expected-responses`** (on `write`/`upload`/`diff`) to append each PID's persisted
  `response_frames` count to its request string, so the device's ELM327 co-processor answers as soon
  as that many frames arrive instead of waiting out its `ATST96` ~614 ms budget. Opt-in because the
  **firmware has no desync recovery** — it accumulates into one static buffer cleared only *after* a
  parse, so an undercount's queued tail would silently prefix the next PID's response; the emit path
  therefore goes through `elm327_frame_count.requestable()` and skips anything unproven,
  `variable_length`, over the 9-frame ceiling, or on an odd-length request. `autopid stats` shows a
  `Frames` column so you can see which PIDs have earned one. `mode set MODE`
  switches the device protocol and **auto-aligns the config `transport.type`** (`slcan`→`slcan-tcp`,
  `elm327`→`wican-ws`; `--no-transport` opts out).
- **`canair bix`** — Byte-index converter and payload annotator (WiCAN ↔ ISO-TP ↔ Torque ↔ bix). A
  bare `canair bix` prints a guided overview, `--table` the full table grouped by CAN frame, and
  **`--annotate HEX` (`-a`)** maps a real payload byte-by-byte with a `Role` column (`--ecu`/`--pid`
  overlays which signal maps each byte and flags `unmapped` ones). **Never convert a byte index by
  hand** — the mapping depends on the response's *length* (one vs two PCI bytes) and its *service*
  (header width and field order), which `bix` reads off the payload; the skill's byte-index
  reference explains the trap. Code map: roles come from **`canlib/uds_layout.py`** (a `response_SID
  → [(role, width)]` table over the `canlib/uds_services.py` registry), which also owns
  **`SUBFUNCTION_NAMES`**, the single home for per-SID sub-function enums —
  `canlib/formatting.py::decode_uds_response` and `modes/sessions_scan.py` both read it, and a test
  cross-checks the two layout models.
- **`canair config`** — View/manage user config (`~/.config/canair/config.yaml`): `show`, `example`
  (prints `config.example.yaml`, the authoritative fully-commented reference for every key), `path`,
  `get`, `set` (dotted keys create nested mappings; enum keys validated up front; unknown keys
  warned), `unset`, `edit`. Comment-preserving — prefer it over hand-editing.
  **`grid_region`** (`EU`/`UK`/`US`/`JP`/`CN`/`AU`; presets `canlib/grid_regions.py`) selects the
  mains-voltage/line-frequency bands for `hunt --physical`/`investigate`. It is a property of *where
  the car charges*, so it lives in user config, not the profile; unset assumes EU and the first
  physical scan offers to set it.
- **`canair profile`** (alias `prof`) — see Profiles below.

### Import / export / sharing

- **`canair import uds ECU:PID=PAYLOAD …`** — **Device-free capture import** (domain A): record an
  externally-provided UDS payload (a forum/issue paste, or a reading from another tool) through the
  same machinery as a live `--save`, so it is indistinguishable from a device-recorded capture and
  immediately queryable. Payload is the reassembled UDS response (SID-first, PCI stripped).
  `--label` is required; `--time` defaults to the import instant (a payload capture is **always**
  timestamped — an untimed one is silently dropped by every time-aligned analysis). Resolves ECU→RX,
  rejects non-hex payloads, warns on SID/DID echo mismatch. **This is how community readings are
  onboarded — never hand-write `captures/`.**
- **`canair import can <FILE>`** — Domain B: reads a raw broadcast frame log
  (`.asc`/`.blf`/python-can `.csv`/candump `.log`/`.trc`/SavvyCAN GVRET `.csv`; `--format auto` by
  extension, `.csv` header-sniffed) via python-can's readers, stores it **verbatim** in
  `captures/can/`, and appends a metadata entry to `captures/can/index.yaml`. High-volume logs stay
  native — they are never exploded into per-day capture files.
- **`canair import dbc <FILE>`** — Imports a DBC's broadcast definitions into `signals/<bus>.yaml`
  (cantools with `strict=False`, to tolerate real-world overlapping-signal DBCs; `--dry-run`
  previews). Module note: `commands/import_.py` (`import` is a keyword) with `NAME="import"`; the
  `uds` kind lives in `commands/import_uds.py`. Fetch the reference corpus with
  `scripts/fetch_can_corpus.py` (into gitignored `references/can/`).
- **`canair export dbc`** — Writes `signals/` out as a DBC for SavvyCAN/cabana/Wireshark;
  round-trips with `import dbc`. (Domain-A decoded-value export is future work.)
- **`canair contribute`** (alias `share`) — Opens the upstream PR for the **active** profile via the
  GitHub CLI (`gh`), with no manual fork/clone/branch/push. **Storage-location-agnostic:** the
  profile is *copied* into a managed fork checkout (`~/.config/canair/contribute/canair/`), so it
  works whether the source is repo-bundled, in `~/.config/canair/profiles/`, or a `--path` bundle.
  Pipeline: `validate all` → **installed-snapshot guard** (the active profile came from a frozen
  `site-packages`/`uv tool`/`pipx` copy) → **PII pre-flight** (`canlib/pii.py`) → **workspace
  self-collision guard** (hard-refuses, not `--yes`-overridable, when the active profile *is* the
  workspace's own copy) → **rollback guard** (the contribution would remove committed upstream
  definition lines, signalling a stale source) → branch → copy → commit → push → `gh pr create`.
  **The managed workspace is made hermetic before the copy** (`contribute.reset_workspace`: `reset
  --hard` to the upstream base + `clean -fd -- profiles/`) — `checkout -B` alone keeps an
  uncommitted file an earlier `--diff` left behind, and `commit_profile` stages the whole profile
  directory, which is how eight stale capture files once rode along in a PR. A user's own
  `--repo-dir` is **never** reset; it is warned about instead (`_own_checkout_is_clean`), gated on
  `is_managed_workspace`. **A re-run updates the open PR** rather than failing: the branch name is
  date-based, so `find_open_pr` looks it up (`gh pr list --head <plain branch>`) after the push and
  reports `updated PR #N` as success instead of letting `gh pr create` error.
  Captures are **overlaid** (append-only, `canlib/captures_merge.py::overlay_documents`), so a
  source behind upstream never proposes deleting upstream sessions, and a capture log it adds
  nothing to stays byte-identical. `--diff` previews, `--dry-run` stops before pushing, `--yes`
  skips prompts, `--no-captures` ships definitions only. Layering: `canlib/contribute.py` = git/gh
  orchestration; `commands/contribute.py` = CLI + pipeline; `_contribute_gates.py` = the pre-flight
  policy; `_contribute_report.py` = the human/`--json` reporting duality. Read the
  **contributing-profiles** skill before using it.
- **`canair update`** — Updates the CLI from its git-clone install: reports current vs latest
  **released** version, then **checks out that release tag** and reinstalls
  (`uv tool install --reinstall`). Checking out the tag — not fast-forwarding a branch — guarantees
  the installed code is exactly the released version, so it **refuses to update when the latest tag
  can't be determined**. Also reports the **install context** (`canlib/install_context.py`): which
  copy is running (repo working tree vs `uv tool` snapshot), the clone's git HEAD, and an **out of
  sync** warning when the installed tool copy's version differs from the clone's — a bare `canair`
  would then run different code than `uv run canair`. Refuses to touch a dirty clone. Backed by
  `canlib/update_check.py`, which also drives the once-a-day background check (offline-safe, never
  blocks a command; disable with `check_for_updates: false` or `CANAIR_NO_UPDATE_CHECK`).

### Profiles

Vehicle data lives in a **profile** bundle: `ecus/` (one file per ECU — the source of truth),
`profile.yaml`, `captures/`, `references/`, `vehicle_states.yaml`, `can_buses.yaml`, `groups.yaml`,
optional `signals/`, generated `out/`. The repo bundles several under `profiles/`
(`profiles/ioniq-2017/` is the mature reference; `profiles/ioniq-5-2022/` an early seed).

**Since more than one ships, none is auto-selected.** Precedence: `--profile NAME|PATH` (a global
flag, before the subcommand) > `CANAIR_PROFILE` > `default_profile` in config > a single discovered
profile. Discovery: `--profiles-dir`, `$CANAIR_PROFILES_DIR`, `profiles_dir` in config,
`~/.config/canair/profiles/` (user, uncommitted), then the repo's `profiles/`; user profiles shadow
bundled ones by name — **unless the user one declares `extends:`, which layers instead** (see
Layered profiles).

`canair profile list`/`show [NAME]`/`path [NAME]` inspect; `canair profile use NAME` sets the config
default; a bare `canair profile` opens an arrow-key picker on a TTY. `profile create <name>
--car-model "…"` scaffolds a new vehicle and **`profile adopt <name>`** copies a read-only one into
`~/.config/canair/profiles/` (where it shadows the original) so it can be written to. Both live in
the library module `canlib/profile_create.py`; `commands/profile.py` is CLI only. `adopt` **refuses
when a higher-precedence discovery root would keep winning** (an explicit `--profiles-dir` or
`$CANAIR_PROFILES_DIR`), because the copy would then never be read — the message names the blocking
root. It skips `role: generated` members (`out/` is regenerated, not inherited). The bundle
component list is **not hand-written** — it is the `BUNDLE_MEMBERS` registry in `canlib/profile.py`,
which also decides what `contribute` ships and what the blind-test strip withholds, so a new bundle
member is one edit there.

**Layered profiles — a read-only base plus your capture layer.** `canair profile overlay <name>`
(`canlib/profile_create.py::overlay_profile`) writes *only* `~/.config/canair/profiles/<name>/
profile.yaml` with `extends: <name>` plus an empty `captures/`; the base then keeps tracking
upstream while every recording lands in the layer. Adopt when you want to change definitions,
overlay when you only want to record. Resolution is `canlib/profile.py::profile_layers` (walks
`profiles_roots`, stops at the first bundle with no `extends:`) + `::_from_layers`, which errors on
a **dangling** overlay (nothing underneath) and on `extends:` naming a *different* profile (deferred
to `plans/2026-07-30-profile-variant-inheritance.md` — `extends:` is deliberately that plan's key).
- **`Profile` gained `overlays: tuple[Path, ...]`** (least specific first) with `layered`,
  `write_root` and `capture_layers`. Definitions (`ecus_dir`, `states_file`, `signals_dir`,
  `can_buses_file`, `groups_file`, `meta_file`) stay on `root`; `captures_dir`/`dtc_log_file`/
  `out_dir` derive from `write_root`. **Definitions do not overlay in this slice** — an edit is
  refused by `canlib/profile.py::require_writable_definitions()`, called from the single edit funnel
  in each of `pids`/`states`/`groups`/`signals`/`ecu add`. It raises `ProfileError`, which
  `canlib/cli.py` already turns into `error: …` + exit 2, so no command handles it.
- **Reads span layers, writes do not.** `canlib/capture_io.py::resolve_capture_layers` is the read
  seam; `capture_store.load_all_captures` iterates it, dedupes sessions across layers by
  `captures_merge.session_key` (base wins, so a contributed-then-pulled session stays read-only) and
  sorts chronologically **only when layered**, so single-layer results stay byte-identical. A reader
  that bypasses `load_all_captures` needs deciding per call site:
  `commands/validate/captures.py` and `commands/coverage.py` walk **both** layers (a schema break or
  a payload in the base still matters); `canlib/pii.py` and `capture_migrate.py` intentionally stay
  on `captures_dir` (= the overlay, which is exactly what `contribute` ships and the only layer a
  migration may rewrite).
- **Base captures are read-only**, enforced by `canlib/commands/captures/layers.py::refusal` in
  `--delete`/`--set-state`/`--backfill-states` and in the `captures uds --step` TUI. `--dry-run`
  still previews. Tombstones and copy-on-write were rejected: a session has no id — **its identity
  is its content**, which is what makes both the git merge driver and the contribution overlay
  correct. `read_only_files` returns `[]` whenever fewer than two layers are resolved, so nothing
  changes for an ordinary profile.

> **Pass `--profile NAME` explicitly on every mutative/authoring command** — `pids …`, `signals
> upsert`, `import uds|can|dbc`, `discover --register`, `ecu add`, `wican autopid write`, and
> `--save` reads. Without it they write to whatever `default_profile`/`CANAIR_PROFILE` resolves to —
> **not necessarily the car you mean**. This is exactly how Ioniq-28-derived `WHL_SPD11` signals
> once landed in `profiles/ioniq-5-2022/` instead of `ioniq-2017/` (fixed in the git history).
> `signals`/`import` echo the target profile and full path on write — read it back. Read-only
> commands resolve the same way, so prefer explicit `--profile` there too.

**Never let a write land in the install snapshot.** A profile resolved from `site-packages` (a bare
`canair` from `uv tool install`/`pipx`/`pip`) accepts writes that the next reinstall deletes — this
really happened, costing two sessions' captures. The detection and the user-facing remedy are
`canlib/install_context.py::installed_snapshot_kind` and `::snapshot_write_note`, and **every write
path emits that note**: `canlib/captures.py::saved_banner` for captures and
`canlib/edit_echo.py::echo_edit` for every definition edit (`pids`/`signals`/`states`/
`groups`/`ecu add` all route their `✓ …` confirmation through it, which is also what guarantees the
confirmation names a **full path** rather than a bare `bms.yaml`). `canair update` refuses to be the
thing that erases the data: `::snapshot_profile_risks` diffs the snapshot's profiles against the
clone's and lists what a reinstall would delete before asking to proceed. `canlib/first_run.py`
offers to adopt rather than recording a doomed `default_profile`. Fix an existing setup with `canair
config set profiles_dir <clone>/profiles` (preferred — stays git-tracked and contributable) or
`canair profile adopt <name>`. Plan:
`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`.


## Key Files

- **`profiles/<car>/ecus/`** — **SOURCE OF TRUTH**, one file per ECU. Each describes one ECU:
  `identity:`, `tx_id:` (request arbitration ID — 11-bit, or a full 29-bit id in a 29-bit mode),
  optional `rx_id:` (response-address override; defaults to `tx_id` + the profile's
  `addressing.rx_offset`, itself `+0x08`, or the byte-swapped id for `normal_fixed_29bit`), optional
  per-ECU `addressing:` (`mode` + the make-specific `target_address`/`source_address`/`fc_id`),
  `can_bus:` (flow list of `can_buses.yaml` codes), optional `wake:` (a per-ECU **wake ritual** for
  a fast-sleeping ECU — resolver `canlib/wake.py`), `scan_log:`, `dtcs:`, `pids:`, `iocontrol:`,
  optional `iocontrol_scan_ranges:`, `routines:`, `research:`. Identity-only modules (AMP/SRS) have
  no `pids:`. A PID may also carry **`response_frames:`** — how many CAN frames its response occupies,
  written back automatically once a session proves it (never hand-set; mutually exclusive with
  `variable_length:`). Edit via `canair pids`/`ecu`; validate with `canair validate pids`.
- **`profiles/<car>/captures/`** — Recorded UDS payloads, one **JSON** file per date
  (`captures/YYYY-MM-DD.json`; JSON parses ~60× faster than YAML, the dominant cost of every
  history-consuming command). The read/write seam is `canlib/capture_io.py`; there is **no YAML
  fallback** — an older profile fails fast and is converted once with `canair captures migrate`.
  Schema `canlib/schema/captures_schema.json` (format-agnostic, validates the parsed structure) with
  matching `TypedDict`s in `canlib/capture_types.py` that the builders/IO are typed against
  (enforced by `ty`). Human companion: `docs/concepts/captures-and-states.md`. **These are
  append-only session logs**, so same-day recordings from two machines union-merge via canair's git
  merge driver (`.gitattributes` rule + `canlib/captures_merge.py`) — but git never loads a driver
  from a tracked file, so each clone must run **`canair captures merge-driver --install`** once;
  until then merges just fall back to conflict markers. A capture's **`rx`** field is the ECU CAN
  *response* address (request TX + 8) — **not** an ECU name; that is why it was renamed from `ecu`.
  Read it via `capture_io.capture_rx` (tolerates the legacy key) and rename old files with `captures
  migrate-rx`. Optional **`elapsed_ms`** is the wall-clock UDS round-trip (transport + ECU, a
  *relative* speed signal), recorded **only for single per-DID reads** on the live `read` path and
  comparable only within one session's `transport`. **Truncated multi-frame reads are rejected at
  capture time** — the reassembled length is compared to the ISO-TP First-Frame declared length, so
  a misaligned payload is never saved (generic; no per-PID length table). **NEVER hand-write or edit
  these files.** Record with `read/monitor … --save --label … --state …`; onboard a device-free
  reading with `canair import uds`; remove with `captures uds --delete` (or the `canlib.captures`
  helpers `set_capture_note`/`delete_capture`).
- **`profiles/<car>/vehicle_states.yaml`** — The ordered, **UPPERCASE** vehicle-state vocabulary,
  each entry optionally carrying a `when:` predicate over decoded `ECU.PARAM` values that
  **auto-suggests** a capture's states. Predicates evaluate with **three-valued (Kleene) logic** — a
  predicate on an unpolled signal abstains rather than reading false — and *every* matching
  predicate contributes, so a session is composite and predicate order is display-only, not
  priority. **A view with one state slot instead takes the most SPECIFIC match**, resolved from each
  entry's optional `implies:` list — an entailment DAG (`DRIVING implies [READY]` means driving *is*
  a narrower reading of READY), not a priority number, so adding a state never renumbers anything.
  `canlib/states.py::most_specific_states` drops every matched state that another match implies
  (transitively); it **preserves the caller's order** and never re-sorts, and file order only
  tie-breaks states unrelated in the DAG. It backs `suggest_state` (and so the monitor status bar);
  `suggest_states` deliberately does **not** apply it, because a recorded session keeps every state
  it was in. `implies:` must name declared states, may not self-reference or involve `ALL`, and must
  be acyclic — enforced by `validate states`, by `states_edit`'s post-write reparse, and by
  `load_states` itself. The make-neutral base is the ignition ladder `SLEEP/ACC/RUN/CRANK`
  (`RUN`/`SLEEP` because `ON`/`OFF` are YAML booleans) plus the `ALL` meta-token; EV states
  (`PLUGGED`/`READY`/`CHARGING`) and finer vendor rungs are **declared per profile**, and
  `allowed_states()` returns base ∪ `ALL` ∪ the profile's own, so `--state`/`--prereq` validate
  without a static choice list. Input is normalized to uppercase. Edit via `canair states` (never
  hand-edit; `canlib/states_edit.py`); references in `ecus/` are written as inline flow lists.
- **`profiles/<car>/can_buses.yaml`** — The profile's CAN bus segment vocabulary: code → human
  `name` + `description` + optional `bitrate`. Bus naming is **vendor-specific** (Hyundai/Kia
  `B-CAN`/`P-CAN`/`C-CAN`/`MM-CAN`/`H-CAN`/`D-CAN`/`ALL`; Ford `HS`/`MS`; BMW `PT-CAN`/`K-CAN`), so
  it lives per profile, not in a global enum. Loader `canlib/can_buses.py`; `pids set-can-bus`
  accepts only declared codes and `validate pids` errors on an out-of-vocabulary value.
- **`profiles/<car>/groups.yaml`** — Named selector groups (`@name`) — see `canair groups`. Loader
  `canlib/ecu_groups.py`. The bundled Ioniq ships
  `@charging`/`@driving`/`@powertrain`/`@climate`/`@body`.
- **`profiles/<car>/signals/` + `captures/can/`** — The raw-CAN broadcast domain (in progress,
  `plans/2026-07-24-raw-can-analysis.md`), both optional. `signals/<bus>.yaml` holds DBC-compatible
  **linear** maps keyed by arbitration ID; `captures/can/` holds imported logs natively, indexed by
  `index.yaml`. Path accessors `Profile.signals_dir`/`.can_dir`/`.can_index_file`.
  **Storage/licensing:** a profile's **own** logs are committed in full — via **Git LFS** when large
  (`.gitattributes` tracks `*.blf`/`*.asc`/`*.trc` + `profiles/*/captures/can/**`;
  `tests/fixtures/**` is excluded so the tiny excerpts stay plain diffable git). **Third-party**
  logs only when their license permits redistribution; **unlicensed** corpora stay fetch-on-demand
  in gitignored `references/can/` plus a minimal fair-use excerpt in `tests/fixtures/can/`. LFS is
  storage only — it grants no redistribution right. Contributors need `git lfs install`. Full
  policy: `docs/concepts/broadcast-frames.md`.
- **`profiles/<car>/references/`** — External reference material (other-vehicle logs, spreadsheets,
  SavvyCAN exports, charge curves). Distinct from the per-ECU `research:` backlog.
- **`profiles/<car>/profile.yaml`** — Profile-wide settings. Only `car_model` + `init` are required;
  `canair validate` type-checks the rest: `response_timeout_ms`, `multi_did_batching`,
  `multi_did_max` (per-ECU overridable), `can_bitrate`, DTC `failure_types:`, plus these blocks:
  - **`addressing:`** — the CAN diagnostic addressing rule. `mode`: `normal_11bit` (default),
    `normal_29bit`, `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention — RX by
    byte-swap, no offset), `normal_extended_11bit` (an 11-bit header plus a per-ECU `target_address`
    byte in the payload — BMW `0x6F1`/PSA), `extended_29bit`. `rx_offset` sets the 11-bit response
    (`tx_id + rx_offset`, default `0x08`; may be **negative** — PSA `-0x20`). Resolver
    `canlib/addressing.py`
    (`resolve_mode`/`resolve_rx`/`resolve_ecu_address`/`build_isotp_address`), with the flow-control
    address override in `canlib/transport/isotp_stack.py`.
  - **`isotp:`** — client-side ISO-TP tuning for `slcan-tcp` (padding, blocksize, stmin, timeouts,
    CAN-FD); defaults in `canlib/transport/isotp_params.py`. The padding byte also drives multi-DID
    split/strip (`resolve_tx_padding`).
  - **`quirks:`** — make-specific behaviour the profile opts into. Known: `hk_f1xx_minus_one` (the
    Hyundai/Kia identity-DID `-1` offset, `22F188`→`62F187`) and `skm_wakeup` (enables the Ioniq SKM
    relay-wake). Resolver `canlib/quirks.py`.
  - **`physical_bands:`** / **`unit_guess_candidates:`** — vehicle-axis overrides for
    `hunt --physical` and make-specific scalings for the slope→unit sniffer. Resolvers
    `canlib/physical_bands.py`, `canlib/unit_guess.py`. (The grid-axis bands come from the
    user-config `grid_region` instead — an 800 V pack or a non-EU grid otherwise gets no hit.)
- **`templates/`** (repo root) — Scaffolds for `profile create`. Placeholders use `string.Template`
  (`$car_model`), so literal braces in YAML/comments need no escaping. **Shipped in the wheel** via
  `pyproject.toml`'s `force-include`, so installed copies can still scaffold. Change the scaffold
  output by editing these files, not Python strings.
- Historical note: there is no top-level `ecus.yaml` or `dtc.yaml` — both were folded into the
  per-ECU `ecus/` files.

## WiCAN Access

Devices live in `~/.config/canair/config.yaml` (a legacy repo-root `config.yaml` is still read;
both gitignored). Copy from `config.example.yaml`:

```yaml
devices:
  home:
    host: "10.0.2.86"       # Device on local LAN
  vpn:
    host: "192.168.3.2"     # Device via VPN
    transport: wican-ws     # optional per-device transport (slcan-tcp | wican-ws)
default_wican: home
```

Each device carries a `host` plus optional `transport`/`port`/`bitrate` (precedence:
`--transport`/`--wican` CLI > device entry > global `transport:` block > default). Select with
`--wican home|vpn|<ip>`; with no config it falls back to `192.168.80.1` (WiCAN AP mode). The legacy
flat `wican_addresses: {alias: host}` form still works when no `devices:` block exists and is
**auto-migrated into `devices:` on first run** (`canlib/devices_migrate.py`); once `devices:` exists
it is ignored. Resolvers: `canlib/config.py::wican_devices` (authoritative map + `DeviceEntry`) and
`fallback_settings`; `wican_settings` is a back-compat shim.

WebSocket terminal: `ws://<ip>/ws` (send `{"ws_mode": "terminal", "terminal_type": "elm327"}`).

**Auto-fallback** — when the selected device is unreachable at connect time, canair tries the other
configured devices (`transport.fallback`, default true; `--no-fallback` per command).
`transport.connect_timeout` (default 5.0s) is the per-device liveness probe and
`transport.fallback_order` sequences the attempts (the selected device is always tried first).
Candidates: `canlib/transport/config.py::resolve_transport_candidates`; connect-time selection:
`canlib/transport/fallback.py::select_reachable_transport`, called before the raw/ELM branch so a
fallback **can cross transports**. A `wican-ws` device is skipped on a `classic` WiCAN. **`canair
status` runs the same probe**, so a diagnosis matches what a live command will do.

**`--wait` (all live commands)** — block on the initial connect, retrying indefinitely until a
candidate appears, then start; so `canair monitor @driving --save --wait` records the moment the
device comes online. Backed by `fallback.py::wait_for_reachable`.

**Mid-session reconnect / auto-failover (`monitor`)** — a session that drops mid-run is **re-homed**
rather than abandoned: it re-probes reachable **same-transport** devices (raw↔raw / ws↔ws — only the
*initial* connect crosses transports), rebuilds the client, re-opens sessions and resumes, with a
`--save` recording continuing on the same journal (the gap shows in the timestamps). Bounded by
`transport.reconnect_max_wait` (default 60.0s); `--wait` retries forever. Infra
`canlib/modes/monitor_reconnect.py` plus the per-transport connect closures in
`commands/_live/connect.py` and `modes/raw_monitor.py`. Plan:
`plans/2026-08-03-monitor-reconnect-and-wait.md`.

**Reconnect is correctness-triggered, not only liveness-triggered.** A session can stop being usable
while **nothing raises**: a half-open socket keeps accepting writes, and a desynchronised ELM327
pipe keeps returning well-formed replies — to the *previous* request, forever. So three mechanisms
sit under the reconnect above, and a new request path must not bypass them:

- **Every request slot is echo-validated**, so a one-slot offset is detectable at all. Pass
  `expected_sid` *and* `expected_echo` (`canlib/uds_parse.py::request_echo`) on any new
  `send_uds` call — an unvalidated request laughs off a desync and returns the wrong PID's bytes as
  the right PID's value. Keepalives are validated too (`3E00` with `expected_sid=0x3E`): an
  unvalidated one *consumes* the orphaned reply and clears the dirty-pipe flag, which is precisely
  how the offset used to become permanent.
- **The ELM327 engine repairs itself** — a `CAT_STALE` response triggers
  `Elm327Terminal._resync()`: drain for a window derived from the adapter's own `ATSThh` budget
  (never hardcoded — a drain shorter than the adapter's ECU wait cannot discard the reply it is
  chasing), then probe with `ATI` and raise `ConnectionError` if the adapter itself goes silent.
  Each repair is tallied as `resyncs` on `TransportStats`.
- **Hold `terminal.transaction()` across `set_header` + `send_uds`.** `_cmd_lock` is per-*command*,
  so without it a concurrent keepalive can retarget `ATSH` between a read's header and its request.
  It is re-entrant per task; `RawTerminal`'s is a no-op (raw addresses every frame explicitly).

**The link is measured, not configured.** `canlib/link_latency.py::LinkLatency` is an RFC-6298
smoother (`srtt + 4*rttvar`) shared by both domains; a plain mean underestimates half the time and
every underestimate orphans a reply. `seed()` takes a one-shot unambiguous measurement (the TCP
handshake, timed in `SlcanTcpBus.__init__` — available *before* any protocol traffic, which is when
the ISO-TP budgets must be chosen); `observe()` needs `_MIN_SAMPLES` and is fed **only** by
adapter-only AT commands (`_LINK_PROBE_CMDS = ("ATI", "ATSH", "ATFCSH")`) — never a UDS read, which
mixes link and ECU and cannot be decomposed. `allowance(floor)` keeps the caller's default as a
floor. Consumers **add** it to the configured value (the config is the *car's* share, the
measurement the *network's*; the delays are sequential): `build_isotp_params(config, link_budget)`
scales `rx_flowcontrol_timeout`/`rx_consecutive_frame_timeout`, `RawUdsClient._budget` the
per-request deadline (a caller-forced `--timeout` is an instruction, not a budget, so it is
untouched), and `Elm327Terminal._resync` the quiet window. Two once-per-session INFO hints are
derived from it rather than from a hostname heuristic: `isotp_params._warn_blocksize_cost` and
`uds_raw._hint_transport_choice`. User-facing guidance:
`docs/concepts/remote-and-cellular.md`; plan `plans/2026-08-08-high-latency-link-hardening.md`.

Two config keys back this: `transport.ws_ping_interval` (default 20s, `0` disables) makes a dead
`wican-ws` link *raise*, and `transport.stale_cycles_before_reconnect` (default 3, `0` disables) is
the backstop — `MonitorController._check_liveness()` reconnects after N poll cycles in which nothing
answered coherently (an **NRC counts as answered**: the reply landed in the right slot). Plan:
`plans/2026-08-08-elm327-pipe-desync-recovery.md`.

**An ELM327 request's odd-length final nibble is an expected-response-frame count**, and supplying
it is the dominant per-read win on the ELM327 transports (measured ~206→53 ms on a WiCAN Pro,
variance collapsing too) because the adapter stops waiting out its `ATST` budget to be sure no
further frame is coming. `canlib/transport/elm327_frame_count.py` owns the whole policy —
`FrameCountCache` the learned counts, `CountAttempt`/`CountVerdict` the per-request decision — while
`Elm327Terminal.send_uds` keeps only the I/O; `transport.expected_responses` (default true) is the
kill switch. **The count is learned, never guessed** — and the asymmetry is the whole design: an
*over*count is merely slow, an **undercount leaves the response's tail queued, which then answers
the next request**, i.e. it manufactures exactly the desync above. Hence four rules a change here
must not break: learn only from a *plain* request whose reply is `ok` (a digit-bearing reply can
only confirm the count it asked for); `learn()` **opts out rather than clamping** above
`MAX_REQUESTABLE_FRAMES` (clamping is a deliberate undercount — the Ioniq's `0x7EA:21F2` needs 13
frames and so must stay unoptimized); a mismatch realigns the pipe on `_DIRTY_PIPE`
(`drop`/`stale`/`decode` — truncation is precisely the queued-tail case) and retries plain **without
charging the caller's `retries`**, so the optimization can cost latency but never a reading; and the
plain retry's *length* is the disambiguator between two very different failures. A retry of a
**different** complete length proves the response genuinely varies, so the count is **retired** —
`opt_out` clears `response_frames:` from the profile. A **same-length** retry (or an NRC) means the
digit-bearing read merely lost a frame in transit — a dirty pipe the resync already repaired, not a
variable response — so only the *session's* digit is dropped (`disable_digit`) and the **durable
count is kept**; deleting a verified count over one transient drop on a flaky link was a real,
reported bug. Attribution still gates both: a *transiently silent* ECU (the retry brings neither
`ok` nor an NRC) proves nothing and keeps the optimization, so one missed read never deoptimizes a
healthy PID. An **NRC counts as held** (a complete answer that just occupies fewer frames), or every
PID an ECU refuses while a session is closed would opt out. Plan:
`plans/2026-08-09-wican-ws-throughput-ceiling.md`.

**A confirmed count is persisted to the profile, so it survives the link that measured it.** The
learned cache is still per-connection (a count measures one link, and a reconnect may have re-homed
onto another device), but the *fact* about the response is a property of the car, so it is written
back to the PID's `response_frames:` in `ecus/` and re-seeded on the next connect — which is what
makes a cold first read fast instead of paying one plain read per PID to re-learn. Plan:
`plans/2026-08-13-persisted-response-frame-counts.md`.

- **`canlib/frame_counts.py`** is the transport-neutral ledger (`FrameCountLedger`,
  `FrameCountRecord`, `frames_for_payload`, and `CountKey` itself). Evidence has two grades:
  `confirm()` (a digit was *held* — a direct test of the count, so `CONFIRMATIONS_REQUIRED = 1`) and
  `observe()` (a count merely seen, needing `OBSERVATIONS_REQUIRED = 3` agreeing sightings). **Any
  disagreement retires the key permanently** and further agreement cannot rehabilitate it: if the
  length varies, every single count is an undercount for some response.
- **`canlib/response_frames.py`** is the bidirectional bridge — `stored_count()` is the **single
  reader** of the field (its `bool` exclusion is load-bearing: `bool` is an `int` subclass, so a
  stray `response_frames: true` would read as a 1-frame count), `seed_counts()` builds the
  `{(tx_id, request): frames}` seed, `resolve_edits()` diffs a ledger against the profile, and
  `persist()` applies the edits. Two deliberate boundaries: `seed_counts` **does not** apply
  `MAX_REQUESTABLE_FRAMES` (the ceiling belongs to the layer that must emit the nibble, so a profile
  fact is never silently rewritten by whoever reads it), and `resolve_edits` **refuses a TX header
  shared by several ECUs** (under `normal_extended_11bit` they are told apart by a target-address
  byte a `CountKey` does not carry) — reported, not guessed.
- **The write-back seam is `Terminal.frame_counts`**, so one implementation covers every transport:
  `canlib/modes/dispatch/__init__.py::run_session_guarded` calls `_persist_frame_counts` in a
  `finally`, so an interrupted or failed session still banks what it proved (Ctrl-C is the *normal*
  way a long `monitor` ends). `--no-learn-frames` opts out. A failure there never propagates — the
  measurement is a by-product of whatever the user actually ran. It is deliberately **not** wrapped
  in the `pids` command's validate-and-revert guard, which would discard a correct edit over
  unrelated pre-existing breakage in the same file.
- **The raw `slcan-tcp` path participates too**, but must *derive* the count: `parse_uds_response`
  counts response *lines* and the raw stack hands it one already-reassembled message, so every raw
  read reported `isotp_frame_count: 1` until `raw_terminal.send_uds` began computing
  `frames_for_payload(len(resp_bytes))` (classic CAN only — CAN-FD carries up to 64 B/frame). It has
  no digit to hold, so its evidence is only ever `observe()`.
- **`requestable()`/`annotate_request()` in `canlib/transport/elm327_frame_count.py` are the single
  home for the wire rules** — even-length request, `1..MAX_REQUESTABLE_FRAMES`, never clamped — read
  by both the live transport and `autopid_profile.request_with_count`, so the two cannot disagree.

## Transports

> **Before changing a transport backend, debugging a desync/timeout, or calling a device API, load
> the wican-hardware-and-protocol skill.** It carries the device ground truth this section assumes:
> that on WiCAN Pro `wican-ws`/`elm327-tcp` reach the bus through a **separate MIC3624/STN2120
> co-processor** while `slcan-tcp` uses the ESP32's own TWAI controller (which the firmware leaves
> *disabled* under `elm327`/`auto_pid`), plus the wican-fw branch/dead-code traps that make a
> wrongly-sourced citation look plausible.

canair reaches the bus through one **explicitly selected** transport (never auto-detected).
Precedence: `--transport`/`--wican` CLI > per-device `transport:` > global `transport:` block >
default `slcan-tcp`. Registry-driven — each is a `TransportSpec` in
`canlib/transport/config.py::TRANSPORTS`, so adding a backend means registering a spec.

- **`slcan-tcp`** (default, `raw=True`) — raw SLCAN over TCP; canair runs client-side ISO-TP/UDS
  (pipelined). Works on any WiCAN (Pro *and* classic) or gateway; also powers `canair sniff`.
  Backend `RawTerminal` (`canlib/transport/raw_terminal.py`) over `SlcanTcpBus`.
- **`wican-ws`** (Pro-only, `raw=False`) — ELM327 terminal over the WiCAN's `ws://host/ws`; the
  dongle does ISO-TP. Backend `WiCANTerminal` (`canlib/terminal.py`).
- **`elm327-tcp`** (`raw=False`, `wican_http=False`) — a **generic ELM327 adapter over a plain TCP
  socket**: WiFi clones (Kiwi, vLinker, OBDLink) and the ELM327-Emulator's `-n` mode. No WiCAN, no
  HTTP config API. Backend `Elm327TcpTerminal` over a `TcpChannel`; default port 35000.

**Shared ELM327 engine.** The protocol logic lives once in `Elm327Terminal`
(`canlib/transport/elm327_terminal.py`), driven by a swappable async byte `Channel`
(`canlib/transport/channel.py`: `WebSocketChannel` for the WiCAN, `TcpChannel` for a clone).
`WiCANTerminal` (`canlib/terminal.py`) and `Elm327TcpTerminal` (`canlib/transport/elm327_tcp.py`)
are thin subclasses wiring their channel — a new ELM327 wire (e.g. serial) is a new `Channel`, not a
duplicated engine. **The engine keeps the I/O; three sibling modules own the stateful policies it
used to inline**, each exercisable with no channel at all — so put new protocol policy in a sibling,
not in the terminal:
- **`canlib/transport/elm327_pipe.py::ResponsePipe`** — the `>`-prompt ledger (owed prompts,
  carry-over of a half-arrived block, the dirty-pipe flag, ResponsePending). `carry` deliberately
  *prefixes* the next collection, so a reply whose prompt merely arrived late is reassembled whole.
- **`canlib/transport/elm327_frame_count.py`** — the expected-response-count digit:
  `FrameCountCache` (learn/opt-out) plus `CountAttempt`/`CountVerdict`, the per-request decision
  procedure. See the frame-count paragraph under WiCAN Access for the four rules a change here must
  not break.
- **`canlib/transport/elm327_session.py::enter_extended_session`** — session open, the wake ritual,
  the TesterPresent keepalive loop. **`canlib/transport/raw_terminal.py` has its own, divergent
  copy** (shorter timeout, no retry, an unvalidated + exception-swallowed keepalive); unifying them
  changes raw-path behaviour, so it is a deliberate open item, not shared code you can assume.

The engine exposes `drain()` + `recv_frame(timeout)` so modes that collect late frames go through
the transport-agnostic surface, not a raw socket. All three backends satisfy the `Terminal` protocol
(`transport/protocol.py`) and run through the shared `dispatch_mode`. ELM-only features (`repl`,
`skm-wake`) gate on `isinstance(terminal, Elm327Terminal)`, so they work on `wican-ws` **and**
`elm327-tcp` but are refused on raw `slcan-tcp` (which parses no ELM text).

**`is_wican_http`** is spec-driven (`TransportSpec.wican_http` + a host), **not** `host is not
None`: true only for the WiCAN transports (queryable `/load_config`, `/check_status`), false for
`elm327-tcp`. It gates the WiCAN-only paths in `async_main` (`_live/runtime.py`: sleep banner,
`reboot_wican`, `require_ws_reachable`) and `canair status`. The ELM connect factory is
`connect_elm_terminal` (`_live/connect.py`); the direct-ELM pre-check is
`require_elm327_tcp_reachable`. Fallback liveness probes port 80 for WiCAN-HTTP, else the data port.

**Device sleep** is a **`wican-cli`** concern — a *separate* package, not canair
(`wican sleep --disable`/`--enable`). canair only reads sleep/voltage state via `canair status`.

**Offline testing** — point `elm327-tcp` at
[ELM327-Emulator](https://github.com/ircama/ELM327-emulator) (`elm -n 35000`) for a device-free
ELM327 wire. It is **not** a canair dependency (its legacy build breaks `uv sync`; install manually
with `uv pip install "setuptools<80"` then `uv pip install --no-build-isolation ELM327-emulator`).
The opt-in test `tests/test_elm327_emulator.py` auto-skips when absent, so the core suite stays
device-free, and the bundled test profile `tests/fixtures/profiles/elm327-emulator/` (an ENGINE ECU
at 0x7E0 with standard OBD-II Mode-01 PIDs) makes `canair read` decode against it. Docs
`docs/development/offline-testing.md`; plan `plans/2026-08-03-elm327-direct-transport.md`.
