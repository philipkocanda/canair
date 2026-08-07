---
name: contributing-code
description: Guidelines for agents making code or architecture changes
to the canair codebase (CLI subcommands, transports, modes, library code, tests, releases). Load
this whenever you are adding, refactoring, or removing canair Python code — NOT for contributing a
vehicle profile/PIDs upstream (use contributing-profiles) or reverse-engineering signals (use
ioniq-reverse-engineering / reverse-engineer-signal).
---

# Contributing code to canair

Guidelines for *engineering* canair itself (the `canlib/` package: CLI, transports, modes, library,
tests). Related skills: **`contributing-profiles`** if your change is profile *data* rather than
Python; **`reverse-engineer-signal`** / **`ioniq-reverse-engineering`** for the RE workflow itself.

Run and test the tree with `uv run …` from the repo root, never a globally-installed `canair`
(`AGENTS.md` explains why). Flags and subcommand lists are authoritative in `canair <cmd> --help`
and the generated `docs/reference/cli/` — read them there rather than trusting any list.

## First principle — describe intent, not code that will drift

Snippets, signatures, and name lists go stale silently, and an agent that trusts them builds on a
false premise. Point at the file to read *now* ("the guard is in `canlib/safety.py`") instead of
pasting its contents; treat every specific below as a pointer to verify against the tree, and fix it
if it has drifted (Boy Scout). This applies to this skill too — keep it intent-level.

## Non-negotiables

0. **Good design and architecture are CRUCIAL — long-term maintainability outranks short-term
   expedience.** Every change should leave the architecture *clearer*: right seams, separated
   concerns, single-purpose modules, no god objects, no leaky coupling across layers. When a quick
   hack and a clean shape diverge, choose the clean shape — or surface the tradeoff to the user
   rather than quietly accruing debt. Every rule below is an *instance* of this one; uphold the
   principle even where no rule spells out your case.
1. **canair is a CLI built for both human *and* agentic use.** Every capability is a composable,
   scriptable subcommand. New features must work non-interactively (a flag escape hatch for any
   prompt, e.g. `--yes`) and offer `--json` where a result is consumed programmatically. Never add a
   capability reachable *only* through an interactive TUI.
2. **Every transport must always work**, and **`slcan-tcp` is the canonical default** (it runs on
   both the WiCAN Pro and the classic). Don't reintroduce a transport-specific default or code path.
   See Transports.
3. **Never break the real car.** No UDS programming session (`10 02`), no firmware/write/upload
   services. The blocklist in `canlib/safety.py` exists for this — extend it, never bypass it.
4. **Tests pass and cover the change** (see Test coverage).
5. **Two data domains, one tool.** canair analyzes diagnostic request/response traffic *and* raw
   broadcast CAN frames; treat frames as first-class and the WiCAN as a replaceable transport.
6. **Never commit personally identifiable or location data** — see No PII.
7. **Never share ECU security keys, seeds, or unlock algorithms** — see Never share security keys.

## Transports — the most important architectural rule

canair reaches the bus through one **explicitly selected** transport, registered as a
`TransportSpec` in `canlib/transport/config.py::TRANSPORTS` (precedence and the current list:
`AGENTS.md` → Transports). Two families sit behind one surface:

| Family | Transports | How bytes move |
|---|---|---|
| raw | `slcan-tcp` (default) | python-can + **client-side ISO-TP** (`RawTerminal`) |
| ELM327 | `wican-ws`, `elm327-tcp` | device-side ISO-TP via the shared `Elm327Terminal` engine over a swappable `Channel` |

All backends satisfy the `Terminal` protocol (`canlib/transport/protocol.py`) and live commands
dispatch through the **single shared** `canlib/modes/dispatch/::dispatch_mode`, called by both the
ELM path (`commands/_live/runtime.py::async_main`) and the raw path (`modes/raw_ops.py::run_raw`).

**The design contract:**

