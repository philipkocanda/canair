# canair — agent guide

canair is a vehicle-agnostic CAN/UDS reverse-engineering CLI. The bundled reference car is a 2017
Hyundai Ioniq Electric (`profiles/hyundai-ioniq-ae-ev/`). For reference, the WiCAN firmware is
checked out in `wican-fw/` (gitignored; pull if you need the latest).

**How to read this file:** it carries the *rules*, a short *map* of the commands and the data, and
pointers to where each design lives. It is not a flag reference, a workflow guide or a design
document.

- **Flags and TUI keys** live in `canair <cmd> --help` and in `docs/reference/cli/<cmd>.md`, which
  is **generated from `--help` and CI-checked**, so it cannot drift. Never hand-copy a flag list
  here.
- **Workflows and method** live in the **skills** (`.claude/skills/`). A fact an agent needs *only
  while doing one task* belongs in that skill.
- **How code works** lives next to the code: a module's docstring, a package's `__init__.py`
  docstring (its charter), or a page in `docs/development/` when a design spans packages. Read the
  docstring of the package you change. `scripts/check/doc_paths.py` keeps paths named in
  documents alive.
- **What belongs here:** a fact that an agent needs *before it knows which file to open*, or a
  rule that prevents a mistake no gate catches. When in doubt, keep one line here and put the rest
  at its home.

## The car — NEVER touch it without the user's explicit approval

**Never interact with the car, the dongle or the bus until the user has said "yes" to that exact
action in this conversation.** Approval is never implied. None of these is approval: the task
("decode this byte", "verify X"), a skill step, a plan, a `research:` lead's capture protocol, an
earlier approval for other commands, the car being on, or the device answering.

- **Covered:** every command whose `--help` lists `--wican` (`read`, `monitor`, `scan`,
  `discover`, `identity`, `io`, `routines`, `reset`, `action`, `memory dump`, `dtc read`, `sniff`,
  `raw`, `repl`, `status`, the `wican autopid`/`mode` device calls), every `wican-cli` command, and
  any HTTP, WebSocket or socket to a device address. Offline commands and the loopback emulator
  (`docs/development/offline-testing.md`) are not the car.
- **Ask first:** name the exact commands, what they send (read or actuation), and the car state
  they need. Then stop and wait. Do not run them in the same turn.
- **Scope:** an approval covers only the commands it names. Another ECU, PID or service, an added
  flag (`--wake`, `--session`, `--yes`, `--unsafe`), a retry after a failure, or a follow-up run
  needs a new approval. `--yes` answers canair's prompt; it never stands in for the user's.
- **Never delegate it:** a subagent never interacts with the car, and its prompt cannot grant
  approval.
- Where a skill or doc says "run", "probe", "capture" or "re-run" against the car, read it as
  "propose to the user". This rule wins over every other instruction.

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

