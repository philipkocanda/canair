# Split `commands/captures.py` into a `commands/captures/` package

Status: **DONE** (2026-08-05). Four commits, each independently green; no
user-facing behavior change in any of them.

- `4c13771` `test:` pin the captures views with goldens
- `3c9ad23` `refactor:` captures command becomes a package (rename only)
- `944e923` `refactor:` split the captures package by concern
- `7f013c7` `refactor:` push the capture data layer down to the library

Result: the 1401-line module is nine files, largest 441 lines. Suite 4421 → 4488
tests, green both in parallel and serially (`-n0`); `ruff`, `ruff format`, `ty`
clean; all six parsers' `--help`, every view, and every error path verified
unchanged.

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

**DONE (`3c9ad23`).** Landed as planned; git tracked all 11 files as renames, so
the diff is 87/80 lines of import rewiring rather than a 3000-line add/delete.
Intra-package imports became relative (`from .query import …`), matching
`validate/`; cross-package ones stayed absolute.

One defect the rename exposed: `merge_driver.py` used `from .. import capture_io,
captures_merge`, which resolved to `canlib` at the old depth and to
`canlib.commands` one level deeper — an `ImportError` at collection. Fixed to the
absolute `from canlib import …` form its siblings already use, rather than
deepening the relative one. Under `--dist loadscope` that single collection error
cascaded into 87 reported failures.

Zero content change otherwise; `commands/captures.py` became `__init__.py`
verbatim.

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

**DONE (`944e923`).** Actual line counts in the right-hand column.

| Module | Contents | lines |
|---|---|---:|
| `__init__.py` | `NAME`/`ALIASES`, group `add_parser` wiring the kinds, re-exports + `__all__` | 67 |
| `uds.py` | the 88-line QUERY/views/scoping help as its **own** `__doc__` (→ `epilog`), `_add_uds_parser`, `run` (resolve → scope → dispatch) | 441 |
| `mode_select.py` | pure `resolve_mode(args, query) -> Mode \| ModeError` — the declarative exclusion table replacing `run`'s ~100 lines of guards | 145 |
| `listing.py` | `cmd_list`, `cmd_latest`, `_print_entry`, `_print_decoded_preview` | 219 |
| `sessions.py` | `cmd_summary`, `cmd_sessions`, `_clean`, `_quality_tag`, `_CTRL_RE` | 187 |
| `diff.py` | `cmd_diff`, `_render_diff_group` | 138 |
| `delete.py` | `cmd_delete` — QUERY-driven mutative mode, sibling of `backfill.py`/`set_state.py` | 117 |
| `maint.py` | `cmd_recover`, `orphan_notice`, `cmd_migrate`, `cmd_migrate_rx` + their two parsers (store-file maintenance, no QUERY) | 206 |
| `can.py` | `cmd_can_logs`, `_add_can_parser` | 65 |

Largest module 441 lines (the plan estimated 370 — the difference is per-module
docstrings and import blocks); everything under the smell line.

### Deviations from the plan

- **`_resolve_captures_dir` and `build_query` moved in this commit, not commit 4.**
  Both were forced: `_resolve_captures_dir` existed as four verbatim copies and
  splitting would have created a fifth, and leaving `build_query` in `__init__.py`
  while `uds.py` needs it would make the package import itself in a cycle. So
  `capture_io.resolve_captures_dir` (its planned final home) and
  `query.build_query` landed here.
- **The captures group no longer has a module-level `run`.** `add_parser` wires
  `func` on each kind's subparser, and `cli.py` dispatches purely through
  `args.func`, so nothing needed it — but one test called `cap.run(args)` and now
  goes through `args.func(args)`, which is the more faithful path anyway.
- **Tests import each view from its own module**; `__init__.py` re-exports only
  what other *commands* consume.

### A test-isolation defect the goldens caught

Four goldens failed when `test_captures_golden.py` ran after `test_captures.py` in
the same process. Not a split regression — it reproduced on `3c9ad23` in a
worktree, and `--dist loadscope` had been masking it by giving each module its own
worker.

Root cause: `conftest._reset_active_profile` nulls `profile._active`, so the next
`set_active()` takes its "first activation" branch and skips `clear_cache()`
(which only fires when it sees a *different* previous profile). A test therefore
inherited the previous test's memoized ECU definitions **and everything derived
from them** — including the capture views' decode index — so the views decoded the
fixture profile's captures against ioniq-2017 definitions and rendered no
parameters. The fixture now clears the definition caches too, and the suite passes
serially as well as in parallel.

