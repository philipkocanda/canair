# CLI streams, exit codes and messages

Where output goes (stdout/stderr, `--json`), the exit codes, and the four message levels.

## Streams and exit codes

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

## Messages

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
