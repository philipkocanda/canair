# Split `commands/captures.py` into a `commands/captures/` package

Status: **PLANNED** (2026-08-05). Four commits, each independently green; no
user-facing behavior change in any of them.

## Motivation

`canlib/commands/captures.py` is **1401 lines** — roughly 3× the ~500-line smell
line from the contributing-code skill ("Refactor proactively — no monoliths").

It was already flagged in the architecture audit
(`plans/2026-07-27-architecture-cleanup.md:61`, "946 | `commands/captures.py` |
query + rendering") but never got a C-item like `decode`/`correlate`/
`investigate` did under C5. It has since grown **48%**, because `--delete`,
`--backfill-states`, `--set-state`, `migrate` and `migrate-rx` all landed on top
of it.

Measured spans in today's file:

| Lines | Concern |
|------:|---------|
| 88 | the QUERY/views/scoping help text (`__doc__`, used *only* as the **uds** subparser's `epilog` at `:1036`) |
| 40 | domain-B `cmd_can_logs` |
| 165 | aggregate views: `cmd_summary`, `cmd_sessions` + `_clean`/`_quality_tag` |
| 178 | entry views: `cmd_list`, `cmd_latest`, `_print_entry`, `_print_decoded_preview` |
| 117 | rich byte-diff view: `cmd_diff` + `_render_diff_group` |
| 225 | store maintenance/mutation: `cmd_recover`, `cmd_delete`, `cmd_migrate`, `cmd_migrate_rx`, `orphan_notice` |
| 260 | argparse: group + uds + can + 2 migrate parsers |
| 191 | `run` — ~100 lines of it hand-rolled flag-combination guards |

Two things make this more than a size problem.

**1. The `_captures_` prefix is a package straining to exist.** There are already
**9** flat siblings (`_captures_query`, `_captures_join`, `_captures_step{,_model,
_tui,_render}`, `_captures_backfill{,_render}`, `_captures_set_state`) plus
`captures_merge_driver.py`. Splitting `captures.py` the flat way would make
**~15 files** sharing one prefix in a 60-file directory. `commands/validate/` is
the in-tree precedent for a package-shaped command, and `cli.py::_GROUP_DEFAULTS`
/ `commands/__init__.py::COMMAND_NAMES` are keyed by the command *name string*,
so the module layout is free to change.

**2. Adjacent defects in the same neighborhood** (Boy Scout, and cheapest to fix
while the call sites are already being touched):

- `_resolve_captures_dir` is **duplicated verbatim 3×** — `captures.py:697`,
  `_captures_backfill.py:197`, `_captures_set_state.py:151` — and the
  `None → active().captures_dir` idiom is open-coded in 2 more places
  (`captures.save_session:421`, `_captures_query.load_all_captures:149`).
- **Two live layering inversions**, the same class as the one already fixed under
  C1: library code reaches *up* into a command helper.
  - `canlib/align.py:238` and `canlib/capture_dates.py:385` import
    `commands.captures.load_all_captures` — lazily, specifically to dodge a cycle.
  - `canlib/state_infer.py:30` imports `commands._captures_query._resolve_defs`
    at **module level**.
  - Both targets depend only on `capture_io`/`ecus`/`profile`/`pids` — they are
    pure library code sitting in a command helper.
- `build_query` lives in `captures.py:682` but is imported by `decode.py:541`; it
  belongs in the shared query layer.

## Decisions (confirmed with the user)

- **Package shape** (`commands/captures/`, `_captures_` prefix dropped), not more
  flat siblings. Follows the `commands/validate/` precedent.
- **Include the layering/de-duplication fix**, as a separate final commit.
- **Extract `run`'s flag guards** into a pure, testable `resolve_mode()` table.
  The CLI surface is unchanged — same flags, same messages, same exit codes.
- **Add golden regression cases** for the captures views *before* refactoring, as
  the safety net.

**Invariant for the whole change:** every view's stdout is byte-identical, every
`--json` shape unchanged, all 5 parsers' `--help` unchanged. Consequently nothing
is added to `CHANGELOG.md`, `README.md`, or `docs/reference/cli/captures.md` —
this is an internal refactor. The only docs to touch are the two skills carrying
stale module paths (below).

---

## Commit 1 — `test:` pin the captures views with goldens

**DONE.** Landed as described, with the harness at `tests/_golden.py` (underscore
prefix, matching the existing shared `tests/_fakes.py`, not the `tests/golden.py`
first sketched here).

- **`tests/_golden.py`** — `GOLDEN_DIR`, `FIXTURE_PROFILES_DIR`, `REGEN`,
  `SCOPE_FLAGS`, `norm()`, `run_cli()`, `check_golden(name, got, *, hint)`.
  `test_analysis_golden.py` was rewired onto it (its local `_norm`/`_run`/compare
  block deleted, `-27` lines) and its 27 goldens verified unchanged.
- **`tests/test_captures_golden.py`** — 9 cases, 9 new goldens.
- **`tests/test_captures.py`** — +7 tests closing the two gaps found below
  (`TestQualityTag`, capture-note listing/truncation), 118 → 125.

Cases that landed (one per rendering path): `captures-summary`,
`captures-sessions` (`--last-sessions 3`), `captures-list-truncated`
(`--limit 4`, pins the truncation footer), `captures-list-cross-ecu` (the ECU
column), `captures-list-unmatched` (the unmatched-selector notice),
`captures-latest`, `captures-diff`, `captures-diff-multiframe`
(`BMS 2101 --diff --date 2026-07-21 --rulers` — the only `ioniq-2017` case:
multi-frame hex + ruler + 26 real params), `captures-can` (empty domain-B path).

### Why the rationale below still matters

The existing 118 tests in `tests/test_captures.py` cover the `--json` shapes and
behaviors (`TestCmdSessions`, `TestCmdSummaryJson`, `TestCmdListJson`,
`TestCmdLatestJson`, `TestCmdListLimit`, `TestCmdDiffJson`, `TestCmdDelete`,
`TestCmdBackfillStates`, `TestCmdSetState`) but **not the exact human text** —
spacing, ANSI, footers, the `… +N more not shown` truncation banner. That is
precisely what a mechanical move breaks silently.

**Deliberately NOT added to `test_analysis_golden.py::CASES`.** That module is a
*byte-label* gate — its `TestGoldenHarnessItself::test_goldens_contain_byte_labels`
would reject a `--summary`/`--sessions` golden, and its docstring scopes it to
byte-label emission. Bolting captures views on would corrupt its stated purpose.

**PII constraint (deliberate, not incidental).** `_print_entry` and `cmd_sessions`
render capture `label`/`notes`. The free-text-bearing views are pinned **only**
against the synthetic `tests/fixtures/profiles/single-frame` fixture (24 timed
sessions, two ECUs `ALPHA`/`BETA`, invented labels), never against `ioniq-2017`,
so no real capture labels/notes are copied into `tests/fixtures/golden/`. This is
the same rule the screenshot policy applies. It is **enforced**, not just
documented, by `test_free_text_views_use_a_fixture_profile`, which derives
"renders free text" from each case's argv — so a future case can't quietly opt out.

**Did not touch the existing fixture capture files.** Adding a session to
`single-frame` would shift the volume-dependent `sf-*` goldens in
`test_analysis_golden.py`.

**Coverage boundary → the two gaps that got unit tests instead.** The
`single-frame` fixture has no session `notes`, no `quality`, no `keep_mode` and no
capture-level notes, so a golden over it cannot pin those branches. Both were
untested:

- **`_quality_tag` had zero coverage** — nothing in the suite referenced it or
  `quality`. Now `TestQualityTag`: clean → empty string, `drop`+`stale` summed as
  `drops`, `no_data`/`bus`/`decode`/`other` summed as `errors`, the
  `/ N exchanges` suffix present only when recorded, and the tag surfacing in
  `cmd_sessions`' text.
- `cmd_sessions`' `max_notes` truncation (`… +N more capture-notes`) and the
  100-char note shortening were untested. Now covered.

A richer dedicated fixture profile was considered and rejected: these branches are
cheaper and clearer to pin by calling `cmd_sessions(entries)` with hand-built
entries + `capsys`, as `TestCmdSessions` already does.

## Commit 2 — `refactor:` captures command becomes a package (rename only)

Zero content change; `commands/captures.py` becomes `__init__.py` verbatim.

| From | To |
|---|---|
| `commands/captures.py` | `commands/captures/__init__.py` *(verbatim)* |
| `commands/_captures_query.py` | `commands/captures/query.py` |
| `commands/_captures_join.py` | `commands/captures/join.py` |
| `commands/_captures_step.py` | `commands/captures/step.py` |
| `commands/_captures_step_model.py` | `commands/captures/step_model.py` |
| `commands/_captures_step_render.py` | `commands/captures/step_render.py` |
| `commands/_captures_step_tui.py` | `commands/captures/step_tui.py` |
| `commands/_captures_backfill.py` | `commands/captures/backfill.py` |
| `commands/_captures_backfill_render.py` | `commands/captures/backfill_render.py` |
| `commands/_captures_set_state.py` | `commands/captures/set_state.py` |
| `commands/captures_merge_driver.py` | `commands/captures/merge_driver.py` |

`merge_driver.py` moves in because it is *only* reachable as a captures kind —
registered by `captures.py:960-962`, never listed in `COMMAND_NAMES`.

`__init__.py` re-exports the names outside callers use (`load_all_captures`,
`build_query`, `orphan_notice`) with an `__all__`, exactly as
`validate/__init__.py:56` does — so this commit rewires **no** production call
site outside the package.

Call sites to rewire (imports of the moved *private* modules):

- production: `canlib/state_infer.py:30`
- tests: `test_captures.py` (14, 15, 739, 752, 764, 775, 811, 812, 879, 880),
  `test_captures_step.py` (16, 17, 31, 457, 531, 757, 758, 1097, 1103, 1137),
  `test_capture_io.py` (111, 124), `test_pids_cache.py` (69, 80),
  `test_tui_help.py` (97, 124), `test_captures_merge.py:20`
- **monkeypatch path strings** — `test_ecu_list.py` (259, 279),
  `test_decode_dates.py` (264, 276). These are string literals: they fail at
  runtime, not at import, so they are the easiest thing to miss.

Stale module paths to fix: `.claude/skills/reverse-engineer-signal/SKILL.md:302`,
`.claude/skills/contributing-code/SKILL.md:233`. The `_captures_query.py`
references in `plans/2026-07-28-*.md`, `plans/2026-08-04-*.md` are dated design
records — leave them.

## Commit 3 — `refactor:` split the package by concern

| Module | Contents | ~lines |
|---|---|---:|
| `__init__.py` | `NAME`/`ALIASES`, group `add_parser` wiring the kinds, re-exports + `__all__` | 70 |
| `uds.py` | the 88-line QUERY/views/scoping help as its **own** `__doc__` (→ `epilog`), `_add_uds_parser`, `run` (resolve → load → scope-filter → dispatch) | 370 |
| `mode_select.py` | pure `resolve_mode(args) -> Mode \| str` — the declarative exclusion table replacing `run`'s ~100 lines of guards | 90 |
| `listing.py` | `cmd_list`, `cmd_latest`, `_print_entry`, `_print_decoded_preview` | 215 |
| `sessions.py` | `cmd_summary`, `cmd_sessions`, `_clean`, `_quality_tag`, `_CTRL_RE` | 200 |
| `diff.py` | `cmd_diff`, `_render_diff_group` | 140 |
| `delete.py` | `cmd_delete` — QUERY-driven mutative mode, sibling of `backfill.py`/`set_state.py` | 110 |
| `maint.py` | `cmd_recover`, `orphan_notice`, `cmd_migrate`, `cmd_migrate_rx` + their two parsers (store-file maintenance, no QUERY) | 200 |
| `can.py` | `cmd_can_logs`, `_add_can_parser` | 65 |

Largest module ~370 lines; everything under the smell line.

**Seam rationale.** `delete` sits with `backfill`/`set_state` (QUERY-driven
mutative modes) rather than in `maint` (whole-store file operations that take no
QUERY) — that is the actual boundary, and it is why `cmd_delete` reads like
`cmd_backfill_states` today.

**`mode_select.py`** is named to avoid confusion with `canlib/modes/` (device-mode
handlers). `resolve_mode` returns the selected mode or an error string; `uds.py::run`
becomes `resolve → load → filter → dispatch`. New
`tests/test_captures_mode_select.py` covers the exclusion matrix directly — today
each guard is only reachable through a full CLI invocation:

- `--delete` without a QUERY (refuses to delete everything)
- `--set-state` without a scope filter (refuses to relabel the whole history)
- `--limit < 0`
- a standalone mode combined with a QUERY
- `--latest` × `--diff`/`--step`, `--latest` × the standalone modes
- `--summary` × `--sessions`
- no QUERY and no mode → the ECU hint

Also in this commit, closing the gaps found in commit 1: unit tests for
`_quality_tag` (drops in red, errors in yellow, clean → empty string, exchange
suffix) and for the `max_notes` truncation line.

Moving the 88-line docstring into `uds.py` is a real fix, not cosmetics: that text
documents the *uds* kind's QUERY/views/scoping and is already wired as
`epilog=__doc__` (`captures.py:1036`), yet currently sits on the group module.

## Commit 4 — `refactor:` push the capture data layer down to the library

Fixes the two inversions above, the same way C1 fixed `_decode_plot` →
`canlib/inspect_bytes.py`.

- **New `canlib/capture_store.py`** (peer of `capture_io.py`): `load_all_captures`,
  `_resolve_defs`/`_load_ecu_index`, `_decoded_preview`, and the PID-index cache
  with its `canlib.pids.clear_cache` registration. Dependencies are
  `capture_io`/`ecus`/`profile`/`pids` only — no cycle;
  `capture_types.CaptureEntry` remains the contract.
- **`capture_io.resolve_captures_dir(explicit)`** — one home, deleting the 3
  verbatim `_resolve_captures_dir` copies and folding in the two open-coded
  `None → active().captures_dir` sites. Keeps the lazy in-function `profile`
  import (`capture_io` must not import `profile` at module level).
- **`build_query`** → `captures/query.py`; rewire `decode.py:541` and
  `tests/test_decode_query.py:5`.

Then rewire every consumer to import **down**, and **delete the re-export shims**
(per the C1 precedent — "no dead re-export shims left; call sites were rewired"):
`canlib/align.py:238`, `canlib/capture_dates.py:385`, `canlib/state_infer.py:30`,
`commands/decode.py:110`, `commands/correlate.py:352`, `commands/ecu.py`
(130/150/471), `commands/_live.py:637`, plus the monkeypatch strings in
`test_decode_dates.py` (264/276) and the imports in `test_capture_io.py`.

`orphan_notice` stays exported from the package `__init__` — it is genuinely
captures-command API, consumed by `_live.py`.

New `tests/test_capture_store.py`: the loader's `_session_idx`/`_capture_idx`
locator keys, `rx` → `ecu` short-name resolution including the legacy `ecu` key
(`capture_io.capture_rx`), and the untimed/legacy paths.

---

## Verification (per commit)

```
uv run pytest -q                                    # incl. the new goldens
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair captures --help                       # all 5 parsers
uv run canair captures uds --help
uv run canair captures can --help
uv run canair captures migrate --help
uv run canair captures migrate-rx --help
uv run canair validate all
```

Plus a scripted before/after stdout diff over the bundled profile — the C5 oracle
— covering: `--summary`, `--sessions`, `--sessions --json`, `BMS 2102`,
`BMS 2102 --limit 3` (truncation footer), `--latest`, `IGPM 22BC02 --diff`,
`"BMS:2102,2103" --step --json --limit 5`, `OBC 2101 --delete --dry-run`,
`migrate --dry-run`, `migrate-rx --dry-run`, `can`.

## Deliberately out of scope (flagged, not fixed)

- **The mode-as-flag design.** `run`'s guard maze exists because 7 modes
  (`--summary`/`--sessions`/`--latest`/`--delete`/`--recover`/
  `--backfill-states`/`--set-state`) are *flags* argparse cannot mutually
  exclude. The honest fix is sub-subcommands (`captures uds sessions`,
  `captures uds delete …`) — a user-facing CLI change documented across
  `AGENTS.md` and `docs/`, needing its own plan and a deprecation path.
  `mode_select.py` is the non-breaking middle path: same CLI, guards become one
  declarative, unit-testable table.
- **`captures/step_model.py` (750 lines) and `step_tui.py` (610 lines)** are also
  over the smell line, but they are a separate concern (the Textual multi-PID
  stepper from `plans/2026-08-04-captures-step-textual-multi-pid.md`). They are
  the next candidates after this lands.
- **A richer captures-views fixture profile** — rejected in favor of unit tests
  for the `quality`/`notes`/`keep_mode` branches (see commit 1).
- **`cmd_summary`'s session count disagrees with `--sessions`.** Found while
  choosing golden cases: on the `single-frame` fixture `--summary` reports
  `Sessions: 2` while `--sessions` reports `24 total`. `cmd_summary` counts
  distinct `(file, session_label)` pairs (`captures.py:208,221`), so sessions
  sharing a label collapse; `group_sessions` correctly keys on
  `(file, _session_idx)`. `cmd_summary` is almost certainly the wrong one — but
  fixing it changes user-visible output, which would violate this refactor's
  byte-identical invariant (and now its golden). Deliberately left for a separate
  `fix:` commit, where the golden regen is the *point* rather than a red flag.
