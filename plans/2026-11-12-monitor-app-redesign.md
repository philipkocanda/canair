# Monitor: design explorations for an application shell

Status: **EXPLORING** (2026-11-12). Three directions and a combination (D) of the first two,
built as runnable mockups. D is the direction to refine. No production code changed.

The monitor TUI (`canair monitor`) is to become a full application: a proper menu or toolbar and a
redesigned status bar. This document compares three directions for that shell, then describes the
combination chosen from them. Each one is a throwaway Textual app with fake data, so the comparison
is about how the screen feels, not about what is wired.

## Run the mockups

```
uv run python scripts/prototypes/monitor_redesign/run.py desktop
uv run python scripts/prototypes/monitor_redesign/run.py palette
uv run python scripts/prototypes/monitor_redesign/run.py workbench
uv run python scripts/prototypes/monitor_redesign/run.py combined
uv run python scripts/prototypes/monitor_redesign/run.py --shots   # regenerate the PNGs below
```

- **The screenshots are not committed** (`shots/` is gitignored). Run `--shots` once to see the
  images in this document; it needs `rsvg-convert` (`brew install librsvg`).
- The mockups never open a socket. The values pane is the production renderer
  (`live/monitor/render.py`) drawing payloads recorded on the bundled Ioniq. A few bytes move each
  poll cycle, so values change and the change highlights fire.
- Every command from today's monitor is in every mockup, listed once in
  `scripts/prototypes/monitor_redesign/commands.py`. The keys come from the real keymap
  (`tui/keys.py`), so the letter keys work in all of them. Commands that the mockup cannot do show
  a "not wired" message.
- `ctrl+b` puts the session into trouble (frames lost, an output left on, polling paused, an error
  message). `ctrl+t` switches between the terminal palette (`ansi-dark`) and Textual's own theme.
- Resize the terminal below 72 columns to see the narrow layout. The shots use 120×34 and 46×26.

## What today's screen does, and the problems found

Today: a one-line header (recording label, states, the picker hint), the values, and a two-line
status bar. Line 1 holds the metrics and the last message. Line 2 holds the selection, the filter
and 16 key hints. Both lines drop items by priority as the terminal narrows (`tui/status.py`).

Problems found while building the mockups, independent of the direction chosen:

1. **Keys are found only by luck or `?`.** The hint line is the only place that teaches a key. All
   16 hints need about 220 columns, so most terminals show only some of them.
2. **A message competes with the metrics.** The flash is one more item on status line 1. It
   outranks every counter, so a long message pushes the counters off the line.
3. **"Session" means two things.** `s` "save / label", `n` "new session" and `i` "session info" act
   on the *recording*. `t` "session type" and the `sess` status item act on the *UDS diagnostic
   session*. All three mockups say "recording" for the first and "diagnostic session" for the
   second.
4. **Bare hex in the status bar.** `sess BMS:81` shows the session type without its name, which the
   cli-tui-standards skill forbids ("name bare bytes"). The mockups show `BMS 81 standard`, with the
   name from `uds/subfunctions.py::subfunction_name`.
5. **Duplicated text.** The header and status line 2 both show the picker hint. The body's first
   line (`Monitor — cycle N (last …, interval …)`) repeats the status bar.
6. **The values table wraps on a narrow terminal.** `render_param_table` pads each column to its
   widest cell, so at 46 columns every signal takes two lines. The mockups use a compact table below
   96 columns: the name gives way (`…`), the value and the verified mark stay on the line
   (`fake.py::render_compact`).
7. **The profile and the device are not shown.** A user with two cars or two dongles cannot see
   which one the monitor talks to.

## Common ground

The three directions share these pieces, so they can be mixed:

- **One command catalogue.** Menus, the palette and the key bar are views of one list
  (`commands.py`). Each entry has a label, a menu, a key role, a help line, and a state (toggle or
  choice). A command needs no second description anywhere.
