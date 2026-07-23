# Decode Analysis Ergonomics & New Correlation Types — Implementation Plan

Second-generation improvements to the `decode`/`correlate`/`hunt` analysis
suite, driven by two hands-on RE sessions (2026-07-23) that used the
cross-signal tooling in anger. The first plan
(`2026-07-23-cross-signal-analysis.md`) built the alignment foundation and the
core verbs; this plan closes the capability gaps and friction points that
surfaced while *using* them to re-analyze uncertain PIDs.

Approach: **fill the two glaring capability gaps first (Tranche 1), add the
correlation *types* that find relationships Pearson-on-raw-bytes misses
(Tranche 2), then the time-savers and output hygiene (Tranche 3).** Each tranche
lands with unit tests + `pytest`/`ruff` green. All work is **read-only analysis
over existing captures** — no device, no transport, no schema-of-record change.

## Motivation (what the sessions exposed)

- Confirmed **AAF speed / VCU speed / MCU torque** relationships and *decoded a
  new signal* (EPS 220101 B15 = speed-sensitive MDPS assist band) — but every
  win either fit an existing verb awkwardly or needed a manual workaround.
- The single most productive move (correlating motor torque against
  **acceleration** and **angular acceleration**, `d(RPM)/dt`) could only be run
  against **defined params** via `decode --corr --corr-transform delta`. I could
  not run it against the raw-byte sweep, so I could only *corroborate* a known
  torque byte, never *discover* one.
- Wanted "which unmapped AAF 2180 byte separates cleanest across
  sleep/acc/ready/charging?" — `--discriminate` only ranks defined params, so I
  had to hand-write a `--try` per byte first.
- Body-ECU (IGPM/BCM) reverse engineering stalled: the interesting signals are
  **individual status bits** and their **cross-ECU mirrors** (a door bit in IGPM
  that also appears in BCM), and no tool exposes bit-level analysis beyond
  single-PID exact-equality mirrors.
- Time sinks: guessing which ECU/PIDs were actually co-polled (repeated
  "no timed captures for reference in scope"), and re-running the same
  8-command battery (`coverage`→`captures --diff`→`discriminate`→
  `correlate --against`→`hunt`→unit-sniff) by hand for every mystery PID.

## Decisions (locked)

- **Sequencing:** Tranche 1 → 2 → 3. T1 is pure capability parity (reuse
  existing primitives, highest value/effort ratio); T2 adds genuinely new
  statistics; T3 is UX/hygiene and can land piecemeal.
