# Analysis-tooling follow-ups from the monotonic-counter work

Status: **IMPLEMENTED** (A–F, H) / **DEFERRED** (G) — 2026-08-07.

- **A** (counters `--notation` / scoped-run warning / keep-mode banner) — done.
- **B** (untimed-capture ratchet: `validate captures --max-untimed N`, CI gates
  each bundled profile at its baseline) — done.
- **C** (`_remove_field_line` now skips block-scalar/nested bodies) — done.
- **D** (`hunt`'s thin-join sentence names a concrete `--min-n`; `correlate`
  ranked + `--against` empty paths now name the strongest sub-threshold `|r|`).
  The `mirrors.py` near-miss retention is left as its own decision (it costs the
  `_verify` early bail) — not done here.
- **E** (corpus-wide `investigate`: optional positionals + QUERY, ranked summary
  sweep, `--top`; `investigate --counters` sweeps the whole car) — done.
- **F** (bit-flip boundary gradient wired as a **tie-break** in
  `cluster_counters` — `counters.boundary_gradient`) — done.
- **G** (a "tracks a known signal by a constant Δ-ratio" primitive) — **deferred**
  to its own design pass: it is large-ish and the verb-vs-mirror-matcher-flag
  question is unresolved (see Part G below).
- **H** (collapse the untimed-payload warning stream to a footer count;
  `--show-untimed` restores per-file detail) — done.

Nine independent items. A–C are corrections of
things this work got wrong or walked past and should land regardless; D–H are
genuine improvements the work surfaced; the process notes at the end are not
actionable code.

All of it was found while building `canair investigate --counters`
(`plans/2026-08-07-monotonic-counter-detection.md`, commits `c8c8a93` /
`7102314` / `893489e`) and while hand-searching the corpus for further copies of
the odometer. Every claim below was verified against the tree at `893489e`;
file:line references and verbatim command output are included so nothing has to
be re-derived.

**Honest accounting:** Part A is three defects **introduced** by the counters
commit. Part B is a claim made to the user that turned out to be **false**, on
which a decision was taken. The rest is pre-existing.

---

## Part A — three defects in the shipped `--counters` view

All three are small and contained in `canlib/commands/investigate/counters.py`.

### A1 — `--notation` is accepted and silently ignored

`investigate uds` registers the shared notation flag
(`canlib/commands/investigate/parser.py:203`, `add_notation_arg(parser)`), so
`--counters --notation torque` parses fine — but `counters.py` never calls
`resolve_notation`/`relabel_signal`. Its only notation import is `ByteRef`
(`counters.py:22-30`). Demonstrated:

```
investigate CLU 22B002 --notation torque              → F        (relabelled ✓)
investigate CLU 22B002 --counters --notation torque    → i9-i11   (ignored ✗)
```

Every render path in the sibling `render.py` does honour it —
`print_report` (`:65`, `:101`, `:116`), `print_events` (`:349`, `:364`),
`print_dwell` (`:479`, `:487`).

**Scope the fix precisely.** Only the `_label` column is wrong. The
**expression** must stay WiCAN: AGENTS.md's notation policy is *"Display only —
named signals are untouched, and `--json`/`--promote` always emit the canonical
WiCAN expression"*, so `[B12:B14]` beside an ISO-TP label is correct by design,
not a bug. Fix `_label` (`counters.py:93-100`) to render through
`ByteRef.render(notation, …)`, which already exists and is used by
`canlib/commands/hunt.py:678`. Leave `_expression` (`counters.py:84-90`) alone.

### A2 — a scoped run that returns results is unguarded, and can print wrong advice

`--counters` wants the whole history (the calendar span *is* the evidence), and
the help text says so (`parser.py:77-81`) — but nothing enforces or warns it at
runtime. `uds.py:70-72` narrows the corpus *before* the `--counters` branch at
`uds.py:112-113`, so `run_counters` receives an already-filtered `LoadedPid` and
cannot tell that history was withheld. `counters.py` never references
`args.state`, `args.label`, `args.since`, `args.until` or `args.date` anywhere
in its 351 lines.

Two consequences, the second worse than the first:

