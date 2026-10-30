# Profile variants, phase 5 — ECU revisions and gating

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[ECU revisions](2026-10-30-profile-variants-and-rename.md#ecu-revisions-revisions-in-ecusecuyaml)
and [Resolution](2026-10-30-profile-variants-and-rename.md#resolution-happens-once-at-the-load-seam)
sections). Depends on phase 4.

## Goal

Let an ECU declare revisions, let PIDs and signals be gated to them, let a variant say which
revisions (and which ECUs) it can have, and resolve all of it once at the load seam. Prove it on a
**synthetic fixture profile**. No bundled profile gains revisions in this phase; phase 6 does that
for the Ioniq BMS.

## What users see

Nothing for bundled profiles: none declares revisions yet. Authors get the schema, the editors and
the validation rules.

## Changes

### Schema — `canlib/schema/pids_schema.yaml`

- **ECU level:**
  - `revisions:` — a mapping of revision ID → ECU-level overrides (whitelist:
    `identity.id_protocol`, `sessions`, `wake`, `multi_did`, `multi_did_max`,
    `response_timeout_ms`, `addressing`, `rx_id`) plus `fingerprints:`. Fingerprints are stored
    and validated here but not used until phase 8.
  - `variants:` — a list, for ECU presence.
- **PID and signal level:** `revisions:` — an applicability list. The first-listed revision is the
  reference.
- **`variants.yaml`:** each variant may now carry `revisions:` (ECU → allowed list) and
  `profile_overrides:` (whitelist: `car_model`, `physical_bands`, `response_timeout_ms`, `init`,
  `multi_did_max`, `quirks`). Phase 2 rejected both keys.

### The load seam

- `Profile` gains `revisions: Mapping[str, str]` (resolved per ECU), and `cache_key` covers the
  variant and the revisions.
- `canlib/pids.py::load_pids(profile)` returns definitions **filtered** for the variant (ECU
  presence) and the resolved revisions (PID and signal applicability), with ECU-level revision
  overrides applied. `merge_ecu_documents` is untouched.
- `Profile.meta` applies `profile_overrides`.
- A raw-document loader stays for variant-aware callers: `validate`, the editors, and later the
  explorer and coverage.

### Resolving a revision (no detection yet)

1. Pin: `profile_settings.<profile>.revisions.<ECU>`, set with `profile use … --pin ECU=REV`.
2. The only revision the variant allows, or the ECU's only revision.
3. Otherwise **undetermined**: no guess. The ECU's revision-gated PIDs are skipped, with one
   message per session naming the pin; its ungated PIDs still read.

### Validation

- **Subset rule:** a signal's revisions must be within its PID's.
- **References:** revision IDs named in `variants.yaml` and in gates must exist; ECU `variants:`
  must name declared variants.
- **Duplicate shipped signal names** are allowed only when the owning PIDs' revision sets are
  disjoint.
- **`validate states`:** each `when:` reference must resolve under every reachable
  variant × revision combination, enumerated from each variant's `revisions:` map
  (`canlib/states/refs.py`).

### Captures and analysis

- Sessions gain `revisions: {ECU: id}` for captured ECUs with more than one revision, plus their
  attribution (`pinned` / `only`).
- Each session decodes with its own tagged revisions.
- **Byte-level analyses** (`investigate`, `hunt`, `correlate`, `coverage`, `decode
  --dump-bytes`/`--try`) refuse a scope that spans two revisions of the analysed ECU, and name
  `--variant`/`--revision`. `--revision ECU=id` joins `add_scope_args`.

### Editors

- `canair ecu <ECU> revisions list | add ID | set-fingerprint | rename | remove`.
- `pids set-revisions ECU:PID[:SIGNAL] REVS|--clear`.
- `ecu <ECU> set-variants VARIANTS|--clear`.
- `variants set-revisions ID ECU REVS`.
- Each `rename` rewrites every reference, capture tags included.

### The synthetic fixture — `tests/fixtures/profiles/variants/`

- Two variants.
- An ECU with `kwp`/`uds` revisions, a PID in each, and one signal name shared across both.
- A shared ECU with no revisions.
- An ECU present in only one variant.
- One variant allowing both revisions of an ECU, to exercise pins and "undetermined → skip".
- A state predicate that must resolve under both revisions.
- Tagged captures for each variant and revision.

It stays the regression fixture for phases 6–9.

### Skill and docs

- The skill gains **revisions**: what is a revision versus a fingerprint, naming by what differs,
  gating, and why the ECU (not the car) decides.
- `docs/concepts/variants.md` (revisions, gating, pins), `docs/concepts/ecu-protocols.md` (an
  ECU's protocol can differ by revision), the regenerated CLI reference, `CHANGELOG.md`.

## Tests

- Resolution at the load seam for each combination in the fixture.
- Cache invalidation when the variant or a pin changes.
- Every validate rule, both passing and failing.
- Undetermined → skip.
- The byte-level refusal.
- Every editor, including rename-rewrites-everything.

## Acceptance

- Every bundled profile produces byte-identical output to phase 4, since none declares revisions.
  **The existing goldens are re-pointed, not regenerated.**
- The fixture exercises every rule above in CI.

## Out of scope

`per_revision:` field overrides and evidence routing (phase 7); fingerprint matching (phase 8);
the Ioniq BMS (phase 6).

## Risks

- **The load seam is load-bearing for every command.** Resolution must be a pure function of
  (raw documents, variant, revisions), cached on `cache_key`, so a wrong cache key is the main
  failure to test for.
