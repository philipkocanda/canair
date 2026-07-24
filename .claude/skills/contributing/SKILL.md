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

## First principle — describe intent, not code that will drift

**Avoid baking concrete code into this skill (or any doc).** Snippets,
signatures, line numbers, and verbatim names go stale silently; an agent that
trusts them builds on a false premise.

- **Describe intent and *where* to look, don't paste code.** Point at the file
  to read now ("follow `commands/dtc.py` + `modes/dtc.py`"; "the guard is in
  `canlib/safety.py`") — paths are cheap to re-verify, snippets rot invisibly.
- **Never mirror signatures, method lists, arg names, or blocklist contents**
  here; they belong in the source.
- **Treat any specific as a pointer to verify, not a fact.** Confirm against the
  tree; if this skill has drifted, fix it (Boy Scout rule).
- Applies to this skill itself: keep it intent-level, not a snapshot of today's
  code.

## Non-negotiables

0. **canair is a CLI built for both human *and* agentic use.** Every capability
   is a composable, scriptable subcommand; keep it that way. New features must
   work non-interactively (a flag escape hatch for any prompt, e.g. `--yes`) and
   offer structured `--json` output where a result is consumed programmatically,
   so an AI agent (e.g. Claude) can drive the tool autonomously just as a human
   would at a terminal. Don't add capabilities reachable *only* through an
   interactive TUI.
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
5. **Two data domains, one tool: don't hardwire to WiCAN or to diagnostics.**
   canair analyzes both **diagnostic responses** (request/response UDS/KWP2000)
   *and* **raw CAN frame captures** (passive broadcast traffic). Treat raw
   frames as first-class, and the WiCAN as a *replaceable transport* — not a
   baked-in assumption. See "Two data domains" and "Keep the WiCAN replaceable".

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

## Two data domains — diagnostics *and* raw CAN frames

canair grew up request/response (UDS/KWP2000), but the bus is also full of
**passive broadcast traffic** no diagnostic request elicits. Both are
first-class, parallel domains — not one bolted onto the other.

| Domain | What it is | Today's surface | Maturity |
|--------|-----------|-----------------|----------|
| **Diagnostics** | request/response UDS/KWP2000 over ISO-TP | `query`/`scan`/`dtc`/`identity`/…, captures decoded via `ecus/*` PID params | mature |
| **Raw frames** | passively-sniffed broadcast frames | `canair sniff` → live table + python-can `.asc`/`.blf`/`.csv` log | **under-developed** — logs externally, not into the profile |

`SlcanTcpBus` (`transport/slcan_tcp.py`) is already a clean, vehicle-neutral
`can.BusABC` — the seam to build the raw-frame domain on.

**Extending raw-frame support, keep the two domains symmetric — don't fork a
parallel half-baked stack:**

- **Capture parity.** Frame captures use the *same code path* as diagnostic
  captures — recorded into the profile via the shared capture/journal machinery
  (scoped, schema-validated, journaled) and queryable/diff-able/time-alignable
  through the same loaders, not a second bespoke path. (The user-facing
  record/label *workflow* is RE — see the reverse-engineering skills.)
- **Analysis parity.** A broadcast signal (arbitration-ID + bit/byte field)
  flows into the existing `decode`/`correlate`/`hunt`/`align`/`xanalysis`
  tools, not a bespoke analyzer.
- **Definition parity.** A broadcast signal map (arbitration ID → named signals,
  DBC-like) is the frame-domain analogue of a PID's parameters — model it in the
  profile with the same edit-via-tool/schema-validated discipline, never
  hand-edited YAML.
- **Shared primitives, separate concerns.** Frame parsing/signal extraction is
  its own module (mirror `uds_parse.py`), not grown inside a UDS file. Shared
  logic (byte/bit extraction, expression eval, correlation) goes in a neutral
  helper both call.

If diagnostics assumptions leak into shared code (e.g. a capture loader
assuming an ISO-TP `payload` + `pid`), generalize the shared layer rather than
special-casing frames.

## Keep the WiCAN replaceable

The WiCAN is **one device we reach the bus through**, not the tool's definition.
The transport abstraction already reflects this (`slcan-tcp` is plain
SLCAN-over-TCP against any WiCAN or gateway; `SlcanTcpBus` is a generic
`can.BusABC`). Preserve and deepen it:

- **Bus access goes through the transport layer** (`transport/`), never a
  WiCAN assumption baked into a command or mode. A future `socketcan`,
  serial-SLCAN, `.asc`/`.blf` *replay*, or other-gateway transport should slot
  in by implementing the transport surface — commands must not need edits.
- **Isolate WiCAN-specifics.** Device-management (mode switch, datarate/port
  discovery, reboot, AutoPID, AP-mode default IP) lives in the `wican_*` modules
  and `commands/wican.py`, not spread into generic commands. `sniff.py` today
  reaches into the WiCAN modules for port/bitrate/mode; when you touch it, route
  that through the transport/config layer so a non-WiCAN backend needs no WiCAN
  calls.
- **`wican-ws` is WiCAN-coupled by nature** (device-side ELM327); fine, because
  it lives *behind* the transport surface. Keep the coupling there — don't let
  it leak upward.
- **Naming:** generic bus/frame/analysis code is named for what it does
  (`slcan`, `can`, `frame`, `bus`); reserve `wican_*` for device-specific code.

Litmus test for a bus-touching feature: *would it still make sense from a
SocketCAN interface or a replayed `.asc` instead of a WiCAN?* If it works "only
with edits to the command," push the device-specific part into the transport
layer.

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
  comment-preserving), not by hand. This covers parameters (`upsert-param`),
  research entries (`add-research`/`set-status`), PID lifecycle
  (`set-pid-status`), **and curated identity fields** like `notes`/`description`
  (`set-identity`). **Keep this coverage complete:** if you ever find yourself
  hand-editing a field of `ecus/` because no `canair pids` subcommand reaches it,
  the fix is to add the surgical/validated editor (a `canlib.pids_edit` helper +
  a `canair pids` subcommand) — not to normalize hand-editing. A gap in the CLI
  editor is a bug to close, so new hand-curated fields must gain tool support.
