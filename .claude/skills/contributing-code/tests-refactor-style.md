# Tests, refactoring and code style

Test coverage, proactive refactoring (no monoliths), and code style rules.

## Test coverage

- **Every behavioral change ships with tests**; a bug fix ships a regression test that fails before
  and passes after.
- Prefer fast, device-free unit tests — drive modes with a fake terminal implementing the `Terminal`
  surface (`tests/dtc/test_dtc.py`, `tests/live/identity/test_identity.py` are templates). Never require a
  live device.
- Cover the failure paths that matter: NRC responses, `NO DATA`, malformed payloads, declined
  confirmations.
- For anything bus-touching, prove it runs through its handler over the fake terminal (see
  `TestDispatchTransportAgnostic` in `tests/dtc/test_dtc.py`) so every transport stays covered.
- **Analysis tests must not read an unbounded capture range** — `captures/` grows with every
  `--save`, so scope to a frozen date or a fixture profile, or the test gets slower forever and
  eventually breaks (`tests/integration/test_analysis_golden.py::test_cases_cannot_drift_as_captures_grow`).
- Cross-cutting policy gets its own test module (e.g. `tests/safety/test_safety.py`).

## Refactor proactively — no monoliths

Don't silently pile onto a design that no longer fits. Fix the defect you walked past, and **speak
up** when a structural change is warranted instead of bolting on more.

- **File size is a smell.** Approaching ~500 lines — and *well* before 1000 — split by concern (pure
  helpers, async device orchestration, TUI, record/table data). `live/identity/read.py` splitting out
  `decode.py`/`records.py` is the pattern.
- **One kind of item in bulk is the exception.** A lookup table, a definition list, a generated
  file, or an allow-/block-list a reviewer must read whole may pass 500 lines, because a split
  would scatter one list and separate no concern. Ask yourself: can I name two concerns in this
  file that change for different reasons? If yes, split. Logic that has grown, a grab bag of
  helpers, and "it is one command" never qualify. A real exception goes in `long_modules` in
  `tests/contracts/baseline.json` with a ceiling and a one-line reason. **Only the user adds an
  entry or raises its ceiling**: describe the file and ask. The full criteria are in
  `docs/development/architecture.md` ("Long files that are fine").
- **A command is one module until it needs two — then it becomes a package.** Create
  `cli/commands/<name>/`; do **not** add a flat `_<name>_*.py` sibling (retired for new work).
  `cli/commands/captures/` and `cli/commands/decode/` are the precedents, and the command registries are
  keyed by the name *string*, so module layout is free to change. Two corollaries: packaging
  discharges nothing — the ~500-line rule still binds every member; and **split and package in one
  commit**, since splitting now and packaging "later" books the rename twice. No command has a flat
  sibling now: the `_x.py` files left in `cli/commands/` are shared by several commands. Rationale:
  `plans/2026-08-06-command-packages-and-live-split.md`.
- **Command-private vs shared command-layer infra.** Before moving a `_x.py` into a package, check
  who imports it: one command (+ its test) → package-private, move it in; several commands → a
  shared layer, leave it a flat sibling or push it down to `src/canair/` if nothing about it is
  CLI-specific. A *library* module importing up into `canair.commands` is always a bug.
- **Duplication across transports/commands is a refactor signal.** The command blocklist was once
  duplicated *and divergent* between terminals; it now lives once in
  `src/canair/safety/blocklist.py` and both await it. Extract to one shared home rather than
  maintaining two.
- **A protocol fact has one named home.** A service ID, response marker, NRC, sub-function,
  positive-response offset or canned request (`3E00`) is read by name from `src/canair/uds/`
  (`services.py`, `nrc.py`, `subfunctions.py`), never re-declared as a module constant or
  compared as a bare literal. `0x7F` is both the negative-response SID and an NRC, so a bare
  literal hides which one the code means, and grep cannot find its users. If the name you need
  does not exist, add it there. Literals stay where they *are* the data or the mask: the
  registry, NRC and sub-function tables, bit masks (`sub & 0x7F`), byte ranges, and test
  payloads. Build a session, I/O control, routine control or DTC-clear request with
  `src/canair/uds/requests.py`, so a field that actuates reads as its name; read requests
  (`f"22{did:04X}"`) stay hex templates. `make structure` fails on any literal it finds
  outside `canair.uds` (`scripts/check/protocol_literals.py` names the patterns; the history
  is `plans/2026-11-05-named-protocol-constants.md`).
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
  shape matters. CI runs `ty` over `src/canair/`, so hints are **enforced**: prefer narrowing a nullable
  or a precise annotation over `# type: ignore` (reserve those for stdlib false-positives, with a
  comment). Prioritize where hints prevent real mistakes, not as ceremony on trivial locals.