- **No numpy dependency** (continue the first plan's decision). Spearman/rank
  and lag-scan are cheap at current scale (≤ a few thousand points); reuse the
  hand-rolled `_pearson`/`_mean`. Gate any future numpy behind optional import.
- **Byte-series granularity stays layered, but symmetric.** `build_byte_series`
  keeps producing `u8` series for the broad all-vs-all matrix (cheap, dense).
  The *richer* interpretation sweep (signed/multi-byte/float/endian, PCI-aware)
  stays in the `hunt_byte` machinery — but is made reusable so **discriminate**
  and a future **broad hunt** can call it too (see 1.1/1.2).
- **Promotion stays opt-in and explicit** (first-plan rule). Any new hit surface
  (`correlate`, discriminate-on-bytes) that gains a `--promote` routes through
  `pids upsert-param` as enabled+unverified with evidence in `notes`. Analysis
  never auto-writes `ecus/`.
- **Bit analysis reuses the same correlation math.** A bit series is 0/1-coded
  and fed to the existing `_pearson` — which *is* the point-biserial coefficient
  (bit vs continuous) and the φ coefficient (bit vs bit). No new correlation
  math; the only new code is bit-series **extraction** and the bit
  **discrimination** score. Kept in a small helper so the byte path is untouched.

## Confirmed facts (grounding, file:line)

- **`correlate --bytes` already covers undecoded bytes** but only as **single
  unsigned `Bn`**: `build_byte_series()` (`xanalysis.py:136`, `min_distinct=4`,
  skips near-constant) is called from `_gather_series()` when `want_bytes`
  (`correlate.py:133`). No signed/multi-byte/float/endian here.
- **The interpretation sweep is `hunt`-only and single-reference:** `hunt_byte()`
  (`xanalysis.py:235`) reuses `INSPECT_TYPES`/`interpret_bytes`/`wican_expr`
  (`_decode_plot`, imported at `xanalysis.py:252`), aligns each against one
  `--against` ref (`hunt.py:46`), ranks by |r|. No `--corr-transform`, no
  many-reference mode.
- **`--discriminate` is defined-params-only:** `print_discriminate()`
  (`decode.py:653`) buckets `r["decoded"].get(name)` over `param_names`
  (`:666-672`); never reads raw bytes. Score = `_discriminability()`
  (`decode.py:625`, F-like between/within variance).
- **`--corr-transform` is `decode --corr`-only:** applied via `apply_transform`
  (`decode.py:809/837`), gated to require `--corr` (`decode.py:1002`);
  `correlate`/`hunt` never call it.
- **Mirrors are single-PID, exact-equality, byte-or-bit:** `find_mirrors()`
  (`decode.py:697`, bits at `:723`) compares positions *within one PID's*
  frames; there is no cross-ECU or discriminative bit path.
- **Correlation primitives to reuse:** `_pearson()` (`decode.py:431`),
  `join_nearest`/`align_many`/`DEFAULT_JOIN_TOL_S` (`align.py`), the correlate
  matrix loop (`xanalysis.py:200`), `resolve_ref()` (`decode.py:757`) for
  `ECU:PID:PARAM|EXPR` refs.
- **Scope flags** are shared via `add_scope_args()` — every new command/flag
  reuses them verbatim (`--since/--until/--date/--state/--label/--first/--last`).

---

## Tranche 1 — Capability parity (reuse existing primitives)

**1.1 `--discriminate` over raw bytes (the `correlate --bytes` twin).**
- Extend `print_discriminate` (`decode.py:653`) to optionally include the raw
  varying-byte series from `build_byte_series` (same source `correlate --bytes`
  uses), gated by a `--bytes` flag on `decode` (currently rejected there).
- **Lower the distinct-value floor for this path.** `build_byte_series` defaults
  to `min_distinct=4` (`xanalysis.py:139`) to keep the correlation matrix dense,
  but the highest-value discrimination targets are **near-binary relay/mode
  bytes** (e.g. `0x00`/`0x34`, 2 distinct values) — the default would skip
  exactly what we want. Call it with `min_distinct=2` here.
- Rank params **and** bytes together by the existing F score; mark byte rows
  `Bn` distinctly from params.
- This is the direct fix for "which unmapped AAF 2180 byte separates cleanest
  across power states?" — the thermal/relay/mode finder without hand-written
  `--try`s.
- Example: `canair decode AAF 2180 --discriminate state --bytes`
- **Test:** a fixture byte constant-within-state / different-across-states ranks
  top (including a 2-distinct-value relay byte that the `min_distinct=4` default
  would have dropped); a noisy byte ranks bottom; params still rank as before
  (regression).

**1.2 Transform-aware `hunt` and `correlate --against`.**
- Add `--transform {raw,delta,abs,cumsum,normalize,smooth}` to `hunt`
  (`hunt.py`) and `correlate` (`correlate.py`), applied to the **reference**
  before the join — reuse `apply_transform` (`decode.py`), mirroring
  `decode --corr-transform`.
- Rationale: the highest-value move (torque vs *acceleration* / *angular
  acceleration*) must work against the **raw-byte interpretation sweep**, not
  just defined params — so a torque byte can be *discovered*, not only
  corroborated.
- Example (would have found the torque byte from scratch):
  `canair hunt MCU 2102 --against MCU:2102:MCU_MOTOR_RPM --transform delta`
- **Test:** planted byte = k·delta(ref) → top hunt hit is that offset with the
  transform applied; correlate `--against … --transform delta` matches.

**1.3 `--promote` on `correlate --against` and discriminate-on-bytes.**
- Wire the existing `hunt --promote NAME` path (writes enabled+unverified via
  `pids upsert-param`, evidence in `notes`) to the `correlate --against` top hit
  and to a discriminate byte hit. Closes discovery→candidate without a
  hand-copied expression from either surface.
- Note the two surfaces promote *different* things: `correlate --against`/`hunt`
  have a fitted expression (slope/offset/interp → a scaled expression);
  **discriminate has only a state-separation score**, so it promotes the **raw
  byte `Bn`** as an unverified candidate with the F-score + per-state means in
  `notes` (no scale claimed — it's a "this byte is state-dependent, decode later"
  marker).
- **Test:** promote writes a schema-valid enabled-unverified param;
  `validate pids` green; PCI-crossing expression refused (mirror existing test);
  a discriminate promote writes a bare `Bn` expression with the F-score in notes.

---

## Tranche 2 — New correlation *types* (find what Pearson-on-bytes misses)

**2.1 Rank (Spearman) correlation as `--method {pearson,spearman}`.**
- Add a rank-correlation option to `decode --corr`, `correlate`, and `hunt`.
  Pure-python: rank-transform then reuse `_pearson`. Ties averaged.
- Catches **monotone-but-nonlinear** links Pearson under-scores: the quantized
  EPS speed-band (0-3), saturating temp-vs-current, regen knee. These are common
  in vehicle signals and currently rank low/false-negative.
- **Test:** a monotone-nonlinear fixture (e.g. `y = x**2`, x>0) scores ≈1.0 under
  spearman, <1 under pearson; a random pair scores ~0 under both.

**2.2 Lead/lag cross-correlation (`--lag-scan N` / report best lag).**
- For a chosen pair (or `--against`), sweep integer sample offsets in
  `[-N, +N]`, re-join at each, report the lag that maximizes |r| plus that r.
- Two payoffs: (a) **recovers r lost to sequential-poll skew** (torque-vs-accel
  fell 0.60→0.35 cross-ECU purely from ~0.3–3 s inter-ECU skew); (b) suggests
  **ordering** — command→response chains (pedal→torque-request→motor-torque→
  speed).
- **Honesty caveat (must be in the output):** with single-connection sequential
  polling, the ECUs in a cycle are sampled in a *fixed order*, so the measured
  lag is `true_lag + constant_poll_offset`. Lag-scan therefore reveals ordering
  only **relative to that fixed acquisition offset** — it cannot prove causality
  on its own. True causal ordering needs a same-ECU pair (no skew) or a synced
  high-rate capture. Report the lag as "apparent lag (incl. poll offset)", not
  "causal lag".
- Report lag in seconds (median inter-sample interval × offset) alongside r.
- **Test:** two series where B is A shifted by +2 samples → best lag = +2,
  r≈1.0; zero-lag r is lower.

**2.3 Gated / conditional correlation (`--where EXPR` / `--gate state|EXPR`).**
- Restrict the correlation to rows where a predicate holds (`--gate moving`,
  `--where "MCU_MOTOR_RPM>0"`, or a state token). Many links only exist within a
  regime (driving vs charging); whole-history correlation dilutes them.
- Reuse the states predicate machinery (`states.yaml` `when:`) for named gates;
  reuse the expression evaluator for `--where`.
- **Test:** a signal that tracks speed only while `moving` scores high with
  `--gate moving`, low without.

**2.4 Bit-level correlation & discrimination (the body-ECU gold).**
- New bit signal space: expand each varying byte into its 8 `Bn:k` **0/1
  series**. Reuse the WiCAN-frame reconstruction + bit indexing already behind
  `find_mirrors --bits` (`decode.py:723`) / `payload_to_wican_bytes`; feed the
  0/1 series straight into the existing `_pearson` (this yields point-biserial
  for bit-vs-analog and φ for bit-vs-bit — no new correlation math, see
  Decisions).
- **Cross-ECU bit correlation:** "which IGPM bit flips with this BCM event / at
  ignition-on / when a door opens?" Surfaces relays, courtesy lamps, lock
  actuators, warning indicators — the signals that are individual bits, not
  bytes.
- **Bit discrimination by state:** rank each `Bn:k` by how cleanly it splits
  across power states (the discrete analog of 1.1).
- Fold into `correlate --bits` and `decode --discriminate state --bits`. Note
  `decode` **already has a `--bits` flag** (currently only meaningful with
  `--find-mirrors`, `decode.py`); extend its scope to `--discriminate` rather
  than adding a new flag. The only genuinely new code is bit extraction + the
  bit-discrimination score.
- **Test:** a bit that equals a boolean anchor cross-ECU → φ≈1.0 reported; a bit
  constant-within-state, flipped-across-states → tops bit-discriminate.

---

## Tranche 3 — Time-savers & output hygiene (independent, piecemeal)

**3.1 Co-poll / overlap matrix (`correlate --overlap` or `captures --overlap`).**
- Report, for a scope, which `ECU:PID` pairs actually share time-aligned samples
  and how many (`join_nearest` n). The #1 time sink was guessing co-polling and
  hitting "no timed captures for reference in scope."
- Output: a compact matrix / ranked list of overlapping pairs with n and the
  date/time span. `--json`.
- **Test:** two ECUs co-polled in one session, disjoint in another → overlap n
  reflects only the shared session.

**3.2 `canair investigate ECU PID` — one-shot mystery report.**
- Bundle the manual battery into one ranked, per-byte report: for each data
  byte — varying?, state-discriminability F, best cross-signal anchor (via
  `correlate --against` over co-polled signals) with r/lag/fit, unit guess,
  and "already mapped by NAME?". Honors scope flags; `--json`.
- The "point it at an unknown PID and tell me everything" entry point;
  collapses ~8 commands into one.
- **Test:** on a fixture PID with one planted speed-linked byte + constants +
  one state-linked byte, the report ranks the two real signals with correct
  anchor/score and flags the constants.

**3.3 Output signal-to-noise.**
- **Collapse multi-byte interpretation spam:** `hunt` prints u16/i16/u24/… of
  one offset at near-identical r (8 rows per real signal). Show best-per-offset
  by default; `--all-interps` to expand.
- **Cluster co-linear groups:** during balanced charging all ~96 cell voltages
  correlate r≈1.0 and flood the top rows. Report "group of N signals mutually
  r>0.99" as one collapsed line; likewise group mirror sets (`B22==B33==B46` as
  one group, not 3 pairwise rows).
