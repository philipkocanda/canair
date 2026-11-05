---
name: cli-tui-standards
description: Standards for canair's command-line output and Textual TUIs — colour and glyph meanings, message and exit-code format, prompts and --yes, --json hygiene, key bindings, modals, inputs, wording. Load this when you add or change a command's output, a prompt or confirmation, a Textual app, screen or modal, or a key binding, and when auditing an existing command or TUI for deviations.
---

# CLI and TUI standards

How every canair command prints and every TUI behaves. canair has 40-odd commands and five
Textual apps, and they should feel like one tool. Read this before you write a line that a user
sees, and again when you review one.

Decisions and the backlog live in `plans/2026-11-05-cli-tui-standards.md`. That plan is the
authority on **what is not yet true**: its phases 3–6 are unfinished. A rule marked **(target)**
below is the standard, but some code still breaks it. Follow the rule in new code. Do not copy
the existing deviation as precedent, and check the plan's deviation report before you "fix" one
(it may already be scheduled).

Related skills: **contributing-code** (placement, tests, docs, commits). Flags and TUI keys are
authoritative in `canair <cmd> --help` and `src/canair/tui/keys.py`. Do not copy them here.

## Where the pieces live

Point at these. Do not re-implement them.

| Concern | Home |
|---|---|
| Colour gate, palette, `NO_COLOR`/`FORCE_COLOR` | `src/canair/render/ansi.py` |
| Stream-aware painter and column alignment | `src/canair/live/discover_render.py` (**target**: moves to `render/`) |
| Shared formatters, byte styles and their legend | `src/canair/render/formatting.py` |
| Yes/no confirmation | `src/canair/core/prompt.py` |
| Write confirmations | `yaml_edit/echo.py::echo_edit`, `captures/__init__.py::saved_banner` |
| Top-level error handler | `src/canair/cli/__init__.py` (`main`) |
| Terminal size | `src/canair/core/terminal.py` |
| TUI key roles | `src/canair/tui/keys.py` (`ROLES`, `bind`, `keys_for`) |
| Help overlay, legend | `src/canair/tui/help.py` (`HelpMixin`, `HELP_LEGEND`) |
| Generic modals | `src/canair/tui/modals.py` |
| Status bar | `src/canair/tui/status.py` |
| Checklist widgets | `src/canair/tui/widgets.py` |
| Key-role tests | `tests/contracts/test_tui_keymap.py` |
| Colour-gating tests | `tests/integration/test_ansi_gating.py` |

Layering still applies: only `cli/` imports `argparse` and only `tui/` imports `textual`
(`docs/development/architecture.md`).

## CLI output

### Streams and exit codes

- **stdout is the result.** Errors, warnings, notes, hints, progress, prompts and banners go to
  **stderr**.
- With `--json`, stdout holds one JSON document and nothing else. A banner on stdout under
  `--json` is a bug. The document's shape is the business of
  `plans/2026-08-06-json-output-convention.md`.
- A command that reports a result offers `--json`. A command that accepts `--json` honours it.
  Never accept a flag and ignore it.
- Exit codes: **0** success. **1** failure, or the user declined a prompt. **2** a usage error, or
  a guard refused (the safety blocklist, a read-only profile, a prompt with no terminal and no
  flag).

### Messages (target)

| Kind | Form | Stream |
|---|---|---|
| error | `error: …` | stderr, red when stderr is a terminal |
| warning | `warning: …` | stderr, yellow |
| note | `note: …` | stderr |
| hint | `hint: …` | stderr, with the command to try in cyan |

Lower case, not indented, one line where possible. No command name before the level. Raise the
typed error (`ProfileError`, `EditError`, `SafetyBlocked`, `PromptUnavailable`) and let `main`
print it. Do not print an error and `sys.exit` inside library code.

Today `EditError` prints `  Error: …` and exits 1, and several modules print `Error:`,
`ERROR`, `WARN:` or `WARNING`. Do not add new ones.

### Colour

- Every coloured byte is gated by `ansi.use_color` **for the stream it is written to**. The gated
  names (`ansi.RED`) follow stdout, so they are for stdout only. For stderr use `ansi.cerr` with
  `ansi.raw.*` codes, or the stream-aware painter.
- No raw `\033[` outside `render/ansi.py`. Screen control in `tui/__init__.py` is the one
  exception.
- Never freeze a gated code at import time (a module-level dict of `ansi.RED`). The gate is read
  when the name is read.
