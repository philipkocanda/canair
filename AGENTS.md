# canair — agent guide

canair is a vehicle-agnostic CAN/UDS reverse-engineering CLI. The bundled reference car is a 2017
Hyundai Ioniq Electric (`profiles/hyundai-ioniq-ae-ev/`). For reference, the WiCAN firmware is
checked out in `wican-fw/` (gitignored; pull if you need the latest).

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
  `search`/`coverage`/`research`. **Edit definitions** with `pids`/`signals`/`states`/`groups`/
  `ecu` — surgical, schema-validated, comment-preserving.
- **Always pass `--save`** (with `--label`, `--state`, `--notes`) when reading from the device, so
  every payload lands in `captures/`. Works with `read`/`monitor`/`scan`/`raw`/`discover`. Saves are
  **journaled** (write-ahead log in `captures/.journal/`) and reconciled on exit, so a
  killed/crashed session is never lost — recover leftovers with `canair captures uds --recover`.
- **Every write prints the capture file it landed in** (`canlib/captures.py::saved_banner`) — read
  it back to confirm which profile got the data. A save made *inside* the monitor TUI has its banner
  deferred to exit, since Textual owns stdout while the app is up.
- **`monitor` records every poll (`--keep-all`) by default**, so a stored-row count measures the
  polling rate. Much of the history is `keep:changes` (run-length, the previous default), where a
  row count measures **volatility, not sampling**; `--keep-changes` still records that way. The
  keep-mode tradeoffs and the joins' forward fill are in the **reverse-engineer-signal** skill.
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
> **ioniq-reverse-engineering**; changing canair's code is **contributing-code**; whether a car
> needs its own profile and how to name one is **profile-variants**. Deep
> protocol/transport/device-API work (the WiCAN Pro hardware, its ELM327 co-processor, the
> firmware's modes and API) is **wican-hardware-and-protocol** — not needed for ordinary work.

All functionality is one CLI, `canair` (invoked as `uv run canair` here). **Flags and subcommand
lists are in `canair <cmd> --help` and `docs/reference/cli/<cmd>.md`** — the entries below give each
command's purpose plus the agent-facing rules and code pointers that `--help` does not carry.

**TUI keys are not in `--help`.** Every Textual view's keymap is assembled from the shared role
registry **`canlib/tui/keys.py`** (`ROLES` + `bind(role, action)`), so one key means one thing in
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
  Ctrl+C; **a bare `canair monitor` on a TTY opens a group/ECU → PID checklist first**
  (`canlib/modes/monitor_select.py` = pure choice/selector logic,
  `_monitor_select_tui.py` = the screen; it needs only the profile, never the device, and prints
  the equivalent command; saved `@groups` are listed above the ECUs and expand to their ECUs and
  PIDs). **The running monitor's `p` key opens that same screen** on what is polled now
  (`LiveMonitorSelectScreen`, backed by `_monitor_selectors.py`), so there is one picker, not
  two. Non-TTY with no STEP is still an error. **`read` never had a
  `--monitor` flag.** `--wait` blocks until the device is online and makes a mid-session drop
  reconnect forever (see Mid-session reconnect). **Polling is per-PID by default**
  (`--schedule dynamic`): each PID is read at half its profile `period:`
  (`MONITOR_PERIOD_SPEEDUP`), a PID without one every `--interval` (`--schedule fixed` reads
  every PID every cycle). A slow or silent ECU holds up every other ECU's next read, so the
  leaf `canlib/poll_backoff.py::EcuBackoff` slows it (×2 up to ×32, `--no-backoff` off) from
  per-ECU `PollCost`s the pollers leave in `MonitorController.ecu_costs`. The scheduler is a
  dependency-free leaf, `canlib/poll_schedule.py::PollScheduler` (the parser imports its
  vocabulary at CLI start-up, so it must not reach into `canlib/modes`); it drives which PIDs each
  tick reads and, since a slow PID is now polled far less often than a fast one, also supplies the
  per-signal *clock* the state-suggestion staleness is counted on (polls of that PID, not ticks of
  a faster neighbour).
  The monitor records only the PIDs actually read on a tick (`MonitorController.fresh_queries`),
  while the display keeps every selector's last value.
- **`canair scan`** — Kinds: `range` (bare `canair scan <ECU>` is this), `sessions`, `routines`
  (UDS `0x31` SF03 / KWP `0x33`, auto-selected by `id_protocol` so it never blind-sends
  StartRoutine), `iocontrol` (returnControlToECU: UDS `0x2F` / KWP `0x30`). All are **safe
  probes** except a KWP `0x30` sweep, which confirms first (`--yes`; Nissan profiles get a
  non-actuating query instead). Hits are written to the ECU's
  `routines:`/`iocontrol_discoveries:`. `range` takes a comma list of ranges
  (`canlib/id_ranges.py`); without `--range` it sweeps the ECU's **DID families**
  (`canlib/did_families.py` — high bytes from its `22`/`2F` PIDs and IOControl hits), not the
  span between them.
- **`canair discover`** (alias `disc`) — Which ECUs answer right now; `--register` writes a new ECU
  into `ecus/`. **`--functional`** sends one read on the functional id instead and groups every
  responder by `can_bus:` (the gateway routing map). Its request is an **allow-list**
  (`canlib/safety.py::check_functional_request`, never widened by `--unsafe`); reception is
  `Terminal.send_functional` over the pure collector `canlib/transport/functional.py` (raw:
  `functional_raw.py` sends flow control to each responder's *physical* id via
  `addressing.physical_request_id`; ELM: `elm327_functional.py` brackets the request with
  `ATH1`/`ATH0` in one transaction, tracked by `AdapterState.headers`); classification is
  `canlib/modes/functional_sweep.py`.
- **`canair identity`** (alias `id`) — Identity DIDs/records (UDS `22 F1xx`, KWP2000 `1A 8x/9x`),
  printed only: nothing is written to the ECU's `identity:` (which can hold a live VIN when
  filled by hand or `ecu add` — see PII).
- **`canair io`** / **`canair routines`** — IOControl (`0x2F`, or an entry's own KWP `0x30`
  commands) and RoutineControl (`0x31`): interactive TUI or a single actuation. **Mutative** —
  confirm-first, and actuators auto-release when the session ends, with each entry's resolved
  `release` (`canlib/pids.py::build_iocontrol_index`). UDS implies `2F {DID} 00`; KWP `0x30` has
  no standard release, so `canair io` refuses a `0x30` ON until the entry declares `release:`
  (`pids set-iocontrol --release`). That gate is `canlib/actuation.py::on_refusal`, over the
  read/release/actuate classifier the actuation interlock plan
  (`plans/2026-10-22-actuation-safety-interlock.md`) will build its funnel on. Never declare a
  release you have not seen hand the output back on the car.