- **Drop trivial self-matches** (a param vs the byte it is defined from) unless
  `--include-self`.
- Print the scope's **max-possible n** once at the top so thin joins are obvious.
- **Test:** clustered fixture collapses to one group line; `--all-interps`
  restores the per-interpretation rows; self-match hidden by default.

**3.4 Preserve YAML note wrapping in `pids upsert-param`.**
- `upsert-param` reflows multi-line note block-scalars into one long line (it
  churned the MCU torque diff). Preserve the folded/literal block style on
  update. (Touches `pids`, not `decode`, but surfaced by this workflow — Boy
  Scout fix.)
- **Test:** upserting a param with a multi-line note keeps it multi-line;
  `validate pids` green; diff shows only the intended change.

---

## Cross-cutting: verification & rollout

- Per-tranche: `uv run pytest -q` + `uv run ruff …` green; add the unit tests
  above. `uv run canair validate all` green after any `--promote`/`pids` work.
- **Acceptance (re-run the 2026-07-23 deep pass as history-only tests):**
  - `decode AAF 2180 --discriminate state --bytes` surfaces the varying thermal
    bytes (B25, B28) without hand-written `--try`s (1.1).
  - `hunt MCU 2102 --against MCU:2102:MCU_MOTOR_RPM --transform delta` ranks a
    torque byte at top (1.2) — discovery, not just corroboration.
  - `correlate --overlap --date 2026-07-22` shows ESC/EPS/MCU/VCU/AAF co-polled
    and BMS 2101 *not* in the drive (3.1) — the thing I learned by trial.
  - **Bit analysis (2.4):** validated against a **synthetic fixture** (planted
    cross-ECU bit mirror + state-split bit), not existing history — the body
    ECUs (IGPM/BCM) have mostly *one-shot untimed* reads today, so there is no
    dense timed co-poll to reproduce against. First real use will need a
    **targeted co-polled body capture** (IGPM+BCM `--monitor` across
    lock/unlock/door/ignition events); note this as a capture lead, don't gate
    the tranche on data that doesn't exist yet. A `correlate IGPM:22BC03 --bits`
    within-PID smoke test is a fine sanity check but is not the real target.