- Write mode handlers against the `Terminal` surface **only**. A command that goes through
  `dispatch_mode` and uses just those methods works on every transport for free — that's how `dtc`,
  `identity`, `scan`, `routines` get multi-transport support from one implementation.
- **Do not** open your own WebSocket, python-can bus, or socket, and do not branch on
  `transport.type` inside a mode. If you think you must, add the method to *all* terminal
  implementations instead so the mode stays transport-agnostic (matching signatures, identical
  returned dict shape — everything funnels through `uds_parse.parse_uds_response`).
- A genuinely transport-specific optimization (e.g. the pipelined `raw_monitor` fast path) must
  *optimize* an already-working shared path, and the shared path must still work everywhere.
- A new ELM327 wire is a new `Channel`, not a duplicated engine.

## Two data domains — diagnostics *and* raw CAN frames

Diagnostics (request/response UDS/KWP2000, decoded via `ecus/` params) is mature; the raw broadcast
domain (`canair sniff`/`import can`/`correlate can`, `signals/<bus>.yaml`, `captures/can/`) is
younger but **parallel, not bolted on**. `SlcanTcpBus` (`transport/slcan_tcp.py`) is a clean,
vehicle-neutral `can.BusABC` — the seam to build on. Keep the domains symmetric:

- **Capture parity** — frame captures go through the shared capture/journal machinery (scoped,
  schema-validated, journaled), not a second bespoke path.
- **Analysis parity** — a broadcast signal flows into the existing
  `decode`/`correlate`/`hunt`/`align` tools, not a bespoke analyzer.
- **Definition parity** — a broadcast signal map is the frame-domain analogue of a PID's params:
  modelled in the profile with the same edit-via-tool/schema-validated discipline
  (`canair signals`).
- **Shared primitives, separate concerns** — frame parsing lives in its own module (mirroring
  `uds_parse.py`); shared logic (byte/bit extraction, expression eval, correlation) goes in a
  neutral helper both domains call.

If a diagnostics assumption leaks into shared code (e.g. a loader assuming an ISO-TP `payload` +
`pid`), generalize the shared layer rather than special-casing frames.

## Keep the WiCAN replaceable

The WiCAN is one device we reach the bus through, not the tool's definition.

- **Bus access goes through `transport/`**, never a WiCAN assumption baked into a command or mode. A
  future `socketcan`, serial-SLCAN, or `.asc`/`.blf` *replay* transport should slot in by
  implementing the transport surface, with no command edits.
- **Isolate WiCAN-specifics** (mode switch, datarate/port discovery, reboot, AutoPID, AP-mode IP) in
  the `wican_*` modules and `commands/wican.py`. `sniff.py` still reaches into them for
  port/bitrate/mode — route that through the transport/config layer when you touch it.
- `wican-ws` is WiCAN-coupled by nature; that's fine *behind* the transport surface. Don't let it
  leak upward.
- **Naming:** generic bus/frame/analysis code is named for what it does (`slcan`, `can`, `frame`,
  `bus`); reserve `wican_*` for device-specific code.

Litmus test: *would this feature still make sense from a SocketCAN interface or a replayed `.asc`?*
If it works "only with edits to the command", push the device-specific part into `transport/`.

## Adding a CLI subcommand

Commands live in `canlib/commands/<name>.py`, exposing `NAME`, `add_parser(subparsers)` (which calls
`parser.set_defaults(func=run)`), and `run(args) -> int | None`; register `NAME` in
`commands/__init__.py::COMMAND_NAMES` (order = help order).

**Live (device-talking) commands** additionally call `add_connection_args` +
`finalize_live_parser` from `canlib/commands/_live/`, register any new mode-selector attribute in
`CANAIR_DEFAULTS` (`_live/defaults.py`) with a falsy default **and** in
`canlib/modes/dispatch/::_DISPATCH`, then delegate to a handler in `canlib/modes/<name>.py`.
A selector missing from `_DISPATCH` is not an error — it silently falls through to the interactive
REPL, which is why `tests/test_dispatch_table.py` pins both membership and the table's
(load-bearing) order.