- **A status model of four zones**, always in the same order: **link** (verdict, device, transport,
  round trip), **poll** (live or paused, cycle, rate), **record** (`● REC`, elapsed time, frames),
  **car** (an output left on, the vehicle state, the diagnostic sessions). Each direction lays the
  zones out its own way. A fact has a fixed place, so a problem reads as "the link zone went red".
- **The link verdict is a word plus a glyph**, never a coloured dot alone: `✓ link`, `⚠ 2 resync`,
  `✗ 3 drops`, `⟳ reconnecting…`.
- **Messages have their own place**, so no status item ever moves for one.

## Direction A — Desktop

A menu bar, a toolbar, a status bar of fixed zones, and a message line.

![A, wide](../scripts/prototypes/monitor_redesign/shots/a-desktop-wide.png)

![A, the Poll menu](../scripts/prototypes/monitor_redesign/shots/a-desktop-menu.png)

![A, in trouble](../scripts/prototypes/monitor_redesign/shots/a-desktop-trouble.png)

| Narrow | Narrow, in trouble |
|---|---|
| ![](../scripts/prototypes/monitor_redesign/shots/a-desktop-narrow.png) | ![](../scripts/prototypes/monitor_redesign/shots/a-desktop-narrow-trouble.png) |

- **Menu bar:** Record, Poll, View, Signal, Actuators, Help. `F10` opens the first menu,
  `alt+letter` opens one, `←`/`→` move between menus, a click opens one. Each item shows its key on
  the right, `[x]` for a toggle and `(•)` for a choice (the view mode, the filter). An item that
  needs a selection or `--actuate` is disabled, not hidden.
- **Toolbar:** the commands used most, each with its key, in groups that never reflow. A toggle that
  is on is drawn reversed. A mode shows its value and opens a dropdown (`View: full ▾`). Hover shows
  a tooltip.
- **Status bar:** the four zones separated by `│`, the car zone pinned right. **Message line** below
  it: the last message, or the selected signal (`▶ BMS 2101 SOC_BMS · 85.5 % · ✓ verified · B09/2`)
  when there is none.
- **Narrow:** the toolbar goes, the menu titles shorten (`Rec Poll View Sig Act Help`), the zones
  take two lines.

For: the most discoverable. A newcomer can find every command without `?`, by keyboard or mouse,
and learns its key on the way. The toolbar makes the current modes visible.

Against: five rows of chrome (today: three), so 29 value rows at 34 instead of 31. `alt+letter`
needs "Option as Meta" in macOS terminals. Menus are a second route to keys that already exist,
so the menus must stay in step with the keymap. The catalogue makes that cheap.

## Direction B — Palette

No menus. A command palette, a status line that leads with pills, a contextual key line, toasts.

![B, wide](../scripts/prototypes/monitor_redesign/shots/b-palette-wide.png)

![B, the command palette searching "rec"](../scripts/prototypes/monitor_redesign/shots/b-palette-commands.png)

![B, in trouble](../scripts/prototypes/monitor_redesign/shots/b-palette-trouble.png)

| Narrow | Narrow, in trouble |
|---|---|
| ![](../scripts/prototypes/monitor_redesign/shots/b-palette-narrow.png) | ![](../scripts/prototypes/monitor_redesign/shots/b-palette-narrow-trouble.png) |

- **Title line:** the recording label and states on the left, profile and device on the right.
- **Command palette (`ctrl+p`):** Textual's own palette (the monitor turns it off today), fed from
  the catalogue. It searches labels and help text ("ruler", "slow", "rec") and shows each command's
  key and its toggle state.
- **Status line:** pills (reversed blocks) for the states that change what the user should do:
  `LIVE`/`PAUSED`/`RECONNECTING`, `● REC`, `✗ 3 drops`, `⚠ 1 ON`. Plain facts follow, the vehicle
  state is pinned right. An output that is on outlives every other pill.
