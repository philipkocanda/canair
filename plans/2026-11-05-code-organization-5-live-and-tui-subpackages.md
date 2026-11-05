# Code organization, part 5: sub-packages in `live/` and `tui/apps/`

Status: **IN PROGRESS** (2026-11-05): `live/identity`, `elm`, `discover`, `actuate`, `scan` done;
`read`, `monitor` and the `tui/apps` sub-packages follow. Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 2
(hoisted imports make a move safer). This is left-over item 4 of
`plans/2026-11-04-package-layout-and-layering.md`, with the layout of its section 4.

## Goal

Group `live/` and `tui/apps/` by family, in sub-packages, instead of by filename prefix. This
is a move only. No logic changes.

## Findings (at `0ce4f742`)

- `live/` holds 62 files (14,200 lines) and one sub-package (`dtc/`). The families are visible
  only in the file names: `_monitor_*` (10 files), `monitor_*` (6), `multi*` (4), `iocontrol*`
  and `_iocontrol_*` (5), `identity*` (3), `discover*` (3), `*scan*` (8), `routines*` (2),
  `kwp_*` (2), `poll_*` (2).
- A leading underscore marks some modules as private to their family (`_monitor_record.py`).
  Other modules of the same family have no underscore (`monitor_raw.py`). Seven modules
  outside `live/` import a `live._monitor_*` module.
- 93 files outside `live/` import a `canair.live.*` module. `canair.live.monitor` alone has 46
  references.
- `tui/apps/` holds 12 files with the same prefix grouping: `captures_step_*` (5),
  `decode_plot_*` (3), `monitor*` (2), `sniff`.
- The placement table in `docs/development/architecture.md` already sends code to
  `live/<family>/` and `tui/apps/<app>/`. Sub-plan 1 corrects the table to match the tree
  until this plan lands.
- The CLI parser imports `live/poll_schedule.py` and `live/scan_presets.py` at start-up.
  `live/__init__.py` imports nothing, so these imports stay cheap.
- `live/discover_render.py` imports only `render.ansi`. It is a renderer in the live layer.

## Target layout

`live/`:

| Sub-package | Modules (today → after) |
|---|---|
| `read/` | `multi` → `multi`, `multi_exec` → `exec`, `multi_batch` → `batch`, `multi_repl` → `repl`, `param` → `param`, `raw` → `raw`, `raw_ops` → `raw_ops` |
| `monitor/` | `monitor` → `controller`, `_monitor_run` → `run`, `_monitor_record` → `record`, `_monitor_render` → `render`, `_monitor_history` → `history`, `_monitor_link_events` → `link_events`, `_monitor_selectors` → `selectors`, `_monitor_sessions` → `sessions`, `_monitor_state` → `state`, `_monitor_stats` → `stats`, `monitor_edit` → `edit`, `monitor_options` → `options`, `monitor_raw` → `raw_poller`, `raw_monitor` → `raw_run`, `monitor_reconnect` → `reconnect`, `monitor_select` → `select`, `poll_schedule` → `schedule`, `poll_backoff` → `backoff` |
| `scan/` | `scan` → `range`, `scan_presets` → `presets`, `scan_state` → `progress`, `sessions_scan` → `sessions`, `routines_scan` → `routines`, `kwp_routines_scan` → `kwp_routines`, `iocontrol_scan` → `iocontrol`, `kwp_iocontrol_scan` → `kwp_iocontrol`, `discovery_scan` → `discovery` |
| `discover/` | `discover` → `physical`, `discover_functional` → `functional`, `functional_sweep` → `sweep` |
| `identity/` | `identity` → `read`, `identity_decode` → `decode`, `identity_records` → `records` |
| `actuate/` | `iocontrol` → `iocontrol`, `_iocontrol_actuate` → `iocontrol_actuate`, `_iocontrol_render` → `iocontrol_render`, `iocontrol_execute` → `iocontrol_execute`, `routines` → `routines`, `action` → `action` |
| `elm/` | `interactive` → `repl`, `skm_wakeup` → `skm_wakeup`, `tester` → `tester` |
| `dtc/` | unchanged |
| (top level) | `session`, `status`, `revision_probe` stay |

`live/discover_render.py` moves to `render/discover.py`.

`tui/apps/`:

| Sub-package | Modules (today → after) |
|---|---|
| `captures_step/` | `captures_step` → `app`, `captures_step_model` → `model`, `captures_step_render` → `render`, `captures_step_join` → `join`, `captures_step_gaps` → `gaps` |
| `decode_plot/` | `decode_plot` → `app`, `decode_plot_model` → `model`, `decode_plot_draw` → `draw` |
| `monitor/` | `monitor` → `app`, `monitor_dialogs` (from sub-plan 1) → `dialogs` |
| `monitor_select/` | `monitor_select` → `app` |
| `sniff/` | `sniff` → `app` |

