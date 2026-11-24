# Enforcement, review checklist and auditing

The ui_standards scanner and its baseline, the review checklist, and how to audit for deviations.

## Enforcement

`scripts/check/ui_standards.py` counts, per file, what these rules forbid, and
`tests/contracts/test_ui_standards.py` holds each count to its ceiling in
`tests/contracts/baseline.json` (`ui_standards`). A new deviation fails; a fixed one must leave the
baseline (`uv run python -m tests.contracts.test_structure --write`, which only lowers). Never
raise a ceiling to get green: fix the code, or ask the user.

The checks (raw escapes, Rich consoles, prompts, message prefixes, stderr gating, TUI key hints) and
the one place each is allowed are listed in the `scripts/check/ui_standards.py` docstring.

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
    (`contributing-code/docs-commits-release.md` → Docs, changelog and screenshots)?

## Auditing for deviations

When you are asked to flag deviations, or you notice one while working:

1. Run `uv run python scripts/check/ui_standards.py` for the mechanical ones, then read the
   command: press `?`, `esc`, `q` on every TUI screen and modal; run it with `NO_COLOR=1`, piped,
   and with `--json | python -m json.tool`. Live commands follow the rule in `SKILL.md`.
2. Check it against the rules above, then against the plan's deviation report
   (`plans/2026-11-05-cli-tui-standards.md`). If it is listed, cite the item. If it is new, add
   it to the report under the severity it earns: **High** is a missing confirmation, a lost
   recording, or output that breaks `--json`. **Medium** is ungated colour, a wrong stream, or an
   inconsistent error. **Low** is a glyph, a colour meaning, wording or layout.
3. Confirm it by reading the code or running the command. A survey line is a lead, not proof.
4. Fix it if the fix is small and safe (Boy Scout). If it changes visible output that a script
   could read, or touches a safety path, say so and ask first.
