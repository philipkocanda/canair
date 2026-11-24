# Tests, refactoring and code style

Test coverage, proactive refactoring (no monoliths), and code style rules.

## Test coverage

- **Every behavioral change ships with tests**; a bug fix ships a regression test that fails before
  and passes after.
- Prefer fast, device-free unit tests — drive modes with a fake terminal implementing the
  `Terminal` surface (`tests/dtc/test_dtc.py`, `tests/live/identity/test_identity.py` are
  templates). Never require a live device.
- Cover the failure paths that matter: NRC responses, `NO DATA`, malformed payloads, declined
  confirmations.
- For anything bus-touching, prove it runs through its handler over the fake terminal (see
  `TestDispatchTransportAgnostic` in `tests/dtc/test_dtc.py`) so every transport stays covered.
- **Analysis tests must not read an unbounded capture range** — `captures/` grows with every
  `--save`, so scope to a frozen date or a fixture profile, or the test gets slower forever and
  eventually breaks
  (`tests/integration/test_analysis_golden.py::test_cases_cannot_drift_as_captures_grow`).
- Cross-cutting policy gets its own test module (e.g. `tests/safety/test_safety.py`).

## Refactor proactively — no monoliths

Don't silently pile onto a design that no longer fits. Fix the defect you walked past, and **speak
up** when a structural change is warranted instead of bolting on more.

- **Split at about 500 lines**, by concern (pure helpers, async device orchestration, TUI,
  record/table data). One concern per file. Only the user adds a `long_modules` exception; the
  criteria are in `docs/development/architecture.md` ("Long files that are fine").
- **A command becomes a package when it needs two modules**: `cli/commands/<name>/`, with no flat
  `_<name>_*.py` sibling. Split and package in one commit. Rationale:
  `plans/2026-08-06-command-packages-and-live-split.md`.
- **Command-private vs shared command-layer infra.** Before moving a `_x.py` into a package, check
  who imports it: one command (+ its test) → package-private, move it in; several commands → a
  shared layer, leave it a flat sibling or push it down to `src/canair/` if nothing about it is
  CLI-specific. A *library* module importing up into `canair.commands` is always a bug.
- **Duplication across transports/commands is a refactor signal.** Extract to one shared home
  rather than maintaining two.
- **A protocol fact has one named home.** Read protocol bytes (service IDs, NRCs, sub-functions,
  canned requests) by name from `canair.uds`, and build actuating requests (session, I/O control,
  routine control, DTC clear) with `src/canair/uds/requests.py`. If the name you need does not
  exist, add it there. `make structure` fails on a stray literal; the patterns and the home of
  each literal are in `scripts/check/protocol_literals.py`.
- **When incremental changes compound complexity, propose a redesign** before adding another
  layer — surfacing the tradeoff is part of the job even if the user declines.
- Prefer plain functions over single-method classes; decompose god objects into focused
  collaborators.

## Code style

- **Hard-wrap every file at 100 columns** (`AGENTS.md` → Formatting has the rule and its narrow
  exceptions; ruff enforces it for Python, prose is on you). Re-wrap a long line you touch.
- Self-documenting code; comments explain *why*, not *what*. Match surrounding style. Keep new
  files single-purpose from the start.
- **Type-hint the critical paths** — UDS/CAN byte handling (`bytes`/`int` offsets, PID/DID IDs),
  the terminal surface and its returned dict shapes, expression eval, capture/schema records, and
  anything crossing the transport boundary. Use a `TypedDict`/dataclass over a bare `dict` where
  the shape matters. CI runs `ty` over `src/canair/`, so hints are **enforced**: prefer narrowing
  a nullable or a precise annotation over `# type: ignore` (reserve those for stdlib
  false-positives, with a comment). Prioritize where hints prevent real mistakes, not as ceremony
  on trivial locals.