1. The header (`counters.py:252-256`) prints capture count, day count and span
   with no indication the corpus was filtered, so a truncated horizon reads as
   the whole one and the `bits` score — the entire basis of the ranking — is
   silently understated.
2. The empty-path `--min-bits N` recommendation (`counters.py:257-271`) is
   computed from the **scoped subset**, so it can advise a threshold that is
   wrong for the full history.

The existing "widen the scope" line (`counters.py:272-280`) is boilerplate: it
fires whether or not scope flags were passed, and only on the branch where
*nothing at all* was found. The dangerous case —
`investigate BMS 2101 --counters --state driving` returning plausible results —
gets no warning.

**Fix:** detect active scope flags in `run_counters` and print a banner
(stderr, and a `scoped: true` key in `--json`) whenever any is set; suppress or
caveat the `--min-bits` recommendation when the corpus was filtered.

### A3 — the keep-mode banner is skipped

All four `render.py` paths call `print_keep_banner` (`render.py:72, 289, 345,
469`); `counters.py` never does, because `uds.py:112-113` short-circuits before
`print_report` is reached. A `keep:changes` scope is therefore unflagged in the
counters view. It matters less here than for rate analysis (monotonicity
survives run-length dedup) but the *step counts* and `n` do change, so the
caveat belongs.

**Oracle:** extend `tests/test_investigate_counters.py` — a `--notation torque`
assertion on the label, a scoped-run banner assertion, and a `keep:changes`
fixture asserting the banner. All device-free.

**Risk:** low. No detection logic changes.

---

## Part B — `--strict` cannot serve as the CI gate (a false claim, already acted on)

While choosing how to handle untimed captures, the user was told the plan was
*"enforce at write, leave history, keep `--strict` as the CI gate"*, and picked
that option on that basis. The last clause is **not true today**.

- CI runs plain `validate all` per bundled profile
  (`.github/workflows/ci.yml:48-54`) — never `--strict`.
- `--strict` **cannot** be enabled, because it fails on the grandfathered rows:

```
$ canair --profile ioniq-2017 validate captures --strict
284 total errors across 22 files      → exit 1
```

Grandfathering and a zero-tolerance gate are in direct tension: the gate only
works once the legacy rows are gone, which the user explicitly (and sensibly)
declined. So the enforcement added in `893489e` is real at the **write** path,
but the CI backstop behind it is nominal.

**Fix — a ratchet, not a cutoff.** Fail when the untimed-payload count *exceeds
a recorded baseline* rather than requiring zero. That makes the gate real in CI
without touching history, without a date cutoff, and without an allowlist that
needs maintaining. Sketch:

- record the baseline per profile (a small committed file, or a
  `validate captures --max-untimed N` flag CI passes);
- CI runs `validate all` plus the ratchet;
- the count only ever goes down (writes are enforced), so the baseline is
  updated downward opportunistically, never upward.

Prefer the explicit flag over a committed baseline file if only one profile is
ever gated — fewer moving parts, and the number lives next to the CI invocation
that depends on it.

**Oracle:** a test that a synthetic untimed payload capture pushes the count
over the baseline and exits non-zero; and that the current bundled profile sits
*at* its baseline.

**Risk:** low, but it changes CI's pass/fail surface — land it with the baseline
already correct so CI does not go red on an unrelated PR.

---

## Part C — `_remove_field_line` is a trap

`canlib/pids_edit/_text.py::_remove_field_line` removes only a field's **header
line**. On a block scalar it orphans the indented body and produces invalid
YAML. Nothing in its name, signature or docstring says so.

Current callers are safe by luck of what they target — `status` and
`variable_length`, both single-line scalars
(`canlib/pids_edit/params.py:605`, `:650`) — so there is **no live bug**. But
`set_pid_notes` walked straight into it during this work: the first
implementation used it to clear a note and produced a file that failed the YAML
re-parse. (`_safe_write`'s guard caught it and restored the original, which is
the safety net working as designed — see
`plans/2026-08-07-monotonic-counter-detection.md`.) The working fix was
`_replace_field_in_block_at(block, field, [], indent=…)`, which already knows
how to skip block-scalar continuation.

**Fix, cheapest first:**