- **`canair action`** — List, run and edit the profile's guarded, make-specific actions
  (`actions.yaml`; `action run NAME [--dry-run] [--yes]`). Model, rules and loader
  `canlib/actions/`, editor `canlib/actions/edit.py`, live run `canlib/modes/action.py` (its
  docstring fixes the plan → check → confirm → re-check → send order). Two rules a change must
  keep: **every request still passes the blocklist** — a refused one goes out only when a quirk
  grants exactly that `(tx_id, request)` (`canlib/quirks.py::action_grants`), applied through
  `SessionPolicy.granting` for the action's own send and never by `from_profile`, so `read`/`raw`
  steps still refuse it and `--unsafe` plays no part; and **every action is guarded** — mandatory
  `reads`/`require`, only TRUE passes, no `or`/`not`, only read ECUs. The Ioniq's `sleep` sends
  the functional `10 D1` (quirk `hk_forced_sleep`); untested on the car.
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
  decode failures (classified by `canlib/uds/parse.py::classify_response` into
  `drop`/`stale`/`no_data`/`bus`/`decode`) plus unexpected internal exceptions. Written by the
  per-exchange recorder `canlib/transport/stats.py::TransportStats` (attached as `.diag` on both
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
  state, label, notes, ECUs, the car's `variant` tag (or `untagged` on a multi-variant profile),
  acquisition `transport`, recording `version`, a `quality` line; no payloads).
  **The default list view is capped at `--limit N` (default 50) with a loud footer** for hidden
  history, so a bare query can't blow the context window; widen with `--limit 0` or scope tighter.
  Mutating modes: `--delete` (refuses a bare `--delete`, previews with `--dry-run`, confirms unless
  `--yes`), `--backfill-states` (offline state inference, `canlib/states/infer.py`; fills unset
  states and *reports* contradicted ones unless `--overwrite`), `--set-state` (manual counterpart —
  **requires** a scope filter so it can't blanket-relabel history),
  `--set-notes`/`--set-transport`/`--set-variant` (correct a session's notes, recorded transport or
  car, same scope rule — the global `--variant untagged` counts as one; one editor in
  `commands/captures/session_fields.py`, and every `--set-*` shares
  `session_gate.py::refuse_write` for the base-layer refusal and confirmation), `--recover`, and
  the one-time `migrate` (YAML→JSON) / `migrate-rx` (`ecu`→`rx`) / `migrate-discover` (gives legacy
  discover responders their `tx`; `canlib/migrations/discover.py`) / `split-batched` (splits a
  multi-DID reply stored whole under its first DID into per-DID rows;
  `canlib/uds/unsplit_batches.py`) / `compact` (rewrite in the folded on-disk form;
  `canlib/migrations/compact.py`) / `migrate-variants ID` (tag untagged history with the car it
  came from, attributed `migrated`, never retagging; `canlib/migrations/variants.py`) /
  `merge-driver` (see Key Files).
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
  co-polled anchor, triage class from `canlib/analysis/triage.py`, physical band, probable
  multi-byte words). **ECU and PID are both optional** — omit one or both to sweep an ECU or the
  whole profile (`canlib/commands/investigate/sweep.py`). `--events`/`--dwell`/`--bits` are the
  narrated and bit-level views. **`--counters`** finds monotonic counters (odometer, hour meter,
  cycle count): detection is the leaf `canlib/analysis/counters.py` (numpy-free, so the raw-CAN
  domain can reuse it) and the capture-model bridge is `canlib/commands/investigate/counters.py`,
  which aligns a **prefix** payload matrix — aligning on the *modal* length would silently discard
  every capture of a minority length. Plan: `plans/2026-08-07-monotonic-counter-detection.md`.
  **`--support-map`** finds the constant leading bytes some body/climate DIDs use to mark which
  following bits (or bytes) are real signals: leaf `canlib/analysis/support_map.py` (scoring and
  the one marking rule), bridge `canlib/commands/investigate/support_map.py`. **It is per-PID,
  not make-wide** — BMS/VCU/MCU service-21 pages and the chassis DIDs do not fit, and BCM `22B005`
  is a documented exception — so a map is *declared* (`pids set-support-map`), never inferred at
  read time. Sibling DIDs of one ECU that share a placement corroborate each other, because one DID
  with few moving bits cannot be significant alone. Plan:
  `plans/2026-10-29-support-bitmap-coverage.md`.
- **`canair search`** — "Where in my captures is this string?" Searches every payload (and, by
  default, labels/notes/text responses; `--defs` adds `ecus/` identity fields) for a literal,
  `--regex`, `--hex` byte pattern or `--vin`, **without knowing the ECU or PID**; with no pattern it
  inventories every plausible string. Findings are grouped by *location* (ECU, PID, offset), not by
  capture, each with a paste-ready `pids upsert-param --type ascii` command. Two pitfalls: the
  plausibility filter (`canlib/textscan.py::is_plausible_token`) **is** the inventory — numeric
  telemetry is often printable, so `--all-runs` is mostly noise — and the VIN-shape test is shared
  with the `contribute` PII gate (one definition, in `textscan`). Only the `uds` kind exists; the
  leaf primitives are `canlib/textscan.py`, the command is `canlib/commands/search/`. Plan:
  `plans/2026-08-07-search-all-captures.md`.