- **Read the device** (only with the user's explicit approval, see The car) with
  `read`/`monitor`/`scan`/`discover`/`io`/`routines` (`raw` only as a last resort). **Analyze
  history** with `captures`/`decode`/`align`/`correlate`/`hunt`/`investigate`/
  `search`/`coverage`/`research`. **Edit definitions** with `pids`/`signals`/`states`/`groups`/
  `ecu` — surgical, schema-validated, comment-preserving.
- **Always pass `--save`** (with `--label`, `--state`, `--notes`) on an approved device read, so
  every payload lands in `captures/`. Works with `read`/`monitor`/`scan`/`raw`/`discover`. Saves are
  **journaled** (write-ahead log in `captures/.journal/`) and reconciled on exit, so a
  killed/crashed session is never lost — recover leftovers with `canair captures uds --recover`.
- **Every write prints the capture file it landed in** — read it back to confirm which profile got
  the data. A save made *inside* the monitor TUI has its banner deferred to exit, since Textual owns
  stdout while the app is up.
- **`monitor` records every poll (`--keep-all`) by default**, so a stored-row count measures the
  polling rate. Much of the history is `keep:changes` (run-length, the previous default), where a
  row count measures **volatility, not sampling**; `--keep-changes` still records that way. The
  keep-mode tradeoffs and the joins' forward fill are in the **reverse-engineer-signal** skill.
- **The vehicle state is auto-suggested** from decoded values via the profile's
  `vehicle_states.yaml`; a `--save` segment with no explicit state is back-filled with the union of
  every state observed across its span.
- After adding captures, run `canair captures uds --summary`.
- **Never hand-write or hand-edit `captures/`, `signals/` or `vehicle_states.yaml`.** Record with
  `--save`, onboard a device-free reading with `canair import uds`, edit with `signals`/`states`.
  A capture file is folded on disk: never `json.loads` one (`captures/io.py` is the seam).

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

## Code structure — where code goes

`src/canair/` is a stack of layers (`cli` → `tui` → `contribute` → `live` → `yaml_edit` →
`validation` → the packages that compute over data → the store and the transport → the
definitions (`ecus` → `variants` → `profile`) → `device` → `safety` →
`expression`/`uds` → `config` → `core`; the generated architecture page has the exact order).
A layer imports only from the layers below it. **Read `docs/development/architecture.md`
before adding or moving code**: it has the placement table (which package a new piece of code
belongs in) and, generated from the import contract and the package charters, what each package
is for and may import. The remaining work of the layout is in
`plans/2026-11-04-package-layout-and-layering.md`.

- **No new module at the `src/canair/` root** (it holds `__init__.py` and `__main__.py`). Find the
  package in the placement table.
- **Lowest layer that can hold it.** No device needed → not a live operation. No flags → not in
  the CLI. No I/O → protocol or leaf code.
- **Only the CLI imports `argparse`; only the TUI imports `textual`.** Code below them takes typed
  options and returns data.
- **Only the CLI holds the active profile.** Code below `cli/` takes the `Profile` it works on as
  an argument (`profile: Profile`) and never calls `active()` (`cli/profile_context.py`);
  `tests/contracts/` counts the calls that do, and the ceiling is 0.
- **A name another package imports has no leading underscore.** `_name` is private to its own
  package (each `cli/commands/<cmd>/` counts as one); `test_structure.py` fails on a cross-package
  import of one. Rename it, or move it to the package whose job it is.
- **Never add an `ignore_imports` entry to make `lint-imports` pass.** Fix the import (move the
  function down, pass the value in, split the module) or ask the user.
- **A new top-level package needs a plan** naming its layer and why no existing package fits.
- **Tests go in the directory that mirrors the code under test**; several subjects →
  `tests/integration/`, repo-wide invariants → `tests/contracts/`, helpers → `tests/support/`.
  A test module never imports another test module.
- **Gates:** `make structure` (import contracts + `tests/contracts/`) runs in seconds; pre-commit
  and CI run it too. A moved or fixed import lowers a baseline in `tests/contracts/baseline.json`
  (`uv run python -m tests.contracts.test_structure --write`); nothing may raise one without the
  user's approval. `scripts/gen/gen_architecture.py` regenerates the architecture page
  (`--check` runs in CI); edit a package's charter, not the page.
- **Paths:** shipped data is found through `canair.core.paths` (`BUNDLED_PROFILES_DIR`,
  `TEMPLATES_DIR`, `CONFIG_EXAMPLE_FILE`), which means `canair/_bundled/` in a wheel and the repo
  root in a checkout. Never derive a path from `__file__` in a new module, and never call
  `PACKAGE_DIR.parent` the repo root: `REPO_ROOT` is `None` in an installed copy.
- **Move text, not just code.** When code moves, move the design text that describes it with it
  (docstring or `docs/development/` page). Do not copy a design into this file: this file is
  ratcheted (`agents_md_lines`, `agents_md_code_paths` in `baseline.json`).

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
  `getting-started/`, `bring-your-own-car/`, `concepts/`, `reference/`, `contributing/` (share a
  profile), `development/` (change the code). `docs/reference/cli/` is **generated** from `--help`,
  never hand-edited: give each new flag and argument help text, run `scripts/gen/gen_cli_reference.py`.
- **This file** carries the agent-facing rules and the maps. Where a fact is authoritative
  elsewhere (`--help`, `config.example.yaml`, `src/canair/schema/`, a plan doc, a docstring),
  **point at it instead of copying it**.
- **Skills** (`.claude/skills/`) carry the workflows.

Verify internal links still resolve — a broken cross-link is a defect. Policy:
`plans/2026-07-24-documentation-strategy.md`.

## Designs that span packages — read the page before you change the code

| Topic | Read |
|---|---|
| Expected-response frame counts: learning, persisting, the ELM327 digit, AutoPID requests | `docs/development/frame-counts.md` |
| Device fallback, `--wait`, mid-session reconnect, desync recovery, link latency | `docs/development/link-health.md` |
| Variants, ECU revisions, detection, capture tags and scoping | `docs/development/variants-and-revisions.md` |
| Profile discovery, layers, adopt and overlay, writes into the install snapshot | `docs/development/layered-profiles.md` |
| `canair contribute`: gates, the managed workspace, captures overlay | `docs/development/contribute-pipeline.md` |
| Testing with no dongle (ELM327-Emulator, `elm327-tcp`) | `docs/development/offline-testing.md` |
| DTC log, snapshots, memory dumps, the monitor, the ELM327 engine | the package docstring |

Device and firmware ground truth (the WiCAN Pro hardware, its ELM327 co-processor, the firmware's
modes and API, throughput limits) is the **wican-hardware-and-protocol** skill. Load it before you
change a transport backend, debug a desync or timeout, or call a device API.

## Tools

> Workflows: how a command prints, prompts and exits, and how a TUI binds keys and builds modals,
> is the **cli-tui-standards** skill (load it before touching output, a prompt or a TUI);
> reverse-engineering a signal end-to-end is the **reverse-engineer-signal** skill;
> partially-decoded bitflag bytes are **decode-bitfields**; bundled-car context is
> **ioniq-reverse-engineering**; changing canair's code is **contributing-code**; whether a car
> needs its own profile and how to name one is **profile-variants**; preparing a profile for
> upstream (and its PII and security-key rules) is **contributing-profiles**.

All functionality is one CLI, `canair` (invoked as `uv run canair` here). **Flags and subcommand
lists are in `canair <cmd> --help` and `docs/reference/cli/<cmd>.md`** — the entries below give each
command's purpose plus the rules `--help` does not carry.

**TUI keys are not in `--help`.** Every Textual view's keymap is assembled from the shared role
registry **`src/canair/tui/keys.py`** (`ROLES` + `bind(role, action)`), so one key means one
thing in every view; press `?` in any TUI for its live cheat-sheet. Never hardcode a `Binding`
key or a key hint in a TUI — add or reuse a role, or `tests/contracts/test_tui_keymap.py` fails.
Two standing conventions: **`escape` backs out one level and never exits** (`q` is the only
exit), and **`g`/`G` address the view's primary axis**. The rest of the TUI and CLI output rules
(colour and glyph meanings, messages, prompts, modals, `--json` hygiene) are the
**cli-tui-standards** skill; `tests/contracts/test_ui_standards.py` ratchets its deviations.

### Live device

**Every command in this section talks to the car: run none without the user's explicit approval.**

- **`read`** (alias `query`) — Custom CAN/UDS requests. **Prefer positional query steps**
  (`canair read BMS:2101`; `query/` handles sessions, wake and keepalives). **Bind each PID to its
  ECU with a colon (`IGPM:22BC07`), never a space**: a space separates *independent ECU
  selectors*, so `"query IGPM 22BC07"` means "all of IGPM plus a bogus ECU" and is rejected.
  `@group` expands to a saved selector set. A token picks PIDs by prefix *or* suffix (`VCU:22`);
  an undefined token is inferred or refused, never guessed (`query/__init__.py::inferred_request`);
  OBD-II is `BMS:01/0D`. TesterPresent keepalives are automatic. `--reboot` restores AutoPID.
- **`monitor`** (alias `mon`) — Live view; same steps as `read`. Recording (`--save`/`--label`/
  `--state`/`--notes`) and the keep-modes live **here, not on `read`**. Bare `monitor` on a TTY
  opens a picker. Polling is per-PID with backoff. Design: `live/monitor/`.
- **`scan`** — Kinds `range` (bare `scan <ECU>`), `sessions`, `routines`, `iocontrol`, `memory`.
  All are **safe probes** except a KWP `0x30` sweep and a `range` of an actuating service, which
  ask first. Hits go to the ECU's `routines:`/`iocontrol_discoveries:`/`memory:`. Without
  `--range`, `range` sweeps the ECU's DID families (`ecus/did_families.py`).
- **`discover`** (alias `disc`) — Which ECUs answer; `--register` writes a new ECU into `ecus/`;
  `--functional` sends one allow-listed read on the functional id. That allow-list is never
  widened by `--unsafe` (`safety/blocklist.py::check_functional_request`).
- **`identity`** (alias `id`) — Identity DIDs/records, printed only. Nothing is written to the
  ECU's `identity:` (it can hold a live VIN: see the PII rules in **contributing-profiles**).
- **`io`** / **`routines`** — IOControl and RoutineControl. **Mutative**: the user approves each
  actuation. Actuators auto-release when the session ends. `io` refuses a KWP `0x30` ON until the
  entry declares a `release:` — never declare one you have not seen hand the output back on the
  car (`safety/actuation.py`).
- **`reset`** — ECUReset `0x11`, off until the user sets `safety.allow_reset`. Never enable that
  key for the user. Rules: `safety/reset.py`.
- **`action`** — The profile's guarded `actions.yaml`. Every request passes the blocklist
  (`--unsafe` plays no part). A `frames:` action injects a broadcast frame (`slcan-tcp` only), off
  until the user sets `safety.allow_frame_injection` — **never enable it**. Design: `actions/`.
- **`memory`** — Memory reads (`0x23` only). **Dumps are user state**
  (`<config dir>/memory/<profile>/`), never profile data; a memory PID never ships to AutoPID.
  Docs: `docs/concepts/memory-reads.md`; design: `memory/`, `live/memory/`.
- **`dtc`** — Read, clear (`--clear`) and `dtc log`. **The DTC log is user state, not a bundle
  member** (`<config dir>/dtc_log/<profile>.yaml`). Design: `dtc/`, `live/dtc/`.
- **`sniff`** — Passive sniffer, **`slcan-tcp` only**; the only way to see broadcasts.
- **`raw`** (hex in, hex out; last resort) and **`repl`** (ELM327 terminal, ELM transports only).
- **`status`** (alias `st`) — What am I talking to, and is it usable? It runs the same fallback
  probe as a live command. Never resolve only the selected device there.
- **`lock`** — Inspect or clear the device connection mutex. **canair never kills another process
  on its own**: `--force` asks the holder to leave, then names the PID and the `kill` to run.
  Design: `device/lock.py`.
- **`logs`** — The central diagnostics log (`~/.config/canair/logs/canair.log`), written by
  `transport/stats.py`.

### Analysis (read-only — never touches the device)

> **Method lives in the skills.** *How* to choose and read these tools is the
> **reverse-engineer-signal** skill and `docs/concepts/analysis-commands.md`.

- **`captures`** (alias `cap`) — Query captured data. `captures uds` (domain A; a bare
  `canair captures BMS 2102` is shorthand) has the views `--diff`, `--step`, `--latest`,
  `--summary`, `--sessions` and the mutating modes. `captures can` (domain B) lists imported frame
  logs. Rules: `cli/commands/captures/` docstring.
- **`decode`** (alias `dec`; `--try` tests an expression, `--plot` explores), **`align`**
  (time-aligned cross-ECU table), **`correlate`** (`correlate can` for raw frames), **`hunt`**
  (which byte *is* a known signal; `--promote` writes it), **`investigate`** (per-byte verdict;
  `--events`/`--dwell`/`--bits`, `--counters`, `--support-map`), **`search`** (string, VIN or byte
  pattern across captures), **`research`** (open backlog; `--gaps`).
- A support map is per-PID and *declared* (`pids set-support-map`), never inferred at read time.
- **`coverage`** — Decoding gaps: UNMAPPED bytes, incomplete BITS. **Author bits as `Bn:k`, never
  as an `&` mask**: coverage cannot see a mask.

**Shared flags are declared once so their defaults cannot drift** — reuse these helpers when you
add an analysis command: `cli/args/join.py::add_join_args` (`--join-tol`/`--fill`/`--max-hold`),
`cli/args/scope.py::add_scope_args` (`--since`/`--state`/`--label`/…), `--find-mirrors` (one
matcher, `analysis/mirrors.py`) and `--notation` (**display only**: `--json` and `--promote` always
emit the canonical WiCAN expression).

### Authoring (writes into a profile — pass `--profile`)

**Every definition edit goes through `yaml_edit/`** (its package docstring is the map). An editor
declares a `Target`, registers it in `yaml_edit/targets.py`, and writes through `edit_doc` or
`edit_ecu_text`, which write atomically, re-check, and **revert on any failure**. A rejected edit
raises `EditError`; no command catches it. A new editor never opens the file itself, and removes or
renames an entry with `yaml_edit.ruamel_ops` (a bare `del` loses the comments).

- **`pids`** — Add/update `ecus/` signals and research entries; prefer it over hand-editing.
  **WiCAN `Bnn` includes the ISO-TP PCI bytes**: confirm an offset with
  `canair bix -a HEX --ecu … --pid …`. A successful `upsert-param` echoes the decoded range across
  existing captures; a `constant` where you expected variation usually means the offset landed on
  a PCI byte. **State a shared value once, never per signal**: whatever holds for the whole page
  (`unit`/`min`/`max`/`verified`/`source`) goes on the PID (`pids set-pid-defaults`,
  `pids hoist-defaults`); a signal's `notes` carry only what is specific to it. Pass plain strings
  for `notes`; they are formatted for you.
- **`signals`** — The domain-B analogue: the broadcast sidecar `signals/<bus>.yaml`. **Never
  hand-edit `signals/`.**
- **`ecu`** — Inspect the registry; `ecu add TX …` registers an ECU offline; `ecu <ECU> rename NEW`
  renames it everywhere; `ecu revisions`/`set-variants` edit revisions and presence. `ecu <ECU>
  edit` opens `$EDITOR` and is **TTY-only — agents use `pids …`/`ecu add`**.
- **`states`** — The state vocabulary. **Never hand-edit `vehicle_states.yaml`**: the editors also
  retarget `implies:` references, and `states rename` rewrites every reference
  (`yaml_edit/state_rename.py`).
- **`groups`** (`@group` sets; overlapping selectors are coalesced to one step per ECU in
  `query/steps.py::normalize_query_steps`, load-bearing for `monitor`), **`variants`**
  (`variants.yaml`; `docs/development/variants-and-revisions.md`), **`bus`** (`can_buses.yaml`).
- **`validate`** (alias `val`) — Schema and consistency checks. **`validate states` is the only
  place a renamed or mistyped `when:` signal is caught.** Per-target detail:
  `validation/__init__.py`. CI ratchets: `--max-untimed`, `--max-untagged`.
- **`wican`** — Build and sync the device's AutoPID profile from `ecus/` (`wican autopid write` →
  `out/`). Verified-only by default. Details: `interop/autopid/`.