1. Docstring warning + a note pointing at the empty-replacement idiom for
   block-capable fields. (XS)
2. Better: make it detect a block-scalar/nested body and delegate, so the
   trap cannot be stepped on at all. It is the more obvious API — a function
   called `_remove_field_line` removing *a field* is what a caller will assume.

**Oracle:** a test that removing a folded `>-` field leaves valid YAML.

**Risk:** trivial for (1); low for (2), and `tests/test_pids_edit*.py`
(45 + 10 tests) covers the callers.

---

## Part D — generalize the data-derived threshold hint

The one genuinely good UX idea to come out of this work is in `--counters`: when
a threshold excludes everything, **name the value that would have worked**,
computed from the data. Sweeping at a floor and filtering in the caller costs
nothing, because the threshold only ever filters (`counters.py:201-213`,
`257-271`):

```
Nothing above 4 bits. Best below it: [B12:B14] at 2.0 bits (70047 → 73048).
Re-run with --min-bits 2 to see it — but treat it as a lead, not a finding:
2 clean rise(s) happen by chance about 1 in 4 times.
```

Nothing else in the family does this. Verified empty paths:

| Command | Empty message | Data-derived? |
|---|---|---|
| `hunt` | `No byte on CLU 22B002 correlates with … in scope.` (`hunt.py:570-572`) | ✗ |
| `correlate` (ranked) | `No cross-signal correlations with \|r\| ≥ 0.999 (n ≥ 5).` (`correlate/uds.py:357-359`) | ✗ echoes input |
| `correlate --against` | *nothing* — header then a blank line (`correlate/uds.py:320-330`) | ✗ |
| `investigate` | `no varying bytes to report` (`render.py:75-79`) | ✗ |
| `decode --find-mirrors` | bare `none` under a `≥100%` header (`decode/analysis.py:161-168`) | ✗ |
| `investigate --counters` | names the window, its score and the retry flag | ✓ |

Two notes that change the work estimate:

- **`hunt` has no `--min-r`** (only `--min-n`, `hunt.py:84-86`); ranking is by
  `|r|` with no floor. It *does* already have the family's only data-derived
  diagnostic — the thin-reference-join warning (`align.py:839-845`) names the
  achieved `n=5` — but says *"lower --min-n"* without a number, and only fires
  for the join cause, never for a weak-correlation cause. Finishing that
  sentence is the cheapest win in this part.
- **`find-mirrors` needs a deeper fix, and it has a real cost.**
  `canlib/mirrors.py::_best_relation` (`:284-289`) `continue`s past every
  sub-threshold candidate and returns `None`, so the achieved agreement fraction
  never reaches the CLI — nothing to report even if the renderer wanted to. Worse,
  the fraction is not merely discarded but **never computed**: `_verify`
  (`:170-192`) early-exits the moment the disagreement budget is blown, and that
  exit *"is what makes an O(signals²) sweep affordable: a pair that is not a
  mirror is dropped after `budget + 1` rows instead of being fully scanned"*.
  Retaining a near-miss therefore means giving up the early bail, or relaxing it
  conditionally (e.g. a second pass over only the best few rejected pairs).
  **Do not fold this in with the cheap renderer-level hints** — it is a
  performance trade-off in the matcher and deserves its own decision.

**Suggested shape:** a tiny shared helper — "given the rejected candidates and
the flag name, render the retry hint" — so the phrasing stays consistent and
each command only has to stop throwing its near-misses away.

**Oracle:** per command, a test that forces an empty result and asserts the
suggested threshold appears and is achievable.

**Risk:** low per command, but it touches five call sites; land incrementally,
`hunt`'s sentence first.

---

## Part E — corpus-wide `investigate`

`investigate uds` is the only analysis command of its family that **mandates** a
single target (`parser.py:108-109`): two required positionals, no `nargs`, and
no QUERY mini-language.

```
$ canair investigate CLU
canair investigate uds: error: the following arguments are required: pid

$ canair investigate "CLU:22B002,22B003"
canair investigate uds: error: the following arguments are required: pid
```

The second is telling — the multi-PID QUERY form used by `decode`, `captures`,
`read` and `monitor` is swallowed as a bare `ecu` token. Contrast:

