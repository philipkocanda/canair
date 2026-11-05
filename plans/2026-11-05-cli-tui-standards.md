# CLI and TUI standards: one rule set for colour, text, prompts, keys and modals

Status: **DONE** (2026-11-05), apart from the leftovers listed at the end. Phases 1–6 are
implemented; `tests/contracts/test_ui_standards.py` keeps the tree from drifting back.

## Why

canair has 40-odd commands and five Textual apps, and they grew independently. Some areas
already have one home and a test: the gated palette (`render/ansi.py`,
`plans/2026-08-06-ansi-palette-consolidation.md`), the key roles (`tui/keys.py` with
`tests/contracts/test_tui_keymap.py`) and the shared modals (`tui/modals.py`). Most rules about
the interface exist only as habit, so each new command picks its own:

- `discover` printed no colour at all until commit `476bb663`. That commit added
  `live/discover_render.py`, the only module that writes down what each colour means.
- Plain output reaches the terminal through four mechanisms: the `ansi` palette, Rich (45
  modules), raw `\033[` codes and no colour. Rich applies `NO_COLOR` differently from `ansi`. It
  also wraps at 80 columns when stdout is piped.
- One colour has many meanings. Yellow is "warning" and also "PID id", so `read` prints every
  PID header in the warning colour. Cyan has six or more meanings. In three TUIs a failure such
  as "Save failed" flashes green, the same as a success.
- Error messages use five prefix styles (`error:`, `Error:`, `  Error:`, `ERROR`, none), on
  stdout and stderr, with exit codes 0, 1 and 2 for the same kind of outcome.
- Three commands confirm nothing before a state change, or lose data when a prompt cannot be
  answered (phase 2).

This plan states the rules once (in a skill), fixes the safety items now, and phases the rest.
The deviation report at the end is the backlog.

## Decisions (2026-11-05)

| # | Question | Decision |
|---|---|---|
| 1 | Where the standards live | A skill only: `.claude/skills/cli-tui-standards/SKILL.md`, plus one-line pointers in `AGENTS.md` and `contributing-code`. No `docs/development/` page. |
| 2 | Rich in plain CLI output | Allowed, but only through one console factory in `render/`. The factory applies `ansi.use_color` for its stream, sets `highlight=False` and does not wrap when the stream is not a TTY. |
| 3 | `EditError` output | Unify `  Error: …` (red, indented, exit 1) into `error: …`. Update `AGENTS.md` to match. |
| 4 | Exit codes | 0 success. 1 failure or a declined prompt. 2 usage error or a refusal by a guard (safety, read-only profile, a prompt needed with no TTY). |
| 5 | `q` in a read-only overlay | `q` and `esc` both close a read-only overlay (help, event log, session info, the plot info view). `q` quits only at the top level. This is the pager convention (`less`, `man`, `htop` help) and what Help, the event log and session info do today. In a modal with a text input, `q` is text, so only `esc` closes it. |
| 6 | `select_from_list` and `esc` | Allowed. A one-shot picker that returns a value is a dialog, so `esc` cancels it. The skill states this exception to "escape never quits". |
| 7 | Skill name | `cli-tui-standards`. |
| 8 | Scope of this round | Phase 1 (skill and pointers), the deviation report (this file) and phase 2 (safety fixes). The ratcheted contract tests are phase 3, later. |
| 9 | What cyan means | A name the user can type back: ECU, PID, profile, variant, group, bus, state, a command to copy. Units, counts, fill lines and other uses move to dim or plain. |
| 10 | PID ids | Cyan, like other typeable names. Yellow then means only warning, unverified or NRC (negative response code). |

## The standards

This is the content of the skill. The skill states each rule and points at the code that
carries it. It does not copy that code.

### Streams and exit codes

- stdout holds the result. Errors, warnings, notes, hints, progress, prompts and banners go to
  stderr.
- With `--json`, stdout holds only the JSON document. The shape of that document belongs to
  `plans/2026-08-06-json-output-convention.md`, not to this plan.