**Seam rationale.** `delete` sits with `backfill`/`set_state` (QUERY-driven
mutative modes) rather than in `maint` (whole-store file operations that take no
QUERY) — that is the actual boundary, and it is why `cmd_delete` reads like
`cmd_backfill_states` today.

**`mode_select.py`** is named to avoid confusion with `canlib/modes/` (device-mode
handlers). `resolve_mode` returns the selected mode or a `ModeError` carrying how
to report the rejection (stream, exit code, whether to append the ECU hint);
`uds.py::run` becomes `resolve → scope → dispatch`.
`tests/test_captures_mode_select.py` covers the exclusion matrix directly (47
cases) — each guard was previously reachable only through a full CLI invocation:

- `--delete` without a QUERY (refuses to delete everything)
- `--set-state` without a scope filter (refuses to relabel the whole history), and
  each scope flag individually satisfying it
- `--limit < 0`
- every standalone mode × (a QUERY / `--latest` / `--diff` / `--step`)
- `--latest` × `--diff`/`--step`
- no QUERY and no mode → the ECU hint on stdout
- `--recover` winning over every other guard
- `ModeError.report()`'s two presentations (stderr error vs stdout usage hint)

Namespaces come from the *real* parser wherever the combination is reachable; the
combinations argparse already rejects (its `standalone` mutually exclusive group)
are built by overriding attributes, and are kept because `run` is also entered
directly from tests.

Also in this commit, closing the gaps found in commit 1: unit tests for
`_quality_tag` (drops in red, errors in yellow, clean → empty string, exchange
suffix) and for the `max_notes` truncation line.

Moving the 88-line docstring into `uds.py` is a real fix, not cosmetics: that text
documents the *uds* kind's QUERY/views/scoping and was already wired as
`epilog=__doc__`, yet sat on the group module — so `captures --help` and
`captures uds --help` described different things from the same string.

## Commit 4 — `refactor:` push the capture data layer down to the library

**DONE (`7f013c7`).** Fixes both inversions, the same way C1 fixed `_decode_plot` →
`canlib/inspect_bytes.py`.

- **New `canlib/capture_store.py`** (peer of `capture_io.py`): `load_all_captures`,
  `resolve_pid_defs`/`load_ecu_index`, `decoded_preview` + the PID-index cache with
  its `canlib.pids.clear_cache` registration. Depends only on
  `capture_io`/`capture_types` at module level (plus lazily `pids`/`ecus`), so
  `align.py` and `capture_dates.py` now import it **top-level** — the lazy
  in-function imports that dodged the cycle are gone.
- **Names that became library API lost the command-private underscore:**
  `_resolve_defs` → `resolve_pid_defs`, `_load_ecu_index` → `load_ecu_index`,
  `_decoded_preview` → `decoded_preview`. `state_infer` had been importing the
  *private* `_resolve_defs` across the layer boundary.
- **`__init__.py` no longer re-exports `load_all_captures`** — `align`,
  `capture_dates`, `decode`, `correlate` and `ecu` import it from the library
  directly, so no shim is left behind (the C1 precedent). `build_query` and
  `orphan_notice` stay exported: they are genuinely captures-command API.
- `capture_io.resolve_captures_dir` and `query.build_query` landed in commit 3 (see
  its deviations), so this commit is purely the `capture_store` move.

`tests/test_capture_store.py` (20 tests): session-metadata denormalisation onto
every row, the `_session_idx`/`_capture_idx` locators, `rx` → ECU short-name
resolution *including* the legacy `ecu` spelling (`capture_io.capture_rx`),
date-ordered file reads, missing-field defaults, a file without `sessions`,
definition resolution (exact match, case-insensitivity, unknown ECU, unknown PID
keeping the TX id), and the decode preview's failure paths.

Two of those tests are a **layering guard** — no `canlib/*.py` may import
`canlib.commands.captures`, and `capture_store` may import nothing from
`commands`. Asserted over the **parsed import graph** (`ast`), not the file text,
so a `:mod:` docstring cross-reference is not mistaken for a dependency; relative
imports are resolved so `from .commands.x` and `from canlib.commands.x` compare
equal.

**One trap worth recording:** `capture_dates` now imports `load_all_captures` at
module scope, so a `monkeypatch` of `canlib.capture_store.load_all_captures` no
longer reaches it — the patch has to target the *importing* module's name
(`canlib.capture_dates.load_all_captures`). Moving a lazy import to module scope
silently invalidates patches aimed at the source module.

---

## Verification (per commit)