- `coverage` — `nargs="?"` on both (`coverage.py:250,253`); a bare invocation
  audits the whole profile and ECU/PID are *filters*.
- `research` — no positionals at all; a bare invocation reports the whole
  backlog.

`--counters` is exactly the case that wants a sweep: *"find every counter in
this car"* is the natural question, and answering it during this work required a
hand-written Python loop over `discover_signal_specs`. That loop is also how the
two new signals were found, so this is not hypothetical.

**Fix:** make both positionals optional (the `coverage` precedent), so
`investigate BMS --counters` sweeps an ECU and a bare `investigate --counters`
sweeps the profile. Cap and rank the output; the per-PID view is already compact
enough. Ideally accept the shared QUERY mini-language rather than a bare ECU,
per the contributing-code guidance that new selection surfaces should reuse it —
but note the default (non-counters) per-byte view is verbose, so a corpus-wide
default view needs a summary mode, not N full reports.

**Oracle:** a fixture profile with two PIDs, asserting a bare sweep reports
both and that the single-target form is unchanged.

**Risk:** medium — the default view's output volume is the real design question,
not the argparse change.

---

## Part F — wire the dead `bit_flip` gradient into the counter sweep

`canlib/triage.py::bit_flip_rates` is computed for every byte of every
`investigate` run and **never read**. Confirmed: zero `.bit_flip` attribute
accesses in `canlib/` or `tests/`. The only production consumer of
`triage_byte` copies three fields out (`investigate/uds.py:196`, `:212-214`:
`kind`, `entropy`, `lag1`). `classify()` does its own work from
`byte_entropy`/`mean_abs_step`/`flip_rate` and never consults the per-bit rates,
despite `triage.py:110-112` describing exactly the discrimination they enable.
It is `O(8 × n)` per byte per run, wasted — tested, documented, exported, dead.

**Do not just delete it.** Its docstring names precisely the signal the counter
sweep works hardest for:

> a multi-byte little counter shows a monotonic gradient (LSB flips often, high
> bits rarely)

That gradient locates a multi-byte counter's **byte boundary**, which is the
single hardest thing `find_counters` does — it currently brute-forces every
window × endianness and prunes afterwards with `msb_jump`/`step_ratio`. Using
the gradient as a *pre-filter* (or as a tie-break in `cluster_counters`, where
the canonical-window choice is the weakest link) would be both cheaper and more
principled than the current search.

**Decision required:** wire it up (Part F) or delete the field and keep the free
function for its tests. Wiring it is more valuable; deleting is honest if nobody
will.

**Oracle:** `tests/test_counters.py` already has synthetic multi-byte
accumulators with known boundaries — a gradient-based boundary guess can be
asserted directly against them.

**Risk:** medium. It changes detection behaviour, so the existing 30 counter
tests are the guard; keep the brute-force path as the fallback rather than
replacing it outright.

---

## Part G — a "tracks a known signal" command (the Δ-ratio test)

Searching for further copies of the odometer needed a primitive canair does not
have, and correlation could not substitute for it.

**Why the existing tools fail.** An odometer is near-constant within any
session, so Pearson is degenerate; `--find-mirrors --allow-offset` needs
co-polled samples with real variance, which the six sparse CLU reads do not
provide. Three ad-hoc scripts were written instead, and the one that worked is
worth keeping:

> **Is Δcandidate / Δreference constant across intervals?**

A distance counter in *any* linear unit gives a constant ratio; a time or energy
counter does not. It needs no scaling table and no unit guesses. Measured on the
bundled profile, with CLU's verified `ODOMETER` as the reference: BMS's
cumulative registers scored cv 0.27–0.67 and BCM's event counter 0.08/km — all
correctly rejected as *not distance*.

**The strongest argument for making this a command rather than a script:** the
first run reported `cv=0.00, ratio exactly 256.0000/km` and was nearly published
as proof. It was **circular** — the reference set had been built from both CLU
*and* VCU, so VCU was being compared against itself. A command enforces
self-exclusion structurally; a script relies on the analyst remembering. Against
an independent reference the same window has exactly **one** usable interval
(ratio 247.44 vs the theoretical 256, the gap explained by CLU's integer-km
truncation over a 9 km span) — which is why `VCU_ODOMETER` is correctly still
`verified: false`.