- Docs: extend `AGENTS.md` tool list and the **reverse-engineer-pid** skill
  cheat-sheet (discriminate `--bytes`, transform-aware hunt/correlate, spearman,
  lag-scan, gated corr, bit analysis, `overlap`, `investigate`).
- Both transports untouched (offline over `captures/`).

## Non-goals (this plan)

- No numpy hard dependency (rank/lag stay pure-python at current scale).
- No interpolation/resampling join (nearest-neighbour only; lag-scan tests
  integer sample offsets, still nearest-neighbour at each).
- No persisted decode/correlation cache; values stay regenerated.
- No change to the capture on-disk schema.
- Not solving acquisition skew itself (companion pipeline work) — lag-scan just
  *measures and compensates* it in analysis and always reports the realised
  lag/n.

## Open questions

- **Bit-space explosion:** 8 bits × every varying byte × cross-ECU is large for
  a full session. Start scoped (one target ECU:PID `--against` an anchor, or
  `--bits` only when explicitly requested); revisit a pre-filter (only bits that
  actually toggle) if the all-vs-all bit matrix is slow.
- **`investigate` anchor selection:** auto-pick anchors from co-polled verified
  signals (speed/RPM/temp/SOC) or let the user pass `--against`? Start with
  auto + override.
- **Lag-scan default range N:** tie to observed inter-sample interval; expose
  `--lag-scan N` (samples) with a sensible default (~±3) and report seconds.