Follow an existing command as a template: `commands/dtc.py` + `modes/dtc.py`.

## Selecting ECUs/PIDs — prefer the query mini-language

When a command needs the user to name what to act on, **take a positional QUERY in the shared
mini-language rather than adding `--ecu`/`--pid`/`--param` flags.** The mini-language is the
canonical selection surface across `read`/`captures`/`decode`/`correlate`, so reusing it is
consistent, composable and multi-ECU-capable for free; a flag pair is single-target and drifts.

- **Parse through the shared helper, never re-implement the grammar:** `canlib/query.py`
  (`parse_query`/`parse_selector`); capture/analysis commands use the alias-aware `_parse_query` in
  `commands/captures/query.py`; device-pipeline commands use `modes/multi_parse.py`. Templates:
  `commands/captures/` (positional QUERY), `commands/correlate/` (optional `ECU[:PID]`).
- Surviving `--ecu`/`--pid`/`--param` flags are **legacy narrow filters, not precedent**.
- If you need a genuine *filter* on top of a QUERY, extend the shared scoping surface below rather
  than adding a one-off flag.

## Time & scoping conventions

Capture-consuming commands share one scoping surface via
`canlib/capture_dates.py::add_scope_args`, and the join/mirror/notation flags are likewise declared
once (`AGENTS.md` → Analysis lists the shared-flag homes). Two standing rules:

- **A time-bound flag accepts a timestamp down to microseconds, not a date only** — use
  `parse_iso_datetime`, not `parse_iso_date`, whenever a flag bounds *when* something happened. A
  bare date keeps its whole-day meaning (start-of-day lower bound, end-of-day upper).
- **Add a shared scope flag to `add_scope_args`, not to one command's parser.**

## Mutative / sensitive operations

Anything that changes ECU or device state (clearing DTCs, IOControl, actuating routines, config
writes, reboots) must **confirm before acting** with an explicit `--yes` escape hatch for scripting
— follow the DTC-clear and routine-start paths. Reads are free, but stay gentle: old, slow ECUs and
one connection at a time.

## Data & generated artifacts — the plumbing you maintain

This is about the *code* that reads/writes profile data; the authoring/scrubbing discipline for the
data itself is **`contributing-profiles`**.

- `profiles/*/ecus/` is the source of truth, edited only through the surgical/validated editors
  (`canlib/pids_edit/`, `canlib/ecus_edit.py`). **Keep editor coverage complete** — a field
  reachable only by hand is a bug to close, not a reason to normalize hand-editing.
- **Free-text fields render themselves** via one shared policy (`canlib/yaml_rt.py`): short notes
  inline, longer ones a wrapped folded `>-` block, never reflowing the rest of the file. A new
  ruamel-written free-text field just needs adding to `ecus_edit.FREE_TEXT_FIELDS`.
- `profiles/*/captures/*.json` are written by the `--save` path, never by hand, and are
  **append-only** logs unioned by canair's git merge driver. If you touch the on-disk format, keep
  `canlib/captures_merge.py` aligned with `canlib/capture_io.py`.
- `profiles/*/out/*.json` is generated (`canair wican autopid write`) — regenerate, never hand-edit.
- Schemas are tool-owned in `canlib/schema/`; validate with `canair validate`.

## No PII or location data — the tree is public

Everything committed is shareable and git history is forever, so nothing that could identify or
geolocate the author or any car owner may land in **code, data, docs, tests, fixtures, commit
messages, or history**. (`contributing-profiles` mirrors this for *data*; here it's about not baking
a real IP/email/VIN into code, `--help` strings, or fixtures.) Reason about the *class*, not a list:

- **Identity** — names, emails, phone numbers, usernames, faces, signatures. `canair contribute`
  only scans for emails; a phone number in a note is on you.
