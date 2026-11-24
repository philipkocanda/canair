# CLI colour and glyphs

Colour gating per stream, Rich use, the colour meanings, and the glyph table.

## Colour

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

## Glyphs

Import them from `render/glyphs.py`, which says what each one means. Never use `✖` for failure
(it is `✗`), `▸` for the cursor (it is `▶`), or `...` for truncation (it is the one character
`…`). `⚠` is always yellow. A new glyph needs a new meaning. Do not reuse one for a second
meaning. In scan and sweep output `+` is a positive reply, `~` an NRC that proves the id exists,
`-` nothing there and `!` a failed probe; `?` is only ever "unverified".