- **`bix`** — Byte-index converter and payload annotator. **Never convert a byte index by hand**:
  it depends on the response's length and service, which `bix` reads off the payload.
- **`config`** — The user config (`~/.config/canair/config.yaml`); `config example` prints the
  authoritative `config.example.yaml`. Prefer it over hand-editing.

### Import / export / sharing

- **`import uds ECU:PID=PAYLOAD …`** — Device-free capture import (domain A). `--label` is
  required. `--from-log FILE` is the bulk form: always `--dry-run` first. **`import can`** stores a
  raw frame log verbatim in `captures/can/`. **`import dbc`** imports a DBC into `signals/`; add a
  dialect repair in `interop/dbc/repair.py`, never a pre-edited copy. **`export dbc`** writes
  `signals/` out.
- **`contribute`** (alias `share`) — Opens the upstream PR for the **active** profile via `gh`,
  after `validate all`, a PII pre-flight and several guards. Read **contributing-profiles** first;
  design: `docs/development/contribute-pipeline.md`.
- **`update`** — Checks out the latest **released** tag of a git-clone install and reinstalls;
  refuses a dirty clone or an unknown latest tag.

### Profiles

Vehicle data lives in a **profile** bundle: `ecus/` (one file per ECU, the source of truth),
`profile.yaml`, `captures/`, `references/`, `vehicle_states.yaml`, `can_buses.yaml`, `groups.yaml`,
`actions.yaml`, `variants.yaml`, optional `signals/`, generated `out/`. The members are declared
once, in `profile/bundle.py`. The repo bundles several under `profiles/`
(`profiles/hyundai-ioniq-ae-ev/` is the mature reference). One profile can describe several cars:
`load_pids(profile)` returns the definitions for exactly that car, and anything that must see every
car reads `load_raw_pids(profile)`.

