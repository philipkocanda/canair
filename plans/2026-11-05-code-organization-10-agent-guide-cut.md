# Code organization, part 10: cut the agent guide

Status: **DONE** (2026-11-06). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plans 5, 6,
7 and 9, which move most of the code that `AGENTS.md` names. This is left-over item 5 of
`plans/2026-11-04-package-layout-and-layering.md`.

## Goal

`AGENTS.md` holds what an agent needs in every session: the rules, a short map of the commands,
and a short map of the code. Design detail moves next to the code it describes, where an agent
reads it when it changes that code.

## Findings (at `0ce4f742`)

- `AGENTS.md` is **about 1,370 lines**, with **187 `src/canair/…` path references**. Its header
  says it carries "the rules, a one-line-per-command map, and the code/data map — not a flag
  reference and not a workflow guide".
- Lines per section:

  | Section | Lines |
  |---|---:|
  | Running the CLI, formatting, tooling, vocabulary, Boy Scout, code structure, docs | 133 |
  | Tools: intro | 24 |
  | Tools: live device | 167 |
  | Tools: analysis | 118 |
  | Tools: authoring | 251 |
  | Tools: import, export and sharing | 91 |
  | Tools: profiles | 109 |
  | Key files | 189 |
  | WiCAN access | 181 |
  | Transports | 90 |

- Many "one-line" command entries are 10 to 40 lines. They describe how the code works: the
  frame-count policy (four rules and a ledger), the reconnect and desync mechanisms, the
  variant and revision resolution, the layered-profile read and write rules, the contribute
  pipeline, the DTC log's storage. That text is accurate and useful, but an agent needs it only
  when it changes that code.
- The text repeats the module docstrings. For example, the frame-count rules are in
  `AGENTS.md`, in `transport/elm327/frame_count.py` and in a plan.
- Sub-plan 1 found six stale module names. Every move in sub-plans 3 to 9 adds more paths to
  correct, unless the paths leave `AGENTS.md` first.

## What stays

A fact stays in `AGENTS.md` if one of these is true:

1. An agent needs it before it knows which file to open. Examples: run `uv run canair` from
   the repo root, pass `--profile` on every write, never hand-write `captures/` or `signals/`.
2. It prevents a mistake that no gate catches. Examples: bind a PID with a colon
   (`IGPM:22BC07`), `--save` on every device read, `escape` never quits a TUI.
3. It is the map: one or two lines per command (what it is for), and one line per package
   (pointing at the generated architecture page).

Everything else moves to one of these homes:

| Kind of text | New home |
|---|---|
| How one module works | that module's docstring |
| How one package works, or a rule for its code | that package's charter, or a "Design" section in its `__init__.py` docstring |
| A design that spans packages (frame counts, reconnect and desync, variants and revisions, layered profiles) | a page in `docs/development/`, linked from `AGENTS.md` in one line |
| Device and firmware ground truth | the wican-hardware-and-protocol skill |
| A workflow | the skill that owns it |
| Flags and defaults | `--help` and `docs/reference/cli/` (already generated) |

Before deleting a paragraph, check that its new home says the same thing. If the home already
has the text, delete the copy. If the two disagree, find out which one is true from the code.

## Steps

