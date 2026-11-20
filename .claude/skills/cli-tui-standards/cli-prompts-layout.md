# CLI prompts, confirmation and layout

`core.prompt`, `--yes`, declining, no-terminal behaviour; report layout, widths, naming bare bytes.

## Prompts and confirmation

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
  that can refuse it. See `contributing-code/cli-conventions.md` → Mutative / sensitive
  operations.
- A recording is never discarded because a prompt could not be answered
  (`captures/metadata.py`).
- A value or menu prompt (a label, a region, a REPL line) may use `input()`. It runs only on a
  terminal and has a flag or a default for scripts. These few sites are in the scanner's
  baseline.

## Layout

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