- A command that reports a result offers `--json`. A command that accepts `--json` honours it.
- Exit codes follow decision 4.

### Messages

| Kind | Form | Stream |
|---|---|---|
| error | `error: …` | stderr, red when stderr is a TTY |
| warning | `warning: …` | stderr, yellow |
| note | `note: …` | stderr, plain |
| hint | `hint: …` (a command to try, in cyan) | stderr, dim prefix |

All lower case, not indented, one line where possible. A command name before the level
(`hunt: warning: …`) is not used. The top-level handler in `cli/__init__.py` prints every
`ProfileError`, `EditError`, `SafetyBlocked` and `LegacyCaptureError` this way.

### Colour

- Every coloured byte is gated by `render/ansi.py::use_color` **on the stream it is written
  to**. Gated names (`ansi.RED`) follow stdout, so they are for stdout only.
- Plain output colours through one stream-aware painter. `live/discover_render.py::Painter` moves
  to `render/` for this.
- Rich output comes only from the console factory (decision 2). Text from data (profile names,
  paths, notes, error text) is escaped before it goes into Rich markup.
- No raw escape codes outside `render/ansi.py`. Screen control in `tui/__init__.py` is the one
  exception.
- Colour never carries meaning alone. A glyph or a word goes with it (`tui/widgets.py` already
  does this for checkboxes).

| Style | Meaning |
|---|---|
| red | error, failure, refusal |
| yellow | warning, needs attention, unverified, NRC |
| green | success, OK, verified |
| cyan | a name the user can type back (decision 9) |
| bold | a heading |
| bold cyan | a heading that names an entity |
| dim | secondary text: column headers, paths, sources, tips |

A domain may add a meaning only where the table has none, for example the byte styles in
`render/formatting.py` (changed, support map, uncovered). It writes the meaning in a legend next
to the style.

### Glyphs

| Glyph | Meaning |
|---|---|
| `✓` | success, verified |
| `✗` | failure (`✖` is not used) |
| `⚠` | warning, always yellow |
| `→` | "the result is at" (saved path, rename target) |
| `▶` | cursor |
| `·` | separator |
| `—` | empty cell |
| `…` | truncation (one character, never `...`) |
| `!!` | the start of a danger prompt |

### Prompts

- One shared helper, `core/prompt.py` (phase 2): `confirm(question, *, yes, danger=False)` and
  `typed_confirm(question, word, *, yes)`. Both write to stderr.
- `[y/N]` with "no" as the default. A default of yes is not used.
- Every prompt has a flag that answers it: `--yes` for a confirmation, a value flag (`--label`)
  for an input.
- If stdin is not a TTY, the helper does not prompt. It raises an error that names the flag,
  and the command exits 2.
- `--json` never means yes. A `--json` run that needs a confirmation needs `--yes` too.
- A typed confirmation (`YES` or the action name) is only for actuation and safety bypass.
- A declined prompt prints `Aborted — nothing changed.` and exits 1.
- A recording is never discarded because a prompt could not be answered.

### Layout

- Two-space indent for report lines.
- List views follow the existing pattern (`variants`, `groups`, `bus`, `states`):
  `Title — N in <profile>`, then the rows, then a dim `source: <path>` footer.
- Column headers are dim. Columns are padded on the plain text, so they align with colour on
  or off (`discover_render.column_widths`).
- Width comes from `core/terminal.py`, never a fixed number.
- Every definition write confirms through `yaml_edit/echo.py::echo_edit`, every capture write
  through `captures/__init__.py::saved_banner`. Both name the full path.
- Progress lines go to stderr, only on a TTY, and are removed when done.

### TUI keys

- A key is bound only through `tui/keys.py::bind`. This rule exists and is enforced.
- A key hint (status bar, modal hint line) is built from the role: `keys_for(role)` plus the
  role's description. A hint is never hand-written text.