- **Vehicle identity** — VINs, plates, registration numbers, and remember these hide *inside
  captured payloads* (the VIN comes back from `F190`). "Raw bytes" is not automatically safe. A
  per-unit ECU serial (`F18C`/`F18B`) is *not* in this class — it names a module, not a person.
- **Location** — addresses, GPS, Wi-Fi SSIDs/BSSIDs, and network coordinates that pin a place (real
  LAN/VPN IPs, MACs, hostnames). Real values live only in the gitignored
  `~/.config/canair/config.yaml`; `config.example.yaml`, docs and tests use obvious placeholders.
- **Free-text that quietly accumulates PII** — capture `--label`/`--notes`, `research:` notes,
  DTC-log labels, profile descriptions. Keep them technical.

**Practices:** examples use synthetic placeholders, always; scrub a real-car capture or fixture (and
its notes/labels) before committing — `canair validate` is not a privacy scanner; don't build
features that leak by default (no resolved device IP baked into a committed artifact); and if
something sensitive already reached the tree, treat it as an **incident** and flag it to the user —
deleting it in a new commit does not erase history.

## Never share security keys — it's illegal

**Sharing the material that defeats an ECU's SecurityAccess (`0x27`) is illegal in many
jurisdictions and dangerous.** It gates the services that reflash or reprogram an ECU, and defeating
it is exactly what manufacturers restrict to authorized dealers and licensed repairers.

**canair deliberately ships no key-cracking / seed→key solver.** One existed and was removed; do not
re-add it, nor a per-marque algorithm bank, a seed:key pair identifier, or a `security` pipeline
step. canair stays *aware* of `0x27` at the protocol level (service table, `securityAccessDenied`
NRC classification, response pretty-printing) but never attempts to break it.

**Forbidden everywhere (code, data, captures, docs, tests, commit messages, history):** keys,
seed→key pairs, recorded successful-unlock exchanges; seed→key transforms, PINs, passwords,
certificates, manufacturer secrets; solvers/brute-forcers in any form; and anything that lets a
reader reconstruct the above, including a "worked example" or a realistic fixture built from a real
ECU's seed/key.

**Practices:** tests use invented seeds/keys only, and only to cover protocol *awareness*, never a
solver. If a user needs a capability requiring security access, **do not source, derive, or share
the key** — direct them to an authorized dealer or licensed repairer. Key material already in the
tree is a **security incident**: flag it (history purge, key rotation), don't delete-and-move-on.

## Test coverage

- **Every behavioral change ships with tests**; a bug fix ships a regression test that fails before
  and passes after.
- Prefer fast, device-free unit tests — drive modes with a fake terminal implementing the `Terminal`
  surface (`tests/test_dtc.py`, `tests/test_identity.py` are templates). Never require a live
  device.
- Cover the failure paths that matter: NRC responses, `NO DATA`, malformed payloads, declined
  confirmations.
- For anything bus-touching, prove it runs through the shared `dispatch_mode` (see
  `TestDispatchTransportAgnostic` in `tests/test_dtc.py`) so every transport stays covered.
- **Analysis tests must not read an unbounded capture range** — `captures/` grows with every
  `--save`, so scope to a frozen date or a fixture profile, or the test gets slower forever and
  eventually breaks (`tests/test_analysis_golden.py::test_cases_cannot_drift_as_captures_grow`).
- Cross-cutting policy gets its own test module (e.g. `tests/test_safety.py`).

## Refactor proactively — no monoliths

Don't silently pile onto a design that no longer fits. Fix the defect you walked past, and **speak
up** when a structural change is warranted instead of bolting on more.

- **File size is a smell.** Approaching ~500 lines — and *well* before 1000 — split by concern (pure
  helpers, async device orchestration, TUI, record/table data). `modes/identity.py` splitting out
  `identity_decode.py`/`identity_records.py` is the pattern.
