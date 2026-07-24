# Body-ECU Event Analysis & Tooling Hardening — Implementation Plan

Status: **DONE** (2026-07-24) — all tranches implemented except T3.4 (deferred by
decision: no speculative sampling-metadata fields until a second concrete need).

Third-generation analysis improvements, driven by a hands-on RE session
(2026-07-24) that reverse-engineered a **narrated body/comfort event capture**
(IGPM/BCM in sleep: fob unlock → trunk → doors → hood, recorded with
`--keep-unique`). Where the two `2026-07-23-*` plans made *cross-signal
continuous* analysis first-class (speed/torque/temp on powertrain ECUs), this
session exposed that **event-driven, bit-level body-ECU** RE is still largely a
manual exercise — and surfaced several smaller tooling gaps (a real byte-offset
bug hidden by lack of param-overlay, a duplicate-signal-name collision caught
too late, and no way to rename/delete a param without hand-editing YAML).

Approach: **make bit-edge/event analysis first-class by wiring up primitives
that already exist (Tranche 1), make the just-added `keep_mode` actually change
analysis behavior (Tranche 2), then the small correctness/ergonomics fixes that
each cost real time this session (Tranche 3).** Each tranche lands with unit
tests + `pytest`/`ruff` green. Tranches 1–2 are **read-only analysis over
existing captures**; Tranche 3 touches `pids`/`validate`/`bix` and the capture
schema (additive only).

## Motivation (what the 2026-07-24 session exposed)

- The entire capture's value was **individual status bits toggling over time**
  (door/lock/hood/trunk on IGPM `22BC03`/`BC04`/`BC05`, BCM `B004`/`B005`/`B006`).
  I decoded all of it **by hand**: `captures --diff`, manual `bix --annotate`,
  then eyeballing hex against the narrated event order.
- **`investigate` was useless for this PID.** `investigate IGPM 22BC03` printed
  "no varying bytes to report"; `--all` only listed `B10 [DOOR_DRV_OPEN]` /
  `B11 [ACC2_IGN_ON]` — byte names, no bits, no timing, no event correlation.
- **The decode already knew how to find toggling bits** (`decode --discriminate
  --bits`) — but that ranks by *vehicle power state*, not by the narrated event
  timeline, and never emits the rising-edge timestamp that lets you say "B10:5
  went 1 at 09:37:37, exactly when the driver door opened."
- **`--keep-unique` silently changes the meaning of the data** (only rising
  edges are stored; closes/durations are dropped) and **no analysis tool warns
  about it.** I only reasoned correctly because I read the journal dedup code
  mid-session.
- **A byte-offset bug survived because nothing overlays params onto bytes.**
  `BCM_B005_UNLOCK_BITS`/`B006` read `B12` (a constant) when the toggling byte
  is `B10`; I only caught it by hand-diffing `bix --annotate` output against
  `bcm.yaml`. `bix --annotate` shows protocol roles but **not** which param maps
  each byte.
- **The `AUX_BATTERY_VOLTAGE` duplicate only blew up at `wican autopid write`**,
  after all the RE work — `validate pids` never flagged it.
- **Fixing that duplicate required hand-editing YAML** because `canair pids` has
  no `rename-param`/`rm-param`.

## Decisions (locked)

- **Sequencing:** Tranche 1 → 2 → 3. T1 reuses existing bit primitives (highest
  value/effort); T2 is a small correctness layer on the new `keep_mode`; T3 is
  independent correctness/ergonomics fixes that can each land alone.
