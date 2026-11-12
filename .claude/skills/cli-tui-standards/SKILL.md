---
name: cli-tui-standards
description: Standards for canair's command-line output and Textual TUIs — colour and glyph meanings, message and exit-code format, prompts and --yes, --json hygiene, key bindings, modals, inputs, wording. Load this when you add or change a command's output, a prompt or confirmation, a Textual app, screen or modal, or a key binding, and when auditing an existing command or TUI for deviations.
---

# CLI and TUI standards

How every canair command prints and every TUI behaves. canair has 40-odd commands and seven
Textual apps, and they should feel like one tool. Read this before you write a line that a user
sees, and again when you review one.

The decisions and the history are in `plans/2026-11-05-cli-tui-standards.md`. The rules below are
enforced, not advisory: `tests/contracts/test_ui_standards.py` fails on a new deviation (see
**Enforcement**). A few deviations are still in its baseline; do not copy one as precedent.

Related skills: **contributing-code** (placement, tests, docs, commits). Flags and TUI keys are
authoritative in `canair <cmd> --help` and `src/canair/tui/keys.py`. Do not copy them here.

## Where the pieces live

Use these. Do not re-implement them.

| Concern | Home |
|---|---|
| Colour gate, palette, screen control, `NO_COLOR`/`FORCE_COLOR` | `src/canair/render/ansi.py` |
| Colour and align text for one stream | `src/canair/render/paint.py` (`Painter`, `column_widths`) |
| The four message levels, `fail` | `src/canair/render/messages.py` |
| Glyphs | `src/canair/render/glyphs.py` |
| Rich console for plain output | `src/canair/render/console.py` (`console`, `escape`) |
| Shared formatters, byte styles and their legend | `src/canair/render/formatting.py` |
| Signal tables (a PID's signals, their ranges) | `src/canair/render/signal_table.py` |
| Yes/no and typed confirmation | `src/canair/core/prompt.py` |
| Write confirmations | `yaml_edit/echo.py::echo_edit`, `captures/echo.py`, `captures/save.py::saved_banner` |
| Top-level error handler | `src/canair/cli/__init__.py` (`main`) |
| Terminal size | `src/canair/core/terminal.py` |
| TUI key roles, hints | `src/canair/tui/keys.py` (`ROLES`, `bind`, `keys_for`, `hint`, `key_hint`) |
| Help overlay, legend | `src/canair/tui/help.py` (`HelpMixin`, `HELP_LEGEND`) |
| Modals | `src/canair/tui/modals.py` (`KitModal`, `TextPromptModal`, `ConfirmModal`, `ListPickerModal`) |
| Transient messages | `src/canair/tui/flash.py` |
| TUI style names, dialog CSS | `src/canair/tui/styles.py` |
| Overlay scrolling | `src/canair/tui/scroll.py` (`OverlayScroll`) |
| Status bar, checklist widgets | `src/canair/tui/status.py`, `src/canair/tui/widgets.py` |
| App shell: menu bar, palette, zoned status line, bottom line, messages | `src/canair/tui/shell/` |

Layering still applies: only `cli/` imports `argparse` and only `tui/` imports `textual`
(`docs/development/architecture.md`). `expression/`, `uds/`, `config/`, `core/`, `safety/`,
`device/` and `install/` may not import `render/`: such code returns text or raises a typed
error, and the caller prints it.

## CLI output

### Streams and exit codes

- **stdout is the result.** Errors, warnings, notes, hints, progress, prompts, banners and
  "saved to" lines go to **stderr**.
- With `--json`, stdout holds one JSON document and nothing else. A failure under `--json` prints
  `error: …` on stderr and exits non-zero; it does not print half a document. The document's
  shape is the business of `plans/2026-08-06-json-output-convention.md`.
- A command that reports a result offers `--json`. A command that accepts `--json` honours it.
  Never accept a flag and ignore it (`monitor` and `repl` take no `--json`).
- Exit codes: **0** success. **1** failure, or the user declined a prompt. **2** a usage error, or
  a guard refused (the safety blocklist, a read-only profile, a prompt with no terminal and no
  flag). **141** the reader closed stdout early (`| head`): `cli/__init__.py::main` ends the
  command quietly, as SIGPIPE would. Never catch `BrokenPipeError` in a command for that.

### Messages

Use `render.messages`. Do not format a level by hand.

| Kind | Form | Colour |
|---|---|---|
| `error(text)` | `error: …` | red |
| `warning(text)` | `warning: …` | yellow |
| `note(text)` | `note: …` | plain |
| `hint(text)` | `hint: …` | dim prefix; put the command to try in the text |
| `fail(text, code=1)` | `error: …`, then exit | red |

All on stderr, coloured by stderr, lower case, not indented. A second line is indented by two
spaces. No command name before the level. Raise the typed error (`ProfileError`, `EditError`,
`SafetyBlocked`, `PromptUnavailable`) and let `main` print it. Library code does not print an
error and exit: it raises.

A result column or decoded text that contains `ERROR` or `WARNING` as data is not a message
(`decode/format.py`, `expression/decode_value.py`, `uds/describe.py`); those stay.

### Colour

- Every coloured byte is gated by `ansi.use_color` **for the stream it is written to**. The gated
  names (`ansi.RED`) follow stdout, so they are for stdout only. For stderr, or for stdout under
  `--json`, use `Painter(stream)`, `ansi.c(text, ansi.raw.X, stream=stream)` or `ansi.cerr`
  with `ansi.raw.*` codes.
- No raw `\033[` outside `render/ansi.py`. Cursor and screen control are `ansi.screen.*`.
- Never freeze a gated code at import time (a module-level dict of `ansi.RED`). The gate is read
  when the name is read.
- Rich only through `render.console.console(stderr=…)`. It follows the same colour policy, turns
  off all styling when colour is off, does not highlight numbers or paths, and does not wrap at
  80 columns when piped. Build it where you print, not at import. Escape data (names, paths,
  notes, error text) with `render.console.escape` before it goes into markup.
- Colour never carries meaning alone. A glyph or a word goes with it.

| Style | One meaning |
|---|---|
| red | error, failure, refusal |
| yellow | warning, needs attention, unverified, NRC (negative response code) |
| green | success, OK, verified |
| cyan | a name the user can type back: ECU, PID, DID, profile, variant, group, bus, state, a command |
| bold | a heading |
| bold cyan | a heading that names an entity |
| dim | secondary text: column headers, paths, sources, tips |

Units, counts, fill lines, expressions and candidates are dim or plain, not cyan. Research
priority and similar non-errors are not red. A domain may add a meaning the table lacks (the byte
styles in `render/formatting.py`), and then writes it in a legend beside the style; a TUI shows
that legend in its help.

### Glyphs

Import them from `render/glyphs.py`.

| Glyph | Meaning |
|---|---|
| `✓` | success, verified |
| `✗` | failure (never `✖`) |
| `⚠` | warning, always yellow |
| `→` | "the result is at" (saved path, rename target) |
| `▶` | cursor (never `▸`) |
| `·` | separator |
| `—` | empty cell |
| `…` | truncation (one character, never `...`) |
| `█` `░` | the done and the remaining part of a progress bar |
| `!!` | the start of a danger prompt |

A new glyph needs a new meaning. Do not reuse one for a second meaning. In scan and sweep output
`+` is a positive reply, `~` an NRC that proves the id exists, `-` nothing there and `!` a failed
probe; `?` is only ever "unverified".

### Prompts and confirmation

Use `canair.core.prompt`. Never call `input()` or `sys.stdin.readline()` for a yes/no.

- `confirm(question, yes=args.yes, danger=…)` for a yes/no. `typed_confirm` is only for actuation
  and a safety bypass (`--unsafe`, the SKM relay, an action name).
- `[y/N]`: the default is no. Anything but an explicit yes declines, and so does end of input.
- The question goes to stderr.
- Every prompt has a flag that answers it: `--yes`/`-y` for a confirmation, a value flag
  (`--label`) for an input.
- One prompt has no flag, on purpose: enabling a safety gate with `canair config set
  safety.allow_unsafe true` or `safety.allow_reset true`. Those keys exist so that no flag,
  passed by a script or an agent, reaches `--unsafe` or `canair reset`
  (`cli/commands/config/edit.py`). Do not add one.
- **`--json` never means yes.** Pass `yes=args.yes`, never `yes=args.yes or args.json`.
- With no terminal on stdin the helper raises `PromptUnavailable`, which names the flag. The
  top-level handler and `run_session_guarded` turn it into `error: …` and exit 2. Do not catch it
  to carry on without asking.
- A decline prints `Aborted — nothing changed.` (the helper does) and the command exits 1.
- A mutative command asks before its first write to the car or the device, and after every check
  that can refuse it. See `contributing-code` → Mutative / sensitive operations.
- A recording is never discarded because a prompt could not be answered
  (`captures/metadata.py`).
- A value or menu prompt (a label, a region, a REPL line) may use `input()`. It runs only on a
  terminal and has a flag or a default for scripts. These few sites are in the scanner's
  baseline.

### Layout

- Two-space indent for report lines.
- List views follow `variants`, `groups`, `bus`, `states`: `Title — N in <profile>`, the rows,
  then a dim `source: <path>` footer.
- Column headers are dim. Pad on the plain text so columns align with colour on or off
  (`render.paint.column_widths`).
- Take the width from `core/terminal.py`. Do not hard-code one.
- Confirm every definition write with `echo_edit` and every capture write with `echo_write` or
  `saved_banner`. All name the full path.
- Progress and `\r` status lines go to stderr, only on a terminal, and are cleared when done
  (`render/progress.py`).
- Say "signal", not "parameter" or "param" (`AGENTS.md` → Vocabulary).
- **Name bare bytes.** When a CLI or TUI shows a request, a service id, a sub-function, a DID, an
  NRC or another protocol code as hex, show its meaning next to it, e.g. `3E00` →
  `TesterPresent (zeroSubFunction)`. Look the name up in the existing registries
  (`uds/services.py::service_name`, `uds/subfunctions.py::subfunction_name`,
  `uds/nrc.py::nrc_description`, the profile's PID names). Do not hand-write a name in the view.
  Add a missing name to its registry.
  If no name is known, show the hex alone; never guess one. Leave it out only when the user asks.

## TUI

### Keys

- Bind a key only through `keys.bind(role, action)`. A new key means a new role in `ROLES`, or
  reusing one. `tests/contracts/test_tui_keymap.py` fails otherwise.
- **Build a key hint from the role** (`keys.hint`, `key_hint`, `arrows_hint`). Do not write hint
  text by hand: the scanner's `tui_key_hints` check fails on it. The role description is the only
  wording, so the same key reads the same everywhere.
- `esc` backs out one level and never quits the app. `q` quits at the top level and closes a
  read-only overlay (help, event log, session info, an info view). In a modal with a text input,
  `q` is text, so only `esc` closes it. `ctrl+c` and `ctrl+q` force-quit (role `quit_force`).
- `?` opens help on every screen. In a modal with a focused text input it is text; once focus is
  elsewhere it opens help (`KitModal`).
- One exception to "esc never quits": a one-shot picker that returns a value and then exits
  (`tui/picker.py::select_from_list`, used by `canair profile`) counts as a dialog, so `esc`
  cancels it.
- `g`/`G` address the view's primary axis. `enter` accepts. `ctrl+s` applies a form.

| Screen kind | `esc` | `q` | `?` |
|---|---|---|---|
| top-level app | no-op (monitor: clears the selection; plot: closes the info view) | quits (plot: closes the info view first) | help |
| read-only overlay | closes | closes | help (inside help: closes it) |
| dialog without a text field | cancels | inert | help |
| dialog or picker with a text field | cancels | text | text while the field is focused |

### Screens and modals

- An overlay on an app is a `ModalScreen`. A plain `Screen` pushed over an app leaves the app's
  keys live; the ECU/PID picker is a `ModalScreen` for that reason.
- Build a generic dialog on `tui/modals.py` (`KitModal` gives it `?`). Use `ListPickerModal` for a
  filter-and-list pick. Keep an app-specific dialog with its app (`tui/apps/*_dialogs.py`).
- Anatomy: a bold sentence-case title, the body, buttons on the right (primary first, Cancel
  last), a hint line at the bottom. It fits 80×24. CSS comes from `tui/styles.py::dialog_css`.
- Border `$accent`. A destructive dialog uses `$error` and starts with focus on Cancel.
- Cancel dismisses with `None`. `ConfirmModal` is the one exception: it returns a `bool`.
- A destructive action goes through `ConfirmModal`. It never fires on a single key.
- State is shown by shape as well as colour (`CheckSelectionList` shows `[x]`).

### Inputs and feedback

- A dialog checks its own input and shows the error inside the dialog
  (`TextPromptModal(validate=…)`). A required field is enforced there, not after the dialog
  closes.
- A one-field dialog submits on `enter`. A form submits on `ctrl+s` (role `apply`).
- A printable app key never conflicts with a focused `Input`. A priority binding on a printable
  key would swallow typing, so use a modified key.
- Filter inputs say `type to filter…`.
- A transient message goes through `tui/flash.py` with a severity (error red `✗`, warning yellow
  `⚠`, success green `✓`, info plain), no trailing period. A failure never looks like a success.
- In an app on the shell, a message goes through `ShellMixin.message`
  (`tui/shell/messages.py`), which keeps that look. It shows on the bottom line, never over a
  value and never in the status line. An error stays until the next key. Every message is kept
  for the app's event log.

### The application shell

The monitor runs on `tui/shell/`; the other TUIs may move onto it. An app on the shell follows
these rules.

- Every command is in the app's `Catalogue`, and the menu bar, the palette and the help sheet read
  it. A command names an existing action and a key role. Do not describe a command twice.
- The menu bar never drops a title: it steps down to shorter titles, then to one letter each.
  `f10` (role `menu`) opens it, and `alt` with the underlined letter opens one menu. The menu bar
  derives these mnemonics from the titles, so two menus never start with the same letter.
- A dropdown shows each item's key on the right, `[x]` for a toggle and `(•)` for a choice. An
  item that cannot run now is disabled, and running it from anywhere says why.
- `ctrl+p` (role `palette`) opens the palette over the same catalogue. It lists no Textual
  system commands.
- The status line groups facts into zones in a fixed order. Only an exception is a badge (a
  reversed block): `PAUSED`, a link fault, an output left on. The normal state is plain text.
- The bottom line ends with the routes (`f10 menu · ctrl+p commands · ? help`).

### Styling and wording

- Textual CSS uses theme variables only (`$accent`, `$error`, `$warning`, `$text-muted`, …). No
  hex or named colours. The apps pin `ansi-dark`, so surface variables are transparent. Test a
  dialog on it.
- Rich style names in a TUI come from `tui/styles.py` and carry the same meanings as the colour
  table above. A view that uses colour for meaning sets `HELP_LEGEND`.
- Sentence case for titles, labels and buttons. Buttons are verbs: `Save`, `Delete`, `Cancel`.
- Key notation in text: `esc`, `enter`, `tab`, `space`, `ctrl+s`, `↑/↓`, `pgup/pgdn`.
- Say "signal", not "param".

## Enforcement

`scripts/check/ui_standards.py` counts, per file, what these rules forbid, and
`tests/contracts/test_ui_standards.py` holds each count to its ceiling in
`tests/contracts/baseline.json` (`ui_standards`). A new deviation fails; a fixed one must leave the
baseline (`uv run python -m tests.contracts.test_structure --write`, which only lowers). Never
raise a ceiling to get green: fix the code, or ask the user.

| Check | Finds | Allowed in |
|---|---|---|
| `raw_escapes` | an output escape sequence in a string | `render/ansi.py` |
| `rich_console` | `Console(...)` | `render/console.py` |
| `prompts` | `input(...)`, `sys.stdin.readline()` | `core/prompt.py`, `captures/metadata.py` |
| `message_prefixes` | a string starting `Error:`, `ERROR`, `WARN:`, `Warning:`, `WARNING`, `Note:`, `NOTE:` | nowhere |
| `stderr_gating` | stdout-gated `ansi.*` written to stderr | nowhere |
| `tui_key_hints` | a key spelled out in a string under `tui/` | `tui/keys.py`, `tui/help.py` |

The same test checks that the definition-only commands print one JSON document under `--json`,
and `tests/integration/test_ansi_gating.py` that a piped command prints no escape code.

## Review checklist

Run it on a diff that touches output, a prompt or a TUI. Each line is a question.

1. Does stdout hold only the result, and only valid JSON under `--json`?
2. Is every message a `render.messages` call or a typed error, with exit 0, 1 or 2 as defined?
3. Is every coloured byte gated for the stream it goes to? Any raw `\033[`, new `Console(`, or
   import-time palette?
4. Does each colour and glyph keep its one meaning? Is meaning ever carried by colour alone?
5. Does each prompt use `core.prompt`, default to no, have a flag, and refuse without a terminal?
   Is `--json` kept apart from `--yes`?
6. Does a mutative command ask after its refusal checks and before its first send?
7. Is every TUI key bound through `bind()`, and every hint built from a role?
8. Does `esc` back out without quitting? Does `?` work, and `q` do what the table says?
9. Is an overlay a `ModalScreen`? Does a modal use the shared kit and fit 80×24?
10. Does an error in a TUI look like an error (`flash`)? Does every protocol code shown as hex
    carry its name from a registry?
11. Does a test cover it? Colour: a subprocess run with piped output has no `\x1b[`
    (`tests/integration/test_ansi_gating.py`). Keys: the keymap contract. Prompts: a decline, a
    yes, and no terminal.
12. Did a user-facing change update the docs, and the screenshots if output changed
    (`contributing-code` → Keep the docs and README current)?

## Auditing for deviations

When you are asked to flag deviations, or you notice one while working:

1. Run `uv run python scripts/check/ui_standards.py` for the mechanical ones, then read the
   command: press `?`, `esc`, `q` on every TUI screen and modal; run it with `NO_COLOR=1`, piped,
   and with `--json | python -m json.tool`. Run a live command only against the loopback emulator
   (`docs/development/offline-testing.md`), or against the car with the user's explicit approval.
2. Check it against the rules above, then against the plan's deviation report
   (`plans/2026-11-05-cli-tui-standards.md`). If it is listed, cite the item. If it is new, add
   it to the report under the severity it earns: **High** is a missing confirmation, a lost
   recording, or output that breaks `--json`. **Medium** is ungated colour, a wrong stream, or an
   inconsistent error. **Low** is a glyph, a colour meaning, wording or layout.
3. Confirm it by reading the code or running the command. A survey line is a lead, not proof.
4. Fix it if the fix is small and safe (Boy Scout). If it changes visible output that a script
   could read, or touches a safety path, say so and ask first.