- **A command is one module until it needs two — then it becomes a package.** Create
  `commands/<name>/`; do **not** add a flat `_<name>_*.py` sibling (retired for new work).
  `commands/captures/` and `commands/validate/` are the precedents, and the command registries are
  keyed by the name *string*, so module layout is free to change. Two corollaries: packaging
  discharges nothing — the ~500-line rule still binds every member; and **split and package in one
  commit**, since splitting now and packaging "later" books the rename twice. Existing flat-sibling
  commands are grandfathered — convert opportunistically, not as a campaign. Rationale:
  `plans/2026-08-06-command-packages-and-live-split.md`.
- **Command-private vs shared command-layer infra.** Before moving a `_x.py` into a package, check
  who imports it: one command (+ its test) → package-private, move it in; several commands → a
  shared layer, leave it a flat sibling or push it down to `canlib/` if nothing about it is
  CLI-specific. A *library* module importing up into `canlib.commands` is always a bug.
- **Duplication across transports/commands is a refactor signal.** The command blocklist was once
  duplicated *and divergent* between terminals; it now lives once in `canlib/safety.py` and both
  await it. Extract to one shared home rather than maintaining two.
- **When incremental changes compound complexity, propose a redesign** before adding another layer —
  surfacing the tradeoff is part of the job even if the user declines.
- Prefer plain functions over single-method classes; decompose god objects into focused
  collaborators.

## Code style

- **Hard-wrap every file at 100 columns** (`AGENTS.md` → Formatting has the rule and its narrow
  exceptions; ruff enforces it for Python, prose is on you). Re-wrap a long line you touch.
- Self-documenting code; comments explain *why*, not *what*. Match surrounding style. Keep new files
  single-purpose from the start.
- **Type-hint the critical paths** — UDS/CAN byte handling (`bytes`/`int` offsets, PID/DID IDs), the
  terminal surface and its returned dict shapes, expression eval, capture/schema records, and
  anything crossing the transport boundary. Use a `TypedDict`/dataclass over a bare `dict` where the
  shape matters. CI runs `ty` over `canlib/`, so hints are **enforced**: prefer narrowing a nullable
  or a precise annotation over `# type: ignore` (reserve those for stdlib false-positives, with a
  comment). Prioritize where hints prevent real mistakes, not as ceremony on trivial locals.

## Keep the docs and README current — non-negotiable

**User-facing docs are part of the change.** Anything that adds, removes, or alters a user-facing
capability (subcommand, flag, default, setup step, workflow, profile field) MUST update the docs in
the same change; if nothing user-facing changed, confirm that rather than assume. The README-vs-
`docs/` split and the "point at the authoritative source instead of copying it" rule are in
`AGENTS.md` → Keep docs & README current; strategy in
`plans/2026-07-24-documentation-strategy.md`. Two things that section doesn't cover:

- **`docs/reference/cli/` is generated** from `--help` (`scripts/gen_cli_reference.py`, CI-checked)
  — never hand-edit it. `docs/development/` (not `contributing/`) is where an *engineering* workflow
  change belongs.
- **`CHANGELOG.md` — do not edit it.** It's release-please output. The changelog equivalent of
  keeping docs current is **writing a good commit subject**; the only manual editing happens on the
  release PR.

Verify internal links still resolve — a broken cross-link is a defect.

**Screenshots are generated, not hand-made** from `docs/screenshots/shots.yaml` by
`scripts/gen_screenshots.py`, against the read-only bundled profile (device-free, PII-free). If your
change alters a screenshotted command's output, re-render (`make screenshots`) and commit the asset;
adding a shot means a `shots.yaml` entry. CI and the pre-push hook run `--check` (asset presence +
command validity), so a renamed command/flag fails until you regenerate. Never screenshot views that
surface free-text capture notes/labels.

## Commit messages

