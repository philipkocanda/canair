---
name: contributing-code
description: Guidelines for agents making code or architecture changes
to the canair codebase (CLI subcommands, transports, modes, library code, tests, releases). Load
this whenever you are adding, refactoring, or removing canair Python code — NOT for contributing a
vehicle profile/PIDs upstream (use contributing-profiles) or reverse-engineering signals (use
ioniq-reverse-engineering / reverse-engineer-signal).
---

# Contributing code to canair

Guidelines for changing canair's own code (the `canlib/` package: CLI,
transports, modes, library, tests). This is about *engineering* the tool.

Related skills — load the right one for the job:

- **`contributing-profiles`** — contributing a vehicle profile / PIDs *upstream*
  (the `canair contribute`/`share` flow, PII & security scrubbing, profile data
  discipline). If your change is *data*, not Python, that's the skill.
- **`reverse-engineer-signal`** / **`ioniq-reverse-engineering`** — the RE
  *workflow* itself (discover → capture → analyze → define → verify).

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

00. **Good design and architecture are CRUCIAL.** canair is meant to live and
    grow for a long time, so **long-term maintainability outranks short-term
    expedience**. Every change should leave the architecture *clearer*, not
    murkier: right seams and abstractions, clear separation of concerns,
    single-purpose modules, no god objects, no leaky coupling across layers.
    When a quick hack and a clean shape diverge, choose the clean shape — or
    stop and surface the tradeoff to the user rather than quietly accruing debt.
    The specific rules below are all *instances* of this one principle — uphold
    the principle even where no rule spells out your case.
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
   `canlib/safety.py` (`BLOCKED_UDS_SERVICES` / `check_command_safety`) exists
   for this reason — extend it, never quietly bypass it.
4. **Tests pass and cover the change.** Run `uv run pytest -q` (parallel by
   default via `addopts = -n auto --dist loadscope`; add `-n0` when iterating on
   a single file or reading one failure's output). Add tests for new behavior.
   `uv run canair validate all` must stay green after data-schema touching
   changes. **Analysis tests must not read an unbounded capture range** —
   `captures/` is append-only and grows with every `--save`, so scope a test to a
   frozen date (`--until`) or a fixture profile, or it gets slower forever and
   eventually breaks (see `tests/test_analysis_golden.py`'s
   `test_cases_cannot_drift_as_captures_grow`).
5. **Two data domains, one tool: don't hardwire to WiCAN or to diagnostics.**
   canair analyzes both **diagnostic responses** (request/response UDS/KWP2000)
   *and* **raw CAN frame captures** (passive broadcast traffic). Treat raw
   frames as first-class, and the WiCAN as a *replaceable transport* — not a
   baked-in assumption. See "Two data domains" and "Keep the WiCAN replaceable".
6. **Never commit personally identifiable or location data.** The profiles,
   captures, docs, tests, and git history are public/shareable — nothing that
   could identify or locate the author (or any owner) belongs in the tree. See
   "No PII or location data".
7. **Never share ECU security keys, seeds, or unlock algorithms.** Distributing
   the material that defeats an ECU's SecurityAccess (0x27) is *illegal* in many
   jurisdictions and unsafe — it must never land in the tree, not even by
   accident. See "Never share security keys".

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
the **single shared** `canlib/modes/dispatch/::dispatch_mode`, which both the
ELM path (`commands/_live/runtime.py::async_main`) and the raw path
(`modes/raw_ops.py::run_raw`) call. It lives in `canlib/modes/` because it
dispatches *to* that package and is called *from* it — a mode importing up into
the command layer was a layering inversion.

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
  `uds_parse.parse_uds_response`).

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
  `canlib/commands/_live/` (re-exported from the package root).
  `finalize_live_parser` backfills every attribute in `CANAIR_DEFAULTS`
  (`_live/defaults.py`) the parser doesn't expose and wires `func=run` (which
  delegates to `run_live`).