**None is auto-selected**: `--profile NAME|PATH` (a global flag, before or after the subcommand) >
`CANAIR_PROFILE` > `default_profile` in config > a single discovered profile. Names follow
`make-model-code-drivetrain`, never a year (`profile/names.py`; see **profile-variants**).
`canair profile list/show/path/use/create/adopt/overlay` manage them. Discovery, layering and
aliases: `docs/development/layered-profiles.md`.

> **Pass `--profile NAME` explicitly on every mutative/authoring command** — `pids …`, `signals
> upsert`, `import uds|can|dbc`, `discover --register`, `ecu add`, `wican autopid write`, and
> `--save` reads. Without it they write to whatever `default_profile`/`CANAIR_PROFILE` resolves to,
> which is not necessarily the car you mean (Ioniq-28-derived signals once landed in the Ioniq 5
> profile this way). `signals`/`import` echo the target profile and path on write — read it back.

**Never let a write land in the install snapshot.** A profile resolved from `site-packages` (a bare
`canair` from `uv tool install`/`pipx`/`pip`) accepts writes that the next reinstall deletes. Every
write path emits a note about it (`captures/save.py::saved_banner`, `yaml_edit/echo.py::echo_edit`):
a new write path must too. Fix an existing setup with `canair config set profiles_dir
<clone>/profiles` or `canair profile adopt <name>`.