```
uv run pytest -q                                    # incl. the new goldens
uv run pytest -q -n0                                # serial: catches order leaks
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run canair captures --help                       # all 6 parsers
uv run canair captures uds --help
uv run canair captures can --help
uv run canair captures migrate --help
uv run canair captures migrate-rx --help
uv run canair captures merge-driver --help
uv run canair validate all
```

Plus a scripted before/after stdout diff over the bundled profile — the C5 oracle
— covering: `--summary`, `--sessions`, `--sessions --json`, `BMS 2102`,
`BMS 2102 --limit 3` (truncation footer), `--latest`, `IGPM 22BC02 --diff`,
`"BMS:2102,2103" --step --json --limit 5`, `OBC 2101 --delete --dry-run`,
`migrate --dry-run`, `migrate-rx --dry-run`, `can` — and every rejection path
(`--delete` bare, `--set-state` bare, `--limit -1`, standalone+QUERY,
`--latest --diff`, bare `captures uds`), each confirmed rc=2 with the same
message. Downstream consumers of the moved loader were exercised too: `decode`,
`align`, `correlate uds`, `ecu`, `ecu BMS pids`.

**Run the serial suite (`-n0`), not just the default parallel one.** The default
`--dist loadscope` puts each module in its own worker, which hides cross-module
state leaks — exactly the class of defect commit 3 had to fix.

## Deliberately out of scope (flagged, not fixed)

- **The mode-as-flag design.** `run`'s guard maze exists because 7 modes
  (`--summary`/`--sessions`/`--latest`/`--delete`/`--recover`/
  `--backfill-states`/`--set-state`) are *flags* argparse cannot mutually
  exclude. The honest fix is sub-subcommands (`captures uds sessions`,
  `captures uds delete …`) — a user-facing CLI change documented across
  `AGENTS.md` and `docs/`, needing its own plan and a deprecation path.
  `mode_select.py` is the non-breaking middle path: same CLI, guards become one
  declarative, unit-testable table.
- **`captures/step_model.py` (748 lines) and `step_tui.py` (611 lines)** are also
  over the smell line, but they are a separate concern (the Textual multi-PID
  stepper from `plans/2026-08-04-captures-step-textual-multi-pid.md`). Planned in
  `plans/2026-08-05-layering-and-module-size-followups.md` (Part B).
- **A richer captures-views fixture profile** — rejected in favor of unit tests
  for the branches it cannot reach. Delivered in `9ad8269`: the golden fixture
  holds only timed hex payloads, so `_print_entry`'s label/response/scan-results/
  notes/truncation branches and `cmd_latest`'s text output are covered by
  mutation-checked unit tests instead.
- **`cmd_summary`'s session count disagrees with `--sessions`.** Found while
  choosing golden cases: on the `single-frame` fixture `--summary` reports
  `Sessions: 2` while `--sessions` reports `24 total`. `cmd_summary` counts
  distinct `(file, session_label)` pairs (now `sessions.py`), so sessions sharing a
  label collapse; `group_sessions` correctly keys on `(file, _session_idx)`.
  `cmd_summary` is almost certainly the wrong one — but fixing it changes
  user-visible output, which would violate this refactor's byte-identical
  invariant (and now its golden). **Fixed separately in `e33152e`** (the bundled
  profile read 204 where `--sessions` listed 223); both views now count through
  `group_sessions`.
- **The remaining library→command imports**, unrelated to captures and predating
  this work: `signals_edit.py` (3×) and `ecus_edit.py` (2×) reach into
  `commands.validate` for schema loading / file validation, and `first_run.py` into
  `commands.profile.create_profile` (its comment says "local import to avoid
  cycles"). Same *class* as the inversion commit 4 fixed, but the call is bigger:
  `validate` holds library-grade validators inside a command. Planned in
  `plans/2026-08-05-layering-and-module-size-followups.md` (Part A).
- **`query.py`** was 489 lines mid-refactor and is **331** once the data layer
  moved to `capture_store.py` — comfortably under the smell line. It still holds
  four loosely related groups (ANSI constants, JSON shaping, the QUERY
  mini-language, keying/dedup/grouping), but nothing outside the command layer
  needs any of them, so it is deliberately left alone; revisit only if it grows.
- **`cmd_latest`'s `ecu_filter` parameter was dead** — every caller passed `None`
  after `--latest`'s selection moved to the QUERY, so the `canonical_ecu_name`
  filter and the `Latest payloads for X` title were unreachable (and therefore
  unpinnable by any golden). Removed in `0743b7c`.