- `?` opens help on every screen, modals included.
- `esc` goes back one level and never quits the app. `q` quits the app at the top level and
  closes a read-only overlay (decision 5).
- `ctrl+q`, which Textual adds to every app, is either a role in `keys.py` or removed.

### TUI screens and modals

- An overlay on an app is a `ModalScreen`. A plain `Screen` pushed over an app must block the
  app's keys itself.
- Generic dialogs come from `tui/modals.py`. One shared filter-and-list picker replaces the
  three copies in `monitor.py`, `captures_step.py` and `decode_plot.py`.
- Anatomy: a bold sentence-case title, the body, buttons on the right (primary first, Cancel
  last), a hint line at the bottom. It fits 80×24.
- Border `$accent`. A destructive dialog uses `$error` and starts with focus on Cancel.
- Cancel returns `None`. `ConfirmModal` is the one exception: it returns a `bool`.
- Every view that uses colour for meaning has a `HELP_LEGEND`.

### TUI inputs and feedback

- A dialog checks its input itself and shows the error inside the dialog. A required field is
  enforced there, not after the dialog closes.
- A one-field dialog submits on `enter`. A form submits on `ctrl+s` (the `apply` role).
- All filter inputs use one placeholder, `type to filter…`.
- A transient message goes through one flash helper with a severity: error red, warning yellow,
  success green, info plain.

### Styling and wording

- Textual CSS uses theme variables only (`$accent`, `$error`, `$warning`, `$text-muted`, …).
- Rich style names in a TUI come from one semantic style table in `render/`, the same meanings
  as the colour table above.
- Sentence case for titles, labels and buttons. Buttons are verbs (`Save`, `Delete`, `Cancel`).
- Say "signal", not "parameter" or "param" (`AGENTS.md` → Vocabulary).
- Key notation: `esc`, `enter`, `tab`, `space`, `ctrl+s`, `↑/↓`, `pgup/pgdn`.
- A flash message has no trailing period.

## Phase 1 — the skill and the pointers (this round)

1. Write `.claude/skills/cli-tui-standards/SKILL.md` from the standards above.
   - Frontmatter `description` as a trigger: load when adding or changing a command's output, a
     prompt, a Textual app, a modal or a key binding.
   - Point at `render/ansi.py`, `tui/keys.py`, `tui/modals.py`, `tui/help.py`, `tui/status.py`,
     `tui/widgets.py` and `core/prompt.py`. Do not copy their contents.
   - A short review checklist at the end, for use on a diff.
   - Mark each rule that phase 3–6 has not reached yet as "target", with a pointer to this plan,
     so an agent does not copy a deviation as precedent.
   - Under 300 lines, hard-wrapped at 100 columns.
2. `AGENTS.md`: name the skill in the Tools workflow note and in the TUI-keys paragraph.
3. `contributing-code`: add the skill to its related-skills line.
4. Check: `uv run pytest -q tests/contracts/test_validate_skills.py`.

## Phase 2 — safety fixes (done, 2026-11-05)

Each item is one commit with a regression test that fails without the fix.

1. **`core/prompt.py`.** `confirm` and `typed_confirm` write to stderr, default to no, and raise
   `PromptUnavailable` (naming `--yes`) when stdin is not a terminal. `cli/__init__.py` and
   `live/session.py::run_session_guarded` turn it into `error: …` and exit 2. A decline prints
   `Aborted — nothing changed.`
2. **`lock steal` and `lock kill` with `--json`.** They used to skip the confirmation, so
   `lock kill --json` signalled the holder unasked. Both now confirm whatever the output mode.
   `--json` with no `--yes` and no terminal exits 2 with a `needs_confirmation` outcome, and a
   decline exits 1 with `declined`.
3. **`canair io ECU --did DID`.** It now asks after the release check and before the first send,
   and has `-y/--yes`. The one-shot path moved to `live/iocontrol_execute.py` to keep
   `live/iocontrol.py` under its size ceiling.
