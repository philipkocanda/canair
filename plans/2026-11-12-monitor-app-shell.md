# Monitor: an application shell (menu bar, palette, zoned status line)

Status: **IN PROGRESS** (2026-11-12) on branch `feat/monitor-app-shell`.

Builds direction D of `plans/2026-11-12-monitor-app-redesign.md` into the real monitor. That
document holds the design, the screenshots of the mockups and the reasons. This one holds the build:
where the code goes, the decisions taken, and the phases.

Paths are relative to `src/canair/`. Point at code by file and symbol: line numbers drift.

## Goal

The monitor gets a menu bar, a command palette, one zoned status line and a bottom line for
context and messages, in three rows of chrome (today: three). Every command stays on its key. The
narrow layout fits an iPhone 16 Pro in portrait with the keyboard up (about 45×22).

The frame is built once in `tui/shell/` so the other TUIs can move onto it later, one at a time.
Only the monitor moves now.

## Decisions

1. **A shared kit, one adopter.** The menu bar, the palette provider, the status line and the
   bottom line live in a new subpackage, `tui/shell/`. It knows nothing about the monitor: an app
   hands it a command catalogue, its status zones and its context. `tui/status.py` and
   `tui/flash.py` keep working for the TUIs that do not move.
2. **One command catalogue per app.** A `Command` has an id, a label, a menu, a key role, a help
   line and a kind (action, toggle, choice). The menu bar, the palette and the help sheet read it,
   so a command is described once. The action behind a command is the app's existing action
   method: the catalogue adds no second way to do anything.
3. **New key roles, through the keymap.** `menu` (`f10`) and `palette` (`ctrl+p`) join
   `tui/keys.py`. Menu mnemonics (`alt+` the underlined letter) are derived from the menu titles by
   the menu bar, not bound one by one, because the letters belong to each app's menus. The keymap
   docstring says so, and the menu bar refuses two menus with one letter.
4. **Badges only for exceptions.** `PAUSED`, `RECONNECTING`, a link fault and an output left on are
   reversed blocks. The normal state is plain text.
5. **Messages on the bottom line.** A message replaces the context until it expires. An error stays
   until the next key. Every message goes to the run's message history, which the event log
   overlay shows above the diagnostics events. Messages are not written to the central diagnostics
   log: that file is for transport faults.
6. **The status zones are clickable**: link opens the event log, poll opens the Poll menu above the
   status line, recording opens recording info, car opens the diagnostic session picker.
7. **"Recording" and "diagnostic session".** The monitor says "recording" for what `--save` writes
   and "diagnostic session" for `10 xx`. A saved recording is still a capture *session* in
   `captures uds --sessions`, and recording info says so once.
8. **A session type carries its name**: `BMS 81 standard`, from
   `uds/subfunctions.py::subfunction_name`.
9. **The values table fits its pane.** `render/formatting.py::render_param_table` takes the
   available width: a name gives way (`…`) so the value and the verified mark stay on the line.
   The TUI passes the pane width; the non-interactive path does not, and is unchanged.
10. **The device is shown.** The link zone names the device host. The controller reads it from the
    active client (`MonitorController.device_host`), so it follows a reconnect to another device.

## Placement

| Piece | Where |
|---|---|
| `Command`, catalogue, the menu-bar ladder | `tui/shell/commands.py`, `tui/shell/menubar.py` |
| The dropdown | `tui/shell/menubar.py` (`MenuScreen`) |
| The palette provider | `tui/shell/palette.py` |
| Zones, their layout, the status line widget | `tui/shell/statusline.py` |
| The bottom line, message policy and history | `tui/shell/contextline.py`, `tui/shell/messages.py` |
| The app mixin that wires them | `tui/shell/app.py` (`ShellMixin`) |
| The monitor's catalogue | `tui/apps/monitor/commands.py` |
| What the monitor's zones and context say | `tui/apps/monitor/status.py` (rewritten) |
| The width-aware signal table | `render/formatting.py` |
| `device_host` | `live/monitor/controller.py` |

## Phases

1. **The kit.** `tui/shell/` with its charter and tests: the catalogue, the menu bar and its
   ladder, the dropdown, the palette provider, the zone layout and status line, the bottom line,
   the message history, the mixin. The key roles `menu` and `palette`.
2. **The monitor's content fixes.** The width-aware signal table, the body without its
   `Monitor — cycle N` line in the TUI, session types with their names, `device_host`.
3. **The monitor on the shell.** The monitor's catalogue, zones and context; the menu bar, status
   line and bottom line replace the header and the two-line status bar; zone clicks; the message
   history in the event log; "recording" wording in the monitor's dialogs and overlays.
4. **Docs and standards.** The cli-tui-standards skill (menu bar, palette, zones, message
   placement), `docs/getting-started/first-read.md`, the monitor's `--help` epilog and the
   generated CLI reference. The live monitor screenshots need a re-record on the car, which is the
   user's call.

## Not in this work

- Moving the other TUIs onto the shell.
- The inspector and PID-tree panes of direction C.
