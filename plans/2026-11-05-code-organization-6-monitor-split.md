# Code organization, part 6: split the monitor

Status: **DONE** (2026-11-06); see "As built" at the end. Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 5
(the files are in `live/monitor/` and `tui/apps/monitor/` first). Coordinate with
`plans/2026-11-05-one-session-owner.md`, which changes `MonitorSessions` and the raw monitor.

## Goal

Make the monitor's controller and its Textual app small enough to read, and take presentation
out of the controller. After this plan, no module of the monitor family is over 500 lines, and
the controller does not import `rich`.

## Findings (at `0ce4f742`)

### `MonitorController` (`live/monitor.py`, 1,170 lines)

- The class has **75 methods in 1,009 lines**, and `__init__` alone is 168 lines with 15
  parameters. It sets about 70 attributes.
- Earlier work already moved most concerns into collaborators: `MonitorRecorder`,
  `MonitorSessions`, `MonitorSelectors`, `MonitorStateSuggester`, `ParamStats`,
  `LinkEventTracker`, `MonitorRawPoller`, `PollScheduler` and `PollBackoff`.
- **About 30 methods only forward to a collaborator.** Lines 330 to 451 are getter and setter
  pairs for `total_frames`, `unique_frames`, `hex_history`, `save_history`, `journal`,
  `session_label`, `session_states`, `session_notes`, `_state_explicit` and `interval`, plus
  one-line methods such as `suggested_state`, `state_options` and `offer_states`. A comment says
  they "keep the tested public surface (and the TUI/renderer reads) stable".
- The class still does several jobs itself:

  | Concern | Methods |
  |---|---|
  | ELM327 polling | `_poll_elm` (75 lines), `poll_once`, `_warmup_reads`, `_record` |
  | Raw polling glue | `_raw_lengths`, `_raw_nobatch`, `_build_raw_submissions`, `_poll_raw`, `_apply_raw_submission`, `_raw_build_queries` |
  | Health and backoff | `_observe_ecu_answers`, `_apply_backoff`, `reset_backoff`, `_check_liveness`, `mark_disconnected` |
  | Display | `render`, `_displayify`, `_decide_display`, `cycle_view`, `_history_render_limit`, `query_label`, `segment_title`, `session_summary`, `_ecu_ref`, `_event_rx` |
  | Life cycle | `setup`, `interrupt`, `close`, `close_client`, `rebind` |

- **The controller does presentation.** It imports `rich.text.Text`, and the private helpers
  `_render_hex_line`, `_HIGHLIGHT_STYLE` and `_bytes_to_ascii` from `render/formatting.py`.
- `live/_monitor_render.py` (550 lines) also imports `rich`. The Textual app imports
  `MONITOR_LEGEND` from it.

### `MonitorApp` (`tui/apps/monitor.py`, 1,624 lines)

- `MonitorApp` has **45 methods in 756 lines**.
- The same file holds five more screens: `SaveDialog` (174 lines), `EditParamDialog` (112),
  `EventLogModal` (84), `SessionInfoModal` (142) and `SessionPickerScreen` (130), plus helper
  functions for state tokens and status items. Sub-plan 1 moves the first two to
  `monitor_dialogs.py`.
- The app reads about 18 controller attributes, and one of them is private
  (`controller._on_partial`).

## Design

Target, after sub-plan 5 has made the packages:

```text
live/monitor/
  controller.py   MonitorController: life cycle, one poll tick, and the collaborators
  poll_elm.py     the ELM327 poll cycle (today _poll_elm, _warmup_reads, _record)
  raw_poller.py   MonitorRawPoller, plus the raw glue methods of the controller
  health.py       answers, backoff, liveness and disconnect (one collaborator)
  view.py         the display model: rows, labels, titles and the session summary, as data
  render.py       today's _monitor_render.py: turns the view into rich Text
  …               the other modules of sub-plan 5, unchanged

tui/apps/monitor/
  app.py          MonitorApp
  dialogs.py      SaveDialog, EditParamDialog
  modals.py       EventLogModal, SessionInfoModal, SessionPickerScreen
  status.py       the status-bar items and state-token helpers
```

Principles:

1. **The controller holds state and runs a tick.** It does not format text. `view.py` returns
   plain data (dataclasses). `render.py` is the only monitor module that imports `rich`.
2. **No forwarding properties** (decision E). The app and the tests read the collaborator
   directly: `controller.recorder.session_label`, not `controller.session_label`. Each
   collaborator is already a public attribute of the controller.
3. **The collaborators do not know each other.** A collaborator gets what it needs as an
   argument, not a reference to the controller. Where one needs another today, the controller
   passes the value in.
4. **`__init__` takes `MonitorOptions`.** Today `MonitorOptions` carries the options of the
   monitor run, and `__init__` takes 15 separate parameters. The controller takes the options
   object, plus the terminal, the steps and the definitions.
5. **`_on_partial` becomes public** (`on_partial`), because the app sets it.

## Decision E: remove the forwarding properties

The forwarding properties keep a stable surface for the tests and the app. They cost about 120
lines, and they hide which collaborator owns a value. A reader who sees
`controller.session_label = x` cannot tell that the recorder owns it.

**Recommendation: remove them.** Rewrite the tests and the app to use the collaborator. Keep
only the methods that do work across several collaborators.

## Steps

One concern per commit. Each one moves code and does not change what the monitor does.

1. **Remove the forwarding properties.** Rewrite their users in `tui/apps/monitor/`,
   `live/monitor/run.py` and the tests. Find the users with a search for each name. A setter
   that a test uses to set up state can often become an argument of the collaborator.