- **No new correlation math, no numpy** (continue both prior plans' rule). Bit
  series are 0/1-coded and already flow through `_pearson`; event/edge detection
  is just transition-finding over an existing time-ordered bit series.
- **Event timeline = capture-level `notes` + timestamps, not a new schema.**
  The session already records per-capture `time` and free-text `notes`; the
  narrated event order lives in the session `notes`. T1 aligns bit edges to
  capture timestamps and prints them next to the session/capture notes — it does
  **not** invent a structured event-marker field (that stays a T3 *optional*
  additive schema item, gated behind a decision below).
- **`keep_mode` awareness is a caveat, never a behavior fork.** Analysis output
  gains a one-line banner and suppresses duration/rate claims on `keep:unique`
  sessions; it does **not** silently reinterpolate or fabricate falling edges.
- **Schema-of-record changes stay additive and optional.** Any new session field
  (T3.4) is optional with `additionalProperties` kept `false` by explicitly
  listing it (as `keep_mode` already is). No existing capture is rewritten.
- **Promotion stays opt-in** (prior-plan rule). A bit-edge/event surface that
  gains `--promote` routes through `pids upsert-param` as enabled+unverified with
  evidence in `notes`.

## Confirmed facts (grounding, file:line)

Point 1 (bit/event analysis) is **partially addressed** — the bit primitives
exist in `decode` but `investigate` doesn't use them and nothing is
time/event-aware:

- **`decode --discriminate --bits` already detects & ranks toggling bits.**
  `--bits` is defined at `decode.py:1009-1014`
  ("…with --discriminate: also rank individual toggling bits by state").
  `_byte_state_buckets(..., include_bits=...)` extracts `b = (fr[off] >> k) & 1`
  per bit and buckets bits with ≥2 distinct values (`decode.py:708-718`);
  `print_discriminate(..., include_bits=True)` merges & F-ranks them
  (`decode.py:751-769`, scorer `_discriminability` at `decode.py:640-665`).
- **…but it buckets by *state*, not time, and emits no edge timestamps.** Comment
  at `decode.py:677-679` is explicit ("buckets by state, not time"); grouping is
  `_join_states(cap.get("vehicle_states"))` (`decode.py:745,691`). Nothing reads
  `notes` or event markers.
- **`decode --find-mirrors --bits` finds cross-position equal bits**
  (`find_mirrors(..., bits=True)`, `decode.py:790-834`; output `Bn:k == Bm:j`,
  `decode.py:837-847`) — but only exact-equality across all captures, no edges.
- **`investigate` is byte-only and needs a co-polled anchor.** Target series is
  `build_byte_series(lp, min_distinct=2)` (`investigate.py:147`), keyed on
  integer byte `offset` (`investigate.py:101-112,173`); no `--bits`/`--bytes`
  flag (`investigate.py:80-96`); it calls `_byte_state_buckets(...)` **without**
  `include_bits=True` (`investigate.py:157`). Anchors are "every param on the
  *other* co-polled ECU/PIDs" (`investigate.py:159-169`); a body PID with no
  co-polled partner gets `anchors={}` → `_best_anchor` returns `None`
  (`investigate.py:211-225`). "no varying bytes to report" is the empty-`reports`
  path (`investigate.py:233-235`).
- **Nothing consumes `keep_mode` for analysis.** The loader copies it into each
  entry (`_captures_query.py:155,164`); only `captures.py` reads it, purely to
  *display* a `keep:unique` TOC tag + JSON field (`captures.py:179,205,249,281-283`).
  `decode.load_captures` deliberately omits it (`decode.py:87-108`); `correlate`
  and `investigate` never reference it. → **no caveat, no banner anywhere.**
- **`bix --annotate` has no param overlay.** `_annotate_payload`
  (`bix.py:192-222`) prints WiCAN|Hex|ISO-TP|Torque|bix|**Role**, where Role is
  protocol framing only (PCI/SID/PID/DID, `bix.py:208-214`). It never loads PID
  defs and takes no ECU/PID arg. The helper that *would* answer "which param
  covers offset N" — `mapped_offsets(parameters, …)` (`byteindex.py:187-213`) —
  exists and is used by `investigate`, but not by `bix`.
- **`canair pids` has no rename/delete.** Subcommands: `upsert-param`,
  `add-research`, `set-status`, `set-pid-status`, `set-identity`
  (`pids.py:186-261`). `pids_edit.py` has **no** `rename_parameter`/
  `delete_parameter` (only `upsert_parameter` mutates params).
- **Duplicate param names caught only at generation time.**
  `generate_profile` raises `DuplicateParameterError` on collisions among
  shipped params (`wican.py:147-177,192-201`). `validate pids`' only cross-file
  uniqueness check is **ECU name/alias**, not params (`_duplicate_name_errors`,
  `validate.py:817-845`, invoked `validate.py:763`); `validate_ecu_file`
  (`validate.py:336-365`) has no param-name-collision check.
- **Session metadata today:** `date`, `label`, `vehicle_states`, `notes`,
  `keep_mode`, `captures` (`build_query_session`, `captures.py:86-132`; schema
  `captures_schema.json:16-42`, `additionalProperties:false`). No interval, no
  wake/session-held, no event-marker field.

## Tranche 1 — Bit-edge & event-timeline analysis (the headline gap)

Make body/comfort-ECU RE first-class by exposing the bit primitives `decode`
already has, adding *time/edge* awareness, and aligning to the narrated event
order.

- **1.1 `investigate --bits`.** Pass `include_bits=True` through
  `investigate`'s call to `_byte_state_buckets` (`investigate.py:157`) and extend
  the report from byte-only (`_ByteReport`, integer `offset`) to a bit-capable
  key (`Bn` and `Bn:k`). For each varying bit: show mapped-param status (reuse
  `mapped_offsets`), state-discriminability F, and — when a co-polled anchor
  exists — the anchor r. This turns `investigate IGPM 22BC03 --bits` into the
  one-shot "what moved and what does it track?" for a body PID.
  - **Fix the empty-report UX:** when there are no co-polled anchors (the body-PID
    case), don't imply "nothing here" — print the varying bits/bytes with F-scores
    and a hint ("no co-polled anchor in scope; add one with a wider selector or
    use `--events`"). Current message is misleading (`investigate.py:233-235`).
- **1.2 Edge extraction + event timeline (`--events`).** New read-only mode
  (on `investigate`, and/or a thin `decode … --edges`) that, for each varying
  bit on a low-cadence PID, walks the **time-ordered** series and reports each
  **rising/falling edge with its timestamp** and the value before/after. Then
  align each edge to the nearest session/capture `notes` (the narrated event
  log) and print them interleaved, e.g.:
  ```
  09:37:37  B10:5 0→1   [DOOR_DRV_OPEN]   ~ note: "open drv door"
  09:38:03  B11:0 0→1   [candidate]       ~ note: "open hood"
  ```
  This is exactly the table I built by hand; it's transition-finding over an
  existing bit series + nearest-note join (reuse the `join_nearest` used for
  anchor alignment, `investigate.py:215`). No new math, no schema change.
- **1.3 Cross-ECU bit mirrors in scope.** `decode --find-mirrors --bits` is
  single-PID (`decode.py:790-834`). Add the cross-ECU case to `correlate --bits`
  (or a `--find-mirrors` on `correlate`) so "a door bit in IGPM that also appears
  in BCM" is discoverable — the exact body-ECU need the 2026-07-23 plan named
  (`decode-analysis-ergonomics.md:29-32`) but left to single-PID.
- **Tests:** synthetic captures with a known bit toggling at a known time and a
  matching capture note → assert `--bits` ranks it, `--events` reports the edge
  time + before/after + nearest-note join, cross-ECU mirror is found. No-anchor
  body-PID path prints bits (not "nothing").

## Tranche 2 — `keep_mode`-aware analysis (finish what was started)

The field is persisted and displayed but changes no analysis behavior. Close the
correctness gap so a future reader can't misread rising-edge-only data.

- **2.1 Thread `keep_mode` into the analysis loaders.** `decode.load_captures`
  (`decode.py:87-108`) drops it; carry it through (and into `correlate`/
  `investigate`'s signal loaders) so tools can see it per session.
- **2.2 Caveat banner.** When any in-scope session is `keep:unique`, print a
  one-line banner on `decode`/`correlate`/`investigate`:
  "⚠ scope includes keep:unique sessions — only rising-edge transitions were
  stored; falling edges/durations are absent." Especially important for
  `--compact`/`--changes-only` (a value "persisting" is an artifact of dedup).
- **2.3 Suppress/flag duration & rate math on `keep:unique`.** `--corr-transform
  delta|cumsum`, `--lag-scan`, and any dwell/duration stat should refuse or
  loudly caveat on dedup'd sessions (the time gaps are not real sampling gaps).
- **Tests:** a `keep:unique` session in scope → banner emitted; a mixed scope →
  banner names it; delta/rate transform on a pure `keep:unique` scope →
  caveat/refusal.

## Tranche 3 — Correctness & ergonomics fixes (each independent)

Small, high-leverage fixes; each cost real time this session and can land alone.

- **3.1 `bix --annotate --ecu ECU --pid PID` param overlay.** Optionally accept
  an ECU/PID, load its params, and add a `Param` column via `mapped_offsets`
  (`byteindex.py:187-213`) marking which param (and bit) reads each byte —
  flagging **unmapped** bytes and bytes read bit-by-bit. This is what would have
  made the `B12`-vs-`B10` bug obvious instantly. Backward compatible: no ECU/PID
  → today's protocol-only table.
- **3.2 `validate pids` duplicate-shipped-name check.** Port `generate_profile`'s
  `name_origin`/`collisions` logic (`wican.py:147-177,192-201`) into a read-only
  validator pass so a duplicate signal name across *active + enabled* PIDs is a
  **validation error** (CI-catchable), not a surprise at `wican autopid write`.
- **3.3 `canair pids rename-param` and `rm-param`.** Add
  `rename_parameter(ecu, pid, old, new)` and `delete_parameter(ecu, pid, name)`
  to `pids_edit.py` (comment-preserving, YAML-reparsed + schema-validated +
  auto-reverted, like `upsert_parameter`), exposed as `pids rename-param` /
  `pids rm-param`. Removes the last "must hand-edit YAML" case (AGENTS.md
  discourages it) and would have made the `AUX_BATTERY_VOLTAGE` fix a one-liner.
  Mutative → confirm/`--yes` per the contributing skill's mutation rule.
- **3.4 (Optional, decision-gated) generalize capture-session sampling metadata.**
  `keep_mode` is the first "how was this sampled" field. If we want the same for
  monitor **interval** and **wake/session-held**, add them as optional session
  fields (schema additive, listed explicitly to keep `additionalProperties:
  false`). **Decision needed:** do we want structured sampling metadata now, or
  is session `notes` sufficient? Recommend deferring until a second concrete need
  appears — don't add speculative fields.

## Non-goals

- No structured event-marker schema in T1 (capture `notes` + timestamps suffice;
  revisit only if 3.4 is adopted).
- No new correlation coefficients or numpy (prior-plan rule stands).
- No auto-writing of `ecus/` from analysis (promotion stays explicit).
- No rewriting existing captures; `keep_mode` backfill stays the manual
  `set_session_keep_mode` helper.

## Suggested landing order

T1.1 → T1.2 (the two that most change body-ECU RE day-to-day) → T3.1 + T3.2
(cheap correctness wins) → T2 (caveat layer) → T1.3 → T3.3 → T3.4 (only if
decided). T3 items are independent and may be pulled forward opportunistically.