**canair uses [Conventional Commits](https://www.conventionalcommits.org/)** — `release-please`
parses the subject to derive the version bump and changelog section, so the subject's shape is fixed
machine-readable input (read `git log --oneline` for the house style of the *body*).

- **`type(scope): lower-case summary`.** The type enum and the scope vocabulary are shared with the
  `commit-msg` pre-commit hook and `release-please-config.json`'s `changelog-sections` — read those
  (and `git log` for existing scopes) rather than coining a synonym, and change all three together.
  Scope is optional; omit it for repo-wide changes.
- **Only three things move the version:** `feat` → minor, any other allowed type → patch, `!` (or a
  `BREAKING CHANGE:` footer) → major. Use `!` for an incompatible CLI/behaviour change or a
  profile/capture-schema break, matching `RELEASING.md`.
- **A malformed subject fails silently** — an unrecognized type is dropped from the changelog
  without complaint and a bare subject parses as nothing; the hook rejects both. Pre-v1.15.0 history
  is full of free-form prefixes (`tui:`, `analysis:`, `captures:`, bare `plan`) — map them to a type
  plus that area as the scope; never copy them.
- **Fixing something that never shipped keeps the *original* commit's type** (`ci:` corrects `ci:`),
  so the entry stays out of the release notes. Reserve `fix:` for something a released version did
  wrong.
- **The body explains the *why* and the shape of the change** — wrapped prose and bullets, not a
  file-by-file list (the diff already has that). It isn't parsed, so it's prose for humans.
  **Reference the plan doc** when the change implements one, and keep internal scaffolding (Stage N,
  phase numbers) out of the subject.
- **Write the subject for the changelog reader** — it's the first draft of a release note; a vague
  one turns the maintainer's rewrite into a re-investigation.
- **Commit only when asked, and only intended files.** Follow the git rules in `AGENTS.md`: inspect
  status/diff/log first, stage deliberately (a pre-existing partial index is a smell — reconcile
  it), never commit secrets/PII, don't push/tag/amend unless asked.

### Committing safely when other agents may be working concurrently

The index and the stash are **global shared state**, so one agent's staging races another's and a
broad `git add` can commit files another agent is mid-edit on.

- **Never `git add -A`, `git add .`, or `git commit -a`** — always scope to explicit pathspecs you
  touched.
- **Prefer `git commit -o -- <paths>`** (`--only`): it builds the commit from HEAD's tree plus
  exactly those paths via a temporary index, ignoring whatever else is staged. (It still reads those
  paths from the working tree, so scoping to files you own matters too.)
- **`git stash` is not a fix** — it's more global shared state; scope your commit instead.

## Cutting a release

**Releases are automated by release-please and `RELEASING.md` is authoritative** — follow it, don't
duplicate it. In short: commit subjects drive the bump, a `chore(main): release X.Y.Z` PR stays open
and current, and merging it tags and publishes. You never bump the version, edit `uv.lock`, write a
`## [X.Y.Z]` heading, tag, or `gh release create` by hand; force a version with a `Release-As:`
footer instead. Design record: `plans/2026-08-06-release-please.md`.

**The one thing a human writes is the changelog prose.** Rewrite release-please's raw subjects into
canair's voice **on the release PR's branch immediately before merging** — the branch is
force-pushed whenever the notes change, so earlier edits are lost. Write for the *reader*: no
internal scaffolding (reference the plan doc instead of stage numbers), keep the categorized shape
of prior releases (Highlights → themed sections → Fixes & docs → compare link), expand the generated
subjects from the commit bodies and `git log vPREV..HEAD`, and drop entries for defects that never
shipped.

## Before you finish

```bash
uv run pytest -q                 # green (parallel by default; -n0 when iterating on one file)
uv run ruff check . && uv run ruff format --check .
uv run ty check                  # canlib/ — must be clean
uv run canair <yourcmd> --help   # parser sane
uv run canair validate all       # if you touched ecus/captures/schema
```

Enable the mirrored local gates once per clone (`uv run pre-commit install --install-hooks`) so
ruff/`ty`, the Conventional Commits check, and the `gen_*.py --check` currency checks run before CI
does — CI stays the hard gate. Then confirm the docs and README reflect any user-facing change and
that internal links still resolve.
