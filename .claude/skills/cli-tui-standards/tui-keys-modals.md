# TUI keys, screens, modals and inputs

Key bindings and roles, `esc`/`q`/`?` behaviour, modals, input handling, flash messages.

## Keys

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

## Screens and modals

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

## Inputs and feedback

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