- Rich is allowed in plain output only through one console factory in `render/` **(target; the
  factory does not exist yet)**. Rich ignores `ansi.use_color`, wraps at 80 columns when piped
  and highlights numbers and paths unless `highlight=False`. Escape data (names, paths, notes,
  error text) before it goes into Rich markup.
- Colour never carries meaning alone. A glyph or a word goes with it.

| Style | One meaning |
|---|---|
| red | error, failure, refusal |
| yellow | warning, needs attention, unverified, NRC (negative response code) |
| green | success, OK, verified |
| cyan | a name the user can type back: ECU, PID, profile, variant, group, bus, state, a command |
| bold | a heading |
| bold cyan | a heading that names an entity |
| dim | secondary text: column headers, paths, sources, tips |

A domain may add a meaning the table lacks (the byte styles in `render/formatting.py`). It writes
the meaning in a legend beside the style, and a TUI shows that legend in its help.

Deviations to know about: PID ids print yellow, cyan has other uses, red marks research P1
priority. The plan schedules them (phase 5). New code follows the table.

### Glyphs

| Glyph | Meaning |
|---|---|
| `✓` | success, verified |
| `✗` | failure (not `✖`) |
| `⚠` | warning, always yellow |
| `→` | "the result is at" (saved path, rename target) |
| `▶` | cursor |
| `·` | separator |
| `—` | empty cell |
| `…` | truncation (one character, not `...`) |
| `!!` | the start of a danger prompt |

A new glyph needs a new meaning. Do not reuse one for a second meaning.

### Prompts and confirmation

Use `canair.core.prompt`. Never call `input()` or `sys.stdin.readline()` for a yes/no.

- `confirm(question, yes=args.yes, danger=…)` for a yes/no. `typed_confirm` is only for actuation
  and a safety bypass.
- `[y/N]`: the default is no. Anything but an explicit yes declines, and so does end of input.
- The question goes to stderr.
- Every prompt has a flag that answers it: `--yes`/`-y` for a confirmation, a value flag
  (`--label`) for an input.
- **`--json` never means yes.** Pass `yes=args.yes`, never `yes=args.yes or args.json`.
- With no terminal on stdin the helper raises `PromptUnavailable`, which names the flag. The
  top-level handler and `run_session_guarded` turn it into `error: …` and exit 2. Do not catch it
  to carry on without asking.
- A decline prints `Aborted — nothing changed.` (the helper does) and the command exits 1.
- A mutative command asks before its first write to the car or the device, and after every check
  that can refuse it. See `contributing-code` → Mutative / sensitive operations.
- A recording is never discarded because a prompt could not be answered
  (`captures/metadata.py`).

### Layout

- Two-space indent for report lines.
- List views follow `variants`, `groups`, `bus`, `states`: `Title — N in <profile>`, the rows,
  then a dim `source: <path>` footer.
- Column headers are dim. Pad on the plain text so columns align with colour on or off
  (`discover_render.column_widths`).
- Take the width from `core/terminal.py`. Do not hard-code one.
- Confirm every definition write with `echo_edit` and every capture write with `saved_banner`.
  Both name the full path.
- Progress and `\r` status lines go to stderr, only on a terminal, and are cleared when done.
- Say "signal", not "parameter" or "param" (`AGENTS.md` → Vocabulary).

## TUI

### Keys

- Bind a key only through `keys.bind(role, action)`. A new key means a new role in `ROLES`, or
  reusing one. `tests/contracts/test_tui_keymap.py` fails otherwise.
