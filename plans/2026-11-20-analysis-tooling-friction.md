# Analysis-tooling friction: what a capture-analysis pass hit, and the fixes

Status: **proposed** (2026-11-20). Written from a signal-hunting pass over
`profiles/hyundai-ioniq-ae-ev/` (recovering the orphaned 2026-11-15 journal, then sweeping
`investigate` / `hunt` / `correlate` / `decode` for new signals). Not user-facing except where
marked; no profile data changes.

The pass did its job — it found the orphaned journal, one mislabelled enum and several dead
bytes — but it spent most of its time fighting the tooling rather than reading the bus. This plan
records that friction, with the exact evidence, so the next pass is faster and its results are
trustworthy.

## Findings (measured 2026-11-20)

| # | What | Evidence | Impact |
|---|---|---|---|
| F1 | `investigate <ECU> <PID>` over the full corpus is **>100 s** with no progress output | `time uv run canair investigate uds BCM 22B003` → `106.59 s`; a whole-profile sweep exceeded the 120 s shell timeout twice | An agent (or person) cannot tell "thinking" from "hung"; one unattended call was cancelled by a restart |
| F2 | The sweep column labelled **`unmapped`** actually counts *unmapped **or unverified-mapped*** bytes | `cli/commands/investigate/sweep.py:183` — `if off not in mapped or not mapped[off][1]: n_unmapped += 1` | The top-ranked PID is the *least-verified*, not the *most-unknown*; `HVAC 2201A0 unmapped=22` ranked high while its real unknowns are 2 bytes |
| F3 | `investigate` per-byte anchors report **`r=±1.000` against a constant or single-state-dominated reference** with no guard | `AAF 2181 B14 r=+1.000 vs BMS_MAIN_RELAY slope 1.0000` (B14 is a constant 0); several `n=1` fits; `stateF=318096.0` repeated verbatim across unrelated bytes | Sends the reader to dead bytes; `hunt` already warns about degenerate references, `investigate` does not |
| F4 | `correlate --find-mirrors` returns an **all-ones cluster** when one state dominates the scope | recent window: `BMS.CHARGING == OBC_EVSE_READY == IGPM.CHARGE_PORT_LOCK == AAF_B14 …` (n≈900), all `1` | Looks like 30 real mirrors; only `BMS B65==B67`/`B66==B68` was real |
| F5 | Multi-frame PIDs with **variable response length** inflate the sweep's `varying` count | `HVAC 2201A0` sweep `varying=25` but a single session varies in 4 bytes | Over-ranks long PDUs; wastes a deep-dive |
| F6 | **`captures uds --recover` has no `--dry-run`, no per-journal selection, and no duplicate detection** | It folded an already-saved 11-12 SKM session (486 captures) as a second copy; I had to delete it via `session_edit.delete_capture` | A routine recovery silently doubles a session |
| F7 | **Two orphaned journals + a stale `.journal/write.lock` survived a clean-ish exit**, and the exit path swallows reconcile failures | `20261112T111020812148-35467.jsonl` (closing `meta` present, session already saved), `20261115T085820440223-42462.jsonl` (no closing meta); `live/monitor/record.py:432` wraps `journal.reconcile()` in `contextlib.suppress(Exception)` | A real session (11-15, 52 min) sat invisible for 5 days; a failed save prints nothing |
| F8 | `pids set-research-result` / `set-research-notes` **do not touch `updated`**, and there is no `set-research-updated` | after a 266-word result edit, the item still read `updated: "2026-10-30"` | The backlog's freshness signal drifts from reality with no tool to fix it |
| F9 | A global `--profile` is **rejected after a nested subcommand** with a bare argparse error | `canair investigate --profile X uds HVAC 2201A0` → `error: unrecognized arguments: 2201A0` | Contradicts "before or after the subcommand"; costs a retry loop |
| F10 | `investigate --counter` needs the whole corpus but carries no hint that scoping kills it (the warning exists) while the *default* sweep happily runs scoped and over-ranks | `investigate uds OBC --counters` (full) fast; default sweep `--since` fast but noisy | Minor; documentation, not code |

## Proposals

### A. `investigate` (F1, F2, F3, F5)