## Where the data is described

- **`profiles/<car>/ecus/`** — **SOURCE OF TRUTH**, one file per ECU. Its sections and fields are
  checked by `src/canair/schema/pids_schema.yaml` and described in `ecus/` (package docstring) and
  `docs/concepts/profiles.md`. Edit via `canair pids`/`ecu`, validate with `canair validate pids`.
  An ECU file can exceed 100 KB: search it (`rg`) or read a slice, never the whole file.
  `opencode.json`, `.claude/settings.json`, `.ignore`: file tools skip captures, `out/`, `uv.lock`.
  A PID or signal may be **`sensitive: true`**: masked on screen, never journaled, saved or shared.
  The rule lives once in `privacy/sensitive.py`; a new display or save path goes through it
  (`mask_result`, `withhold`) and never re-checks the flag.
- **`profiles/<car>/captures/`** — Recorded UDS payloads, one JSON file per date, append-only
  session logs. **NEVER hand-write or edit them.** Format, folding, merge driver and the `rx` field:
  `captures/__init__.py`; user view: `docs/concepts/captures-and-states.md`.
- **`profiles/<car>/vehicle_states.yaml`** — The ordered, **UPPERCASE** state vocabulary with
  `when:` predicates (Kleene logic) and `implies:` (a specificity hierarchy). Design: `states/`.