**Shape:** `canair correlate --tracks ECU:PID:PARAM` (or a flag on `hunt`),
reporting per-candidate the median ratio, its coefficient of variation, the
interval count, and a verdict — with the reference's own PID excluded from the
candidate set by construction, and a loud refusal when fewer than ~3 independent
intervals exist.

**Risk:** large-ish, and it overlaps `--find-mirrors --allow-offset`
conceptually. Worth a design pass on whether it is a new flag on the mirror
matcher (which already models "same quantity at a different offset/scale") or a
separate verb. The mirror matcher is the more natural home; what it lacks is the
*interval* framing that makes a near-constant signal tractable.

---

## Part H — `validate captures` warning noise

The bundled profile emits 290 warnings, of which **284 are the grandfathered
untimed rows**. The 6 that need attention — echo mismatches, degraded-transport
sessions — are drowned.

**Fix:** collapse a repeated warning class to a single counted line (the footer
already does this for the untimed count specifically, `validate/captures.py:189-193`,
so the pattern exists) and keep per-row detail behind a flag. Interacts with
Part B: once the ratchet exists, the untimed class is a tracked number rather
than a warning stream.

**Risk:** trivial, but it is user-facing output — keep `--strict`'s per-row
errors unchanged, since there the detail is the point.

---

## Process notes (not actionable)

Both cost real time this session and are inherent to concurrent agents in one
working tree.

- **Another session committed this work's profile edits** as
  `b37ed28 feat(profiles): Update ioniq-2017 profile (VCU and BCM)` before they
  could be committed here — the exact race the contributing-code skill warns
  about. Benign (content intact), but it meant `bcm.yaml`'s remaining diff was
  only the note re-applied through the new editor, and the regenerated
  `docs/profiles/*.md` stats had to ride in an unrelated commit because
  `b37ed28` could not be amended.
- **The pre-commit hook stashes tracked-but-unstaged changes and spares
  untracked files**, so a commit whose staged code depends on an unstaged change
  fails `ty` against a tree that never existed. Here `canlib/counters.py`
  (untracked) imported `canlib.stats.linear_fit` (unstaged), and the first
  commit ordering had to be reversed. **Rule: order commits so no commit depends
  on a later one**, and add new files with a scoped `git add` before
  `git commit -o -- …` (`-o` cannot introduce untracked paths).

---

## Ordering

1. **A1–A3** — defects in shipped code; one commit, `fix(investigate):`.
2. **B** — the gate that does not gate; the claim was made to the user, so it
   should not sit open.
3. **C** — XS, and it is the trap that already bit once.
4. **D** — start with `hunt`'s unfinished sentence (`align.py:839-845`) and the
   renderer-level hints, which are nearly free. Treat `mirrors.py` retaining
   near-misses as a **separate** decision: it costs the `_verify` early bail that
   makes the O(signals²) sweep affordable.
5. **F** — decide wire-up vs delete before the dead field is copied into
   another consumer.
6. **E**, then **G** — both need a design call first (output volume; new verb vs
   mirror-matcher flag).
7. **H** — cosmetic; fold into B's cleanup if convenient.

## Out of scope

- **A `can` (raw broadcast frame) counterpart to `--counters`.** Tracked in
  `plans/2026-08-07-monotonic-counter-detection.md`; `canlib/counters.py` is
  byte-space-agnostic specifically so it stays a thin addition.
- **Renaming triage's `counter` class.** It now means something different from
  `--counters` (a fast rolling byte vs a monotonic accumulator) and the
  collision is genuinely confusing, but it is a user-visible label in
  `investigate`'s output and `--json`, so it needs its own call.
- **Backfilling or purging the 284 untimed rows.** Explicitly declined; see
  `plans/2026-08-07-monotonic-counter-detection.md` → "Untimed captures".
- **Splitting the `_replace_field_in_block_at` whitespace fix out of
  `7102314`** so it lands as `fix(pids):` in the changelog's Fixed section. A
  history rewrite for a changelog line; only worth it if the release notes are
  being curated by hand anyway.
