# Profile variants, phase 7 — per-revision overrides and evidence

Status: **DONE** (2026-10-31). Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[Evidence fields](2026-10-30-profile-variants-and-rename.md#evidence-fields--where-writes-land)
section). Depends on phase 5 (phase 6 recommended first, so there is real data).

## As built

- **"Reference revision" is per definition:** the first declared ECU revision the PID or signal
  applies to. A PID gated to `[uds]` has `uds` as its reference, so its own `verified` and
  `response_frames` are `uds`'s — the Ioniq's ported `220101` needed no override to stay
  coherent.
- **Override whitelists differ by level.** A PID: `period`, `response_frames`, `verified`,
  `notes`, `source`, `source_links`. A signal: `expression`, `unit`, `min`, `max`, `type`,
  `values`, `bits`, `fields`, `verified`, `notes`, `source`, `source_links`. `unit`/`min`/`max`
  are overridden per signal, not inherited from a PID override.
- **An inherited `verified` is not trusted across references.** A signal whose reference
  differs from its PID's and whose `verified` equals the PID's (so may be inherited) resolves
  unverified until its own `per_revision` says otherwise. Conservative by design.
- **An undetermined ECU** keeps no frame count and verifies nothing, so it neither seeds the
  ELM327 digit nor ships to AutoPID.
- **Frame-count routing** is `response_frames.py::revision_target`, used by `persist`; base
  writes keep the text editor (`set_response_frames`), `per_revision` writes go through
  `revisions_edit.set_override`. Tested through `persist` and `seed_counts` (what
  `run_session_guarded`'s teardown calls), not a mock-bus session.
- **Identity routing is not built:** `canair identity` writes nothing to `identity:` (AGENTS.md
  said it did; corrected), so there is no write to route. Matching a reading against fingerprints
  — and reporting a possible new revision — needs a DID → identity-field map and belongs with
  detection (phase 8).
- **There is no `pids verify`;** `upsert-param --revision` is the verify path (`--verified`).
  Other `pids` editors (period, notes, …) don't take `--revision` yet.
- `ecu revisions rename` rewrites `per_revision` keys too, and `rm` counts them as references.

## Goal

Let a PID or signal differ per revision field by field (`per_revision:`), and make the three
evidence fields — `verified`, `response_frames`, identity readings — belong to a revision rather
than to the car. **This is the highest-risk phase:** it touches the frame-count write-back that
keeps the ELM327 pipe from desynchronising.

## What users see

- Signals verified on one revision count as unverified on another until verified there.
- AutoPID output for a non-reference revision gets stricter accordingly.
- `pids upsert-param --revision R` for authors.

## Changes

### Overrides — `per_revision:`

- Schema: on PIDs and signals, a mapping of revision ID → whitelisted fields (`expression`, `unit`,
  `min`, `max`, `type` and its maps, `verified`, `notes`, `source`, `source_links`, `period`,
  `response_frames`).
- Not allowed: `revisions`/`per_revision` inside an override, `parameters:` inside a PID
  override, or an override key outside the entity's applicability.
- The load seam applies overrides after gating; the raw loader exposes them for validate and the
  editors.

### `verified`

- The base value means "verified on the reference (first-listed) revision".
- Another revision resolves to `false` unless `per_revision.<r>.verified: true`.
- An ECU without revisions has one revision, so it is unaffected.

### `response_frames` — the risky part

- `canlib/response_frames.py`:
  - `stored_count()` reads the resolved value for the session's revision;
  - `seed_counts()` builds the seed from it;
  - `resolve_edits()`/`persist()` write to the base for the reference revision, else to
    `per_revision.<r>.response_frames`.
- An unresolved revision persists nothing.
- `frame_counts.py::FrameCountLedger` needs no change: its keys are per session, and a session has
  one revision per ECU.
- Required test: a count learned under `uds` must never seed a `kwp` read, end to end through
  `run_session_guarded`'s write-back.

### Identity readings

- `canair identity` (`canlib/modes/identity.py` and the records writer) files a reading under the
  revision whose fingerprints it matches.
- A reading matching none is **reported as a possible new revision** and not written over the
  base.

### Editors

- `pids upsert-param … --revision R`: on an existing signal, writes `per_revision.R`; on a new
  one, creates it with `revisions: [R]`.
- `pids verify` and the other edit paths honour `--revision` too.

### Skill and docs

- The skill gains **evidence discipline**: verification belongs to a revision; never assume across
  revisions; seeds stay unverified.
- `docs/concepts/variants.md` (overrides, evidence), `CHANGELOG.md` (the stricter AutoPID output
  for non-reference revisions).

## Tests

- Override application order and whitelist enforcement.
- `verified` resolution.
- Frame-count isolation between revisions (unit tests plus a mock-bus session on the fixture).
- Identity routing, including the "matches none" report.
- `upsert-param --revision` on existing and new signals.

## Acceptance

- The fixture's `kwp` and `uds` frame counts stay separate across sessions.
- **Invariant:** `--variant 28kwh` output byte-identical to phase 6.

## Out of scope

Detection (phase 8); per-variant AutoPID directories (phase 9).

## Risks

- **An undercount from another revision desyncs the ELM327 pipe**, so the write-back must route by
  revision or not at all. Review this against the four frame-count rules in AGENTS.md before
  merging.
