# Time-aligned multi-signal export (`align`) + generalized `--discriminate` axis

Two closely-related analysis affordances, plus one bug to confirm and two
papercuts. All motivated by a single recurring friction: during a real RE session
(deriving LDC 12V output current, decoding HVAC climate/heat power, splitting
PTC vs heat-pump), every drop into a hand-written Python script was for the *same*
missing capability — pulling several **cross-ECU** signals into one time-aligned
table so they can be eyeballed, regime-split, or arithmetic-combined.

Not yet implemented — captured for later. Design settled from the 2026-08-01
session retrospective.

## Why (the gap)

canair's analysis family (`decode` / `correlate` / `hunt` / `investigate`) all
**consume** a nearest-timestamp cross-signal join internally, but none **emit**
the aligned data, and none let the analysis *axis* be an arbitrary signal:

- `decode --compact` — single-PID param table, one ECU.
- `decode --dump-bytes` — single-PID raw-byte matrix, one ECU.
- `correlate` / `hunt` — do the cross-ECU join, but emit only correlation
  *summaries* (r, fit), never the joined rows.
- `decode --discriminate state` — regime-splits by variance, but the axis is
  hardcoded to the vehicle power `state`.

So the moment a question is "show me signals A, B, C from three ECUs side by side
over this window" or "which byte separates compressor-on from compressor-off",
there is no command — you script it. This session hand-rolled nearest-join in
Python **three times** (joining `HVAC_COMPRESSOR_ON` + `HVAC_HEAT_MODE`(B42) +
`BATTERY_POWER` + `HVAC_PTC_HEATER_FLAG` across 220102 / 2201A0 / 2201A2 / 2102 /
2101), and byte-mean-by-regime once.

The reverse-engineer-signal skill already *refers to* an `align`/`xanalysis` tool
as part of the analysis family — but no such command exists. This closes that
gap between the documented intent and the implementation.

## Positioning — how the analysis family fits together

> **Doc note:** this section (both the quadrant map and the "I have… / I want to…"
> table) should be ported into a durable **`docs/concepts/`** page — e.g.
> `docs/concepts/analysis-commands.md`, "which analysis command when" — once
> `align` + the `--discriminate` axis land. There is no single map of the analysis
> family today; this is a genuine doc gap. Update it to drop the "(proposed)"
> markers and add `align`/`--discriminate <signal>` as shipped. Also add a
> one-line pointer from the reverse-engineer-signal skill's tool cheat-sheet.

Every analysis command answers one of two questions — *"what are the values?"* or
*"how do signals relate?"* — at one of two grains — *one PID* or *many signals / a
whole drive*. That gives four quadrants, and it shows exactly why `align` and the
generalized `--discriminate` are the missing pieces:

```
                    READ THE VALUES  ─────────────►  FIND RELATIONSHIPS
                    (what does it say?)              (how do things move together?)

   ┌─────────────────────────────────────┬──────────────────────────────────────┐
 S │  captures       raw payloads / diff  │  decode --corr    param vs 1 reference │
 I │  decode         one PID's params     │  decode --try     test a hypothesis    │
 N │  decode         --dump-bytes matrix  │  decode --plot    sweep interps (TUI)  │
 G │                                      │  hunt             which byte IS ref Y? │
 L │                                      │  investigate      one PID, all angles  │
 E │                                      │  decode --discriminate STATE (only)    │
 ─ ├─────────────────────────────────────┼──────────────────────────────────────┤
 M │                                      │  correlate        rank all pair rels    │
 A │   ★ align ★  (PROPOSED)              │  correlate --against   vs 1 reference   │
 N │   several signals, time-aligned      │  correlate --overlap   what's co-polled │
 Y │   rows — the empty quadrant today    │  correlate --find-mirrors  dup signals  │
   └─────────────────────────────────────┴──────────────────────────────────────┘
     cross-ECU / whole-drive grain ▲
```

The story in one glance: **`align` fills the empty bottom-left-of-the-MANY-row**
(read *several* signals together — cross-ECU, time-aligned), and **generalized
`--discriminate <signal>`** widens the single-PID relate cell from "STATE only" to
"any grouping axis" (compressor on/off, heat mode, …).

### "I have X, I want Y → use Z"