4. **`--save` without `--label`.** The label prompt (now `captures/metadata.py`) saves under the
   suggested label when it cannot be answered: no terminal, end of input, or Ctrl-C. It keeps
   `--state` and `--notes` from the flags, prints a `note:` with
   `canair captures uds --delete --last-session`, and discards only after an empty label with no
   suggestion. `read`, `raw`, `discover` (both forms) and `scan range` use it.

   The plan first also called for a pre-flight exit 2 before connecting. That is dropped: `dtc
   --save` and `monitor --save` never prompt, and the runtime cannot tell which commands will, so
   a pre-flight check would refuse commands that work today. Saving under a generated label
   loses no data and needs no such list.

## Phase 3 — ratcheted contract tests (done, 2026-11-05)

`tests/contracts/test_ui_standards.py`, with scanners in `scripts/check/`, in the style of
`protocol_literals.py`. Each scanner gets a list baseline in `tests/contracts/baseline.json`,
set to the violations of the day it lands. A new violation then fails at once, and the old ones
can only leave the list. **Adding these baseline keys needs the user's approval** (`AGENTS.md`).

| Check | Flags |
|---|---|
| raw escapes | `\033[` or `\x1b[` outside `render/ansi.py` and the screen control in `tui/__init__.py` |
| Rich console | `rich.console.Console(` outside the factory |
| prompts | `input(` or `sys.stdin.readline` outside `core/prompt.py` |
| message prefixes | string literals starting `Error:`, `ERROR`, `WARN`, `Warning:`, `WARNING`, `Note:`, `NOTE:` |
| stream gating | `ansi.<NAME>` or `ansi.c(` in a `print(…, file=sys.stderr)` call |
| `--json` stdout | a subprocess test: each device-free command with `--json` prints stdout that `json.loads` accepts |
| colour gating | add `captures uds --backfill-states` and other uncovered commands to `tests/integration/test_ansi_gating.py::COMMANDS` |
| TUI hints | a string literal in `tui/` that names a key (`esc `, `ctrl+`, `enter `, `↑`) outside `keys.py` and `help.py` |
| TUI keys | `test_tui_keymap.py` also covers the inherited `ctrl+q`, and that `?` resolves on every `ModalScreen` |

## Phase 4 — CLI output (done, 2026-11-05)

1. The Rich console factory in `render/`. Move every `Console(` to it.
2. Move `Painter` and `column_widths` from `live/discover_render.py` to `render/`. Replace the
   private palettes in `captures/backfill_render.py` and `captures/backfill_spans_render.py`.
3. The `io` and `routines` screens (`live/_iocontrol_render.py`, `live/iocontrol.py`,
   `live/routines.py`): use the painter and the bright palette. They stop sending blink.
4. Stream gating: the ~14 sites that colour stderr with stdout-gated names (deviation list).
   `ansi.cerr` takes raw codes only.
5. Messages and exit codes: one prefix set (decision 3, decision 4). This changes visible
   output, so it is a `fix!` or `feat!` commit if a script could depend on it.
6. Prompts: move every remaining confirmation to `core/prompt.py`. Declined prompts exit 1.
   `routines --sf start` gets `--yes`. The scan wizard's "Run this scan now?" defaults to no.
7. `--json`: `read`, `monitor` and `repl` honour it or stop accepting it. Banners, the sleep
   notice, `saved_banner` and `orphan_notice` go to stderr.

## Phase 5 — colour and glyph meanings (done, 2026-11-05)

1. PID and DID ids to cyan (decision 10). Units, counts, fill lines and expressions to dim or
   plain (decision 9).
2. Red only for errors. Research P1 priority and similar non-error uses move to bold or a word.
3. One glyph per meaning (deviation list): `✖` → `✗`, `...` → `…`, `▸` → `▶`, `⚠` always
   yellow, `?` in discover replaced by a word.
4. Re-render the screenshots (`make screenshots`).

