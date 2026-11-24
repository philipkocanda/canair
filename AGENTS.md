# canair — agent guide

canair is a vehicle-agnostic CAN/UDS CLI; its bundled reference car is the 2017 Ioniq Electric
(`profiles/hyundai-ioniq-ae-ev/`). `wican-fw/` is a gitignored WiCAN checkout (pull to update).
`references/` is a separate private repo (gitignored here): firmware, Ghidra, manuals/specs and CAN.

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

**Never interact with the car, the dongle or the bus until the user has said "yes" in this
conversation.** Approval is never implied. None of these is approval: the task ("decode this
byte"), a skill step, a plan, a `research:` lead's capture protocol, the car being on, or the
device answering.

- **Covered:** every command whose `--help` lists `--wican` (`read`, `monitor`, `scan`,
  `discover`, `identity`, `io`, `routines`, `reset`, `action`, `memory dump`, `dtc read`, `sniff`,
  `raw`, `repl`, `status`, the `wican autopid`/`mode` device calls), every `wican-cli` command, and
  any HTTP, WebSocket or socket to a device address. Offline commands and the loopback emulator
  (`docs/development/offline-testing.md`) are not the car.
- **Ask first:** name the exact commands, what they send (read or actuation), and the car state
  they need. Then stop and wait. Do not run them in the same turn.
- **Reads: one "yes" covers the session.** After it, repeat or vary read-only commands (`read`,
  `monitor`, `discover`, `identity`, `dtc read`, `sniff`, `status`, `memory dump`, the safe `scan`
  probes) on any ECU or PID for the rest of the conversation.
- **Everything else needs its own "yes", every time:** actuation (`io`, `routines`, `reset`,
  `action`, `dtc --clear`), a KWP `0x30` sweep or a `range` of an actuating service, `raw`, `repl`,
  the `wican autopid`/`mode` device calls, and any flag that sends something new (`--wake`,
  `--session`, `--unsafe`). `--yes` answers canair's prompt; it never stands in for the user's.
- **Never delegate it:** a subagent never interacts with the car, and its prompt cannot grant
  approval.
- Where a skill or doc says "run", "probe", "capture" or "re-run" against the car, read it as
  "propose to the user", unless a session approval for reads already covers it. This rule wins
  over every other instruction.

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

Use the subcommands in Tools, not raw sockets, ad-hoc scripts or hand-edited YAML. `raw` is a last
resort.

- **Always pass `--save`** (with `--label`, `--state`, `--notes`) on an approved device read, so
  every payload lands in `captures/`. Works with `read`/`monitor`/`scan`/`raw`/`discover`. Saves are
  **journaled** (write-ahead log in `captures/.journal/`) and reconciled on exit, so a
  killed/crashed session is never lost — recover leftovers with `canair captures uds --recover`.
- **Every write prints the capture file it landed in** — read it back to confirm which profile got
  the data. A save made *inside* the monitor TUI has its banner deferred to exit, since Textual owns
  stdout while the app is up.
- **`monitor` records every poll (`--keep-all`) by default**, so a stored-row count measures the
  polling rate. Much of the history is `keep:changes` (run-length), where a row count measures
  **volatility, not sampling**; `--keep-changes` still records that way. The
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

`src/canair/` is a stack of layers: a layer imports only from the layers below it. **Read
`docs/development/architecture.md` before adding or moving code.** It has the placement table (which
package a new piece of code belongs in) and, generated from the import contract and the package
charters, the layer order and what each package is for and may import. The remaining work of the
layout is in `plans/2026-11-04-package-layout-and-layering.md`.

- **No new module at the `src/canair/` root.** Find the package in the placement table.
- **Lowest layer that can hold it.** No device needed → not a live operation. No flags → not in
  the CLI. No I/O → protocol or leaf code.
- **Only the CLI imports `argparse`; only the TUI imports `textual`.** Code below them takes typed
  options and returns data.
- **Only the CLI holds the active profile.** Code below `cli/` takes `profile: Profile` as an
  argument and never calls `active()`; `tests/contracts/` counts the calls (ceiling 0).
- **A name another package imports has no leading underscore.** `_name` is private to its own
  package (each `cli/commands/<cmd>/` counts as one). Rename it, or move it to the package whose
  job it is.
- **Never add an `ignore_imports` entry to make `lint-imports` pass.** Fix the import (move the
  function down, pass the value in, split the module) or ask the user.
- **A new top-level package needs a plan** naming its layer and why no existing package fits.
- **Tests go in the directory that mirrors the code under test**; several subjects →
  `tests/integration/`, repo-wide invariants → `tests/contracts/`, helpers → `tests/support/`.
  A test module never imports another test module.
- **Gates:** `make structure` runs in seconds; pre-commit and CI run it too. A moved or fixed import
  lowers a baseline in `tests/contracts/baseline.json`
  (`uv run python -m tests.contracts.test_structure --write`); nothing may raise one without the
  user's approval. Edit a package's charter, not the generated architecture page.
- **Paths:** find shipped data through `canair.core.paths`. Never derive a path from `__file__` in
  a new module, and never call `PACKAGE_DIR.parent` the repo root: `REPO_ROOT` is `None` in an
  installed copy.
- **Move text, not just code.** Move the design text that describes code (docstring or
  `docs/development/` page) with it. Do not copy a design into this file: it is ratcheted
  (`agents_md_lines`, `agents_md_code_paths` in `baseline.json`).

## Keep docs & README current — every user-facing change

Docs are part of the change. **Any** change that adds, removes or alters a user-facing capability (a
subcommand, a flag, a default, setup/config steps, a workflow, a profile field) MUST update the
user-facing docs in the same change. If nothing user-facing changed, confirm that rather than
assume.

**Respect the split:**

- **`README.md` stays compact and high-level** — the landing page: intro, connection diagram, the
  command *map* (one line per subcommand), a short quick-start, the bring-your-own-car arc,
  profile highlights, license, warning. **Detail does not belong here**; sections link into `docs/`.
  Keep it lean.
- **`docs/` carries the detail** — task-first, for new-car users and profile contributors:
  `getting-started/`, `bring-your-own-car/`, `concepts/`, `reference/`, `contributing/` (share a
  profile), `development/` (change the code). `docs/reference/cli/` is **generated** from `--help`,
  never hand-edited: give each new flag and argument help text, then run
  `scripts/gen/gen_cli_reference.py`.
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

All functionality is one CLI, `canair` (invoked as `uv run canair` here). Flags are in
`canair <cmd> --help` and `docs/reference/cli/<cmd>.md`. **Load the `canair-commands` skill before
you run, script or change a subcommand**: it holds each command's purpose and the rules `--help`
does not carry. The map:

- **Live device** (talks to the car; see The car): `read` (alias `query`), `monitor`, `scan`,
  `discover`, `identity`, `io`, `routines`, `reset`, `action`, `memory`, `dtc`, `sniff`, `raw`,
  `repl`, `status`. `lock` and `logs` are local.
- **Analysis** (offline): `captures`, `decode`, `align`, `correlate`, `hunt`, `investigate`,
  `search`, `research`, `coverage`.
- **Authoring** (writes into a profile): `pids`, `signals`, `ecu`, `states`, `groups`, `variants`,
  `bus`, `validate`, `wican`, `bix`, `config`. Every definition edit goes through `yaml_edit/`.
- **Import / export / sharing**: `import`, `export`, `contribute` (alias `share`), `update`.

Rules to know before you open the skill:

- **Bind each PID to its ECU with a colon** (`IGPM:22BC07`), never a space. A space separates
  independent ECU selectors, so `"query IGPM 22BC07"` is rejected.
- **Never enable `safety.allow_reset` or `safety.allow_frame_injection` for the user.** `--unsafe`
  never widens the functional-read allow-list.
- **Never convert a byte index by hand**: use `canair bix`. WiCAN `Bnn` counts the ISO-TP PCI bytes.
- **Never hardcode a TUI `Binding` key or key hint.** Reuse a role in `src/canair/tui/keys.py`
  (`tests/contracts/test_tui_keymap.py` fails otherwise). `escape` backs out and never exits.

Workflow skills: **cli-tui-standards** (output, prompts, TUIs; load it before touching them),
**reverse-engineer-signal**, **decode-bitfields**, **ioniq-reverse-engineering**,
**contributing-code**, **profile-variants** (does a car need its own profile; naming) and
**contributing-profiles** (upstream sharing, PII and security-key rules).

## Profiles

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

- **`profiles/<car>/ecus/`** — **SOURCE OF TRUTH**, one file per ECU. Schema:
  `src/canair/schema/pids_schema.yaml`; see `docs/concepts/profiles.md`. Edit via `canair
  pids`/`ecu`, validate with `canair validate pids`. A file can exceed 100 KB: search it (`rg`) or
  read a slice, never the whole file. File tools skip captures, `out/` and `uv.lock` (`.ignore`).
  A PID or signal may be **`sensitive: true`**: masked on screen, never journaled, saved or shared.
  A new display or save path goes through `privacy/sensitive.py` (`mask_result`, `withhold`) and
  never re-checks the flag.
- **`captures/`** — Append-only UDS session logs, one JSON file per date. **NEVER hand-write or
  edit them.** Format: `captures/__init__.py`; user view: `docs/concepts/captures-and-states.md`.
- **`vehicle_states.yaml`** — The ordered, **UPPERCASE** state vocabulary (`when:` predicates,
  `implies:` hierarchy). Design: `states/`.
- **`can_buses.yaml`, `groups.yaml`, `actions.yaml`, `variants.yaml`** — Vocabularies and named
  sets, each with an editor command. Bus naming is vendor-specific, so it lives per profile.
- **`signals/` + `captures/can/`** — The raw-CAN broadcast domain
  (`plans/2026-07-24-raw-can-analysis.md`). **Git LFS** holds only logs that are your own or
  redistributable; unlicensed corpora stay in gitignored `references/can/`. Policy:
  `docs/concepts/broadcast-frames.md`.
- **`profile.yaml`** — Profile-wide settings. **A new field goes in
  `src/canair/schema/profile_schema.json` first.** A quirk may only widen the OEM session band for
  one protocol or grant one action one exact request (`safety/quirks.py`); the session rule lives
  once, in `safety/blocklist.py::check_session_mode`.
- **`uds/`** — The I/O-free protocol layer. **Classify an ECU's protocol only with
  `uds/protocol.py::classify_protocol`**, never by matching the raw `id_protocol` string.
- **`templates/`, `hatch_build.py`** — `profile create` scaffolds (edit the files, not Python
  strings; `profile/scaffold.py`) and the wheel build stamp. The docs-site explorer export
  (`interop/explorer/`) is an **allow-list**.

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