| I have… | I want to… | Command |
|---|---|---|
| an unknown PID, no idea | everything about it in one shot | **`investigate <ECU> <PID>`** |
| a **known reference** signal | which *byte* on a target PID **is** it | **`hunt <ECU> <PID> --against REF`** |
| a known reference signal | *everything* that tracks it across a drive | **`correlate --against REF`** |
| nothing specific | the strongest relationships in a whole drive | **`correlate`** (bare) |
| a candidate expression | to test it without editing YAML | **`decode --try "N=EXPR"`** |
| a defined PID | its value ranges / stats / distribution | **`decode [--stats]`** |
| a grouping signal (state, mode, on/off) | which bytes *separate* the groups | **`decode --discriminate <axis>`** ← STATE today; *any signal* proposed |
| several cross-ECU signals | them **side-by-side, time-aligned** (eyeball / export / regime-split) | **★ `align` ★** (proposed) → today = a Python join |
| raw bytes of one PID | the timestamp×byte matrix | **`decode --dump-bytes`** |
| raw payloads | the hex / byte-diff / sessions index | **`captures`** |
| a finished-ish profile | what bytes are still undecoded | **`coverage`** |
| a session's end | what to work on next | **`research`** |
| a confounder to remove | correlation with a nuisance regressed out | **`… --control REF`** (hunt/correlate) |
| no on-bus anchor | a byte that lands in a physical band | **`hunt --physical`** |

### Where each sits in the RE lifecycle

```
orient ──► discover ──► capture ──► INSPECT ──► HYPOTHESIZE ──► DEFINE ──► VERIFY
research    scan/       query/      captures     investigate     pids       decode
coverage    discover    monitor     decode        hunt           upsert     (--stats,
                        (--save)    ★align★       correlate                  --corr)
                                    decode        decode --try
                                    --dump-bytes  --discriminate
                                                  --plot
            └─────────────── correlate --overlap (what's even co-polled?) ──┘
```

`captures`/`decode` are the "look at it" tools; `investigate`/`hunt`/`correlate`
are the "reason about it" tools; `decode --try` → `pids upsert` →
`decode --stats/--corr` is the define→verify loop.

### Worked example — how the 2026-08-01 session traversed the map

The LDC/climate work walked left-to-right across the quadrants, and only fell out
of the map (into Python) at exactly the two cells this plan fills:

1. `investigate OBC 2101` — orient on unknowns
2. `correlate --overlap` — confirm OBC⟷VCU were co-polled
3. `hunt … --against VCU_AUX_POWER` — "which byte is it?" → none (only the rail droops)
4. `correlate --against BATTERY_POWER --bytes` — found HVAC 2201A2 B42
5. `hunt HVAC 2201A2 --against BATTERY_POWER --control FAN` — proved B42 fan-independent
6. **fell out into Python** for: joining COMPRESSOR_ON + B42 + BATTERY_POWER + PTC
   across 4 DIDs (→ **`align`**), and a byte-mean split by compressor state
   (→ **`--discriminate <signal>`**).

Steps 1–5 are native; step 6 is the empty quadrant + the STATE-only limitation —
which is the whole argument for this plan.

## Part 1 — `canair align` (the primary deliverable)

A read-only command that emits a **wide, time-aligned table** of several signals
(cross-ECU) over a scoped window, using the *same* nearest-join the other
analysis commands already use internally.

```
canair align "HVAC:220102:HVAC_COMPRESSOR_ON VCU:2102:VCU_AUX_POWER BMS:2101:BATTERY_POWER" \
    --state charging --since … --until … --csv
```

- **Selectors:** the shared query mini-language, each token an
  `ECU:PID:PARAM` (or `ECU:PID:EXPR`, like `hunt`/`correlate --against` already
  accept). Whitespace = the set of columns; quote it. Multi-ECU by construction.
- **Join:** nearest-timestamp within `--join-tol` (default matches the other
  tools' 2.5s), anchored on a chosen reference column (default: the first
  selector, or the densest — decide in impl). Reuse the existing join helper the
  correlate/hunt path already calls; **do not** re-implement the grammar or the
  join.