1. **Measure and mark.** For each paragraph, record its kind and its new home in a work list
   (in this plan's status line or a scratch file that is not committed).
2. **Write the development pages.** One page for each design that spans packages:
   `docs/development/frame-counts.md`, `docs/development/link-health.md` (reconnect, desync,
   link latency), `docs/development/variants-and-revisions.md`,
   `docs/development/layered-profiles.md`. Add them to `mkdocs.yml`. `mkdocs --strict` must
   pass.
3. **Move the module-level text** into docstrings, one package per commit. A docstring that
   grows past what a reader wants at the top of a file is a sign that the text belongs on a
   development page instead.
4. **Rewrite `AGENTS.md`** to the shape of "What stays". Each command entry has at most three
   lines. Each design has one line with a link.
5. **Add a ratchet.** Add `agents_md_lines` and `agents_md_code_paths` to `baseline.json` as
   ceilings, with a test in `tests/contracts/`. They stop the guide from growing back without a
   decision.
6. **Check the skills.** The contributing-code and reverse-engineer-signal skills point at
   `AGENTS.md` sections by name ("`AGENTS.md` → Transports"). Update every such pointer.

## Acceptance

- `AGENTS.md` is under 450 lines, with fewer than 60 `src/canair` paths.
- `scripts/check/doc_paths.py` (sub-plan 1) reports nothing.
- `mkdocs --strict` passes, and every internal link resolves.
- No fact is lost: each deleted paragraph is in the work list of step 1, with its new home.

## Risks

- **Agents read less.** Text in a docstring is read only by an agent that opens the file. This
  is the intended trade, but it can hide a rule that matters before the file is opened. The
  "What stays" test is the guard. When in doubt, keep one line in `AGENTS.md` that names the
  rule and points at its home.
- **Concurrent edits.** Other sessions edit `AGENTS.md` often (the cli-tui-standards work changed
  it on 2026-11-05). Do step 4 in one short session on a quiet tree, and rebase the work list
  onto the latest text first.

## As built

Measured at `a8401fb0` (every other sub-plan merged). `AGENTS.md` before: **1,401 lines, 203
`src/canair` paths**. After: **436 lines, 7 paths**. Both are ceilings now
(`agents_md_lines`, `agents_md_code_paths` in `tests/contracts/baseline.json`, checked by
`tests/contracts/test_agent_guide.py`, measured by `scripts/check/agent_guide.py`).

Differences from the plan:

- Five pages, not four: `docs/development/frame-counts.md`, `link-health.md`,
  `variants-and-revisions.md`, `layered-profiles.md` and `contribute-pipeline.md`. The cut found
  no other design that needs a page. The DTC log and snapshots, the monitor, memory dumps and the
  ELM327 engine already had full docstrings, so those paragraphs were deleted, not moved.
- Paths in `AGENTS.md` are package-relative (`live/monitor/`) where they are only a pointer, so
  `doc_paths.py` (which checks `src/canair/...py` paths) sees few of them. A scratch script
  checked every backticked path in the new file against the tree: none is missing.
- The cut found stale text in the old guide. Examples: the old text said `load_pids()` with no
  path, `set_active`, `Profile.meta` and `_check_liveness`; those are now
  `load_pids(profile)`, `cli/profile_context.py`, `variants.effective_meta` and
  `MonitorHealth.check_liveness`. The pages and docstrings name the current ones. The docstring of
  `config/transport.py::reconnect_max_wait` said "default 6.0"; the code default is 60.0, and it
  is fixed.
- Not done: `mkdocs build --strict` fails on the unmodified tree, because
  `scripts/mkdocs_hooks/explorer.py` calls `export_profile` without the `profile` argument that
  sub-plan 3 made mandatory. Outside this territory, not fixed. With that one hook removed from a
  scratch config, the strict build passes with the new pages in the nav.

### Work list (paragraph topic, kind, new home)

Kinds: **stays** (a rule, or the map), **docstring** (module or package), **page**
(`docs/development/`), **skill**, **help** (`--help` or the generated CLI reference),
**dropped** (historical, or the same as its home, checked).

| Old section and topic | Kind | New home |
|---|---|---|
| Header: how to read this file | stays | `AGENTS.md` (shorter) |
| Running the CLI; Formatting; Boy Scout; Vocabulary | stays | `AGENTS.md`, unchanged |
| Built-in tooling; `--save`; journal; states auto-suggest | stays | `AGENTS.md` |
| Saved-banner function name | docstring | `captures/save.py::saved_banner` |
| Keep-mode tradeoffs | skill | reverse-engineer-signal (already there) |
| Code structure rules, gates, paths | stays | `AGENTS.md`, plus the ratchet rule |
| Docs and README split | stays | `AGENTS.md` |
| Tools intro, skill pointers | stays | `AGENTS.md` |
| TUI keymap registry and conventions | stays, docstring | `AGENTS.md` (rules); `tui/keys.py` (rest) |
| read: colon binding, `@group`, `SERVICE/ID` | stays | `AGENTS.md`; grammar in `query/__init__.py` |
| read: PID token inference | docstring | `query/__init__.py::inferred_request` |
| read: TesterPresent, NRC 0x11 | stays, docstring | one clause in `AGENTS.md`; `transport/keepalive.py` |
| monitor: picker, `p` key | docstring | `live/monitor/select.py`, `selectors.py` |
| monitor: schedule, backoff, reset on rebind | docstring | `schedule.py`, `backoff.py`, `health.py` |
| monitor: `request_s`, `fresh_queries` | docstring | `live/read/exec_read.py`, `controller.py` |
| scan: kinds, DID families, comma ranges | docstring | `live/scan/__init__.py`, `ecus/did_families.py`, `core/id_ranges.py` |
| scan memory: block limit, map | docstring, page | `live/memory/`, `ecus/memory.py`, `docs/concepts/memory-reads.md` |
| discover: functional, allow-list | stays, docstring | one line in `AGENTS.md`; `safety/blocklist.py`, `transport/functional.py`, `transport/elm327/functional.py`, `transport/raw/functional.py` |
| identity: printed only, stale F1xx | stays, docstring | `AGENTS.md` (PII pointer); `live/identity/read.py` |
| io/routines: release rule | stays, docstring | `AGENTS.md`; `safety/actuation.py`, `live/actuate/__init__.py` |
| action: two rules | stays, docstring | `AGENTS.md`; `actions/__init__.py`, `live/actuate/action.py` |
| memory: user state, 64-byte rule, memory PID | stays, docstring | `AGENTS.md`; `memory/__init__.py`, `privacy/sensitive.py`, `ecus/pid_status.py`, `uds/echo.py` |
| dtc: log location, scope, refs, snapshots | stays, docstring | `AGENTS.md` (one line); `dtc/log.py`, `log_upgrade.py`, `scope.py`, `refs.py`, `history.py`, `snapshot*.py`, `live/dtc/` |
| sniff; raw; repl | stays | `AGENTS.md` |
| status: fallback probe | stays, docstring | `AGENTS.md`; `cli/commands/status/__init__.py` (added) |
| lock: mutex, steal, watchdog, notice | stays, docstring | `AGENTS.md` (never kills); `device/lock.py`, `lock_watchdog.py`, `cli/lock_notice.py`, `core/stop_signals.py` |
| logs: event log | stays, docstring | `AGENTS.md`; `transport/stats.py`, `uds/response.py` |
| captures: views, `--limit`, mutating modes | docstring | `cli/commands/captures/__init__.py` (added), `mode_select.py` |
| decode, align, correlate, hunt | help | `--help` and `docs/reference/cli/` |
| investigate: counters, support map, fields | docstring | `analysis/counters.py`, `support_map.py` (rule added), `support_fields.py`, `cli/commands/investigate/` |
| search: inventory, VIN test | docstring | `core/textscan.py` |
| coverage: mask blind spot | stays, docstring | `AGENTS.md` (one line); `cli/commands/coverage/` |
| research: `--gaps` | docstring | `cli/commands/research/gaps.py` |
| Shared flags (join, mirrors, notation, scope) | stays, docstring | `AGENTS.md` (helper names); `cli/args/join.py`, `analysis/mirrors.py`, `expression/notation.py` |
| Authoring: `yaml_edit` write path | stays, docstring | `AGENTS.md`; `yaml_edit/__init__.py`, `core/edit_errors.py` |
| pids: PCI trap, decoded-range echo | stays, skill | `AGENTS.md`; reverse-engineer-signal |
| pids: `category:` | docstring | `ecus/signal_category.py` |
| pids: PID defaults, `effective_param` | stays, docstring | `AGENTS.md` (state once); `ecus/pids.py` (added) |
| signals: DBC numbering, schema, SIGNAL_FIELDS | stays, docstring | `AGENTS.md` (never hand-edit); `yaml_edit/signals.py`, `validation/signals.py` |
| ecu: add, rename, TTY-only edit | stays | `AGENTS.md` |
| states: `implies`, rename, never hand-edit | stays, docstring | `AGENTS.md`; `states/__init__.py`, `yaml_edit/state_rename.py` |
| groups: textual expansion, coalescing | docstring | `ecus/ecu_groups.py`, `query/steps.py` |
| variants: design, revisions, detection, tags | page | `docs/development/variants-and-revisions.md` |
| bus | stays | `AGENTS.md` |
| validate: targets, states refs, ratchets | stays, docstring | `AGENTS.md`; `validation/__init__.py` (added), `states/refs.py` |
| wican: AutoPID files, verified-only, `send_to` | docstring | `interop/autopid/__init__.py` (added) |
| wican: frame counts in requests | page | `docs/development/frame-counts.md` |
| bix: layout and subfunctions | stays, docstring | `AGENTS.md` (never convert by hand); `uds/layout.py`, `uds/subfunctions.py` |
| config: `grid_region` | docstring | `analysis/physical/grid_regions.py` (added) |
| import uds: `--from-log` | docstring | `cli/commands/import_uds/`, `interop/frame_log.py`, `uds/isotp_passive.py` |
| import can; import dbc; export dbc | stays, docstring | `AGENTS.md`; `interop/dbc/repair.py` |
| contribute: pipeline, guards, workspace | page | `docs/development/contribute-pipeline.md` |
| update: tag checkout, install context | stays, docstring | `AGENTS.md`; `install/context.py`, `update_check.py` |
| Profiles: bundle, selection, names, aliases | stays, page | `AGENTS.md`; `docs/development/layered-profiles.md`; `profile/names.py` |
| Profiles: adopt, overlay, layers, base read-only | page | `docs/development/layered-profiles.md` |
| Profiles: `--profile` on every write | stays | `AGENTS.md` |
| Install snapshot writes | stays, page | `AGENTS.md`; `docs/development/layered-profiles.md` |
| Key files: `ecus/` fields; `sensitive:` | stays, docstring | `AGENTS.md` (sensitive rule); `ecus/__init__.py`, `ecus/dtc_definitions.py`, `ecus/pid_status.py` |
| Key files: `response_frames:`, `variable_length:` | page | `docs/development/frame-counts.md` |
| Key files: `captures/` format, merge driver | docstring | `captures/__init__.py` (added), `io.py`, `folding.py`, `merge.py` |
| Key files: `vehicle_states.yaml` | docstring | `states/__init__.py`, `states/implies.py` |
| Key files: buses, groups, actions, variants files | docstring | `ecus/can_buses.py`, `ecus/ecu_groups.py`, `actions/`, `variants/` |
| Key files: `signals/`, `captures/can/`, LFS | stays, page | `AGENTS.md` (one line); `docs/concepts/broadcast-frames.md` |
| Key files: `profile.yaml`, addressing, isotp, quirks | stays, docstring | `AGENTS.md` (schema first, quirk limits); `profile/__init__.py` (added), `uds/addressing.py`, `transport/isotp_params.py`, `safety/quirks.py`, `safety/blocklist.py` |
| Key files: explorer | docstring | `interop/explorer/__init__.py` (added) |
| Key files: `templates/`, `hatch_build.py` | docstring | `profile/scaffold.py` (added), `hatch_build.py`, `install/build_info.py` |
| Key files: protocol classification | stays, docstring | `AGENTS.md`; `uds/protocol.py` |
| WiCAN access: devices, precedence | stays, docstring | `AGENTS.md`; `config.example.yaml`, `config/devices.py`, `migrations/devices.py` |
| WiCAN access: firmware generation | docstring | `device/wican/firmware.py` |
| WiCAN access: fallback, `--wait`, reconnect | page | `docs/development/link-health.md` |
| WiCAN access: echo validation, resync, transaction | stays, page | `AGENTS.md` (rule); `docs/development/link-health.md` |
| WiCAN access: link latency | page, docstring | `docs/development/link-health.md`; `transport/link_latency.py` |
| WiCAN access: frame counts, ledger, bridge, seam | page | `docs/development/frame-counts.md` |
| Transports: three specs, one engine | stays, docstring | `AGENTS.md`; `transport/config.py`, `transport/elm327/terminal.py` |
| Transports: pipe, addressing, session, eeprom | docstring | `transport/elm327/pipe.py`, `addressing.py`, `session.py`, `eeprom.py` |
| Transports: `is_wican_http`, ELM-only gating | docstring | `transport/config.py` (added) |
| Device sleep; offline testing | stays, page | `AGENTS.md`; `docs/development/offline-testing.md` |
| Historical note (no `ecus.yaml`/`dtc.yaml`) | dropped | none: nothing reads either file |
| `read` never had `--monitor` | dropped | none: historical |

Skill pointers: `contributing-code` (Transports now reads "WiCAN access and transports", and
names the two transport pages) and `wican-hardware-and-protocol` (the desync checklist points at
`docs/development/link-health.md`). The other pointers name sections that still exist
(Vocabulary, Formatting, Analysis, Keep docs and README current).