## Phase 6 — TUI (done, 2026-11-05)

1. The flash helper with severity. Failures stop flashing green.
2. Key hints built from roles, starting with the two that are wrong today (deviation list).
3. `?` in modals. The `q` behaviour of decision 5 in the plot info view.
4. `LiveMonitorSelectScreen` blocks the monitor's keys.
5. The shared filter-and-list picker, and one copy of the overlay scroll actions and the app CSS.
6. Input checks inside `TextPromptModal` (a validator argument) and `SaveDialog`.
7. One semantic style table for Rich styles in the TUIs. Legends for sniff, the stepper and
   the plot.
8. "param" → "signal" in user-visible TUI text (`keys.py` role descriptions, the plot).

## Deviation report (2026-11-05)

Three read-only surveys of `src/canair/` found these. File and line numbers are from that day.
The items marked † were checked by hand. The others come from the survey and are to be checked
when their phase starts.

### High

1. † `lock steal --json` and `lock kill --json` skip the confirmation (`cli/commands/lock.py:158,
   235`). Phase 2.
2. † `canair io ECU DID` actuates with no confirmation and no `--yes` (`live/iocontrol.py:123`).
   Phase 2.
3. † `--save` with no `--label` prompts with no TTY check and discards the recording on end of
   input (`captures/__init__.py:55-78`, `live/multi.py:134-142`). Phase 2.
4. `read --json`, `monitor --json` and `repl --json` accept the flag and ignore it
   (`cli/live/handlers/multi.py:70-82`, `live/multi.py:152-163`). Phase 4.
5. With `--json`, human text also goes to stdout: `discover` (`live/discover.py:111-198`),
   `scan range` (`live/scan.py:73,175-200`), `io` (`live/iocontrol.py:174-203`), every
   `--save` run (`captures/__init__.py:565`, `captures/maint.py:327`) and the connect banners
   (`cli/live/connect.py:63`, `live/raw_ops.py:167`, `cli/live/runtime.py:102`). Phase 4.

### Medium

6. `routines --sf start` confirms but has no `--yes` (`cli/live/handlers/actuators.py:58-72`).
   A declined DTC clear, routine start or KWP sweep exits 0 (`handlers/diagnostics.py:70`,
   `actuators.py:72`, `scans.py:98,167`). Phase 4.
7. `io`, `routines` and `captures --backfill-states` ignore `NO_COLOR` and `FORCE_COLOR`
   (`live/_iocontrol_render.py`, `live/iocontrol.py:429-704`, `live/routines.py:283-644`,
   `captures/backfill_render.py:18-28`, `captures/backfill_spans_render.py:18-27`). The screens
   use a second palette and blink. Phase 4.
8. About 14 sites colour stderr with stdout-gated names, for example `import_uds/__init__.py:108,
   128,151`, `ecu.py:739-786`, `states.py:127,267`, `variants.py:48`. `cerr()` with gated codes
   loses its colour when only stdout is piped (`cli/__init__.py:382`, `bix.py:480-570`).
   Phase 4.
9. Rich and `ansi` disagree on `NO_COLOR` and `FORCE_COLOR`. Rich wraps at 80 columns when
   piped and highlights numbers and paths unless `highlight=False` (`status.py`, `config.py`,
   `update.py`, `contribute.py`, `scan.py`, `cli/live/connect.py`). Phase 4.
10. Error prefixes: 5 families, `Error:` on stdout in 5 places (`live/raw.py:87`,
    `multi_repl.py:208`, `multi_exec.py:455`, `interactive.py:206`, `decode/entry.py:76`).
    `EditError` exits 1, the other top-level errors exit 2 (`cli/__init__.py:356-383`).
    `io` and `routines` print lookup errors to stdout and exit 0 (`live/iocontrol.py:139-152`).
    Phase 4.
11. Rich markup is not escaped for profile names, paths, notes and error text
    (`status.py:405,409,480-482`, `render/formatting.py:549`). Phase 4.
