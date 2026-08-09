Status: **DONE** — shipped 2026-08-09. All five stages landed; the bundled Ioniq's
history was back-filled (22 sessions gained a timeline) and re-measured. See
[Results](#results) at the end.

# Vehicle states are session-scoped but vary within a session

`vehicle_states` is stored once per recording session, yet a session is a *time span* during which
the car changes state. Every analysis consumer reads the session's tag list as if it described each
individual capture, so filtering or grouping by state silently mixes in captures recorded in a
different state. On the bundled Ioniq profile, `--state CHARGING` returns **9,162 captures that were
recorded while driving**.

This is a data-model defect, not a bug in any one command: the model cannot express the difference
between two states holding *at once* and two states holding *at different times*.

## The conflation

`canlib/schema/captures_schema.json:31-35` types `vehicle_states` as an array of strings on the
**session**; the `capture` object sets `"additionalProperties": false`
(`canlib/schema/captures_schema.json:55`), so a per-capture state cannot be stored even if something
computed it. On read, `canlib/capture_store.py:132` and `canlib/capture_store.py:144` stamp the
session list onto every flattened row (`"vehicle_states": list(vehicle_states)`; also
`canlib/capture_store.py:188` in `load_pid_captures`), so `CaptureEntry.vehicle_states`
(`canlib/capture_types.py:161`) is a per-row *view* of a per-session *fact*.

That single representation carries two incompatible meanings:

- **Simultaneous** overlap — `READY, PARKED` (a ready car in Park). The set is true of every instant
  in the span. Filtering and grouping are correct.
- **Sequential** overlap — `CHARGING, …, DRIVING` (charged, then drove off). The set is true of *no
  single instant*. Filtering and grouping are wrong.

Only the first kind is documented. `docs/concepts/captures-and-states.md:259-261` explains composite
tagging purely as simultaneity ("*every* predicate that matches contributes, so a parked, ready car
reads as `READY, PARKED`"), and the file never mentions the temporal case. Worse, the docs describe
the tag as belonging to a *capture*: `docs/concepts/captures-and-states.md:249-254` says "The
**state** you tag a capture with … is what powers state-aware analysis like `decode --group-by
state` and `investigate`'s discriminability ranking". The tooling was built against that reading;
the storage never supported it.

The sequential tags are produced deliberately, by the documented span union at
`docs/concepts/captures-and-states.md:232-236`: a `--save` segment is back-filled with "the **union
of every state auto-suggested across that segment's whole span** — not just the state active at the
instant it closed". Implementation `canlib/state_infer.py:146-158`, which evaluates predicates per
pseudo-cycle and then collapses to a union. That union is the right answer to "what did this
recording cover?" and the wrong answer to "what state was this byte read in?", and today only the
first question has a representation.

## Measured impact (profiles/ioniq-2017, 237 sessions / 96,086 captures)

Ground truth reconstructed by evaluating the profile's own predicates **per pseudo-cycle** rather
than per session — `canlib/state_infer.py:61` `_bucket_cycles(captures, cycle_tol)` with
`DEFAULT_CYCLE_TOL_S = 10.0` (`canlib/state_infer.py:40`), then `canlib/state_infer.py:103`
`_decode_cycle(cycle, ecu_index)`, then `canlib/states.py:674` `suggest_states(rules, values,
None)` — and compared against what the session-level filter returns.

| filter | captures returned | actually in that state | contamination | contaminants really were |
|---|---|---|---|---|
| `--state CHARGING` | 61,661 | 49,713 (82.3%) | **10,725 (17.7%)** | DRIVING 9,162 · READY 9,294 · PARKED 1,528 |
| `--state DRIVING` | 23,367 | 18,591 (80.7%) | **4,432 (19.3%)** | PARKED 3,243 · CHARGING 511 |
| `--state PARKED` | 87,399 | 68,166 (79.1%) | **17,957 (20.9%)** | DRIVING 14,000 · CHARGING 2,750 |
| `--state PLUGGED` | 61,943 | 50,638 (82.0%) | **11,148 (18.0%)** | DRIVING 10,298 |
| `--state READY` | 38,073 | 34,051 (94.7%) | 1,911 (5.3%) | PARKED 1,288 |

Of 67 multi-state sessions, **23 (34%) contain at least one tagged pair that never co-occurred in
any single cycle** — 20,816 captures, ~22% of the corpus. Most common purely-sequential pairs:
`DRIVING+PARKED` (12 sessions), `PLUGGED+READY` (5), `CHARGING+PARKED` (3), `CHARGING+DRIVING` (2).

`DRIVING+PARKED` is physically impossible simultaneously and is the single most common case.

### Worst offender

`profiles/ioniq-2017/captures/2026-08-08.json` session #10 — 6,872 captures, 15:47:56–17:19:26,
tagged `READY, ACC2, DRIVING, PARKED, PLUGGED, CHARGING`, label `IGPM OBC AAF IGPM BMS:2101 …`. One
long `monitor --save` across a drive → charge → drive trip. Its 494 pseudo-cycles resolve to **52
contiguous state runs**:

```
15:48:06-15:56:38  n=787   DRIVING, READY
16:02:44-16:27:37  n=2153  DRIVING, READY
16:31:44-16:32:18  n=30    CHARGING, PARKED, PLUGGED
16:35:10-16:43:42  n=307   CHARGING, PARKED, PLUGGED
16:44:50-16:44:58  n=9     DRIVING, READY
16:51:07-17:02:32  n=951   DRIVING, READY
17:12:05-17:19:25  n=603   DRIVING, READY
```

Coarsely: 448 captures charging, 6,282 driving. So `--state CHARGING` on this session yields data
that is **91% driving and 6.5% charging**. Companion session #8 (2,965 captures) is worse: 63
charging, 2,880 driving — **2.1% precision**.

## Two aggravating defects in the same area

Both are independent of the temporal issue and cheap to fix, so this plan folds them in.

1. **The discriminability grouping key is the raw joined string.** `canlib/xanalysis.py:415`:
   `grp = join_states(cap.get("vehicle_states")) or "(no state)"` (accumulation
   `canlib/xanalysis.py:425-431`; param analogue `canlib/commands/decode/analysis.py:64`). So
   `[READY, CHARGING]` becomes a bucket unrelated to `READY` and to `CHARGING`, the `implies:`
   hierarchy is ignored, and neither case nor order is normalised. On this profile **9 declared
   states produce 39 buckets**, of which 8 state *sets* are split across 2 keys each (case or order)
   — 38 keys where 30 are correct.

   The error is bidirectional, which makes it impossible to reason around. Raw key → case/order
   normalised → hierarchy-reduced:

   | PID | byte | groups | raw F | normalised | most-specific |
   |---|---|---|---|---|---|
   | `BCM:22B004` | B9 | 14 → 12 → 11 | **inf** | 147.8 | 148.0 |
   | `BCM:22B004` | B14 | 10 → 8 → 7 | **inf** | 55.6 | 55.8 |
   | `CLU:22B002` | B11 | 11 → 9 → 8 | 204.2 | 55.6 | 69.5 |
   | `HVAC:2201A3` | B59 | 13 → 12 → 11 | 2911.8 | 3237.1 | 3643.6 |
   | `EPS:220101` | B14 | 10 → 8 → 6 | 516.3 | 664.5 | 904.4 |

   The `inf` cases are the dangerous ones. `canlib/xanalysis.py:366-369` returns `float("inf")` when
   `msw == 0`. For `BCM:22B004` B9 every surviving group is internally constant, because the
   case-split pair `'SLEEP, PARKED'` (n=2, value 0) and `'parked, sleep'` (n=2, value 12) sit in
   different buckets. Merge them and the byte demonstrably varies *within* one logical state, and F
   drops to a finite 147.8. Fragmentation manufactured a perfect discriminator from 2 samples. This
   feeds the ranking at `canlib/commands/investigate/uds.py:303`
   (`reports.sort(key=lambda r: (-(abs(r.anchor_r or 0)), -(r.state_f or 0)))`).

   This compounds the temporal defect: session #10's charging *and* driving samples land in one
   bucket, so genuine between-state variance is booked as within-group variance (`msw`,
   `canlib/xanalysis.py:360-364`) — which *penalises* exactly the bytes that best separate charging
   from driving.

   Note `plans/2026-08-04-offline-state-inference-backfill.md:78-79` deferred the casing issue as
   "cosmetic; `parse_states` uppercases on read". That premise is false: nothing normalises on the
   read path into `canlib/xanalysis.py:415`, which sees the raw stored list.

2. **`--state` is a naked substring match, so `ACC` selects `ACC2`.** `canlib/capture_dates.py:270`:
   `if s_needle is not None and s_needle not in join_states(e.get("vehicle_states")).lower():`.
   Declared at `canlib/capture_dates.py:359-364` as one plain string (metavar `SUBSTR`), never
   passed through `canlib/states.py:455` `parse_states`, so it is not comma-splittable, not
   repeatable, and never validated against `allowed_states()` — a typo yields a silent empty
   result. On this profile
   `--state ACC` returns only `ACC2` sessions and not one plain-`ACC` session. `ALL` is not
   special-cased on any capture path (the only `ALL` case is ECU readability,
   `canlib/states.py:541`). The substring behaviour is currently pinned by
   `tests/test_decode_dates.py:294-296`, which asserts `state="PARK"` matches `["ready", "parked"]`.

   Separately, `canair research` uses exact upper-cased token membership
   (`canlib/commands/research.py:135-138`), so one flag name means two different things.

## Why the current design chose a flat set

`plans/2026-08-04-offline-state-inference-backfill.md:66-67` recorded the decision: "**Plural
matching, no `axis:` schema field** — a session is a set of tokens; contradictions are caught via
`definitely_false`, not an exclusivity model." That was right for *inference*; the gap is that
nothing carries the inference's per-cycle resolution forward, and nothing validates the resulting
set for coherence. Two consequences to fix rather than revisit wholesale:

- **There is no exclusivity concept anywhere.** `canlib/schema/states_schema.yaml` and
  `canlib/states.py` have exactly one relational primitive, `implies:` (`canlib/states.py:617`).
  Nothing can say DRIVING and PARKED cannot hold at once.
- **`validate captures` cannot catch an incoherent set.**
  `canlib/commands/validate/captures.py:226-251` only warns when a session has *no* token in the
  declared vocabulary
  (`canlib/commands/validate/captures.py:251`). Combinations are never inspected.

## Proposed fix

The state of a car is **piecewise constant in time**. Model it that way: keep the session union as
the recording-level summary it correctly is, and add the temporal detail as spans. Then resolve a
capture's states at the *read* seam, so no analysis command needs to change.

Compactness makes this cheap: session #10 needs **52 spans for 6,872 captures** (~132× smaller than
a per-row list), because the state genuinely is piecewise constant.

### Stage 1 — token-aware state matching (no schema change)

Replace the substring test at `canlib/capture_dates.py:270` with token-set matching that understands
the vocabulary:

- Parse `--state` through `canlib/states.py:455` `parse_states`, so it accepts a comma-separated
  list and is repeatable; validate tokens against `allowed_states()` and **error on an unknown
  token** instead of silently returning nothing.
- Match on set membership, expanded through `implies:` — `--state ACC` matches an `ACC2` capture
  because `ACC2 implies ACC` (`profiles/ioniq-2017/vehicle_states.yaml:70`), which is the *reason*
  it should match, not the string-prefix accident that makes it match today. `--state ACC2` must
  **not** match a plain-`ACC` capture.
- Special-case `ALL` to select everything, matching its documented meaning.
- Multiple tokens are AND by default (`--state CHARGING,PARKED`), with `--state-any` for OR.

This is a **behaviour break** (`tests/test_decode_dates.py:294-296` must be rewritten:
`state="PARK"` no longer matches `PARKED`). Ship it as `feat(analysis)!` per
`.claude/skills/contributing-code` → Commit messages, and align
`canlib/commands/research.py:135-138` onto the same helper so one flag name has one meaning.

Put the matcher in `canlib/states.py` (it is vocabulary logic, not CLI logic) and have
`canlib/capture_dates.py` call it.

### Stage 2 — canonical grouping key for discriminability

Introduce one helper — `canlib/states.py::state_bucket_key(states, rules) -> str` — that
upper-cases, reduces through `canlib/states.py:635-650` `most_specific_states`, orders via
`canlib/states.py:483-499` `_order_states`, and joins. Use it at `canlib/xanalysis.py:415` and
`canlib/commands/decode/analysis.py:64`.

Also **guard the `msw == 0` path** at `canlib/xanalysis.py:366-369`: returning `inf` from
zero-variance groups of 2 samples is not a finding. Require a minimum per-group sample count (the
existing `len(vals) >= 2` filter at `canlib/xanalysis.py:352` is too weak) and report
`inf` as a distinct "degenerate" verdict rather than sorting it to the top of
`canlib/commands/investigate/uds.py:303`.

While here, drop the unused `field: str` parameter at `canlib/xanalysis.py:375` and fix the stale
docstring at `canlib/capture_store.py:100` (says `state`; the key is `vehicle_states`).

### Stage 3 — `state_spans:` on the session, resolved at the read seam

Add an **optional** `state_spans` key to the session object in
`canlib/schema/captures_schema.json` (additive, so no migration and no version break), with a
matching `TypedDict` in `canlib/capture_types.py` alongside `SessionMeta`
(`canlib/capture_types.py:106`).

A span carries **only its start**, and holds until the next span's start — half-open intervals over
the session's own day, in the same `HH:MM:SS.mmm` string form as a capture's `time` (the date
already lives on the session, so a span needs no date either):

```json
"state_spans": [
  {"at": "15:47:56.192", "states": ["READY", "ACC2", "DRIVING"]},
  {"at": "15:48:06.533", "states": ["READY", "DRIVING"]},
  {"at": "15:56:38.882", "states": ["READY"]},
  {"at": "16:31:33.847", "states": ["PARKED"]},
  {"at": "16:31:44.222", "states": ["CHARGING", "PLUGGED", "PARKED"]},
  {"at": "16:44:32.108", "states": ["READY", "DRIVING"]}
]
```

**`at`-only rather than `from`/`to`, for three reasons.** It cannot express a gap, so there is no
"which span owns this capture?" ambiguity to resolve at read time — a question that *would* arise
for live-recorded spans, where a poll cycle that decodes nothing leaves a hole. It cannot express
an overlap either, so the representation is total and unambiguous by construction. And `from` is a
Python keyword, forcing `canlib/capture_types.py` into functional `TypedDict` syntax for no gain.

Reconstructed for real on session #10 (the timeline above), this is **52 spans / 2,443 bytes** for
6,872 captures — against a 4.2 MB capture file. Across all 148 multi-state sessions in the profile
it is **42 KiB**, 0.2% of the 19 MB `captures/` tree. That is the whole argument against per-capture
storage.

New leaf module `canlib/state_spans.py` — pure, no capture-model or profile imports, so the raw-CAN
domain can reuse it (same discipline as `canlib/counters.py`, per `AGENTS.md` → Analysis):

- `build_spans(cycles) -> list[StateSpan]` — coalesces consecutive equal-state cycles into runs.
- `states_at(spans, when) -> list[str] | None` — `bisect` on the sorted `at` values; `None` before
  the first span, and `[]` for a span that matched nothing (a genuinely different answer from "no
  information", and one the fallback ladder below must not conflate — session #10 has 9 such spans).

Two invariants worth pinning in tests, both verified to hold on the real data: the spans' state
union equals the session's `vehicle_states`, and every timed capture in the session resolves to a
span.

Then change `canlib/capture_store.py:132`, `canlib/capture_store.py:144` and
`canlib/capture_store.py:188` to resolve per row instead of copying the session union, with an
explicit, honest fallback ladder:

1. Session has `state_spans` and the capture has a timestamp → `states_at(...)`. **Exact** — and
   an empty result is exact too ("nothing matched here"), so it must be kept, not treated as a miss.
2. Session has ≤1 state → the union is already exact. Use it.
3. Otherwise (multi-state, no spans, or an untimed capture) → use the union and **mark the row as
   unresolved**, so the imprecision is visible rather than assumed away.

This is the load-bearing choice: **one seam, and every existing consumer becomes correct without
edit** — the Stage 1 filter, `canlib/xanalysis.py:415`, `canlib/commands/decode/analysis.py:64`,
`canair align`, `canair correlate`, `canair hunt`, `canair investigate`.

Case 3 must not be silent. Analysis commands that group or filter by state should print a one-line
provenance note when their scope includes unresolved multi-state sessions — how many captures and
how many sessions — in the spirit of the existing fill reporting (`canlib/fill.py`) and the
`quality` line on `captures uds --sessions`.

### Stage 4 — populate the spans

- **Live.** `canlib/modes/monitor.py:1046` already calls `suggest_states` per poll cycle for the
  status bar. Record each evaluation into the `--save` journal and coalesce on segment close, so a
  newly recorded session ships exact spans. The session union stays as it is today — spans are
  additive, not a replacement. Same for the pipeline path at `canlib/modes/multi.py:237`.
- **History.** `canair captures uds --backfill-state-spans`, modelled on the existing
  `--backfill-states` (`canlib/commands/captures/backfill.py`, whose `_classify` lives at
  `canlib/commands/captures/backfill.py:41-64`): reuse `_bucket_cycles` + `_decode_cycle` +
  `suggest_states`, feed `build_spans`, write via a new `canlib/captures.py` setter beside
  `set_session_states` (`canlib/captures.py:547`). Follow the house mutating-mode pattern — report
  first, `--dry-run`, confirm on a TTY unless `--yes`.

Reconstructing spans from stored payloads is deterministic *given today's definitions*, so record
what produced them (the same provenance instinct as the `version` field on a session), and never
overwrite spans recorded live — the live evaluation saw signals that a stored capture may not.

Keep `canlib/captures_merge.py` aligned with the new key, since sessions merge by union
(`AGENTS.md` → Key Files).

### Stage 5 — make incoherent tagging detectable

- Add an optional `excludes:` list to `canlib/schema/states_schema.yaml`, validated like `implies:`
  (declared states only, symmetric, no self-reference) by `canair validate states`
  (`canlib/commands/validate/other.py:79`) and by the `canlib/states_edit.py` post-write reparse.
  Declare `DRIVING excludes PARKED` and `CHARGING excludes DRIVING` in
  `profiles/ioniq-2017/vehicle_states.yaml`.
- Have `canair validate captures` **warn** on a session whose `vehicle_states` contains a mutually
  exclusive pair *and* has no `state_spans` — that combination is precisely "this session needs a
  span back-fill". Warning, not error: the tag is not wrong, it is under-specified. Wire it beside
  the existing vocabulary check at `canlib/commands/validate/captures.py:226-251`.
- Surface exclusivity in `canair states` output the way `implies:` already shows as a dim
  `specializes:` line.

## Decisions

- **Spans on the session, not states on the capture.** A per-capture list duplicates a
  piecewise-constant fact ~132× (session #10: 52 spans vs 6,872 lists), and adding a key to the
  capture object requires relaxing `"additionalProperties": false`
  (`canlib/schema/captures_schema.json:55`) — the guard that keeps a misfiled payload detectable.
- **Do not split sessions at state boundaries.** A session is a *recording* unit carrying label,
  transport, `quality` and `keep_mode` provenance; splitting destroys the "one monitor run"
  identity, is irreversible, and breaks index-addressed mutation (`canlib/captures.py:547`
  `set_session_states(fpath, session_idx, vehicle_states)`).
- **Do not derive state on the fly at analysis time.** Re-decoding every capture on every analysis
  run reintroduces exactly the parse cost that motivated the YAML→JSON move, and it makes history
  silently reinterpret itself whenever a definition is renamed. Derive once, store, record
  provenance.
- **Keep the session union.** It correctly answers "what did this recording cover?", drives
  `captures uds --sessions`, and is the honest fallback for a span-less session.
- **Degrade loudly.** A span-less multi-state session must be reported, not silently treated as
  exact. Following the existing precedent that every forward fill is reported (`canlib/fill.py`).
- **Fix `--state` semantics via `implies:`, not via string prefixes.** `--state ACC` matching `ACC2`
  is correct *because* `ACC2 implies ACC`; that it currently works by substring accident is why
  `--state ACC2` wrongly matching a future `ACC2X` would go unnoticed.

## Out of scope

- **Reworking discriminability from k-way ANOVA over composite sets to per-state one-vs-rest.**
  Arguably the statistically right question is "does this byte separate CHARGING from not-CHARGING?"
  rather than an F test across composite buckets. That changes what `state_f` *means* and what
  `canlib/commands/investigate/uds.py:303` ranks, so it needs its own plan and its own golden-test
  review.
- **Renaming the `--state` flag** despite it now meaning token-set matching. Left alone; the
  `research` divergence is fixed by aligning behaviour, not by renaming.
- **Normalising the 141 legacy lowercase sessions on disk.** Stage 2's canonical key makes casing
  irrelevant to analysis, so an on-disk rewrite is cosmetic churn against append-only files.
- **The raw-CAN domain.** `canair correlate can`, `hunt can` and `investigate can` expose no
  `--state` at all (`canlib/commands/hunt.py:107`, `canlib/commands/correlate/parser.py:50`,
  `canlib/commands/investigate/parser.py:224`), so there is nothing to fix yet — but
  `canlib/state_spans.py` is deliberately a numpy-free leaf so that domain can adopt it.

## Testing

- **Regression test for the reported defect**, and it must fail before the fix: a fixture session
  tagged `[CHARGING, DRIVING]` with spans, asserting `--state CHARGING` returns only the charging
  captures. Use a frozen fixture profile, never the live `captures/` tree
  (`tests/test_analysis_golden.py::test_cases_cannot_drift_as_captures_grow`).
- `canlib/state_spans.py` unit tests: coalescing, boundary timestamps, a gap between spans, a
  timestamp before the first span, an untimed capture.
- Fallback-ladder tests for all three cases at the `canlib/capture_store.py` seam, including the
  assertion that case 3 is *flagged*.
- `--state` matcher: `ACC` matches `ACC2` via `implies:`, `ACC2` does not match `ACC`, `PARK` no
  longer matches `PARKED`, an unknown token errors, `ALL` selects everything, AND vs `--state-any`.
- `state_bucket_key`: case-insensitive, order-insensitive, hierarchy-reduced; plus a test pinning
  that `BCM:22B004` B9's `inf` becomes finite once the case-split buckets merge.
- `validate states`: `excludes:` rejects an undeclared state, a self-reference, and an asymmetric
  declaration. `validate captures`: warns on an exclusive pair without spans, silent with spans.

## Docs

- `docs/concepts/captures-and-states.md` — the substantive edit. Distinguish simultaneous from
  sequential overlap (the current composite explanation at lines 259-261 covers only the former),
  document `state_spans` and the fallback ladder, and stop calling a session-scoped tag a
  *capture's* state (lines 249-254).
- `docs/reference/cli/` — regenerate (`python3 scripts/gen_cli_reference.py`) for the new
  `--backfill-state-spans` and the `--state`/`--state-any` changes; never hand-edit.
- `README.md` — no change expected; verify rather than assume.
- Add the `excludes:` field to the `vehicle_states.yaml` documentation and note the `--state`
  behaviour break prominently, since it changes results for existing scripts.

## Results

Measured on `profiles/ioniq-2017` (96,086 captures / 237 sessions) after back-filling.

Ground truth is re-derived per pseudo-cycle from the stored payloads. A row counts as wrong only
when its cycle could actually **falsify** the state it claims — a state the cycle merely could not
evaluate is UNKNOWN, not a miss, so this measures provable error rather than sampling gaps.

| State | rows returned | with evidence | provably wrong | rate | before |
|---|---|---|---|---|---|
| CHARGING | 51,533 | 47,635 | 0 | 0.00% | 17.7% |
| DRIVING | 18,981 | 16,611 | 0 | 0.00% | 19.3% |
| PARKED | 71,955 | 66,646 | 0 | 0.00% | 20.9% |
| PLUGGED | 52,629 | 48,656 | 0 | 0.00% | 18.0% |
| READY | 37,397 | 31,317 | 0 | 0.00% | 0.8%* |
| ACC2 | 46,554 | 38,664 | 29 | 0.08% | — |
| SLEEP | 1,897 | 416 | 0 | 0.00% | — |

Overall **29 provably-wrong rows out of 249,945 (0.01%)**, down from ~18–21%. `--state CHARGING`
returns 51,533 rows instead of 61,661, and the 9,162 captures recorded at speed are gone from it.

\* The "before" column was measured with a coarser probe that counted any non-matching cycle as a
miss. That over-counts a state whose predicate signal is polled less often than the sweep's cycle
rate — the same round-robin flapping the latch model exists to solve. The direction and magnitude of
the fix hold (both columns are the same measurement on the same corpus), but the absolute "before"
figures are upper bounds.

### Back-fill verdicts

`captures uds --backfill-state-spans` over the whole history: **timeline 22**, **flat 39**,
**single-state 89**, **no evidence 87**. Only the 22 needed spans; `flat` is a correct outcome —
those sessions' states genuinely held simultaneously, so the union was already exact. 36,423
captures in 126 sessions remain without a timeline and are reported as unresolved rather than
silently trusted.

`validate captures` warnings fell 35 → 18; all 17 exclusive-pair-without-timeline warnings cleared,
and the remainder are pre-existing dropped/stale ISO-TP quality lints.

### Effect on analysis

State bucketing for `IGPM:22BC03` went from **17 buckets to 13** — the removed four were case-split
duplicates (`ready` beside `READY, ACC2`) and `implies:`-redundant variants. Two golden outputs
moved, both toward sharper signal:

- `investigate uds IGPM 22BC03 --bits`: `IGN_STATUS_MIRROR` stateF **2306.8 → 11849.1**. A genuine
  ignition bit separates states far more sharply once each capture is attributed to the state it was
  actually recorded in.
- `decode IGPM 22BC03 --discriminate state --bytes`: the `F=inf` entries are gone. They came from
  case-split buckets holding two identical samples — zero within-group variance manufacturing a
  perfect discriminator out of an artifact.

### Residual: 29 ACC2 rows

All fall in `flat`/`no evidence` sessions where ACC2 could not be placed in time and was carried
into every span. That is the documented carry rule: dropping an unplaceable state would make
captures stop matching a state they used to match, trading a precision problem for a data-loss one.
At 0.08% it is well inside the noise the fix removed.