- **Lag vs poll-order confound:** since lag = true_lag + fixed poll offset (see
  2.2), is a per-pair baseline correction worth it (e.g. subtract the median
  offset inferred from a known-instantaneous mirror pair on the two ECUs)? Start
  without correction, label output "apparent lag", revisit if it misleads.
- **Spearman vs Pearson default:** keep Pearson default (back-compat), add
  `--method spearman`; consider showing both when they disagree materially
  (a nonlinearity flag).

## Status

- [x] Tranche 1 — discriminate `--bytes` (1.1); transform-aware hunt/correlate
      (1.2); `--promote` on correlate/discriminate (1.3).
      - 1.1: `decode --discriminate state --bytes` ranks every varying, non-PCI
        raw byte (`min_distinct=2`, so near-binary relay bytes survive) alongside
        params (`_byte_state_buckets` in `decode.py`). Verified: AAF 2180 B28/B29
        (the thermal mirror the byte-series bug hid) now top the ranking (F=6646)
        with no `--try`.
      - 1.2: `--transform {raw,delta,abs,cumsum,normalize,smooth}` on `hunt` and
        `correlate --against`, applied to the reference via `xanalysis.transform_ref`
        (sorts by time first). Verified: `hunt MCU 2102 --against
        MCU:2102:MCU_MOTOR_RPM --transform delta` *discovers* the torque byte
        `[S12:S13]` (r=0.60) from the raw sweep vs angular acceleration.
      - 1.3: `correlate --against --promote NAME` promotes the top *raw-byte* hit
        via the shared `commands/_promote.py::write_candidate` (guarded, schema-
        validated, auto-reverted); `hunt._promote` refactored onto the same helper.
        Discriminate-promote deferred (its hit has only an F-score, no expression —
        lower value; `--bytes` output already lists the byte to `--try`).
      - Tests: `test_xanalysis.py` (TransformRef, CorrelatePromote, parser flags),
        `test_decode_try.py` (discriminate `--bytes` surfaces/skip-PCI). Full suite
        (1721) + ruff + `validate all` green.