- **Key line:** the keys of what has focus. With a signal selected it is
  `e edit · v verify · x toggle ship · esc deselect`. `ctrl+p`, `?` and `q` are always there.
- **Messages are toasts** at the top right, with the severity glyph (a toast's colour bar alone
  would carry meaning by colour).
- **Narrow:** the title loses its right half, the status keeps its pills, the key line keeps only
  `ctrl+p commands · ? help · q quit`.

For: the least chrome (three rows, the same as today) and the most room for values. The palette
costs little: Textual provides it, and the other canair TUIs could share the provider.

Against: a command is only found if the user knows to press `ctrl+p` and can guess a word for
it. No mouse route. A toast covers values for a few seconds, and on a phone it covers half the
screen. At 46 columns in trouble the pills alone fill the status line, so `● REC` and the vehicle
state drop off.

## Direction C — Workbench

An instrument cluster on top, three panes (PIDs, values, signal), and a function-key bar.

![C, wide](../scripts/prototypes/monitor_redesign/shots/c-workbench-wide.png)

![C, F9 opens the menu bar over the cluster](../scripts/prototypes/monitor_redesign/shots/c-workbench-menu.png)

![C, in trouble](../scripts/prototypes/monitor_redesign/shots/c-workbench-trouble.png)

| Narrow | Narrow, in trouble |
|---|---|
| ![](../scripts/prototypes/monitor_redesign/shots/c-workbench-narrow.png) | ![](../scripts/prototypes/monitor_redesign/shots/c-workbench-narrow-trouble.png) |

- **Cluster:** the four zones as labelled columns (`LINK`, `POLL`, `RECORD`, `CAR`).
- **PIDs pane:** the profile's ECUs and PIDs as a tree, `[x]` for a polled PID. `enter` polls a
  PID or stops polling it while the values keep updating. It replaces the picker modal.
- **Signal pane (inspector):** the selected signal's value, verified and ship state, bytes,
  expression, the range seen this run, a sparkline of its history, and its notes. The monitor
  already collects this history and does not show it.
- **Key bar:** Midnight Commander's ten slots, `F1`–`F10`, clickable. `F9` opens a menu bar over the
  cluster with every command. The letter keys keep working.
- **Messages** are the caption of the values pane's bottom border.
- **Panes:** `tab` moves between them. Between 72 and 110 columns the inspector hides. Below 72,
  one pane at a time, with a tab strip.

For: it turns the monitor into a reverse-engineering workbench. Picking PIDs and reading a signal's
definition no longer need a modal. It continues the "monitor made of panes" step that the actuator
pane started (`plans/2026-11-07-monitor-actuator-pane.md`).

Against: the most chrome (26 value rows of 56 columns at 120×34). The most work: the picker,
the edit dialog and the actuator pane all move into panes. Function keys are awkward on a Mac
keyboard (`fn`), and the narrow key bar shortens labels to two letters (`1He 2Pi`).

## Direction D — the combination of A and B

A's menu bar, B's command palette, and one status line that uses A's zones with B's badges. A's
toolbar and B's toasts are dropped. Three rows of chrome, as today.

![D, wide](../scripts/prototypes/monitor_redesign/shots/d-combined-wide.png)

![D, the View menu](../scripts/prototypes/monitor_redesign/shots/d-combined-menu.png)

![D, the command palette searching "rec"](../scripts/prototypes/monitor_redesign/shots/d-combined-commands.png)

![D, modes that are not the default](../scripts/prototypes/monitor_redesign/shots/d-combined-modes.png)

![D, in trouble](../scripts/prototypes/monitor_redesign/shots/d-combined-trouble.png)

| Narrow | Narrow, in trouble |
|---|---|
| ![](../scripts/prototypes/monitor_redesign/shots/d-combined-narrow.png) | ![](../scripts/prototypes/monitor_redesign/shots/d-combined-narrow-trouble.png) |

**From A**

- **Menu bar** with each item's key, `[x]` for a toggle and `(•)` for a choice. `F10`,
  `alt+letter` or a click opens it. The recording label and states sit on its right, and the
  profile name when there is room, so no title line is needed.