- Add any **new mode-selector attribute** to `CANAIR_DEFAULTS` with a falsy
  default, **and** an entry to `canlib/modes/dispatch/::_DISPATCH` — the selection
  table pairs each handler with the predicate that selects it. A selector with no
  table entry is not an error: it falls through to the interactive REPL.
  `tests/test_dispatch_table.py` fails if you forget, and pins the table's order
  (which is load-bearing: `multi` + `monitor` must be tested before bare `multi`).
- Add the dispatch branch in `dispatch_mode` (keep options read *inside* the
  guarded branch so they need not be global defaults). Delegate to a handler in
  `canlib/modes/<name>.py` and export it from `canlib/modes/__init__.py`.

Follow an existing command as a template: `commands/routines.py` +
`modes/routines.py`, or `commands/dtc.py` + `modes/dtc.py`.

## Selecting ECUs/PIDs — prefer the query mini-language

When a new or updated command needs the user to name what ECU(s)/PID(s) to act
on, **take a positional QUERY in the shared mini-language rather than adding
`--ecu`/`--pid` (or a bespoke `--param`) flag pair.** The mini-language
(`ECU:PID`, whitespace = OR across selectors, colon binds a PID to its ECU) is
the canonical selection surface across `query`/`captures`/`decode`/`correlate`,
so a new command that reuses it is consistent, composable, and multi-ECU-capable
for free — whereas a `--ecu X --pid Y` pair is single-target, non-composable, and
drifts from the established UX.

- **Parse through the shared helper, don't re-implement the grammar.** The
  canonical parser lives in `canlib/query.py` (`parse_query`/`parse_selector`,
  the `Query`/`Selector` dataclasses); capture/decode-style commands use the
  ECU-alias-aware wrapper `_parse_query` in
  `canlib/commands/captures/query.py` (it canonicalizes selector ECUs against
  the registry so `SMK`→`SKM`). Device-pipeline commands go through
  `canlib/modes/multi_parse.py::parse_sub_commands` (which owns the
  "`IGPM 22BC07` is a bogus ECU — bind with a colon" guard). Follow
  `commands/captures/` (positional QUERY) or `commands/correlate/` (optional
  `ECU[:PID]` selector) as templates.
- **The remaining `--ecu`/`--pid`/`--param` flags are legacy, not the pattern to
  copy.** A few survive as narrow filters (`decode --param`, `research --ecu`,
  `bix --annotate --ecu/--pid`); don't treat them as precedent for new commands.
- If you genuinely need a *filter* on top of a QUERY (not the primary selector),
  prefer extending the shared scoping surface (see "Time & scoping conventions")
  over a one-off flag.

## Time & scoping conventions

Capture-consuming commands (`captures`/`decode`/`correlate`/`hunt`/`investigate`)
share one scoping surface via `canlib/capture_dates.py::add_scope_args`
(`--since`/`--until`/`--date`/`--state`/`--label`). Two standing rules for any
**new** time-bound or scope flag:

- **Time-bound flags accept a timestamp down to microseconds by default, not a
  date only.** `--since`/`--until` parse `YYYY-MM-DD` *or*
  `YYYY-MM-DD[ T]HH:MM[:SS[.ffffff]]` (see `parse_iso_datetime`). A bare date
  keeps its whole-day meaning (start-of-day for a lower bound, end-of-day for an
  upper bound); a timestamp narrows to the instant. Don't ship a date-only bound
  and force users back to the tool later — reach for `parse_iso_datetime`, not
  `parse_iso_date`, whenever a flag bounds *when* something happened.
- **Add shared scope flags to `add_scope_args`, not per-command.** A new scope
  affordance that several analysis commands should share belongs in the shared
  helper so every consumer gains it consistently — don't re-declare it in one
  command's parser.

## Mutative / sensitive operations

- Anything that changes ECU or device state (clearing DTCs, IOControl,
  routines that actuate, config writes, reboots) must **confirm before acting**
  (interactive `[y/N]` prompt) with an explicit `--yes`/flag escape hatch for
  scripting. See the DTC clear path in `dispatch_mode` and the routine-start
  confirmation for the pattern.
- Reads are free; be gentle regardless (old, slow ECUs; one connection at a
  time).