1. **Make the cost visible and bounded.** Print a one-line progress header
   (`Investigating N PID(s), M captures…`) before the join, and a per-PID tick in a sweep. Add a
   `--max-pids` guard (or refuse a >K-PID sweep without `--all`) so an accidental whole-profile
   sweep is a warning, not a 2-minute silence. Consider caching `LoadedPid.decoded` across PIDs
   (the `investigate`/`hunt`/`correlate` passes re-parse the same rows).
2. **Rename the sweep column.** `unmapped` → `open` (or split into `unmap` and `unverif`). One
   word in `sweep.py` and its `--json` key; keeps the ranking honest.
3. **Port `hunt`'s degenerate-reference guards into the anchor ranking.** Drop (or mark
   `unreliable`) an anchor whose reference is constant, bimodal, or has `n < ~30`, and whose
   `|r| == 1.0`. Reuse the `hunt` warnings verbatim so the two commands agree.
4. **Flag variable-length PIDs** (`set-pid-variable-length` already exists) in the sweep line, and
   skip byte offsets that are absent from part of the responses, so a long PDU stops dominating.

### B. Captures: journal and recovery (F6, F7)

5. **`captures uds --recover --dry-run`** listing what each journal would write and its
   `date`/`label`/capture count, plus per-journal selection (`--only <file>`). `--discard` stays.
6. **Duplicate detection in `reconcile_file`.** Before appending a rebuilt session, compare
   `(date, time_start, time_end, captures count, label)` (or a content hash) against the target
   file; skip + report a `would duplicate …` note. This is the direct fix for F6.
7. **Stop swallowing the reconcile failure.** In `_close_segment` / `__exit__`, catch, then
   surface a visible `⚠ session not saved — run canair captures uds --recover` (through the
   same banner channel), instead of `contextlib.suppress(Exception)`. Investigate why the two
   journals survived: the 11-12 one had its closing `meta` (so `update_meta` ran) yet was never
   unlinked — either `reconcile()` raised past its `save_session`/`unlink`, or a second path
   saved the session.
8. **Sweep orphaned journals opportunistically.** A `write_lock`/startup check that prints a
   one-line "N unrecovered journal(s) — run --recover" would have surfaced 11-15 within minutes,
   not five days. Reuse `journal.list_orphans`.

### C. Authoring (F8)

9. **Bump `updated` on every research-field edit** (`set-research-result`/`-notes`/`-status`/
   `-capture-protocol`, `add-research` on update) — the create/update pair is the backlog's only
   freshness signal. Add `pids set-research-updated ECU TARGET --updated DATE` for manual overrides.

### D. CLI arguments (F9)

10. **Accept the global `--profile` (and the other globals) after a nested subcommand**, or fail
    with a hint (`did you mean: canair --profile X investigate uds …`) rather than argparse's
    `unrecognized arguments`. The `investigate uds|can` and `captures uds|can` split is where this
    bites.

## Priorities

| Order | Items | Why |
|---|---|---|
| 1 | F7, F6 (B6-B8) | Silence on a failed save is the only *data-loss* class here |
| 2 | F3, F2, F4 (A3, A2) | Trust: the ranking decides what a person looks at next |
| 3 | F1 (A1), F5 (A4) | Speed and signal-to-noise of the deep-dive |
| 4 | F9, F8 (D10, C9) | Papercuts, cheap |

## Non-goals

- No new analysis command: every item hardens an existing one.
- No schema or profile-data change; F2/F8 are output/field-edit changes only.
- Not the `param` → `signal` identifier rename (`plans/2026-08-06-signal-naming-convention.md`),
  and not the package-layout work (`plans/2026-11-04-package-layout-and-layering.md`).

## Verification

- F1: a whole-profile sweep prints progress and finishes or refuses within a stated bound.
- F2: `investigate uds --since …` on `HVAC 2201A0` no longer outranks a genuinely unknown PID.
- F3: `investigate uds AAF 2181` no longer lists `B14` (constant) as a top anchor.
- F6: recovering the two saved journals now reports `would duplicate` and writes nothing new;
  `captures uds --summary` session count is unchanged.
- F7: `make structure` (import contracts) passes; a simulated reconcile failure prints the
  recover hint.
- F8: `pids set-research-result …` moves `updated` to today.
