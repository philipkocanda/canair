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
`.claude/skills/cli-tui-standards/enforcement-review-audit.md`). A few deviations are still in
its baseline; do not copy one as precedent.

Related skills: **contributing-code** (placement, tests, docs, commits). Flags and TUI keys are
authoritative in `canair <cmd> --help` and `src/canair/tui/keys.py`. Do not copy them here.

## Critical rules (apply to any output, prompt or TUI change)

- **stdout is the result.** Errors, warnings, notes, hints, progress and prompts go to stderr.
  Under `--json`, stdout is one JSON document and nothing else.
- Messages go through `render.messages` or a typed error. Lower case `error: …`; exit 0 ok,
  1 failure or declined, 2 usage error or guard refusal.
- Every coloured byte is gated for the stream it is written to. No raw `\033[` outside
  `render/ansi.py`; Rich only via `render.console.console`. Colour never carries meaning alone.
- Prompts use `canair.core.prompt`, default to no, have a flag, and refuse (exit 2) without a
  terminal. `--json` never means yes. A mutative command asks after its refusal checks and
  before its first write to the car or device.
- A TUI key is bound only through `keys.bind(role, action)`, and a hint is built from the role.
  `esc` backs out without quitting; `?` opens help; an overlay is a `ModalScreen`.
- A destructive TUI action goes through `ConfirmModal` and never fires on a single key.
- Never raise a `ui_standards` baseline ceiling to get green: fix the code, or ask the user.
- Run a live command only against the loopback emulator, or against the car with the user's
  explicit approval.
- Say "signal", not "param". Name bare protocol bytes shown as hex from the registries.
- Layering: only `cli/` imports `argparse`, only `tui/` imports `textual`; `expression/`, `uds/`,
  `config/`, `core/`, `safety/`, `device/` and `install/` may not import `render/` (they return
  text or raise a typed error).

## Workflow

1. Find the module that already owns the concern (do not re-implement it).
2. Write CLI output to the right stream with the right message level, colour and glyph.
3. Route any prompt through `core.prompt`; route any TUI key, modal and flash through the kit.
4. Run `uv run python scripts/check/ui_standards.py` and the contract tests.
5. Walk the review checklist; update docs and screenshots if visible output changed.

## Routing table

| When you are...                                   | Read                                                            |
|---------------------------------------------------|-----------------------------------------------------------------|
| Looking for the module that owns a concern        | `.claude/skills/cli-tui-standards/where-the-pieces-live.md`     |
| Choosing a stream, exit code, `--json`, a message | `.claude/skills/cli-tui-standards/cli-streams-messages.md`      |
| Choosing a colour or glyph, or using Rich         | `.claude/skills/cli-tui-standards/cli-colour-glyphs.md`         |
| Adding a prompt, a list view or a report layout   | `.claude/skills/cli-tui-standards/cli-prompts-layout.md`        |
| Adding a TUI key, modal, input or flash message   | `.claude/skills/cli-tui-standards/tui-keys-modals.md`           |
| Working on the shell, Textual CSS or TUI wording  | `.claude/skills/cli-tui-standards/tui-shell-styling.md`         |
| Reviewing a diff, the scanner, or auditing        | `.claude/skills/cli-tui-standards/enforcement-review-audit.md`  |
