---
name: contributing
description: Guidelines for agents making code or architecture changes to the canair codebase (CLI subcommands, transports, modes, library code, tests). Load this whenever you are adding, refactoring, or removing canair Python code — NOT for reverse-engineering PIDs or editing vehicle data (use the ioniq-reverse-engineering / reverse-engineer-pid skills for that).
---

# Contributing to canair

Guidelines for changing canair's own code (the `canlib/` package: CLI,
transports, modes, library, tests). This is about *engineering* the tool — for
vehicle/PID work load the **ioniq-reverse-engineering** and
**reverse-engineer-pid** skills instead.

Always run and test the tree with `uv run …` from the repo root (never a
globally-installed `canair`). See `AGENTS.md` for why.

## Non-negotiables

1. **Both transports must always work.** Every feature that talks to the CAN
   bus MUST function over **both** transports (see the Transports section).
2. **`slcan-tcp` is the canonical default.** It runs on both the WiCAN Pro and
   the classic WiCAN, so it is the default when nothing is configured. Do not
   reintroduce a `wican-ws`-only default or a `wican-ws`-only code path.
3. **Never break the real car.** No UDS programming session (`10 02`), no
   firmware/write/upload services. The command blocklist in
   `canlib/elm327.py` (`BLOCKED_UDS_SERVICES` / `check_command_safety`) exists
   for this reason — extend it, never quietly bypass it.
4. **Tests pass and cover the change.** Run `uv run pytest -q`; add tests for
   new behavior. `uv run canair validate all` must stay green after data-schema
   touching changes.

## Transports — the most important architectural rule

canair speaks to the bus through one of two transports, selected explicitly in
`canlib/transport/config.py::resolve_transport` (precedence:
`--transport`/`--wican` > config `transport:` block > default `slcan-tcp`):

| Transport   | Class            | How it moves bytes                              |
|-------------|------------------|-------------------------------------------------|
| `slcan-tcp` | `RawTerminal`    | python-can + **client-side ISO-TP** (default)   |
| `wican-ws`  | `WiCANTerminal`  | WiCAN WebSocket ELM327 terminal (device ISO-TP) |

Both classes expose the **same async surface**: `set_header(tx_id)`,
`send_uds(pid, timeout=, expected_sid=, expected_did=)`, `send_command(cmd)`,
`enter_extended_session(wake=)`, `close()`. Live commands are dispatched through
the **single shared** `canlib/commands/_live.py::dispatch_mode`, which both the
ELM path (`async_main`) and the raw path (`modes/raw_ops.py::run_raw`) call.

**Consequence — the design contract:**

- Write mode handlers against that terminal surface **only**. If a new command
  goes through `dispatch_mode` and uses just those methods, it works on both
  transports for free. This is exactly how `dtc`, `identity`, `scan`,
  `routines`, etc. get dual-transport support with one implementation.
- **Do not** open your own WebSocket, python-can bus, or raw socket, and do not
  branch on `transport.type` inside a mode. If you think you need to, stop and
  reconsider — nearly always the right move is to add a method to *both*
  terminal classes so the mode stays transport-agnostic.
- If a genuinely transport-specific optimization is unavoidable (e.g. the
  pipelined `raw_monitor` fast path), it must be an *optimization of* an
  already-working shared path, and the shared path must still function on both.
- When you add a terminal method, add it to **both** `WiCANTerminal`
  (`canlib/terminal.py`) and `RawTerminal`
  (`canlib/transport/raw_terminal.py`) with matching signatures, and keep the
  returned dict shape identical (both funnel through
  `elm327.parse_elm_response`).

## Adding a CLI subcommand

Commands live in `canlib/commands/<name>.py`. Each module exposes:

- `NAME` — the subcommand string.
- `add_parser(subparsers)` — register an argparse subparser and call
  `parser.set_defaults(func=run)`.
- `run(args)` — return an int exit code (or `None`).

Then register `NAME` in `canlib/commands/__init__.py::COMMAND_NAMES` (order =
help order).

**Live (device-talking) commands** additionally:

- Call `add_connection_args(parser)` and `finalize_live_parser(parser, …)` from
  `canlib/commands/_live.py`. `finalize_live_parser` backfills every attribute
  in `CANREQ_DEFAULTS` the parser doesn't expose and wires `func=run_live`.