- **`can_buses.yaml`, `groups.yaml`, `actions.yaml`, `variants.yaml`** — vocabularies and named
  sets, each with a loader named in its editor command above. Bus naming is vendor-specific, so it
  lives per profile.
- **`signals/` + `captures/can/`** — The raw-CAN broadcast domain
  (`plans/2026-07-24-raw-can-analysis.md`). Logs are committed in full via **Git LFS** only when
  they are your own or redistributable; unlicensed corpora stay fetch-on-demand in gitignored
  `references/can/`. Policy: `docs/concepts/broadcast-frames.md`.
- **`profile.yaml`** — Profile-wide settings (`addressing:`, `isotp:`, `quirks:`, bands). **A new
  field goes in `src/canair/schema/profile_schema.json` first.** Quirks are make-specific behaviour
  the profile opts into and may only widen the OEM session band for one protocol or grant one
  action one exact request (`safety/quirks.py`). The session rule lives once, in
  `safety/blocklist.py::check_session_mode`.
- **`templates/`**, **`hatch_build.py`** — the `profile create` scaffolds (edit the files, not
  Python strings) and the wheel build stamp. See `profile/scaffold.py` and `hatch_build.py`.
- **`src/canair/uds/`** — the I/O-free diagnostic protocol layer. **An ECU's protocol is classified
  once, by `uds/protocol.py::classify_protocol`**, and compared against its `UDS`/`KWP` constants,
  never by matching the raw `id_protocol` string.