2. **Extract `health.py`.** Move the answer, backoff, liveness and disconnect logic into one
   collaborator. Keep the rule from `AGENTS.md`: a reconnect resets the backoff and the
   scheduler's slowdown together (`reset_backoff`, called from `rebind`).
   `mark_disconnected` stays the only way to mark the link down, because the link-event
   tracker depends on it.
3. **Extract `poll_elm.py`.** Move the ELM327 poll cycle. Keep the transaction rule: hold
   `terminal.transaction()` across `set_header` and `send_uds`.
4. **Move the raw glue into `raw_poller.py`.**
5. **Extract `view.py`**, and change `render.py` to take the view. The controller stops
   importing `rich` and the private helpers of `render/formatting.py`. Sub-plan 8 gives those
   helpers public names.
6. **`__init__` takes `MonitorOptions`.** Update the one place that builds a controller
   (`live/monitor/run.py`) and the tests.
7. **Split the app file.** Move the three modals to `modals.py` and the status and state-token
   helpers to `status.py`. Rename `_on_partial`.
8. Lower the ceilings, and remove the monitor modules from `oversized_modules`.

## Acceptance

- No module in `live/monitor/` or `tui/apps/monitor/` is over 500 lines.
- `live/monitor/controller.py` does not import `rich` or any underscore name from another
  package.
- `tests/live/` and `tests/tui/apps/` pass. The monitor golden outputs and the screenshots
  (`scripts/gen/gen_screenshots.py --check`) are unchanged.
- One manual run of `canair monitor` on a fixture or the ELM327 emulator, on a TTY and piped,
  with `--save`, shows no change.

## Risks

- **Test churn.** `tests/live/test_monitor.py` is 1,339 lines, and it uses the forwarding
  properties. Step 1 changes many lines of it. Keep step 1 a mechanical rewrite, with no
  change to what each test checks.
- **The one-session-owner plan.** That plan removes the raw monitor's own session handling and
  changes `MonitorSessions`. If it lands first, step 4 is smaller. If this plan lands first,
  tell the owner of that plan which files moved.
- **The reconnect paths.** `rebind`, `mark_disconnected` and the liveness check are the
  correctness-triggered reconnect that `AGENTS.md` describes. Step 2 must keep every call
  site. `tests/live/test_monitor_reconnect.py` and `tests/live/test_monitor_stop.py` are the
  check.

## As built

All eight steps landed, one concern per commit, on `refactor/co-6-monitor-split`.

| Step | Result |
|---|---|
| 1 Forwarding properties | Removed all of them and the pure forwarding methods. The app, `run.py` and the tests read `controller.recorder`, `.state`, `.sessions`, `.selectors`, `.raw_poller` or `.schedule`. The test fakes in `tests/support/` gained the same collaborators. |
| 2 `health.py` | `MonitorHealth` owns `poll_costs`, the per-ECU unanswered counts, `dead_cycles`, `cycle_answered`, backoff application and the liveness check. `reset_backoff` is `controller.health.reset_backoff`, called from `rebind`. `mark_disconnected` stays on the controller and is passed in, so it is still the only way to mark the link down. |
| 3 `poll_elm.py` | `MonitorElmPoller` has the ELM327 session setup, the poll cycle and `batch_state`. The raw setup went into `MonitorRawPoller.setup`, the shared warm-up plan into `MonitorSessions.warmup_reads`. |
| 4 Raw glue | Nothing was left in the controller after step 1 (the `_raw_*` methods were forwarders). |
| 5 `view.py` | `MonitorView` (`controller.view`) returns labels, titles, the session summary and view modes as plain values. `render.py` is the only module that builds a block of Rich text (`MonitorRenderer`, `controller.renderer`), with `render_cache.py` split out. The controller imports no `rich` and no private name of another package, and no longer re-exports names for old paths. |
| 6 Options | `MonitorController(terminal, options, pids_data, raw_client=None)` and `mode_monitor(terminal, options, pids_data, *, raw_client, transport_type, reconnect)`. `MonitorOptions.mode_kwargs` is gone. `cli/live/handlers/multi.py` changed at its one call. Tests use `tests/support/monitor_controller.py::make_controller`. |
| 7 App split | The three modals were already in `overlays.py`. `status.py` holds the status-bar and header content, `polling.py` and `recording.py` are mixins for the poll loop and the save keys. `_on_partial` is `on_partial`. |
| 8 Ceilings | `controller`, `render`, `record` and `app` left `oversized_modules`. `deferred_internal_imports` fell from 1114 to 1107. |

Also: `record.py` was 539 lines, so `merge_history`, `write_merged` and `open_journal`
(no longer underscored) moved to `record_write.py`.

Measured at the end (limit 500): `controller.py` 500 (was 1,152, 18 methods), `app.py` 478
(was 919), `render.py` 454 (was 550), `raw_poller.py` 452, `record.py` 449 (was 539).
No module of `live/monitor/` or `tui/apps/monitor/` is over 500 lines.

Deviations: collaborators still hold a back-reference to the controller (`self.c`); principle 3
(pass values in) was not done, because the plan's steps do not call for it. `query_ecu_error`
stays in `controller.py` because tests and two commands patch it by that path. `diag()` and
`diag_recorder()` are still two names for one body.

Manual run: `canair monitor` piped against ELM327-Emulator (`--save`, three Mode-01 PIDs, 25 s,
then SIGTERM) polled, journaled, reconciled 72 captures, wrote the frame counts back and
printed the final values. The TTY screen was checked only through the Textual tests and
`scripts/gen/gen_screenshots.py --check` (unchanged).