- Add any **new mode-selector attribute** to `CANREQ_DEFAULTS` with a falsy
  default. `dispatch_mode`'s `elif` chain reads `args.<selector>` for *every*
  command, so a missing default will `AttributeError` on unrelated commands.
- Add the dispatch branch in `dispatch_mode` (keep options read *inside* the
  guarded branch so they need not be global defaults). Delegate to a handler in
  `canlib/modes/<name>.py` and export it from `canlib/modes/__init__.py`.

Follow an existing command as a template: `commands/routines.py` +
`modes/routines.py`, or `commands/dtc.py` + `modes/dtc.py`.

## Mutative / sensitive operations

- Anything that changes ECU or device state (clearing DTCs, IOControl,
  routines that actuate, config writes, reboots) must **confirm before acting**
  (interactive `[y/N]` prompt) with an explicit `--yes`/flag escape hatch for
  scripting. See the DTC clear path in `dispatch_mode` and the routine-start
  confirmation for the pattern.
- Reads are free; be gentle regardless (old, slow ECUs; one connection at a
  time).

## Data & generated artifacts

- `profiles/*/ecus/` is the source of truth — edit via `canair pids` (validated,
  comment-preserving), not by hand.
- `profiles/*/captures/*.yaml` are **never** hand-written; record with
  `canair … --save`. Edit/remove via `canlib.captures` helpers.
- `profiles/*/out/*.json` is generated by `canair wican` — never hand-edit;
  regenerate.
- Schemas are tool-owned in `canlib/schema/`. Validate with `canair validate`.

## Test coverage

- **Every behavioral change ships with tests.** New command/mode/helper → new
  tests; bug fix → a regression test that fails before and passes after.
- Prefer fast, device-free unit tests. Drive modes with a fake terminal
  exposing the `set_header`/`send_uds`/`enter_extended_session` surface
  (`tests/test_dtc.py`, `tests/test_identity.py` are templates); never require a
  live WiCAN in tests.
- Cover both the happy path and the failure paths that matter: NRC responses,
  `NO DATA`, malformed payloads, declined confirmations.
- For anything touching the bus, add at least one test that proves it runs
  through the shared `dispatch_mode` (the transport-agnostic path) so both
  transports stay covered — see `TestDispatchTransportAgnostic` in
  `tests/test_dtc.py`.
- Cross-cutting policy gets its own test module (e.g. `tests/test_safety.py`),
  not just incidental coverage inside one caller.

## Refactor proactively — no monoliths

Do not silently pile onto a design that no longer fits. When you touch an area,
leave it better than you found it, and **speak up** when a structural change is
warranted rather than bolting on more:

- **File size is a smell.** As a file approaches ~500 lines — and *well* before
  1000 — stop and split it by concern (separate the pure helpers, the async
  device orchestration, the TUI, the record/table data). `modes/identity.py`
  splitting out `identity_decode.py`/`identity_records.py` is the pattern to
  copy.
- **Duplication across transports/commands is a refactor signal.** If you find
  the same policy implemented in two places (as the command blocklist once was —
  duplicated and *divergent* between `WiCANTerminal` and `RawTerminal`), extract
  it to one shared home and have both call it. That guard now lives in
  `canlib/safety.py::enforce_command_safety`; both terminals await it, so the
  policy is identical on every transport. Blocklist data itself stays in
  `elm327.py` (`BLOCKED_UDS_SERVICES`).
- **When incremental changes are compounding complexity, propose a redesign**
  before adding another layer. Surface the tradeoff to the user (a short "this
  is drifting; here's the cleaner shape" note) instead of quietly extending a
  strained abstraction. Suggesting the refactor is part of the job — even if the
  user ultimately declines it.
- Prefer plain functions over single-method classes; decompose god objects into
  focused collaborators with clear boundaries.

## Code style (see also ~/.config AGENTS.md)

- Self-documenting code; comments explain *why*, not *what*.
- Match the surrounding style; type hints as used elsewhere in `canlib/`.
- Keep new files single-purpose from the start rather than growing a grab-bag.

## Before you finish

```bash
uv run pytest -q                 # all tests green
uv run canair <yourcmd> --help   # parser sane
uv run canair validate all       # if you touched ecus/captures/schema
```