- **`canair coverage`** (alias `cov`) — Audits definitions for decoding gaps: **UNMAPPED** data
  bytes, incomplete **BITS**, **NO CAPTURE** PIDs. Bit coverage counts both models (the
  `Bn:k`/`Sn:k` accessor and a `type: bitmask` param's `bits:` keys) and a whole-byte read does
  **not** suppress the bit-gap report. One known blind spot: an `&`-mask expression contributes no
  bit references, so **author bits as `Bn:k`, never a mask**. A PID with a declared
  **`support_map:`** (`canlib/uds/support_map.py`) stops reporting its map bytes and the bytes the
  map marks as carrying no signal as **UNMAPPED**, counts only marked bits as **BITS** gaps, and
  adds **OUTSIDE MAP** for a signal reading a position the map does not mark. No declaration, no
  change to the output.
- **`canair research`** — The open RE backlog from the per-ECU `research:` sections. Complements
  `coverage` (undecoded *bytes*) by surfacing *planned* work. **`--gaps`** derives unplanned work:
  IOControl DID families no `0x22` read has reached (leaf `canlib/did_families.py`, evidence from
  `scan_log:`/`research:`/saved scans in `commands/research/gaps.py`). An `iocontrol_discoveries:`
  entry that only drew an NRC is not evidence: the DDM refuses whole ranges with one NRC.

**Shared flags are declared once so their defaults cannot drift** — when adding an analysis command,
reuse these instead of re-declaring a flag:

- `--join-tol` / `--fill` / `--max-hold` — `canlib/commands/_join.py::add_join_args`, used by
  `align`/`correlate`/`hunt`/`investigate`/`decode` (`captures uds --step` deliberately keeps its
  own wider *viewer* tolerance). Fill policy is `canlib/analysis/fill.py`, resolved at series-build
  time so the join primitives never learn what a keep mode is; a value is never carried across a
  session boundary, and every fill is reported. Plan:
  `plans/2026-08-05-run-length-forward-fill-joins.md`.
- `--find-mirrors` / `--mirror-match` / `--allow-offset` — one matcher, `canlib/analysis/mirrors.py`,
  behind three sweeps (`decode` intra-PID, `correlate` cross-ECU, `correlate can` cross-ID).
- `--notation {wican,isotp,torque,bix}` — **display only**; `--json`/`--promote` always emit the
  canonical WiCAN expression. A byte is modelled in ISO-TP space (`canlib/uds/notation.py::ByteRef`,
  rendered via `ByteDisplay`); WiCAN/Torque/bix are derived views. Persist a default with
  `config set display.byte_notation`.
- Scope flags (`--since`/`--until`/`--date`/`--today`/`--state`/`--label`/`--last-session`/
  `--first`/`--last`) — `canlib/captures/dates.py::add_scope_args`, shared by `captures` and every
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
  `canlib/expression/decode_value.py`; the WiCAN `expression` stays the pure float.
  **A PID can state `unit`/`ha_class`/`min`/`max`/`verified`/`source`/`source_links` once
  for all its parameters** (`pids set-pid-defaults`; `pids hoist-defaults` folds up existing
  repetition): a parameter that sets none of its own inherits it, and one that does (even to
  `""`/`[]`/`false`) keeps its value. The list is `canlib/pids.py::INHERITED_PARAM_FIELDS` and
  `merge_ecu_documents` resolves it into every loaded parameter, so **readers of loaded
  definitions never see the gap — only code that parses the YAML itself** (the validator, the
  explorer export, the editors) **must call `effective_param`**. PID `notes` are page-level and
  deliberately not inherited. **State a shared value once, never per signal:** whatever holds
  for the whole page (those fields, and a provenance or method note) goes on the PID, and a
  signal's own `notes` carry only what is specific to it — never the source, a restated name or a
  byte order the expression already shows. A bulk seed (from OBDb, OVMS, a Torque sheet) writes
  the PID's defaults and note *first* and gives a signal its own value only where it differs
  (`""` to opt out of an inherited unit); `pids hoist-defaults --dry-run` must then report
  nothing. The seeding workflow is in the **reverse-engineer-signal** skill. Free-text `notes`
  auto-format via `canlib/yaml_rt.py` (short inline, long folded `>-`) — pass plain strings, never
  hand-format block scalars.
- **`canair signals`** — The domain-B analogue: view/edit the broadcast sidecar `signals/<bus>.yaml`
  (arbitration ID → named **linear** signals: start bit/length/byte order/signedness/scale/offset/
  unit). Surgical, validated (`canair validate signals`), auto-reverted (`canlib/signals_edit.py`).
  **Never hand-edit `signals/`.** Distinct from `pids` (freeform WiCAN expressions over `Bnn`); this
  is the DBC-compatible linear model, so `--start-bit` is DBC numbering (the MSB for big-endian).
  The field list and structural check live once in `canlib/validation/signals.py`
  (`SIGNAL_FIELDS`, `check_signals_doc`), shared by the editor and the validator. The schema also
  accepts the typed-decode fields, but CLI flags to author them are a follow-up (typed broadcast
  signals are hand-written in YAML for now).
- **`canair ecu`** — Inspect the ECU registry and per-ECU stats (PIDs/signals/verified counts,
  protocol, addresses, CAN bus, `--states`). `ecu <ECU> pids` lists every PID with its **latest
  decoded state**. **`ecu add TX …`** registers an ECU offline (no device) — the counterpart to
  `discover --register`, and how you seed a known ECU into a blank profile (including a non-standard
  `--rx-id` or a 29-bit/extended addressing block in one valid write). **`ecu <ECU> rename NEW`**
  renames an ECU — its name *is* its top-level YAML key plus its `ecus/<name>.yaml` filename, so the
  rename rewrites the key and moves the file (validated, comment-preserving); use it to promote a
  placeholder like `Unknown-7D5` once identified (it also renames the ECU's key in variants'
  `revisions:` and in capture revision tags). **`ecu revisions ECU …`** and **`ecu set-variants`**
  edit an ECU's revisions and presence (see `canair variants` below). **`ecu <ECU> edit`** opens
  `$EDITOR` and is **TTY-only — agents cannot drive it**; use `pids …`/`ecu add` instead.
- **`canair states`** — List/edit the profile's vehicle-state vocabulary (`vehicle_states.yaml`);
  `canair states <STATE>` is the reverse lookup (which ECUs are readable in that state, via
  `canlib/states.py::ecus_in_state`). A state with a `when:` predicate is marked ● — or **`✗` with
  the reason** when the predicate references a signal the registry doesn't define, so a dead
  predicate can't hide behind a healthy ●. `set-implies NAME [STATES]` (and `add --implies`)
  declares the **specificity hierarchy** — see `vehicle_states.yaml` under Key Files; the list view
  shows it as a dim `specializes:` line and `canair states <STATE>` also reports the reverse edges
  (`specialized by:`). **Never hand-edit `vehicle_states.yaml`** (`canlib/states/edit.py`) — its
  editors also retarget/drop sibling `implies:` references on `rename`/`remove`, which a hand-edit
  would leave dangling (and a dangling target fails the next validate). **`states rename` rewrites
  every reference in the profile** (`canlib/states/rename.py`): `ecus/` tags, capture sessions'
  states and `state_spans`, the DTC log and the raw-CAN index, all or nothing. A state *is* its
  token, so a vocabulary-only rename would leave the whole history out of vocabulary. `canair
  states <STATE>` lists an ECU through `implies:` too (an `[IG3]` ECU appears under READY).
- **`canair groups`** — List/edit `groups.yaml`, the named selector groups recalled with the `@`
  sigil. Expansion is **textual, before the query parser**
  (`canlib/ecus/ecu_groups.py::expand_group_refs`), so a group composes with other groups and with
  ad-hoc selectors. Members are plain selectors (never other groups, never full pipeline steps) and
  carry
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
- **`canair variants`** — List/edit `variants.yaml`, the versions of the profile's car an owner
  can name (battery, facelift, market) with typed metadata, which ECU revisions each car can carry
  (`revisions:`, `set-revisions`) and which `profile.yaml` settings differ for it
  (`profile_overrides:`, a whitelist applied by `Profile.meta`). The rest of the design is
  `plans/2026-10-30-profile-variants-and-rename.md`, phase by phase. The field table and the
  structural check live once in `canlib/variants/__init__.py` (`VARIANT_FIELDS`,
  `PROFILE_OVERRIDE_FIELDS`, `check_variants_doc`), shared by the loader, `validate variants` and
  the editor (`canlib/variants/edit.py`, which types each command-line value from that table).
  `rename` rewrites every tag and ECU `variants:` list that uses the ID and `rm` refuses while one
  does (`canlib/variants/references.py`).
  **ECU revisions** (`canlib/variants/revisions.py`) are versions of one ECU that differ in
  protocol or decoding: an ECU's `revisions:` mapping (overrides from a whitelist plus
  `fingerprints:`, checked but not matched yet), `revisions: [..]` gates on PIDs and signals,
  and `variants: [..]` for ECU presence. **They are resolved once, at the load seam**:
  `load_pids()` (no path) returns definitions already filtered and overridden for the selected
  variant and each ECU's revision — `--revision ECU=ID` > `profile_settings.<p>.revisions`
  (`profile use --pin`) > the only possible one > *undetermined*, which skips the ECU's gated
  PIDs/signals with one stderr note. `Profile.cache_key` covers the variant and the pins;
  `load_raw_pids()` is the as-written loader for variant-aware code (validate, the editors), and
  `load_pids(path)` stays raw too. A profile without revisions or presence gets the raw objects
  back untouched. Sessions are tagged `revisions: {ECU: id}` for captured multi-revision ECUs,
  analysis drops sessions tagged with another revision of a determined ECU
  (`tags.revisions_in_scope`), and the byte-level analyses refuse a scope spanning two revisions
  (`tags.require_one_revision`, via `load_signal_captures(one_revision=…)`). Editors: `ecu
  revisions` / `ecu set-variants` (`canlib/ecus/revisions_edit.py`, whose `rename` rewrites
  gates, variants and capture tags) and `pids set-revisions`. The regression fixture is
  `tests/fixtures/profiles/variants/`.
  **`per_revision:`** on a PID or signal replaces whitelisted fields for one revision
  (`revisions.py::PID_OVERRIDE_FIELDS`/`SIGNAL_OVERRIDE_FIELDS`), applied after gating. **A
  definition's own `verified`/`response_frames` speak for its *reference* revision** (the first
  declared one it applies to, `reference_of`); another revision gets only what its
  `per_revision` entry says, and an undetermined ECU keeps no count and verifies nothing. The
  frame-count write-back routes the same way (`response_frames.py::revision_target`: base for
  the reference, `per_revision.<r>` otherwise, nothing when undetermined), so one revision's
  count can never seed — and undercount — a read of another. `pids upsert-param --revision R`
  writes `per_revision.R` on an existing signal and gates a new one to `[R]`.
  **Detection** ranks after "only one" and before undetermined: `run_session_guarded` calls
  `canlib/modes/revision_probe.py::detect_revisions` before dispatch, which sends each
  ambiguous ECU the current `read`/`monitor` steps target its cheapest `responds:` fingerprint
  (matcher: the I/O-free `canlib/variants/fingerprints.py`), remembers a single match in user
  state (`canlib/variants/detected.py`, carried as `Profile.revision_detections` and in
  `cache_key`) and reloads the definitions. A determined ECU is never probed. `canair identity`
  reports the matching revision and the variants allowing it (`identity_report`).
  **The variant names the car**, resolved by `canlib/variants/selection.py::apply_variant` —
  `--variant` > `CANAIR_VARIANT` > `profile_settings.<profile>.variant` (written by `profile use
  NAME --variant ID`) > the variant an old profile name implies (`aliases:` entry, via
  `Profile.alias`) > the only variant. **No default.** It is applied by `set_active`/`active`,
  never by a bare `resolve_profile`, and carried as `Profile.variant`/`variant_source`.
  **`require_variant` refuses** — listing the variants and how to pick — every command that
  talks to the car (`_live/runtime.py::run_live`, except `discover`/`identity` without `--save`),
  `sniff` and AutoPID generation (`wican._generate`), on a profile with several variants and none
  named. The suite pins its car in conftest's isolated config. It **tags captures**: every save
  funnels through `captures.save_session` and the journal's meta, both via `current_capture_tag`
  (only for the active profile's own store; the journal fixes the tag at open so a later `--recover`
  cannot retag; attribution from the source — `selected`, `alias`, `only`). It **scopes analysis**
  inside `load_all_captures` (only when no explicit `--dir`): the car's sessions plus untagged ones
  read as it, or with no car every tagged session and no untagged ones (a once-per-process stderr
  note says which); `all`/`untagged` are scope keywords from the flag or env only. `validate
  captures --max-untagged N` is the CI ratchet. An untagged copy of a tagged session is the same
  session with less information: the merge driver, the contribute overlay and the layered read keep
  the tagged one (`canlib/variants/tags.py::strip_tag`). Imports (`import uds`, `import can`) on a
  multi-variant profile **require the flag, not the env var** — it is someone else's car
  (`selection.require_named_car`).
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
  `canlib/states/refs.py`. The same check runs as a **non-blocking warning** from `states add
  --when`/`set-predicate` and `pids rename-param`/`rm-param`. **Every target errors on a repeated
  YAML key** in the files it checks (the loaders, ruamel included, silently keep the later value;
  `canlib/yaml_io.py::duplicate_keys`, message in `commands/validate/_common.py`). **`validate
  pids`** also errors on a duplicate *shipped* signal name across PIDs (each becomes a distinct
  device signal), on a
  hand-built word whose lower byte is read signed (`(S15 << 8) | S17`; structural lint over the
  evaluator's own grammar, `canlib/expression/lint.py`) and on an
  `identity:` field outside the schema's `identity_fields`, and warns when two *synonymous* identity
  fields hold the same value (dead mirrored data). **`validate captures`** soft-warns on
  out-of-vocabulary states, SID/DID **echo mismatches** (a misfiled payload), **cross-ECU misfiles**
  (a reply that looks like another ECU's answer to the same PID, which the echo check cannot see;
  cross-file pass, detection in `canlib/analysis/misfiled.py`), **unsplit multi-DID replies** (a
  batch reply starts `62 D1`, so it passes `D1`'s echo check; same corpus load via
  `canlib/captures/payload_corpus.py`, detection in `canlib/uds/unsplit_batches.py`, reply walk in
  `canlib/uds/multi_did.py`, which the live batched read paths also use to discard one), non-hex
  payloads, **untimed payload captures** (excluded from every time-aligned analysis) and
  **degraded-transport sessions** (recorded drops/stale frames). **`--max-untimed N` is the CI
  ratchet**: fail when the count *grows*. The write path now enforces a timestamp so the count
  only falls — CI gates each profile at its baseline (`.github/workflows/ci.yml`), lowered
  opportunistically. `--strict` forbids any untimed row.
- **`canair wican`** — Build/sync the device's AutoPID profile from the active bundle's `ecus/`. A
  bare `canair wican` prints help (writes nothing). `autopid write` generates JSON into `out/`
  (`out/<variant>/` on a multi-variant profile, from the car's resolved definitions, and
  **refused while an ECU's revision is undetermined** — the device can't detect one), **one
  file per firmware generation**: `autopid-v4.json` (the 4.x vehicle profile) and
  `autopid-v6.json` (the v6 AutoPID table, `canlib/wican/autopid_v6.py`). Both render one
  shipping decision, `autopid_profile.py::ship_profile`, so they cannot disagree on what
  ships; the v6 renderer refuses, never truncates, a value the firmware's fixed buffers
  would cut short, and `validate pids` errors on a shipped expression v6's checker refuses
  (`expression/lint.py::firmware_grammar_problems`)
  — **verified-only by default**, `--include-unverified` also emits in-progress candidates;
  `upload`/`download`/`diff` sync the device (Pro-only, same verified-only default). **Each
  PID's persisted `response_frames` count is appended to its request string by default**
  (`--no-expected-responses` on `write`/`upload`/`diff` opts out), so the device's ELM327
  co-processor answers as soon as that many frames arrive instead of waiting out its `ATST96`
  ~614 ms budget. Safe by default only because the **firmware has no desync recovery** — it
  accumulates into one static buffer cleared only *after* a parse, so an undercount's queued tail
  would silently prefix the next PID's response — so the emit path goes through
  `elm327_frame_count.requestable()` and skips anything unproven, `variable_length`, over the
  9-frame ceiling, or on an odd-length request. `autopid stats` shows a `Frames` column so you can
  see which PIDs have earned one. **A signal with `send_to: TOPIC` streams to that MQTT topic on
  every poll** (emitted as `type: MQTT_Topic`; rule in
  `canlib/wican/autopid_send_to.py`), because the batch snapshot's `cycle` (10 s default) is **device
  config canair does not manage** — a faster `period` alone never reaches the broker faster.
  `mode set MODE` switches the device protocol and **auto-aligns the config `transport.type`**
  (`slcan`→`slcan-tcp`, `elm327`→`wican-ws`; `--no-transport` opts out).
- **`canair bix`** — Byte-index converter and payload annotator (WiCAN ↔ ISO-TP ↔ Torque ↔ bix). A
  bare `canair bix` prints a guided overview, `--table` the full table grouped by CAN frame, and
  **`--annotate HEX` (`-a`)** maps a real payload byte-by-byte with a `Role` column (`--ecu`/`--pid`
  overlays which signal maps each byte and flags `unmapped` ones). **Never convert a byte index by
  hand** — the mapping depends on the response's *length* (one vs two PCI bytes) and its *service*
  (header width and field order), which `bix` reads off the payload; the skill's byte-index
  reference explains the trap. Code map: roles come from **`canlib/uds/layout.py`** (a `response_SID
  → [(role, width)]` table over the `canlib/uds/services.py` registry), which also owns
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
  **`import uds --from-log FILE`** is the bulk form: it extracts every data-read reply
  (`62 DID` → `22DID`, `61 LID` → `21LID`) of a registered ECU from a frame log of a diagnostic bus
  that some *other* tool was polling, each with its own timestamp (`--date`/`--time` stamp the first
  reply; the log's own clock does the rest). Always `--dry-run` first. The pieces: the passive
  ISO-TP state machine is the leaf `canlib/uds/isotp_passive.py` (a lost frame costs exactly one
  message, counted once, never repaired), pairing and stamping are `canlib/captures/from_log.py`,
  and the CLI is `commands/import_uds/log.py`. **Replies are filed by their own echo, never by the
  last request** (a tool that pipelines requests or interleaves a scan mis-files them); the request
  is tracked only to skip a *batched* `22 D1 D2 …` read, whose reply cannot be split safely, and to
  file an opt-in (`--keep-nrc`) NRC. Lost frames land in the session's `quality.drop`. Normal
  addressing only; `--until-ts` exists because public logs go intermittent part-way through.
- **`canair import can <FILE>`** — Domain B: reads a raw broadcast frame log
  (`.asc`/`.blf`/python-can `.csv`/candump `.log`/`.trc`/SavvyCAN GVRET `.csv`; `--format auto` by
  extension, `.csv` header-sniffed) via python-can's readers, stores it **verbatim** in
  `captures/can/`, and appends a metadata entry to `captures/can/index.yaml`. High-volume logs stay
  native — they are never exploded into per-day capture files.
- **`canair import dbc <FILE>`** — Imports a DBC's broadcast definitions into `signals/<bus>.yaml`
  (cantools with `strict=False`, to tolerate real-world overlapping-signal DBCs; `--dry-run`
  previews). A DBC cantools rejects as written goes through the in-memory dialect repairs of
  `canlib/dbc_repair.py::load_dbc` (each reported with its line); add a new quirk there, never
  as a pre-edited copy of the file. Module note: `commands/import_.py` (`import` is a keyword)
  with `NAME="import"`; the `uds` kind lives in the `commands/import_uds/` package. Fetch the
  reference corpus with `scripts/fetch_can_corpus.py` (into gitignored `references/can/`).
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
  directory, which is how eight stale capture files once rode along in a PR. **Leftovers are
  cleared before branching:** an earlier run's uncommitted copy makes `checkout -B` refuse
  whenever the old branch and the base disagree on a path, so `gates.leftovers` asks (`--yes`
  passes), the files are copied to `contribute.backup_dir()` (outside the clone), and
  `discard_local_changes` resets to `HEAD` (keeping an earlier `--dry-run` commit). `--diff`
  discards its own preview the same way, so preview-then-contribute never prompts. A user's own
  `--repo-dir` is **never** reset; it is warned about instead (`_own_checkout_is_clean`), gated on
  `is_managed_workspace`. **A re-run updates the open PR** rather than failing: the branch name is
  date-based, so `find_open_pr` looks it up (`gh pr list --head <plain branch>`) after the push and
  reports `updated PR #N` as success instead of letting `gh pr create` error.
  Captures are **overlaid** (append-only, `canlib/captures/merge.py::overlay`), so a
  source behind upstream never proposes deleting upstream sessions, and a capture log it adds
  nothing to stays byte-identical. A source session sharing an upstream one's **anchor** (see
  Layered profiles) but not its content *replaces* it in place — every replacement is listed by
  `--diff` and gated (`_contribute_gates.py::captures`, which also warns on unsplit multi-DID
  replies the contribution adds), since a stale source looks identical. `--diff` previews,
  `--dry-run` stops before pushing, `--yes` skips prompts, `--no-captures` ships definitions only.
  Layering: `canlib/contribute.py` = git/gh orchestration; `commands/contribute.py` = CLI +
  pipeline; `_contribute_gates.py` = the pre-flight policy; `_contribute_report.py` = the
  human/`--json` reporting duality. Read the **contributing-profiles** skill before using it.
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
`variants.yaml`, optional `signals/`, generated `out/`. The repo bundles several under `profiles/`
(`profiles/hyundai-ioniq-ae-ev/` is the mature reference; `profiles/hyundai-ioniq-5-ne-ev/` an
early seed).

**One profile, several cars.** A resolved `Profile` carries the car (`variant`, `variant_source`),
the revision pins (`revision_pins`) and remembered detections (`revision_detections`); its
`cache_key` covers all three, `.meta` applies the variant's `profile_overrides`, and `.revisions`
is each determined ECU's revision. `load_pids()` returns the definitions for exactly that car
(see `canair variants` above); anything that must see every car reads `load_raw_pids()`.

**Since more than one ships, none is auto-selected.** Precedence: `--profile NAME|PATH` (a global
flag, before the subcommand) > `CANAIR_PROFILE` > `default_profile` in config > a single discovered
profile. Discovery: `--profiles-dir`, `$CANAIR_PROFILES_DIR`, `profiles_dir` in config,
`~/.config/canair/profiles/` (user, uncommitted), then the repo's `profiles/`; user profiles shadow
bundled ones by name — **unless the user one declares `extends:`, which layers instead** (see
Layered profiles).

**Names follow `make-model-code-drivetrain`, never a year** (rule and pattern:
`canlib/profile_names.py`; `validate` enforces it for bundled profiles; reasoning in the
**profile-variants** skill). A renamed profile lists its old names under `aliases:` in
`profile.yaml`, and `resolve_profile` maps an old name to it (`alias_index`). A real directory
of the old name always wins, a capture overlay filed under the old name layers onto the renamed
profile (`profile_layers(..., old_names)`), and nothing rewrites a user's config or directories,
since a config is often shared with older checkouts that only know the old name.
`selectable_profiles` is discovery minus such old-named overlays — use it, not
`discover_profiles`, for anything offered to a user.

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
- **Reads span layers, writes do not.** `canlib/captures/io.py::resolve_capture_layers` is the read
  seam; `capture_store.load_all_captures` iterates it, dedupes sessions across layers by
  `captures_merge.session_key` (base wins, so a contributed-then-pulled session stays read-only),
  lets an overlay copy sharing a base session's `session_anchor` but not its content supersede it
  (an edited copy — never counted twice), and sorts chronologically **only when layered**, so
  single-layer results stay byte-identical. A reader that bypasses `load_all_captures` needs
  deciding per call site: `commands/validate/captures.py` and `commands/coverage/` walk **both**
  layers (a schema break or a payload in the base still matters); `canlib/pii.py` and
  `capture_migrate.py` intentionally stay on `captures_dir` (= the overlay, which is exactly what
  `contribute` ships and the only layer a migration may rewrite).
- **Base captures are read-only**, enforced by `canlib/commands/captures/layers.py::refusal` in
  `--delete`/the `--set-*` editors/`--backfill-states` and in the `captures uds --step` TUI.
  `--dry-run` still previews. Tombstones and copy-on-write were rejected: a session has no id —
  **its identity is its content**, plus an **anchor** (`captures_merge.session_anchor`: date,
  label, version, first capture's time/rx/pid — the fields no editor or repair rewrites) that
  pairs an edited copy with its original in the merge driver, the contribute overlay and the
  layered read. A new editor must keep the anchor fields intact. `read_only_files` returns `[]`
  whenever fewer than two layers are resolved, so nothing changes for an ordinary profile; an
  explicit `--dir` is judged by `layers.py::owning_profile` (as `migrate-discover` does).

> **Pass `--profile NAME` explicitly on every mutative/authoring command** — `pids …`, `signals
> upsert`, `import uds|can|dbc`, `discover --register`, `ecu add`, `wican autopid write`, and
> `--save` reads. Without it they write to whatever `default_profile`/`CANAIR_PROFILE` resolves to —
> **not necessarily the car you mean**. This is exactly how Ioniq-28-derived `WHL_SPD11` signals
> once landed in the Ioniq 5 profile instead of the Ioniq Electric one (fixed in the git history).
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
  no `pids:`. A PID may also carry **`response_frames:`** — how many CAN frames its response
  occupies, written back automatically once a session proves it (never hand-set; mutually exclusive
  with `variable_length:`). A PID may also carry **`support_map:`** — where its constant leading
  support map sits (`canair pids set-support-map`; see `investigate --support-map`). Edit via
  `canair pids`/`ecu`; validate with `canair validate pids`.
- **`profiles/<car>/captures/`** — Recorded UDS payloads, one **JSON** file per date
  (`captures/YYYY-MM-DD.json`; JSON parses ~60× faster than YAML, the dominant cost of every
  history-consuming command). The read/write seam is `canlib/captures/io.py`; there is **no YAML
  fallback** — an older profile fails fast and is converted once with `canair captures migrate`.
  **On disk, repeated polls are folded** (a poll repeating its PID's previous payload becomes a
  millisecond gap in the run's first row, `repeat_ms`/`repeat_inline_ms`;
  `canlib/captures/folding.py`) and each capture and each `state_spans` span is one line; loading
  expands every fold back, so only `capture_io.parse_capture_text`/`capture_text` ever see it —
  **never `json.loads` a capture file directly**. Folding is lossless, row order included, and
  every write applies it; `canair captures compact` converts files nothing has rewritten since.
  Schema `canlib/schema/captures_schema.json` (format-agnostic, validates the parsed structure) with
  matching `TypedDict`s in `canlib/captures/types.py` that the builders/IO are typed against
  (enforced by `ty`). Human companion: `docs/concepts/captures-and-states.md`. **These are
  append-only session logs**, so same-day recordings from two machines union-merge via canair's git
  merge driver (`.gitattributes` rule + `canlib/captures/merge.py`) — but git never loads a driver
  from a tracked file, so each clone must run **`canair captures merge-driver --install`** once;
  until then merges just fall back to conflict markers. A capture's **`rx`** field is the ECU CAN
  *response* address (its `rx_id`, else request TX + the profile `rx_offset`, resolved by
  `canlib/ecus.py::rx_for_tx`) — **not** an ECU name; that is why it was renamed from `ecu`. A
  discover capture's `rx` is `broadcast`, and each responder records its probed `tx` plus `rx`.
  **Every `--save` path records `transport` from the live client**
  (`canlib/transport/stats.py::client_transport`), never by re-resolving config — that ignored
  `--transport`/`--wican` and fallback, and mislabelled real sessions.
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
  (`RUN`/`SLEEP` because `ON`/`OFF` are YAML booleans) plus the `ALL` meta-token. A profile *may*
  declare such a word (the Ioniq's `ON`, the manual's name): it must then be quoted (`["ON"]`) in
  YAML, which every writer does and every reader/validator enforces (a bare one reads as `true`;
  `canlib/states/__init__.py::YAML_BOOLEAN_WORDS`). EV states
  (`PLUGGED`/`READY`/`CHARGING`) and finer vendor rungs are **declared per profile**, and
  `allowed_states()` returns base ∪ `ALL` ∪ the profile's own, so `--state`/`--prereq` validate
  without a static choice list. Input is normalized to uppercase. Edit via `canair states` (never
  hand-edit; `canlib/states/edit.py`); references in `ecus/` are written as inline flow lists.
- **`profiles/<car>/can_buses.yaml`** — The profile's CAN bus segment vocabulary: code → human
  `name` + `description` + optional `bitrate`. Bus naming is **vendor-specific** (Hyundai/Kia
  `B-CAN`/`P-CAN`/`C-CAN`/`MM-CAN`/`H-CAN`/`D-CAN`/`ALL`; Ford `HS`/`MS`; BMW `PT-CAN`/`K-CAN`), so
  it lives per profile, not in a global enum. Loader `canlib/can_buses.py`; `pids set-can-bus`
  accepts only declared codes and `validate pids` errors on an out-of-vocabulary value.
- **`profiles/<car>/groups.yaml`** — Named selector groups (`@name`) — see `canair groups`. Loader
  `canlib/ecus/ecu_groups.py`. The bundled Ioniq ships
  `@charging`/`@driving`/`@powertrain`/`@climate`/`@body`.
- **`profiles/<car>/actions.yaml`** — Named, guarded make-specific actions — see `canair
  action`. Edit with `canair action add/set/rm`, never by hand. Withheld by the blind strip, since
  its conditions name decoded signals.
- **`profiles/<car>/variants.yaml`** — The car's variants: local IDs naming only what differs
  (`28kwh`, never a year), each with metadata. See `canair variants`. Loader
  `canlib/variants/__init__.py::load_variants`.
- **`profiles/<car>/signals/` + `captures/can/`** — The raw-CAN broadcast domain (in progress,
  `plans/2026-07-24-raw-can-analysis.md`), both optional. `signals/<bus>.yaml` holds DBC-compatible
  **linear** maps keyed by arbitration ID; `captures/can/` holds imported logs natively, indexed by
  `index.yaml`. Path accessors `Profile.signals_dir`/`.can_dir`/`.can_index_file`.
  **Storage/licensing:** a profile's **own** logs are committed in full — via **Git LFS** when large
  (`.gitattributes` tracks `*.blf`/`*.asc`/`*.trc` + `profiles/*/captures/can/**`;
  `tests/fixtures/**` is excluded so the tiny excerpts stay plain diffable git, and so is
  `captures/can/index.yaml`, which `validate can` must read in CI's pointer-only checkout).
  **Third-party** logs only when their license permits redistribution; **unlicensed** corpora stay
  fetch-on-demand in gitignored `references/can/` plus a minimal fair-use excerpt in
  `tests/fixtures/can/`. LFS is
  storage only — it grants no redistribution right. Contributors need `git lfs install`. Full
  policy: `docs/concepts/broadcast-frames.md`.
- **`profiles/<car>/references/`** — External reference material (other-vehicle logs, spreadsheets,
  SavvyCAN exports, charge curves). Distinct from the per-ECU `research:` backlog.
- **`profiles/<car>/profile.yaml`** — Profile-wide settings. Only `car_model` + `init` are required;
  `canair validate` type-checks the rest. Its first line is a `yaml-language-server` modeline
  (`canlib/constants.py::PROFILE_SCHEMA_MODELINE`) naming the editor schema
  `canlib/schema/profile_schema.json`, published on the docs site by
  `scripts/mkdocs_hooks/schemas.py`. **A new profile.yaml field goes in that schema too**;
  `tests/test_profile_schema.py` pins its enums to the validator's. The fields:
  `response_timeout_ms`, `multi_did_batching`,
  `multi_did_max` (per-ECU overridable), `can_bitrate`, DTC `failure_types:`, plus these blocks:
  - **`addressing:`** — the CAN diagnostic addressing rule. `mode`: `normal_11bit` (default),
    `normal_29bit`, `normal_fixed_29bit` (the ISO `0x18DA{target}{tester}` convention — RX by
    byte-swap, no offset), `normal_extended_11bit` (an 11-bit header plus a per-ECU `target_address`
    byte in the payload — BMW `0x6F1`/PSA), `extended_29bit`. `rx_offset` sets the 11-bit response
    (`tx_id + rx_offset`, default `0x08`; may be **negative** — PSA `-0x20`). Resolver
    `canlib/addressing.py`
    (`resolve_mode`/`resolve_rx`/`resolve_ecu_address`/`AddressBook`/`build_isotp_address`), with
    the flow-control address override in `canlib/transport/isotp_stack.py` (raw path) and the
    ELM327 receive filter/flow control in `canlib/transport/elm327_addressing.py`.
  - **`isotp:`** — client-side ISO-TP tuning for `slcan-tcp` (padding, blocksize, stmin, timeouts,
    CAN-FD); defaults in `canlib/transport/isotp_params.py`. `tx_padding` is what canair sends
    (raw path only; an ELM327 pads its own requests); `rx_padding` (defaulting to `tx_padding`)
    is what replies carry, and drives multi-DID split/strip (`resolve_rx_padding`).
  - **`quirks:`** — make-specific behaviour the profile opts into. Known: `hk_f1xx_minus_one` (the
    Hyundai/Kia identity-DID `-1` offset, `22F188`→`62F187`), `skm_wakeup` (enables the Ioniq SKM
    relay-wake), `nissan_c0_session` (permits the Nissan OEM session `10 C0` on KWP2000 ECUs,
    which the `canlib/safety.py` blocklist otherwise refuses, and switches the KWP2000 IOControl
    scan to Nissan's `30 {LID} 01 00` probe), `hk_90_session` (permits the Hyundai/Kia
    KWP2000 actuator-test session `10 90` on KWP2000 ECUs) and `hk_forced_sleep` (lets a guarded
    `canair action` send the functional `10 D1`; not a session allowance). Resolver
    `canlib/quirks.py`; a quirk may only *widen* the OEM session band for one protocol
    (`allowed_session_modes(meta, protocol)`) or grant an action one exact request
    (`action_grants`), never reach a programming or safety-system session.
    **The session rule is protocol-aware and lives once:** `safety.py::session_mode_block` (UDS
    judges `sub & 0x7F`, so `10 82` is programming there; an unknown protocol gets the stricter
    reading). Runtime applies it through a `SessionPolicy` (`SessionPolicy.from_profile`: each
    ECU's `id_protocol` + the quirk allowances) set on every terminal as `session_policy` and
    passed to `RawUdsClient`, keyed by the current target; `validate pids` uses the same function
    for `session_mode`. A refusal raises `SafetyBlocked` (a `ValueError`), which
    `run_session_guarded` and `cli.py` report as `error: …` + exit 2.
  - **`physical_bands:`** / **`unit_guess_candidates:`** — vehicle-axis overrides for
    `hunt --physical` and make-specific scalings for the slope→unit sniffer. Resolvers
    `canlib/physical_bands.py`, `canlib/unit_guess.py`. (The grid-axis bands come from the
    user-config `grid_region` instead — an 800 V pack or a non-EU grid otherwise gets no hit.)
- **`canlib/explorer/export.py` / `explorer/layout.py`** — The docs-site profile explorer
  (`docs/profiles/explorer.md`). The export turns a profile into an **allow-listed** JSON-able dict
  (never `identity:` values, captures or `scan_log`), with a `src: {file, line}` on every record read
  via ruamel; the layout places ECUs on bus lanes. `scripts/mkdocs_hooks/explorer.py` writes it as
  virtual files at docs build time and `docs/assets/explorer/explorer.{js,css}` only render it, so
  content rules belong in the exporter (unit-tested in `tests/test_explorer_export.py`). Plan:
  `plans/2026-10-21-profile-explorer.md`. Its **data view** (plot captured signals, find related
  signals) decodes captures at build time with the shared evaluator in
  `canlib/explorer/series.py` (decoded numbers only, never payloads; size-budgeted in
  `tests/test_explorer_series.py`); `docs/assets/explorer/{plot,discover}.js` draw it and the pure
  maths is `analysis.js`, tested with `node --test tests/js/analysis.test.js` (a CI step; name the
  file, as older Node treats a directory argument as a module). uPlot is vendored under
  `docs/assets/explorer/vendor/` (MIT). Plan: `plans/2026-10-22-explorer-capture-plots.md`.
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
**auto-migrated into `devices:` on first run** (`canlib/migrations/devices.py`); once `devices:` exists
it is ignored. Resolvers: `canlib/config.py::wican_devices` (authoritative map + `DeviceEntry`) and
`fallback_settings`; `wican_settings` is a back-compat shim.

WebSocket terminal: `ws://<ip>/ws` (send `{"ws_mode": "terminal", "terminal_type": "elm327"}`).

**Firmware generation** — everything above is WiCAN firmware **4.x**; the v6 rewrite drops `/ws`,
the protocol mode and the legacy HTTP routes (plan `plans/2026-10-20-wican-fw-v6-compatibility.md`).
`canlib/wican/firmware.py` probes the generation (GET-only — `/api/info`, then `/check_status`
unless that was v6 — memoised per host) and owns the one policy table, `compatibility(info,
surface)`, used by live connect, `sniff`, `wican mode`/`autopid`, `--reboot`, the monitor's
reconnect and `canair status`. Whether a surface is probed is `probes(surface)` over
`transport.firmware_check` (`false` off, default the WiCAN transports, `always` adds `elm327-tcp`);
`status` always probes a WiCAN transport, being the diagnostic. Live paths run their cheap TCP
reachability check *before* the probe. Gate any new 4.x-only call through it, never an ad-hoc
version check; the suite stubs its `_fetch`, and `tests/conftest.py` fails any test that connects a
socket off the loopback.

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
  `expected_sid` *and* `expected_echo` (`canlib/uds/parse.py::request_echo`) on any new
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

**The link is measured, not configured.** `canlib/transport/link_latency.py::LinkLatency` is an
RFC-6298 smoother (`srtt + 4*rttvar`) shared by both domains; a plain mean underestimates half the
time and every underestimate orphans a reply. `seed()` takes a one-shot unambiguous measurement
(the TCP handshake, timed in `SlcanTcpBus.__init__` — available *before* any protocol traffic,
which is when the ISO-TP budgets must be chosen); `observe()` needs `_MIN_SAMPLES` and is fed
**only** by
adapter-only AT commands (`_LINK_PROBE_CMDS`: `ATI` and the addressing commands) — never a UDS
read, which mixes link and ECU and cannot be decomposed. `allowance(floor)` keeps the caller's
default as a floor. Consumers **add** it to the configured value (the config is the *car's* share,
the measurement the *network's*; the delays are sequential):
`build_isotp_params(config, link_budget)`
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
  dongle does ISO-TP. Backend `WiCANTerminal` (`canlib/transport/wican_terminal.py`).
- **`elm327-tcp`** (`raw=False`, `wican_http=False`) — a **generic ELM327 adapter over a plain TCP
  socket**: WiFi clones (Kiwi, vLinker, OBDLink) and the ELM327-Emulator's `-n` mode. No WiCAN, no
  HTTP config API. Backend `Elm327TcpTerminal` over a `TcpChannel`; default port 35000.

**Shared ELM327 engine.** The protocol logic lives once in `Elm327Terminal`
(`canlib/transport/elm327_terminal.py`), driven by a swappable async byte `Channel`
(`canlib/transport/channel.py`: `WebSocketChannel` for the WiCAN, `TcpChannel` for a clone).
`WiCANTerminal` (`canlib/transport/wican_terminal.py`) and `Elm327TcpTerminal`
(`canlib/transport/elm327_tcp.py`) are thin subclasses wiring their channel — a new ELM327 wire
(e.g. serial) is a new `Channel`, not a
duplicated engine. **The engine keeps the I/O; four sibling modules own the stateful policies it
used to inline**, each exercisable with no channel at all — so put new protocol policy in a sibling,
not in the terminal:
- **`canlib/transport/elm327_pipe.py::ResponsePipe`** — the `>`-prompt ledger (owed prompts,
  carry-over of a half-arrived block, the dirty-pipe flag, ResponsePending). `carry` deliberately
  *prefixes* the next collection, so a reply whose prompt merely arrived late is reassembled whole.
- **`canlib/transport/elm327_frame_count.py`** — the expected-response-count digit:
  `FrameCountCache` (learn/opt-out) plus `CountAttempt`/`CountVerdict`, the per-request decision
  procedure. See the frame-count paragraph under WiCAN Access for the four rules a change here must
  not break.
- **`canlib/transport/elm327_addressing.py`** — what `set_header` sends: `AdapterState` caches the
  adapter's header, flow-control and receive-filter state (observing *every* command, so a mode's
  direct `ATSH` cannot desync it) and plans the commands to reach an ECU's `ElmTarget`. A
  *conventional* ECU (11-bit `tx + 0x08`, fixed-29-bit swap) keeps the filter the profile `init`
  left (the *baseline*, normally auto) and sends the `ATSH`/`ATFCSH` pair it always did; any
  other gets `ATCRA<rx>` plus flow-control mode 1 to `fc_id`/`tx`, and mode 1 then stays on for
  the session (still right for conventional ECUs, since `ATFCSH` follows the header). An
  unregistered or `fc_id`-less broadcast id, and an id several ECUs share with different
  addressing (`AddressBook.shared`), get the auto filter and mode 0; extended-addressing modes and
  out-of-range reply ids are left alone. `ATFCSH` alone does nothing: the adapter honours it only
  in mode 1, which it rejects until `ATFCSH` and `ATFCSD` are set. A rejected (`?`) command is
  logged once and normally not re-sent until a reset — **but a rejected `ATSH` is never skipped**:
  `set_header` raises `AdapterRefusedError` (a `ConnectionError`), since the adapter would still
  address the previous ECU and its reply would pass the echo check. A reply that never came, a
  resync, a reset banner and an idle gap (a WiCAN's AutoPID resumes after 10 s) all make the cache
  restate. AutoPID generation uses the same policy.
  Plan `plans/2026-10-20-elm327-ecu-addressing.md`.
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
