# Per-revision ECU identity

Status: **PLANNED** (2026-11-01). Follows the profile variants work
([`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md), phases
5, 7 and 8) and commit `9819fbb`, which stopped one revision's identity showing on another.

## Problem

An ECU's `identity:` block records what one physical unit reported: part number, calibration,
ECU id, SW id, notes. On an ECU with revisions, those values come from whichever car was read,
which is in practice the reference (first declared) revision. On the Ioniq, `ecus/bms.yaml`
holds the 28 kWh car's `kwp` BMS.

Since `9819fbb`, the load seam (`canlib/variants/revisions.py::_overridden`) withholds those
values from any other determined revision and keeps only `description`, `alias` and
`id_protocol`. `canair ecu BMS` on the 38 kWh car says "None recorded for the uds revision".
That is correct, but it leaves no way to record the `uds` BMS's identity once someone reads one:

- `revisions.<r>.identity` accepts only `id_protocol` (`_entry_errors`: "identity.part_number
  cannot differ by revision").
- The top-level `identity:` is the reference revision's evidence, and overwriting it would lose
  the 28 kWh values.
- `pids set-identity` writes the top-level block only.

## Goal

Let each revision of an ECU carry its own recorded identity, through the editors, with the
load seam resolving the right one for the selected car. The model is the same one `verified`
and `response_frames` already follow: **the base speaks for the reference revision, and another
revision has only what its own entry says.**

## Design

### Where the values live

A revision's existing `identity:` override widens from `id_protocol` only to every field in
`identity_fields` (`canlib/schema/pids_schema.yaml`):

```yaml
BMS:
  identity:                       # the reference revision's (kwp) recorded identity
    description: Battery Management System
    part_number: "37501-G7250"
    calibration: "4604"
    id_protocol: KWP2000
  revisions:
    kwp:
      identity: {id_protocol: KWP2000}
      fingerprints: [...]
    uds:
      identity:                   # recorded from a uds unit
        id_protocol: UDS
        part_number: "37501-XXXXX"
        sw_id: "..."
      fingerprints: [...]
```

Rejected alternatives:

- **Move every revision's values under `revisions.<r>.identity`, the reference's too.** It looks
  more symmetric, but it means migrating every ECU that gains a second revision and breaks the
  "base speaks for the reference" rule the other evidence fields follow. `per_revision:` on PIDs
  and signals made the same choice.
- **A separate `identities:` map keyed by revision.** It adds a second key for what a revision's
  `identity:` override already expresses.
- **Fingerprint globs as the record.** Fingerprints are patterns for matching (`36601-0E1*`), not
  readings. A value can be both (BMS calibration `4604` is a `kwp` fingerprint *and* its recorded
  calibration), and they stay separate.

### Resolution (`_overridden`)

For a determined revision `r`:

| `r` is | Resolved `identity` |
|---|---|
| the reference | base, then `revisions.r.identity` laid over it field by field (today's behaviour) |
| another revision | base's general fields (`GENERAL_IDENTITY_FIELDS`: `description`, `alias`, `id_protocol`), then `revisions.r.identity` laid over them |

An **undetermined** ECU keeps the base unchanged, as it does today. `canair ecu` already notes
that those values come from the reference revision.

`identity_confidence` is not general: a confidence earned on one revision says nothing about
another, so it is withheld like the other recorded fields.

### Readers that must see every revision

These read the raw files, not the resolved definitions, so a value under `revisions.<r>.identity`
would be invisible to them. Each one needs to walk the revision entries too:

- **`canlib/pii.py::_scan_ecus`, the contribute PII gate.** This is the important one. It scans
  only `ecu["identity"]`, so a VIN or free-text PII written under a revision would reach a PR
  unscanned. Report the location as `ecus/ BMS revisions.uds.identity.vin`.
- **`canlib/blind.py::_strip_ecu_doc`**: strip `_STRIP_IDENTITY_FIELDS` from each revision's
  identity too, or the blind test leaks `notes`/`serial`.
- **`canlib/commands/search/haystack.py::identity_rows`** (`search --defs`): include revision
  identities, labelled `identity[uds].part_number`, so a search finds them.
- **`canlib/explorer/export.py`**: no change. It already exports only `id_protocol` per revision
  and never identity values. Add a test pinning that.
- **`canlib/variants/fingerprints.py`**: no change. It matches live readings, not recorded ones.

### Validation (`validate pids`)

- `_entry_errors` accepts any `identity_fields` key under `revisions.<r>.identity` and rejects
  unknown ones, with the same message the top-level block uses.
- **New warning: recorded identity contradicting the fingerprints.** If a revision's resolved
  recorded values match another revision's `identity:` fingerprint and none of its own, the
  values were probably filed under the wrong revision. For example, `calibration: "4604"`
  recorded under `uds` matches a `kwp` fingerprint. It is a warning, not an error, because
  fingerprints are evidence and can be incomplete.
- The existing synonym check (two identity fields holding the same value) runs per resolved
  revision.

### Editors

- **`pids set-identity ECU FIELD VALUE --revision R`** and **`pids rm-identity … --revision R`**.
  Routing follows `response_frames.py::revision_target`: the reference revision writes the base
  block (today's text editor), and another revision writes `revisions.R.identity` through the
  ruamel path in `canlib/ecus/revisions_edit.py` (a new `set_identity`/`remove_identity` beside
  `set_override`). Validation and auto-revert work as they do for every other `pids` edit.
- `--revision` naming an undeclared revision is an error. Without `--revision` on an ECU that
  has revisions, the command writes the base as today and prints a one-line note that the value
  now speaks for the reference revision, naming it. It does not refuse, because most identity
  edits (`notes`, `description`) are deliberately base-level.
- `ecu revisions rename`/`rm` already walk a revision entry's keys. Add a test showing the
  identity override moves with a rename and counts as a reference for `rm`.

### `canair ecu` display

- `_identity_note` in `canlib/commands/ecu.py` says "None recorded for the uds revision" only
  when that revision has no recorded fields of its own. Once it has some, the values are shown
  with no note.
- `--json` keeps `identity` and `identity_note`. It also gains `identity_revision` (the
  revision the shown values speak for, or `null` for an ECU without revisions).

## Docs

- `docs/concepts/variants.md#ecu-revisions`: replace the bullet added in `9819fbb` with the
  routing table above and the `pids set-identity --revision` example.
- `profile-variants` skill: "Evidence belongs to a revision" gains identity, with the
  `--revision` editor.
- `canlib/schema/pids_schema.yaml`: the revision comment ("identity: only id_protocol") and the
  `revision_fields` description.
- `docs/reference/cli/pids.md` is regenerated.

Boy Scout fixes in the same area:

- The schema comment says fingerprints are "not yet matched against a car", but phase 8 built
  detection.
- `canlib/pii.py`'s docstrings say `canair identity` writes a live VIN into `ecus/`. It writes
  nothing; phase 7 already corrected AGENTS.md on this. The VIN reaches `identity:` through
  `pids set-identity`/`ecu add`, and the docstrings should say so.

## Tests

The synthetic fixture `tests/fixtures/profiles/variants/` already gives `VCU` a recorded
`part_number`/`calibration` on `kwp`. Add a recorded `uds` identity to it.

- Resolution: reference, other revision with and without its own values, undetermined. Recorded
  fields never cross from one revision to the other in either direction.
- `validate pids`: an unknown field under a revision identity is an error; the fingerprint
  contradiction warning fires and stays quiet in the right cases.
- `pids set-identity`/`rm-identity --revision`: reference routes to the base, another revision
  to `revisions.R.identity`, an undeclared revision errors, comments survive, auto-revert on a
  broken result.
- PII gate: a VIN under `revisions.uds.identity` is found and named by location. This test
  must fail before the `pii.py` change.
- Blind strip and `search --defs` cover revision identities.
- `canair ecu` note and `--json` `identity_revision` for each case.
- Invariant: on the bundled Ioniq, `--variant 28kwh` output is unchanged, and so is every
  profile without revisions (`load_pids()["ecus"] is load_raw_pids()["ecus"]` still holds).

## Acceptance

- After `pids set-identity BMS part_number 37501-XXXXX --revision uds --profile
  hyundai-ioniq-ae-ev`, `canair --variant 38kwh ecu BMS` shows that part number and the 28 kWh
  car still shows `37501-G7250`.
- `canair contribute --diff` flags a VIN placed under a revision identity.
- `uv run canair validate all` is green for every bundled profile.

## Out of scope

- **Writing identity from a live read** (`canair identity BMS --record`, filing each field under
  the revision the reading's fingerprints match, and reporting a reading that matches none as a
  possible new revision). The original variants plan wanted this and phase 7 deferred it,
  because it needs a DID → identity-field map. This plan provides the storage and editor it
  would write through. It is a follow-up.
- Per-variant identity for ECUs without revisions. An ECU that is identical on two cars has one
  identity by definition. If its identity differs, that difference is a fingerprint or a reason
  for a revision.
- Seeding the Ioniq `uds` BMS identity. Nobody has read one. Values from OVMS or forum posts
  are not readings of this car's revision and must not be recorded as such.

## Risks

- **A PII leak path through an unscanned location.** Every new place a value can live has to be
  added to the PII gate, the blind strip and search in the same change. The PII test above is
  the guard.
- **Misfiled readings.** An identity recorded under the wrong revision would show on the wrong
  car, which is the bug `9819fbb` fixed, coming back through the editor. The fingerprint
  contradiction warning is the backstop. The skill must say to run `canair identity ECU` first
  and record under the revision it names.