- **Output:** `--csv` (default for piping) / `--json` (list of row objects) /
  a compact aligned text table on a TTY (like `decode --compact`, but multi-PID
  cross-ECU). One row per reference sample; columns = decoded values (typed
  labels rendered where a param is typed — enum/bitmask), `NaN`/blank where a
  column has no sample within tol.
- **Scope:** the shared `add_scope_args` surface (`--since`/`--until`/`--date`/
  `--state`/`--label`/`--first`/`--last`) — identical to `decode`/`correlate`.
- **Keep-mode caveat:** emit the same `keep:unique` warning the other tools do.

**Why it's the highest-value item:** it subsumes essentially all the ad-hoc
scripting. Regime inspection, derived-signal eyeballing, exporting a drive slice
for an external tool — all become one call. It's also the natural structured
substrate the other analysis commands could dogfood.

### Design constraints (contributing-code skill)

- **Composable + scriptable + `--json`** (non-negotiable #0). No TUI-only path.
- **Two data domains:** an `align uds` (diagnostic captures) now; the domain
  spine (`uds`/`can`) leaves room for `align can` (raw broadcast frame series,
  `0xID:rN`) later, symmetric with `correlate`/`hunt`. Ship `uds` first; keep the
  group shape so `can` slots in.
- **Reuse the shared join seam**, don't fork it — if the join lives inside
  `correlate`, lift it into a neutral helper both call (the "second consumer
  forces the abstraction" rule). This is the main refactor the feature motivates.
- **Timestamp format:** emit the *same* timestamp string as `decode --json`
  (`HH:MM:SS.ffffff` + date field), not the `dump-bytes` ISO `T` form — see
  papercut below.

## Part 2 — generalize `--discriminate` to any signal axis

`decode --discriminate state [--bytes|--bits]` ranks params/bytes/bits by
between-vs-within-group variance (F) / Cramér's V across the vehicle power state.
The machinery is axis-agnostic; only the grouping key is hardcoded to `state`.

Generalize the axis to an arbitrary (cross-ECU) signal:

```
canair decode HVAC 2201A2 --discriminate HVAC:220102:HVAC_COMPRESSOR_ON --bytes
```

- Groups samples by the discretized value of the given `ECU:PID:PARAM` (a small
  enum/flag is the natural case: compressor on/off, heat mode 0/1/2), time-aligned
  by the same nearest-join.
- Ranks which bytes/params separate the groups — directly answering "which byte
  is compressor-specific?" without scripting the per-group means (exactly the
  hand-written analysis this session).
- `--discriminate state` stays the default/shorthand (back-compat); the arg
  simply also accepts a signal selector.
- Continuous axes: bin (quantile/fixed) or require the axis to be low-cardinality
  and error otherwise — decide in impl; the primary use is enum/flag axes.

Together, Parts 1 + 2 remove ~all of the session's scripting.

## Part 3 — confirm the `correlate --gate` cross-signal bug

Observed this session: `correlate --against … --gate '[HVAC:220102:HVAC_COMPRESSOR_ON] > 0'`
(and `< 1`) returned **empty output — no rows, no error, exit 0**. Either the gate
doesn't support a *cross-ECU* signal reference, or it silently matched nothing.

- **A gate that yields empty without explanation is a trap** (looks like "no
  correlations" rather than "gate matched nothing / unsupported").
- **To do:** write a repro test; if cross-signal gates are unsupported, make it a
  clear error; if supported, fix the silent-empty (and warn when a gate excludes
  every sample, distinct from "no significant correlations"). Confirm against the
  2026-07-26 17:17 heater-high window where `HVAC_COMPRESSOR_ON` genuinely toggles
  0↔1.

## Part 4 — papercuts (small, independent)

- **Timestamp format inconsistency.** `decode --dump-bytes` emits ISO
  `2026-07-26T17:…`; `decode --json` emits `17:17:09.778` (+ separate `date`).
  Harmonize on one form (prefer the `decode --json` shape) so a `dump-bytes` CSV
  and a `--json` pull join without reformatting. Low risk, pure output.
- **Derived/arithmetic `--against` reference (nice-to-have).** `hunt`/`correlate`
  `--against` take one `ECU:PID:PARAM|EXPR`; a derived cross-signal reference
  (e.g. climate ≈ `BMS:2101:BATTERY_POWER - VCU:2102:VCU_AUX_POWER`) currently
  needs `--against-file` (a pre-built CSV) or `--control` (partial correlation).
  `--control` already covers most of the need (it's how the LDC/climate
  independence was shown), so this is **low priority** — but an
  `--against-expr "A - B"` over cross-signals would be more direct/interpretable.
  If `align` (Part 1) lands, the CSV path becomes trivial anyway (`align … --csv`
  → `--against-file`), which may make this redundant.

## Suggested sequencing

1. **Lift the nearest-join into a neutral shared helper** (prerequisite refactor;
   correlate/hunt become its first callers, `align` the second).
2. **`align uds`** on top of it (`--csv`/`--json`/TTY table, shared scope). ← biggest win
3. **`--discriminate <signal>`** axis generalization (reuses the same join).
4. **`--gate` bug** repro + fix (independent; can land anytime).
5. Papercut: timestamp harmonization.
6. Deferred: `align can`, `--against-expr`.

## Testing

- Device-free unit tests over fixture captures (the existing capture-fixture
  pattern): `align` join correctness (nearest within tol, gap → blank, typed
  labels rendered), multi-ECU column ordering, `--json` shape, scope filtering.
- `--discriminate <signal>`: a fixture where a byte is 0 in group A and non-zero
  in group B ranks top; `state` shorthand still works.
- `--gate`: regression that a cross-signal gate either filters correctly or
  errors — never silently empties.

## Docs (when implemented)

- **Port the Positioning section (quadrant map + "I have… / I want to…" table +
  lifecycle strip) into a new durable `docs/concepts/analysis-commands.md`** —
  "which analysis command when." This is the flagged doc gap; drop the
  "(proposed)" markers and mark `align` / `--discriminate <signal>` as shipped.
- `align` → README command map (one line) + a `docs/` reference page + AGENTS.md
  tool list; note it in the reverse-engineer-signal skill's tool cheat-sheet
  (which already gestures at an `align`/`xanalysis` tool — make it real) and link
  it from the new concepts page.
- `--discriminate <signal>` → update the `decode` flag docs + skill.
- CHANGELOG `[Unreleased]` per user-facing change.

## Status

Proposed, not implemented. Motivated by the 2026-08-01 LDC-current / HVAC
climate-power / PTC-vs-compressor RE session, where the absence of Parts 1–2
forced repeated hand-written nearest-join scripts.

## Implemented (2026-08-01)

All of Parts 1–4 landed:

- **Part 1 — `canair align`** (`canlib/commands/align.py`): multi-selector wide
  table, `--csv`/`--json`/TTY, shared scope, `--join-tol`, keep:unique warning.
  Built on the pre-existing `canlib/align.py` join primitives (`align_many` /
  `load_signal_captures` / `extract_series`) — the "lift the shared join" refactor
  was unnecessary; the neutral helper already existed and correlate/hunt already
  use it. Registered in `COMMAND_NAMES` after `decode`.
- **Part 2 — `--discriminate <axis>`**: `decode --discriminate` now accepts a
  cross-signal `ECU:PID:PARAM` axis as well as `state`, via a new
  `_decode_calc.axis_group_keys` (nearest-join + discretize + cardinality guard)
  threaded as a `group_of` resolver through `print_discriminate` and
  `xanalysis.byte_state_buckets`.
- **Part 3 — `--gate` brackets**: `_correlate_calc._parse_gate` now strips a
  surrounding `[…]`, so the documented `[SIGNAL] OP VALUE` form works (was the
  silent-empty trap).
- **Part 4 — timestamps**: `decode --dump-bytes` CSV → absolute
  `YYYY-MM-DD HH:MM:SS.ffffff`, JSON → time-only + `date`; both drop the ISO `T`,
  matching `align`/`decode --json`.

Tests: `tests/test_align.py` (new, 6), `tests/test_xanalysis.py`
(`TestAxisGroupKeys`, `TestByteStateBucketsGroupOf`, gate-bracket cases),
`tests/test_decode_dump_bytes.py` (timestamp harmonization). Docs: new
`docs/concepts/analysis-commands.md` (the positioning matrix + decision table +
lifecycle, "(proposed)" markers dropped), README command map, AGENTS tool list,
CLI reference (`align`), CHANGELOG. Deferred as planned: `align can`,
`--against-expr`.