- **Fixed zones** in the status line: link │ poll │ recording, the car zone pinned right. Below 72
  columns the status takes two lines instead of dropping facts.
- **A bottom line for context**: the last message, or the selected signal when there is none.

**From B**

- **`ctrl+p` palette** over the same catalogue as the menus. It is also in the Help menu.
- **Badges only for exceptions**: `PAUSED`, `RECONNECTING`, `✗ drops`, `⚠ ON` are reversed blocks.
  The normal state is plain text (`LIVE` is green, not a badge), so an exception stands out.
- **The keys of what has focus** on the bottom line, with `F10 menu · ctrl+p commands · ? help` on
  its right.

**What replaces the dropped parts**

- **Modes:** the View menu shows every mode. A mode that is not the default also shows on the bottom
  line (`view signals · filter verified · ruler`).
- **One-click mouse access:** the status zones are clickable. Link opens the event log, poll opens
  the Poll menu above the status line, recording opens recording info, car opens the diagnostic
  session picker.
- **Messages:** on the bottom line, with the severity glyph, never over a value. An error stays
  until the next key. Every message also goes to the event log (`E`, or a click on the link zone).

**Priorities on a narrow line**

- Status: an output that is on outranks every other fact, then a link fault and `PAUSED`, then
  `● REC`, then the vehicle state.
- Bottom line: `? help` outranks the selected signal, which outranks `F10 menu` and `ctrl+p`. With
  nothing selected the two routes stay.

## Comparison

| | Today | A — Desktop | B — Palette | C — Workbench | D — Combination |
|---|---|---|---|---|---|
| Value rows at 120×34 | 31 | 29 | 31 | 28 (56 columns wide) | 31 |
| Value rows at 80×24 | 21 | 19 | 21 | 18 | 21 |
| Value rows at 46×26 | 23 | 21 | 23 | 19 | 22 |
| Find a command without `?` | hint line | menus, toolbar | palette (needs a word) | key bar, `F9` menus | menus, palette |
| Mouse | no | menus, toolbar | no | key bar, panes, tree | menus, status zones |
| Shows the current modes | hint line items | toolbar | palette entries | key bar, inspector | menus, bottom line |
| Where a message goes | status line 1 | message line | toast | pane border caption | bottom line |
| New key roles | — | `F10`, `alt+letter` | `ctrl+p` | `F1`–`F10` | `F10`, `alt+letter`, `ctrl+p` |
| Size of the change | — | medium | small | large | medium |

## Observations

- The catalogue feeds a menu bar and a palette at the same time, so A and B are not exclusive. That
  is what D does. C's inspector and PIDs pane could arrive later as panes in D's shell, using the
  pane support that the actuator pane added.
- The four-zone status model, the separate message place, the compact narrow table and fixes 3–5
  above apply to every direction.
- A key role for each new shell key (`menu`, `palette`) goes into `tui/keys.py`, and
  `tests/contracts/test_tui_keymap.py` must accept them. Messages that leave the status bar change
  `tui/flash.py` and `tui/status.py`, which every TUI shares.

## Not verified

- The screenshots come from Textual's headless renderer through `rsvg-convert`. The mockups have
  not been run in a real terminal, so colours, the `ansi-dark` theme and box glyphs may differ.
- Delivery of `F9`/`F10`, `alt+letter` and mouse clicks in macOS Terminal, iTerm2 and a phone SSH
  client (Termius).
- Repaint cost: the mockups repaint every 0.25 s without the production render cache.

## Open questions

1. Should the shell be shared by the other TUIs (`captures --step`, `decode --plot`, `sniff`, `io`,
   `routines`), or is it monitor-only for now?
2. Does an error that stays until the next key suit a long unattended recording, or should it also
   expire after a minute?
3. Should the status zones keep their click actions, or is the menu bar enough mouse access?