## Data & generated artifacts — the plumbing you maintain

This section is about the *code* that reads/writes profile data. The
authoring/scrubbing discipline for the data itself (edit-via-tool, never
hand-edit, what may/may not be committed) is the **`contributing-profiles`**
skill — load it if you're touching the data rather than the machinery.

- `profiles/*/ecus/` is the source of truth, edited only through the
  surgical/validated `canair pids` editors (`canlib/pids_edit.py` /
  `canlib/ecus_edit.py`). **Keep that editor coverage complete:** if a field of
  `ecus/` can only be changed by hand because no `canair pids` subcommand reaches
  it, the fix is to *add* the surgical/validated editor — a gap in the CLI editor
  is a bug to close, not a reason to normalize hand-editing.
- **Free-text fields render themselves.** Both writer subsystems format notes via
  one shared policy (`canlib/yaml_rt.py`:
  `note_should_inline`/`wrap_note_lines`/`folded`; the text path's
  `_format_block_scalar`) — a short note stays inline, a longer one becomes a
  wrapped folded `>-` block (folding only that scalar, never reflowing the file).
  A new ruamel-written free-text field just needs adding to
  `ecus_edit.FREE_TEXT_FIELDS` to get the same treatment.
- `profiles/*/captures/*.json` are recorded by the tool (the `--save` path), never
  hand-written. Raw-frame captures (see "Two data domains") must go through the
  *same* shared capture/journal machinery, not an external-only file. These
  per-day files are **append-only** logs; two machines' same-day appends are
  auto-unioned by canair's git merge driver (`canair captures merge-driver`). If
  you touch the capture on-disk format, the pure union lives in
  `canlib/captures_merge.py` — keep it format-aligned with
  `canlib/capture_io.py`.
- `profiles/*/out/*.json` is generated by `canair wican autopid write` — never
  hand-edit; regenerate.
- Schemas are tool-owned in `canlib/schema/`. Validate with `canair validate`.

## No PII or location data — the tree is public

Everything committed here is **shareable/public** (profiles are meant to be
contributed upstream; git history is forever). Nothing that could identify or
geolocate the author — or any car owner — may land in the tree, in **code, data,
docs, tests, fixtures, commit messages, or history**. When in doubt, leave it
out and ask. (This mirrors the same rule in **`contributing-profiles`** — there
it's about scrubbing *data*; here it's about not baking a real IP/email/VIN into
*code*, `--help` strings, or test fixtures.)

Watch for these leaks (not exhaustive — reason about the *class*, not the list):

- **Identity:** real names, emails, phone numbers, usernames, faces, signatures.
  The `canair contribute` scan only looks for emails here — a phone number in a
  note is on the author/reviewer to catch.
- **Vehicle identity:** VINs, license plates, insurance/registration numbers —
  and remember these hide *inside captured payloads* (the VIN comes back from UDS
  `F190` / KWP2000 record `90`). A capture is not automatically safe just
  because it's "raw bytes". A per-unit ECU serial (`F18C`/`F18B`) is *not* in this
  class — it names a module, not a person.
- **Location:** home/work addresses, GPS/lat-lon, odometer-at-a-place, Wi-Fi
  SSIDs/BSSIDs, and network coordinates that pin a place — real LAN/VPN IPs,
  MAC addresses, hostnames. Config carrying these (`wican_addresses`, etc.)
  lives in the **gitignored** `~/.config/canair/config.yaml`; keep real values
  out of `config.example.yaml`, docs, and tests — use obvious placeholders
  (`10.0.2.86`-style RFC-1918/example values, `you@example.com`).
- **Free-text fields that quietly accumulate PII:** capture `--label`/`--notes`,
  `research:` notes, DTC-log labels, profile descriptions. Keep them technical.

**Practices:**

- **Examples use placeholders, never real values.** Any address/email/VIN/IP in
  a doc, `--help` string, test fixture, or `*.example.*` file must be a
  synthetic placeholder.
- **Scrub before you commit.** When adding a capture or fixture drawn from a real
  car, check it (and its notes/labels) for the classes above; redact identity
  DIDs. `canair validate` is not a privacy scanner — the reviewer is.