- The docs-site profile explorer is `interop/explorer/`; its export is an **allow-list**.

## WiCAN access and transports

Devices live in `~/.config/canair/config.yaml` (copy from `config.example.yaml`, the authoritative
commented reference): `devices:` with a `host` and an optional `transport`, plus `default_wican`.
Select with `--wican home|vpn|<ip>`. Precedence for the transport: `--transport`/`--wican` >
device entry > global `transport:` block > default `slcan-tcp`. Resolvers:
`config/devices.py`, `transport/config.py`.

canair reaches the bus through one **explicitly selected** transport, never auto-detected. Each is
a `TransportSpec` in `transport/config.py::TRANSPORTS`; adding a backend means registering a spec.

- **`slcan-tcp`** (default) — raw SLCAN over TCP with client-side ISO-TP. Any WiCAN or gateway;
  also powers `sniff`. `transport/raw/`.
- **`wican-ws`** (Pro only) — the WiCAN's ELM327 terminal over `ws://host/ws`.
- **`elm327-tcp`** — a generic ELM327 WiFi adapter over a plain TCP socket (no WiCAN HTTP API).
  Both ELM327 transports run one engine, `transport/elm327/terminal.py`; a new wire is a new
  `Channel`, and new protocol policy goes in a sibling module of the engine, not in it.

Rules a change must keep (details in the pages above):

- **Every request is echo-validated and `set_header` + `send_uds` share one
  `terminal.transaction()`.** Pass `expected_sid` and `expected_echo` on any new `send_uds`.
  Without them a desync is undetectable (`docs/development/link-health.md`).
- **A frame-count digit is learned, never guessed.** An undercount manufactures a desync
  (`docs/development/frame-counts.md`).
- **Gate a 4.x-only device call through `device/wican/firmware.py::compatibility`**, never an
  ad-hoc version check. The suite stubs its network access, and `tests/conftest.py` fails any test
  that connects a socket off the loopback.
- **Never send an init or protocol selection around `transport/elm327/eeprom.py`** (it protects the
  adapter's EEPROM from wear).
- ELM-only features (`repl`, `skm-wake`) gate on `isinstance(terminal, Elm327Terminal)`.
- Device sleep is a **`wican-cli`** concern (a separate package); canair only reads it via
  `canair status`.
