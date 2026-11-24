# CLI subcommands and conventions

Adding subcommands, ECU/PID selection, time/scope flags, and mutative operations.

## Adding a CLI subcommand

Commands live in `src/canair/cli/commands/<name>.py` or `<name>/` (most are packages), exposing
`NAME`, `add_parser(subparsers)` (which calls `parser.set_defaults(func=run)`), and
`run(args) -> int | None`; register `NAME` in `cli/commands/__init__.py::COMMAND_NAMES` (order =
help order).

**Live (device-talking) commands** additionally call `add_connection_args` +
`live_command` from `src/canair/cli/live/` (or call `run_live(args, operation)` from their own
`run`). The command names its `LiveOperation`: a handler in `src/canair/cli/live/handlers/` that
reads the parsed flags and calls a `mode_*` coroutine in `src/canair/live/<family>/` with typed
arguments (a family is a sub-package whose `__init__.py` holds only the charter and imports
nothing). Nothing picks a handler by inspecting flags, so add the command's row to `CASES` in
`tests/cli/commands/test_live_operations.py`: it pins the operation the command reaches and that
the parser defines every `args.x` its handler reads directly (use `getattr` for an optional one).

Follow an existing command as a template: `cli/commands/dtc/` + `live/dtc/`.

**Every flag, positional argument and subcommand needs help text** (`help=`), so `--help` and the
generated reference say what it takes. A hidden flag is the one exception, and says so with
`help=argparse.SUPPRESS`. When you add, rename or remove a flag or subcommand, also fix the
description and epilog text that names it (example lines, `Subcommands:` lists, cross-references),
then regenerate `docs/reference/cli/` with `uv run python scripts/gen/gen_cli_reference.py`.

## Selecting ECUs/PIDs — prefer the query mini-language

When a command needs the user to name what to act on, **take a positional QUERY in the shared
mini-language rather than adding `--ecu`/`--pid`/`--param` flags.** The mini-language is the
canonical selection surface across `read`/`captures`/`decode`/`correlate`, so reusing it is
consistent, composable and multi-ECU-capable for free; a flag pair is single-target and drifts.

- **Parse through the shared helper, never re-implement the grammar:**
  `src/canair/query/__init__.py` (`parse_query`/`parse_selector`); capture/analysis commands use
  the alias-aware `parse_canonical_query` in `cli/args/query.py`; device-pipeline commands use
  `query/steps.py`. Templates: `cli/commands/captures/` (positional QUERY),
  `cli/commands/correlate/` (optional `ECU[:PID]`).
- Surviving `--ecu`/`--pid`/`--param` flags are **legacy narrow filters, not precedent**.
- If you need a genuine *filter* on top of a QUERY, extend the shared scoping surface below rather
  than adding a one-off flag.

## Time & scoping conventions

Capture-consuming commands share one scoping surface via
`src/canair/cli/args/scope.py::add_scope_args`, and the join/mirror/notation flags are likewise
declared once (`.claude/skills/canair-commands/analysis.md` lists the shared-flag homes). Two
standing rules:

- **A time-bound flag accepts a timestamp down to microseconds, not a date only** — use
  `parse_iso_datetime`, not `parse_iso_date`, whenever a flag bounds *when* something happened. A
  bare date keeps its whole-day meaning (start-of-day lower bound, end-of-day upper).
- **Add a shared scope flag to `add_scope_args`, not to one command's parser.**

## Mutative / sensitive operations

Anything that changes ECU or device state (clearing DTCs, IOControl, actuating routines, config
writes, reboots) must **confirm before acting** with an explicit `--yes` escape hatch for scripting
— follow the DTC-clear and routine-start paths. A read needs no prompt in the code, but stay
gentle: old, slow ECUs and one connection at a time. "No prompt" is a rule for the code only. An
agent still needs the user's "yes" for the session before any read it runs on the car.