- **Build a key hint from the role** (`keys_for(role)` and the role's description). Do not write
  hint text by hand: hand-written hints are how `e endian` stayed in the plot status bar after
  endian moved to `b`. **(target; most hints are still hand-written.)**
- `?` opens help on every screen, modals included **(target; it does nothing in modals today)**.
- `escape` backs out one level and never quits the app. `q` quits at the top level and closes a
  read-only overlay (help, event log, session info, an info view). In a modal with a text input,
  `q` is text, so only `escape` closes it.
- One exception: a one-shot picker that returns a value and then exits
  (`tui/__init__.py::select_from_list`, used by `canair profile`) counts as a dialog, so `escape`
  cancels it.
- `g`/`G` address the view's primary axis. `enter` accepts. `ctrl+s` applies a form.
- Textual adds `ctrl+q` to every app. It is not a role yet **(target)**.

### Screens and modals

- An overlay on an app is a `ModalScreen`. A plain `Screen` pushed over an app leaves the app's
  keys live (`s`, `n`, `space` reach the monitor under `LiveMonitorSelectScreen`). If you must use
  a plain `Screen`, block the app's actions in `check_action`.
- Use `tui/modals.py` for a generic prompt or confirmation. Keep an app-specific dialog with its
  app. Before you write a fourth filter-and-list picker, extract the shared one **(target; three
  copies exist)**.
- Anatomy: a bold sentence-case title, the body, buttons on the right (primary first, Cancel
  last), a hint line at the bottom. It fits 80×24.
- Border `$accent`. A destructive dialog uses `$error` and starts with focus on Cancel.
- Cancel dismisses with `None`. `ConfirmModal` is the one exception: it returns a `bool`.
- A destructive action goes through `ConfirmModal`. It never fires on a single key.
- State is shown by shape as well as colour (`CheckSelectionList` shows `[x]`).

### Inputs and feedback

- A dialog checks its own input and shows the error inside the dialog. Enforce a required field
  there, not after the dialog closes **(target)**.
- A one-field dialog submits on `enter`. A form submits on `ctrl+s` (role `apply`).
- A printable app key never conflicts with a focused `Input`. A priority binding on a printable
  key would swallow typing, so use a modified key.
- Filter inputs say `type to filter…`.
- A transient message states its severity. A failure never looks like a success: do not flash
  "Save failed" in green **(target; three apps do)**. Use a toast (`notify(severity=…)`) or one
  shared flash helper with error red, warning yellow, success green, info plain.

### Styling and wording

- Textual CSS uses theme variables only (`$accent`, `$error`, `$warning`, `$text-muted`, …). No
  hex or named colours. The apps pin `ansi-dark`, so surface variables are transparent. Test a
  dialog on it.
- Rich style names in a TUI carry the same meanings as the colour table above. A view that uses
  colour for meaning sets `HELP_LEGEND`.
- Sentence case for titles, labels and buttons. Buttons are verbs: `Save`, `Delete`, `Cancel`.
- Key notation in text: `esc`, `enter`, `tab`, `space`, `ctrl+s`, `↑/↓`, `pgup/pgdn`.
- A flash message has no trailing period.
- Say "signal", not "param".

## Review checklist

Run it on a diff that touches output, a prompt or a TUI. Each line is a question.

1. Does stdout hold only the result, and only valid JSON under `--json`?
2. Is every error `error: …` on stderr, raised as a typed error, with exit 0, 1 or 2 as defined?
3. Is every coloured byte gated for the stream it goes to? Any raw `\033[`, new `Console(`, or
   import-time palette?
4. Does each colour and glyph keep its one meaning? Is meaning ever carried by colour alone?
5. Does each prompt use `core.prompt`, default to no, have a flag, and refuse without a terminal?
   Is `--json` kept apart from `--yes`?
6. Does a mutative command ask after its refusal checks and before its first send?
7. Is every TUI key bound through `bind()`, and every hint built from a role?
8. Does `escape` back out without quitting? Does `?` work, and `q` do what the rules say?
9. Is an overlay a `ModalScreen`? Does a modal use the shared kit and fit 80×24?
10. Does an error in a TUI look like an error?
11. Does a test cover it? Colour: a subprocess run with piped output has no `\x1b[`
    (`tests/integration/test_ansi_gating.py`). Keys: the keymap contract. Prompts: a decline, a
    yes, and no terminal.
12. Did a user-facing change update the docs, and the screenshots if output changed
    (`contributing-code` → Keep the docs and README current)?

## Auditing for deviations

When you are asked to flag deviations, or you notice one while working:

1. Check it against the rules above, then against the plan's deviation report
   (`plans/2026-11-05-cli-tui-standards.md`). If it is listed, cite the item. If it is new, add
   it to the report under the severity it earns: **High** is a missing confirmation, a lost
   recording, or output that breaks `--json`. **Medium** is ungated colour, a wrong stream, or an
   inconsistent error. **Low** is a glyph, a colour meaning, wording or layout.
2. Confirm it by reading the code or running the command. A survey line is a lead, not proof.
3. Fix it if the fix is small and safe (Boy Scout). If it changes visible output that a script
   could read, or touches a safety path, say so and ask first.

Useful probes (read-only):

- `rg '\\033\[|\\x1b\[' src/canair --glob '!render/ansi.py'` — raw escapes.
- `rg 'Console\(' src/canair` — Rich consoles.
- `rg '\binput\(|stdin\.readline' src/canair` — prompts that skip `core.prompt`.
- `rg '"(Error|ERROR|WARN|Warning|WARNING|Note|NOTE):' src/canair` — message prefixes.
- `NO_COLOR=1`, a pipe (`| cat`) and `--json | python -m json.tool` on the command.
- Inside a TUI: press `?`, `escape`, `q` on every screen and modal.