12. † TUI: the plot status bar shows `e endian`, but endian is `b` and `e` is annotate
    (`tui/apps/decode_plot_model.py:245`, `decode_plot.py:138`). Phase 6.
13. TUI: the session-info hint advertises `s` and `n`, which do nothing inside the modal
    (`tui/apps/monitor.py:612`). Phase 6.
14. TUI: failures flash green, the same as success (`monitor.py:1087`, `captures_step.py:402`,
    `decode_plot.py:191`). Phase 6.
15. TUI: app keys (`s`, `n`, `r`, `+`/`-`, `m`, `space`) may stay live under
    `LiveMonitorSelectScreen`, a plain `Screen` (`monitor.py:1243-1261,1513,1567`). Inferred
    from Textual's binding chain, not run. Phase 6.

### Low

16. Glyphs with two meanings or two forms: `✓` for success, verified and hold
    (`live/iocontrol.py:102`); `✗` and `✖` (`status.py:482`, `_monitor_run.py:230`); `?` red in
    discover, yellow elsewhere; `~` with two meanings (`discovery_scan.py`, `sessions_scan.py`);
    `▶` and `▸`; `…` and `...`; `⚠` in four colours. Phase 5.
17. Colour with several meanings: yellow PID ids (`render/formatting.py:549,552`,
    `live/identity.py:155`), cyan in six or more uses, green for both "update available" and "up
    to date" (`update.py:317,319`), red for research P1 (`research/__init__.py:81`). In the
    TUIs: red for both REC and errors, "stale" in three colours, "changed" in three encodings,
    `"white"` in sniff (`render/sniff.py`). Phase 5 and phase 6.
18. Write confirmations that skip `echo_edit`: `config set/unset` (`config.py:361,372`),
    `profile` (`:243,485,609`), the captures editors (`delete.py:120`, `backfill.py:195`,
    `set_state.py:124`), `ecu.py:960`, `_promote.py:72`, `captures/merge_driver.py:131`.
    Phase 4.
19. Progress lines with `\r` on stderr with no TTY check (`live/discovery_scan.py:111-116,179`,
    `live/discover.py:161-165`) and on stdout (`live/tester.py:73,81`). Phase 4.
20. Width and truncation are fixed numbers (`_hints.py:31`, `wican.py:334`,
    `research/__init__.py:164`). `_truncate_text` has two copies (`_iocontrol_render.py:24`,
    `routines.py:46`). Phase 4.
21. Colour codes frozen at import time (`investigate/support_fields.py:154`,
    `investigate/support_map_report.py:146-152`). Phase 4.
22. Prompt and cancel wording vary (`[y/N]`, `[y/N]:`, `(y/n) [y]`, `[Y/n]`; `Aborted.`,
    `Cancelled.`, …). Phase 4.
23. TUI: hand-written key hints almost everywhere (`keys_for` has one caller, `monitor.py:83`);
    hint words differ from the role descriptions; `ctrl+s` is called "confirm", "save" and
    "apply". Phase 6.
24. TUI: three copies of the filter-and-list picker, three of the overlay scroll actions, four
    of the app CSS block. Default focus, hint position and placeholder differ between them.
    Phase 6.
25. TUI: no input checks (`SaveDialog` "Label (required)" is not enforced, `monitor.py:196,289`).
    Numeric prompts are parsed after the dialog closes (`captures_step.py:509,561`). Phase 6.
26. TUI: "param" in user-visible text (`keys.py:77,78,96`, `decode_plot.py:320,355`,
    `decode_plot_model.py:151,224,245`). `wican autopid stats` says "Params" (`wican.py:330`).
    Phase 6 and phase 4.
27. TUI: only the monitor has a colour legend. Phase 6.

### Found outside this plan's scope

- `live/routines.py:39` derives a log directory from `__file__`, which `AGENTS.md` forbids.
- Stale comments: `captures_step.py:174-176`, `monitor.py:11-12`, `decode_plot.py:76`,
  `help.py:8,36,173`, `keys.py:3` (it says four apps, there are five).