- **If something sensitive already reached the tree, treat it as an incident:**
  removing it in a new commit does *not* erase it from history — flag it to the
  user (history rewrite / secret rotation may be needed), don't just delete-and-
  move-on.
- **Don't build features that make leaking easier by default** — e.g. a command
  that bakes the resolved device IP into a committed artifact, or auto-fills a
  real address into shareable data. Default to redaction; require an explicit
  opt-in to include anything identifying.

## Never share security keys — it's illegal

**Sharing the material that unlocks an ECU's SecurityAccess (UDS/KWP2000 `0x27`)
is illegal in many jurisdictions, and dangerous — it never belongs in this tree,
not even by accident.** (Mirrored in **`contributing-profiles`** for the data
angle — captures/notes; here it's about not re-adding a solver to the *code*.)
Security access gates the services that can reflash,
reprogram, or otherwise alter an ECU; the ability to defeat it is exactly what
manufacturers restrict to authorized dealers and licensed repairers.

**canair deliberately ships no key-cracking / seed→key solver.** A `0x27`
pair-solver existed once and was removed; do **not** re-add one (nor a bank of
per-marque unlock algorithms, a `--pair` seed:key identifier, or a `security`
pipeline step). canair stays a read/diagnostics tool. It stays *aware* of `0x27`
at the protocol level — the service table entry, the `securityAccessDenied`
(NRC 0x33) classification in scanners, and the response pretty-printer are fine —
but it does not attempt to *break* security access.

**Forbidden in code, data, profiles, captures, docs, tests, fixtures, commit
messages, and history:**

- **Keys / seed→key pairs / unlock responses** — a captured `27 02 <key>` request
  or `67 01 <seed>` response, a seed paired with the key that unlocked it, or any
  recorded successful-unlock exchange.
- **Unlock algorithms & secrets** — a seed→key transform for any ECU, PINs,
  passwords, certificates, or any manufacturer secret.
- **Key-cracking tooling** — a pair-solver, algorithm bank, or brute-forcer, in
  any form (see above: the removed solver must not come back).
- **Anything that lets a reader reconstruct the above**, including a "worked
  example" in a doc or a realistic test fixture built from a real ECU's seed/key.

**Practices:**

- **Tests use synthetic seeds/keys only** — invented values, never a real ECU's —
  and only where protocol *awareness* (NRC classification, response formatting)
  needs coverage, never to exercise a solver.
- **If a user needs a capability that requires security access** (dealer-level
  reprogramming, coding, or an ECU-specific unlock), **do not attempt to source,
  derive, or share the key** — direct them to their **local authorized dealer or
  a licensed repairer**, who are permitted to perform it.
- **If key material already reached the tree, treat it as a security incident**
  (as with PII above): flag it to the user immediately — a plain delete does not
  purge git history, and the key may need rotating — don't just delete-and-move-on.

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
- **A command is one module until it needs two — then it becomes a package.**
  When a subcommand outgrows `commands/<name>.py`, create `commands/<name>/` and
  put the concerns inside it. Do **not** add a flat `_<name>_*.py` sibling; that
  convention is retired for new work. `commands/captures/` (19 files) and
  `commands/validate/` are the precedents, and `cli.py::_GROUP_DEFAULTS` /
  `commands/__init__.py::COMMAND_NAMES` are keyed by the command *name string*, so
  the module layout is free to change. Two corollaries:
  - **Package shape ≠ module size.** They are orthogonal. The largest file in the
    tree is `commands/validate/pids.py` (1441 lines) — *inside* a package.
    Packaging discharges nothing; the ~500-line rule still binds every member.
  - **Split and package in one commit.** Splitting a monolith into a `.py` plus a
    `_<name>_render.py` sibling, intending to package it "later", just books the
    rename twice.
  Existing flat-sibling commands are grandfathered — convert them when the size or
  concern count justifies the churn, or opportunistically when next touched, not
  as a campaign. Rationale and the measured per-command breakdown:
  `plans/2026-08-06-command-packages-and-live-split.md`.
- **Command-private vs shared command-layer infra.** Before moving a `_x.py` into
  a package, check who imports it. Used by *one* command (+ its test)? It is
  package-private — move it in. Used by several commands (`_group`, `_join`,
  `_categories`, `_hexarg`, `_hints`, `_promote`, `_can_args`)? It is a shared
  layer — leave it a flat sibling, or push it down to `canlib/` if nothing about
  it is CLI-specific. A *library* module importing up into `canlib.commands` is
  always the second case, and always a bug.
- **Duplication across transports/commands is a refactor signal.** If you find
  the same policy implemented in two places (as the command blocklist once was —
  duplicated and *divergent* between `WiCANTerminal` and `RawTerminal`), extract
  it to one shared home and have both call it. That guard now lives in
  `canlib/safety.py::enforce_command_safety`; both terminals await it, so the
  policy is identical on every transport. Blocklist data itself stays in
  `safety.py` (`BLOCKED_UDS_SERVICES`).
- **When incremental changes are compounding complexity, propose a redesign**
  before adding another layer. Surface the tradeoff to the user (a short "this
  is drifting; here's the cleaner shape" note) instead of quietly extending a
  strained abstraction. Suggesting the refactor is part of the job — even if the
  user ultimately declines it.
- Prefer plain functions over single-method classes; decompose god objects into
  focused collaborators with clear boundaries.

## Code style (see also ~/.config AGENTS.md)

- **Hard-wrap every file at 100 columns** — Python, Markdown, YAML, JSON, commit messages, plan
  docs, skills. It matches `line-length = 100` in `pyproject.toml` (ruff enforces it for Python;
  prose is on you). Do not write one-line paragraphs: a multi-thousand-character line is unreadable
  in a terminal, unreviewable in a diff (any edit rewrites the whole line), and expensive to patch.
  Wrap only where it doesn't break meaning — a Markdown table row, a long URL, a YAML frontmatter
  `description:`, verbatim fenced code and generated files (`docs/reference/cli/`, `--help`-derived
  blocks) are the exceptions. If you touch a long line, re-wrap it (Boy Scout). Policy:
  `AGENTS.md` → "Formatting".
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
  worked examples, per-command flags, and walkthroughs go here. Two sections are
  contributor-facing rather than user-facing: `contributing/` (share a profile)
  and **`development/`** (change canair's own code — dev setup, the pre-PR
  checks, commit messages, the screenshot pipeline, offline testing); a change to
  the engineering workflow belongs there, not in `contributing/`.
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
5. **`CHANGELOG.md`** — **do not edit it.** It is generated by release-please from
   commit subjects (there is no longer an `[Unreleased]` section to add to). The
   changelog equivalent of "keep the docs current" is now **writing a good commit
   subject**: it becomes the release note's first draft, so a user-facing change
   (new/renamed command, changed/added/removed flag, shifted default, new profile
   field) needs a `feat:`/`fix:` subject that says what changed. The only manual
   changelog editing happens in the release PR (see "Cutting a release").

Verify every internal doc link still resolves (relative `.md` links and
README→`docs/` links); a broken cross-link is a defect (Boy Scout: fix stale
paths you pass). The docs strategy and the README/`docs/` policy are recorded in
`plans/2026-07-24-documentation-strategy.md`.

**Screenshots are generated, not hand-made.** The docs embed SVG/GIF captures of
the CLI produced from `docs/screenshots/shots.yaml` by
`scripts/gen_screenshots.py` (`freeze` for static output, `vhs` for interactive
TUIs), all against the bundled read-only `ioniq-2017` profile — device-free and
PII-free. If your change alters the **output** of a screenshotted command,
re-render (`make screenshots` / `make screenshots-only ONLY="…"`) and commit the
updated asset; adding a shot means an entry in `shots.yaml`, not a hand-drawn
image. CI + the pre-push hook run `gen_screenshots.py --check` (asset presence +
command validity, no re-render), so a renamed command/flag fails until you
regenerate. Never screenshot views that surface free-text capture notes/labels
(PII).

## Commit messages

**canair uses [Conventional Commits](https://www.conventionalcommits.org/).**
`release-please` reads the subject line of every commit to derive the version
bump and the changelog section, so the subject is machine-readable input, not
just prose. Inspect `git log --oneline` for the house style of the *body*, but
the subject's shape is fixed.

- **`type(scope): lower-case summary`.** The **type** is a fixed enum —
  `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`, `ci`, `build`,
  `revert`, `style`, `deps`. The **scope** is the area touched, in parens, and is
  the home of the old free-form prefix vocabulary (`captures`, `monitor`,
  `pids`, `profiles`, `analysis`, `tui`, `lock`, `bix`, …) — read `git log` and
  reuse an existing scope rather than coining a synonym. Scope is optional; omit
  it for genuinely repo-wide changes.
- **Only three things move the version:** `feat` → **minor**, any other listed
  type → **patch**, and `!` after the type/scope (or a `BREAKING CHANGE:` footer)
  → **major**. Use `!` for an incompatible CLI/behaviour change or a
  profile/capture-schema break, matching the SemVer meanings in `RELEASING.md`.
- **A malformed subject fails silently, so it is gated.** An unrecognized type is
  dropped from the changelog without complaint, and a bare subject (no `type:`)
  parses as nothing at all. The `commit-msg` pre-commit hook rejects both — its
  type list, this list, and `release-please-config.json`'s `changelog-sections`
  are one shared vocabulary and must be changed together.
- **Migrating from the old free-form prefixes** (pre-v1.15.0 history is full of
  them; do not copy them):

  | old | now |
  |---|---|
  | `tui: …` | `feat(monitor):` / `refactor(tui):` |
  | `analysis: …` | `feat(analysis):` / `fix(analysis):` |
  | `profiles(ioniq-2017): …` | `feat(profiles):` / `chore(profiles):` |
  | `skills: …`, `skills(x): …` | `docs(skills):` |
  | `captures: …`, `lock: …`, `bix: …` | a type + that area as the scope |
  | `formatting: …` | `fix(formatting):` / `refactor(formatting):` |
  | `types: …` | `refactor(types):` |
  | bare subject (`bitfields`, `plan`) | **never** — parses as nothing |

- **Fixing something that never shipped keeps the *original* commit's type.** A
  defect introduced and corrected within the same unreleased window did not exist
  for any user, so `fix:` would document a bug nobody could have hit — reuse the
  type of the commit being corrected (`ci:` corrects `ci:`, `chore:` corrects
  `chore:`) so the entry stays out of the release notes. Reserve `fix:` for
  something a released version actually did wrong.
- **Body explains the *why* and the shape of the change**, wrapped prose +
  bullets, not a file-by-file changelog (the diff already lists files). Lead with
  intent. The body is *not* parsed (only the subject and a `BREAKING CHANGE:`
  footer are), so it stays prose for humans.
- **Reference the plan doc when the change implements one** (name
  `plans/YYYY-MM-DD-*.md`), the same durable-pointer rule as release notes — and
  like release notes, **keep internal scaffolding out of the subject** (no "Stage
  N"/phase numbers; describe the capability/contract that landed).
- **Write the subject for the changelog reader.** It is the first draft of a
  release note: release-please puts it in the generated section verbatim, and the
  maintainer then rewrites that section into prose (see "Cutting a release"). A
  vague subject makes that rewrite a re-investigation.
- **Commit only when asked, and only intended files.** Follow the git rules in
  the root/`~/.config` AGENTS.md: inspect `git status`/`git diff`/`git log`
  first, stage deliberately (a pre-existing partial index is a smell — reconcile
  it, don't blindly `git add -A` over surprises), never commit secrets/PII, and
  don't push/tag/amend unless explicitly requested.

### Committing safely when other agents may be working concurrently

Multiple agents can share this working tree at once. The index (staging area)
and the stash are **global shared state** — one agent's `git add`/`git reset`
races another's, so an agent that stages broadly can commit files a *different*
agent was mid-edit on. Commit only the paths you own, and never through the
whole-tree shortcuts:

- **Never `git add -A`, `git add .`, or `git commit -a`.** Each stages whatever
  happens to be in the tree — including another agent's in-flight files — and is
  the direct cause of the "committed the wrong files" race. Always scope to
  explicit pathspecs *you* touched.
- **Prefer `git commit -o -- <paths>`** (`--only` mode, the default when you pass
  pathspecs). It builds the commit from HEAD's tree plus exactly those paths via
  a **temporary index**, ignoring whatever else is staged — so it's insensitive
  to what another agent left in the index, removing the "committed the wrong
  *staged* files" class entirely. (It still *reads* those paths from the working
  tree, so it can't cure a working-tree write race on files you don't own — but
  scoping to your own paths avoids that too.)
- **`git stash` is not a fix.** It's another piece of global shared state; using
  it to "clear" the index only makes interleaving with other agents worse. Don't
  reach for it to work around a dirty index — scope your commit instead.

## Cutting a release

**Releases are automated by release-please; `RELEASING.md` is authoritative** —
follow it and don't duplicate it here. In short: commit subjects drive the version
bump, a `chore(main): release X.Y.Z` PR stays open and current, and **merging it**
tags `vX.Y.Z` and publishes the GitHub Release. You do not bump the version, edit
`uv.lock`, write a changelog heading, tag, or run `gh release create`. Design
record: `plans/2026-08-06-release-please.md`.

**Never hand-bump `pyproject.toml`/`uv.lock` or hand-write a `## [X.Y.Z]`
heading.** Both are release-please's output; doing it by hand puts the manifest,
the tag and the file out of step. If a version genuinely needs forcing, use a
`Release-As: X.Y.Z` commit footer (see `RELEASING.md` → Overrides).

**The one thing a human still writes is the changelog prose.** release-please emits
raw commit subjects into the release PR; rewrite that section into canair's normal
voice **on the release PR's branch**, immediately before merging — the branch is
force-pushed whenever the notes change, so earlier edits are lost. Note the
generated *Release body* comes from the PR body, not the file (`RELEASING.md`
covers the `gh release edit` fix-up).

**Write release notes for the *reader*, not the committer:**

- **Never expose internal development scaffolding** — plan-doc "Stage N", phase
  numbers, internal milestone names, or private branch/ticket IDs mean nothing
  to a user and read as noise. Describe the *capability that shipped*
  (subcommands, flags, behavior), grouped by theme.
- **When a feature is one thread of a larger effort, reference the plan doc
  explicitly** (e.g. link/name `plans/2026-07-24-raw-can-analysis.md`) instead of
  citing the internal stage numbers — the plan is the durable, self-describing
  pointer; "Stages 0–5" is not.
- Keep the same categorized shape as prior releases (Highlights → themed
  sections → Fixes & docs → full-changelog compare link). The generated subjects
  are a *checklist of what landed*, not the notes themselves — expand them from
  the commits' bodies and `git log vPREV..HEAD`, and drop entries for defects that
  never shipped (a bug introduced and fixed within one unreleased window
  documents nothing a user experienced).
- Group breaking changes under an explicit heading **only if** you're keeping
  them in the notes; if the user asks to drop a note, drop it cleanly.

## Before you finish

```bash
uv run pytest -q                 # all tests green
uv run ruff check . && uv run ruff format --check .   # lint + format
uv run ty check                  # type check (canlib/) — must be clean
uv run canair <yourcmd> --help   # parser sane
uv run canair validate all       # if you touched ecus/captures/schema
```

The repo ships a `.pre-commit-config.yaml` mirroring the fast CI gates — enable
it once per clone (`uv run pre-commit install --install-hooks`, which installs
all three stages) so `ruff`/`ty` run on every commit, the Conventional Commits
check runs on every commit *message*, and the `gen_*.py --check` currency checks
run on every push. It's the local early-warning for the CI gates; CI stays the
hard gate.

Then confirm the **docs + README** reflect any user-facing change (see the
policy above) and that internal doc links still resolve.
