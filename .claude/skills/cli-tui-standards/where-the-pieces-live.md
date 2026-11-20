# Where the pieces live (render, prompt, TUI modules)

The module that owns each CLI/TUI concern, and the layering rules. Use these; do not re-implement.

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
