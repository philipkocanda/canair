# Profile variants, phase 8 — detecting an ECU's revision

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[Which revision, per ECU](2026-10-30-profile-variants-and-rename.md#which-revision-per-ecu)
section). Depends on phase 7.

## Goal

Work out an ambiguous ECU's revision from what the ECU itself reports, so pins become the
exception, and let `canair identity` tell an owner which variant they have.

## What users see

- An ECU whose variant allows several revisions is identified on first contact. Its gated PIDs
  read instead of being skipped.
- `canair identity BMS` reports the matching revision and the variants that leaves possible, even
  with no variant selected.
- The refusal message gains its "Not sure which you have? `canair identity BMS`" line.

## Changes

### Matching

- A leaf module, `canlib/variants/fingerprints.py`, with no I/O: given an ECU's identity readings
  and replies, it returns the matching revisions.
- `identity:` fingerprints are glob matches on identity fields; `responds:` is the request a
  revision answers.

### Probing

- At session open (`SessionManager.open_session` / `run_session_guarded`), for each ECU that is
  still **ambiguous** (no pin, more than one possible revision): send the cheapest `responds:`
  read, through the existing session and safety policy and with the usual echo validation. One
  read per ambiguous ECU per session, never per PID.
- The result is cached per profile and variant in user state (local, never contributed; beside
  the config, not in the profile).
- The cached result is re-checked when a later identity read contradicts it.
- On a match, re-resolve that ECU at the load seam (one `clear_cache()` per change). On no match,
  stay undetermined → skip, as in phase 5.

### Captures

- A revision settled part-way through a `--save` session splits the session, the same way a
  reconnect re-homes one.
- Detected revisions are tagged `attribution: detected`.
- `validate captures` warns when a session holds replies to PIDs outside its tagged revision, and
  when its own identity reads contradict its tag.

### Identity without a variant

- `identity` and `discover` (both exempt from the refusal when run without `--save`) match
  fingerprints across every variant.
- They print the matching revision and which variants allow it.
- Phase 4's refusal message gains the `canair identity` hint.

### Skill and docs

- The skill gains **fingerprints**: which kind to use, why `responds:` is robust for protocol
  changes, and why a reading matching none is a question, not a new revision.
- `docs/concepts/variants.md` (detection, the cache, pins as the escape hatch).

## Tests

- Matcher units: globs, `responds:`, no match, multiple matches.
- Mock-bus and ELM327-emulator sessions on the fixture, where the emulator answers `1A90` or
  `22F187` per scenario.
- Cache hit, miss and contradiction.
- Session split on late detection.
- The mismatch warnings.

## Acceptance

- In the fixture variant that allows both revisions, a session with no pin reads the right PIDs for
  each emulator scenario.
- `identity` with no variant selected names the variant.
- **Invariant:** `--variant 28kwh` output byte-identical to phase 7. No bundled ECU is ambiguous,
  so no probe is sent.

## Out of scope

Variant detection from the VIN. It is personal data, and the PII gate forbids persisting it.

## Risks

- **A sleeping ECU answers no fingerprint,** and the extra read costs a round trip on a slow link.
  Hence one read per ambiguous ECU per session, the cache, and skipping rather than guessing.