- Lines over 100 columns in `captures_step.py:313`, `decode_plot.py:121`,
  `monitor.py:202,385,857`, `sniff.py:37`, `decode_plot_model.py:224`,
  `decode_plot_draw.py:228`.

## Docs

- Phase 1: `AGENTS.md` and `contributing-code` pointers. The skill itself.
- Phase 2: `docs/reference/cli/io.md` (generated) for the new `--yes`. A note in the `--save`
  help text that `--label` is needed when stdin is not a terminal.
- Phase 4: `AGENTS.md` → Authoring, where it says a rejected edit prints `  Error: …` and exits 1.
- Phase 5: the screenshots.

## Result and leftovers (2026-11-05)

Phase 3 landed as `scripts/check/ui_standards.py` and `tests/contracts/test_ui_standards.py`.
The `ui_standards` baseline went from 337 findings to 14. The remaining ones are deliberate:
five value or menu prompts (`profile create`, the scan wizard's value prompt, the first-run
menu, the region picker, the REPL) and nine result tokens that are data, not messages
(`decode/format.py`, `expression/decode_value.py`, `uds/describe.py`).

Phases 4–6 added `render/console.py`, `render/paint.py`, `render/messages.py`,
`render/glyphs.py`, `ansi.screen`, `tui/flash.py`, `tui/styles.py`, `KitModal` and
`ListPickerModal`, and split `tui/apps/monitor.py` (1624 → 912 lines). Behaviour changes a script
could notice:

- **Messages.** One form everywhere (`error:`, `warning:`, `note:`, `hint:`, lower case, stderr).
  `EditError` prints `error: …` (exit 1). `validate` prints each finding on stderr.
- **Exit codes.** A declined prompt exits 1 (it was 0 in `dtc --clear`, `routines --sf start`,
  the KWP sweeps and the monitor picker). No terminal and no `--yes` exits 2. `io` and
  `routines` lookup failures exit 1. Usage errors exit 2.
- **Streams.** Banners, "saved to" lines, notes and progress go to stderr. `discover`,
  `scan range`, `raw` and the one-shot `io`/`routines` keep their human report on stderr under
  `--json`. `read --json` prints `{"results": […]}`. `monitor` and `repl` no longer accept
  `--json`. A failing `io`/`routines` listing under `--json` prints no document, only `error:`.
- **Prompts.** Every yes/no is `core.prompt`. `routines --sf start` has `-y/--yes`. The scan
  wizard and first-run offer default to no.
- **Colour and glyphs.** PID and DID ids are cyan. Units, counts, fill lines and expressions are
  dim or plain. Update available is yellow. `✖`, `▸` and `...` are gone. The sweep markers are
  `+ ~ - !`, and `?` is only "unverified".
- **TUI.** Hints come from the roles. `?` works in every modal. `q` closes a read-only overlay.
  The ECU/PID picker is a modal. Failures flash red, never green.
- **Files.** The `io`/`routines` debug logs moved to `~/.config/canair/logs/`.

Left for later:

- A one-shot `io`/`routines` NRC or error response still exits 0; the exit-code rules say 1.
- Read-only base-profile refusals in the captures editors exit 1, not 2.
- `--json` is still missing on `validate`, `profile list/show`, `wican autopid stats/diff` and
  `bix --annotate`.
- `analysis/fill.py::FORCED_HOLD_WARNING` and `analysis/align.py::thin_join_warning` still start
  with `warning:`; `cli/commands/_analysis_warning.py` strips it.
- Short fixed column widths (`[:36]`, `[:30]`, `[:40]`) in the captures report tables.
- `maint` migrate kinds print `Converted N file(s)` without `echo_write`.
- Other deviation-report items that need a decision, not a mechanical fix (the JSON envelope in
  `plans/2026-08-06-json-output-convention.md`) stay with their own plans.