- [x] Tranche 2 — spearman `--method` (2.1); lag-scan (2.2); gated/`--where`
      correlation (2.3); bit-level correlation & discrimination (2.4).
      - 2.1: consolidated the three `pearson` copies into `canlib/stats.py`
        (`pearson`/`rank`/`spearman`/`correlation`); `--method {pearson,spearman}`
        on `decode --corr`, `correlate`, `hunt`. Header shows the coefficient.
      - 2.2: `correlate --against --lag-scan N` (`xanalysis.lag_scan`) shifts each
        signal ±N sample-intervals, reports the lag maximising |r|, labelled
        "apparent lag (incl. poll offset)". Verified: inverter temp peaks at +7.3s
        vs speed (thermal inertia).
      - 2.3: `correlate --against --gate '[SIGNAL] OP VALUE'` filters the reference
        to a regime (`'> 0'` = while-moving, or a named cross-signal). `--state`
        already covered session-state gating.
      - 2.4: `correlate --bits` and `decode --discriminate state --bits`
        (`xanalysis.build_bit_series`, `_byte_state_buckets(include_bits=)`).
        0/1-coded bit series feed the same pearson (point-biserial vs analog, φ
        vs bit). Reused decode's existing `--bits` flag.
      - Tests: `test_stats.py` (pearson/rank/spearman/dispatch), `test_xanalysis.py`
        (LagScan, CorrelateGate, build_bit_series), `test_decode_try.py`
        (discriminate `--bits`). Full suite (1738) + ruff + `validate all` green.
- [x] Tranche 3 — co-poll overlap matrix (3.1); `investigate` verb (3.2); output
      clustering/interp-collapse/self-match hygiene (3.3); preserve YAML note
      wrapping in `pids upsert-param` (3.4).
      - 3.1: `correlate --overlap` (`_print_overlap`) reports which ECU:PID pairs
        share time-aligned samples (and how many) — the "which reference can I
        use here?" index. Verified: shows ESC/EPS/MCU/VCU/AAF co-polled on the
        drive, BMS 2101 absent.
      - 3.2: new `canair investigate ECU PID` command — one ranked per-byte report
        (mapped-by-param? / state-discriminability F / best co-polled anchor with
        r+fit+unit). Bundles coverage→discriminate→correlate→hunt into one call.
      - 3.3: `hunt` collapses to best-per-offset (was u8/i16/u24 spam), `--all-interps`
        to expand; `correlate --against` drops the trivial self-match (`--include-self`
        to keep) and shows the reference's sample count; matrix collapses
        near-perfectly-correlated (|r|≥0.995) groups to one summary line
        (`_colinear_clusters`, `--no-cluster` to disable) — the 66-cell-voltage
        charging flood becomes one line.
      - 3.4: `pids` `_format_notes_block` word-wraps long notes (folded scalar →
        value-preserving; long URLs/tokens not broken), so `upsert-param`/`--promote`
        notes land as readable multi-line text.
      - Tests: `test_investigate.py`, `test_xanalysis.py` (Overlap, OutputHygiene),
        `test_pids_edit.py` (note wrapping). Full suite (1756) + ruff + validate green.
- [ ] Docs — `AGENTS.md` + reverse-engineer-pid skill updated.
- [ ] Acceptance — the four history-only checks above reproduce from single
      commands.
</content>
</invoke>