Inside a sub-package, a module name has no leading underscore. The sub-package is the unit of
privacy, and its charter says which modules other packages may use.

## Rules for the new sub-packages

1. **Every sub-package `__init__.py` holds only its charter and imports nothing.** Two things
   depend on this:
   - The CLI parser imports `live/monitor/schedule.py` and `live/scan/presets.py` at start-up.
     If `live/monitor/__init__.py` imported the controller, start-up would load the whole
     monitor.
   - The view models `tui/apps/captures_step/model.py` and `tui/apps/decode_plot/model.py`
     have no Textual import, because their commands also render them off a TTY. If the
     sub-package `__init__.py` imported `app`, importing the model would load Textual.
2. **Add a contract test for both.** In `tests/contracts/`, import
   `canair.live.monitor.schedule` and `canair.live.scan.presets` in a fresh interpreter and
   check that no other `canair.live` module and no `textual` module is loaded. Do the same for
   the two view models and `textual`.
3. **Each sub-package gets a charter** (Belongs, Not here, Imports). The structure test already
   requires one on every package.

## Decision C: `tui/kit/`

The layout plan put the shared Textual kit (`keys`, `help`, `modals`, `scroll`, `status`,
`widgets`) in `tui/kit/`. Today these modules are at the top of `tui/`. 20 source and test
files import them, `AGENTS.md` names `src/canair/tui/keys.py`, and so does the cli-tui-standards
skill.

**Recommendation: do not create `tui/kit/`.** The kit is already the top level of `tui/`, and
the apps are in `tui/apps/`. The move would rename files that agents look for, and it would not
make any boundary clearer. Sub-plan 1 removes `tui/kit/` from the placement table.

## Steps

One family per commit. In each commit:

1. `git mv` the files and rename them as the tables say.
2. Run a codemod for imports and `monkeypatch` strings in `src/` and `tests/`.
3. Move the family's tests to the mirror directory (`tests/live/monitor/` and so on).
4. Rename the keys in `baseline.json` by hand **before** `test_structure --write`, for
   `oversized_modules` and for the `ui_standards` entries of the cli-tui-standards plan.
   Otherwise the ceilings are lost and the modules count as new.
5. Write the sub-package charter. Regenerate the architecture page.
6. Grep the moved files for `^\s*from \.` at every indent. A module that goes one level deeper
   breaks every relative import that pointed above it.

Order, smallest first, so the procedure is proven before the large families:

1. `live/identity/`
2. `live/elm/`
3. `live/discover/`, and `discover_render` to `render/discover.py`
4. `live/actuate/`
5. `live/scan/`
6. `live/read/`
7. `live/monitor/`, with the contract test of rule 2
8. `tui/apps/sniff/`, `tui/apps/monitor_select/`, `tui/apps/monitor/`
9. `tui/apps/decode_plot/`, `tui/apps/captures_step/`, with the contract test of rule 2
10. Update the placement table to `live/<family>/` and `tui/apps/<app>/`, and the `live/`
    charter. Update the contributing-code skill, which names `live/identity.py` and
    `live/<name>.py`.

## Acceptance

- `live/` holds only `__init__.py`, `session.py`, `status.py`, `revision_probe.py` and the
  eight sub-packages.
- `tui/apps/` holds only `__init__.py` and the five sub-packages.
- The contract tests of rule 2 pass.
- `--help`, the golden outputs and `scripts/gen/gen_screenshots.py --check` are unchanged.
- `canair --help` start-up time is unchanged (within 10 ms).

## Risks

- **Concurrent work.** `live/` is the most edited package in the tree. On 2026-11-05 alone,
  other sessions changed `live/iocontrol.py`, `live/discover.py` and `live/multi.py`. Land each
  family on a quiet tree, and check with the user before the `monitor` and `read` commits.
- **Sub-plan 6 changes the monitor.** Do the `live/monitor/` move first, then the split.
  Splitting first would book every rename twice.

## As built (in progress)

- The codemod is a script outside the repo (`codemods/p5/codemod.py`): it rewrites absolute and
  relative imports, `from pkg import module` forms and dotted strings in one pass, then moves the
  files. `scripts/refactor/move_symbols.py rename-module` cannot do a family at once, because
  `monitor` to `monitor.controller` would rewrite the new names again.
- `live/` held seven modules the plan did not list. They went to: `_not_found`, `iocontrol_list`,
  `routines_list`, `routines_execute` to `actuate/` (`not_found`, ...), `multi_json` to `read/`,
  and `progress_line` (printed through by discover, scan and tester) to `render/progress.py`
  beside `render/discover.py`.
- Tests that span several families (`test_json_hygiene`, `test_oneshot_save_provenance`,
  `test_sweep_markers`, ...) stay in `tests/live/`.
