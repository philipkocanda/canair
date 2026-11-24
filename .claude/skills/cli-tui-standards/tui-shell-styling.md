# TUI application shell, styling and wording

Menu bar, palette, status line and bottom line of shell apps; Textual CSS, style names, wording.

## The application shell

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

## Styling and wording

- Textual CSS uses theme variables only (`$accent`, `$error`, `$warning`, `$text-muted`, …). No
  hex or named colours. The apps pin `ansi-dark`, so surface variables are transparent. Test a
  dialog on it.
- Rich style names in a TUI come from `tui/styles.py` and carry the same meanings as the colour
  table above. A view that uses colour for meaning sets `HELP_LEGEND`.
- Sentence case for titles, labels and buttons. Buttons are verbs: `Save`, `Delete`, `Cancel`.
- Key notation in text: `esc`, `enter`, `tab`, `space`, `ctrl+s`, `↑/↓`, `pgup/pgdn`.