- `profiles/*/captures/*.yaml` are **never** hand-written; they are recorded by
  the tool (the `--save` path) and edited/removed via `canlib.captures` helpers
  — the recording/labelling *workflow* is covered by the RE skills. Raw-frame
  captures (see "Two data domains") must go through the same shared
  capture/journal machinery, not an external-only file.
- `profiles/*/out/*.json` is generated by `canair wican autopid write` — never hand-edit;
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

Do not silently pile onto a design that no longer fits. **Boy Scout rule:** when
you touch an area, leave it better than you found it (fix the defect you walked
past), and **speak up** when a structural change is warranted rather than
bolting on more:

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
- **Type-hint the critical paths.** Where a mistake is easy to make and costly —
  UDS/CAN byte handling (`bytes`/`int` offsets, PID/DID IDs), the terminal
  surface and its returned dict shapes, expression eval, capture/schema records,
  and anything crossing the transport boundary — add explicit type hints (and a
  `TypedDict`/dataclass over a bare `dict` where the shape matters). CI runs the
  `ty` type checker over `canlib/` (`uv run ty check`), so hints are **enforced**
  — a new `int`-vs-`bytes` or wrong-key slip fails the build. Prefer narrowing a
  nullable (`assert x is not None` where the invariant holds) or a precise
  annotation over `# type: ignore`; reserve ignores for genuine stdlib
  false-positives with a comment. Prioritize hints where they prevent accidental
  errors, not as blanket ceremony on trivial locals.
- Keep new files single-purpose from the start rather than growing a grab-bag.

## Keep the docs and README current — non-negotiable

**User-facing docs are part of the change, not an afterthought.** Any change that
adds, removes, or alters a user-facing capability — a new/renamed subcommand, a
changed/added/removed flag, a shifted default, new setup/config steps, a changed
workflow, a new profile field — MUST update the docs in the same change. Stale
docs mislead as badly as stale code. If nothing user-facing changed, confirm that
rather than assume it.

**The README vs `docs/` split (respect it):**

- **`README.md` stays compact and high-level.** It's the landing page /
  gateway: what canair is, the connection diagram, the command *map* (one crisp
  line per subcommand), a short quick-start, the bring-your-own-car *arc*, the
  bundled-profile highlights, license, warning. **Detail does not belong here** —
  every section links *into* `docs/`. Do not re-expand it into a manual (it was
  deliberately cut 311→143 lines; keep it lean).
- **`docs/` carries the detail.** It's task-first, optimized for **new-car users**
  and **PID/profile contributors**: `getting-started/`, the
  `bring-your-own-car/` journey (create → discover → identity → scan → capture →
  analyze → define/verify → share), `concepts/`, and `reference/`. New detail,
  worked examples, per-command flags, and walkthroughs go here.
- **`docs/` is the human-facing rendering of the same knowledge in AGENTS.md and
  the skills** — it should *reference* them, not duplicate them. Where a fact is
  authoritative elsewhere (config keys in `config.example.yaml`, flags in
  `--help`, schema in `canlib/schema/`), point at / derive from it rather than
  copy it, so it can't drift.

**Concretely, when you touch a user-facing surface, check and update as needed:**

1. the relevant **`docs/`** page(s) — the deep detail;
2. the **`README.md`** command map / quick-start / arc — only the high-level
   pointer, kept terse and linking into `docs/`;
3. **`AGENTS.md`** — the exhaustive agent-facing command reference (keep the
   tool list, flags, and file map accurate);
4. the **skills** (`.claude/skills/`) if the RE/contributing *workflow* changed.

Verify every internal doc link still resolves (relative `.md` links and
README→`docs/` links); a broken cross-link is a defect (Boy Scout: fix stale
paths you pass). The docs strategy and the README/`docs/` policy are recorded in
`plans/2026-07-24-documentation-strategy.md`.

## Before you finish

```bash
uv run pytest -q                 # all tests green
uv run ruff check . && uv run ruff format --check .   # lint + format
uv run ty check                  # type check (canlib/) — must be clean
uv run canair <yourcmd> --help   # parser sane
uv run canair validate all       # if you touched ecus/captures/schema
```

Then confirm the **docs + README** reflect any user-facing change (see the
policy above) and that internal doc links still resolve.
