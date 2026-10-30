# Profile variants, phase 3 — capture tags and the `--variant` flag

Status: **DONE** (2026-10-31). Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[Captures](2026-10-30-profile-variants-and-rename.md#captures--every-session-says-which-car-it-came-from)
section). Depends on phase 2.

## Goal

Give captures a `variant` tag, stamp the existing bundled history, and let users opt in to tagging
with an explicit `--variant`. **Non-breaking:** without `--variant` every command behaves as it
does today, and new sessions are simply untagged. Phase 4 adds the remembered choice and the
refusal.

The split is deliberate. Everything here is additive, so it can land, be used and be corrected
before anything starts refusing.

## As built

Where the implementation differs from the plan below:

- **Analysis is scoped in one place.** `load_all_captures` applies the active profile's
  `--variant` scope (when no explicit `--dir` is given), so every analysis command honours it
  without touching `add_scope_args` or any command's filtering.
- **The variant is applied where the active profile is set** (`set_active`/`active`, through
  `canlib/variants/selection.py::apply_variant`), not inside `resolve_profile`, so resolving a
  profile by name for any other purpose is unaffected. `Profile` carries `variant` and
  `variant_source`.
- **Tags are stamped at the two write funnels**, not threaded through every session builder:
  `save_session` and the journal's meta, both via `current_capture_tag`. The journal fixes the
  tag when it is opened, and its reconcile saves without restamping, so a session recovered by a
  later run keeps the car it was recorded with.
- **Imports require the flag, not `CANAIR_VARIANT`.** An import is someone else's car, so an
  environment default meant for the user's own car does not count.
- **`migrate-variants` takes the variant as a positional ID** and its own
  `--date`/`--since`/`--until`/`--label`, since the full scope surface (states, last sessions)
  doesn't apply to stamping. It writes the profile's own store only.
- **The global `--variant` counts as a scope filter for `--set-variant`**, so
  `--variant untagged --set-variant 28kwh` tags every untagged session.
- **The DTC log is tagged at its single append funnel** from the active selection.
- **Bundled history:** 282 Ioniq sessions (`28kwh`), 5 Kona (`64kwh`) and 3 XPeng (`87kwh`)
  sessions were tagged; Leaf and Ioniq 5 stay untagged.

## What users see

- A global `--variant ID` and `CANAIR_VARIANT`. When given, `--save` tags the session, and
  analysis commands scope to that car's sessions.
- `captures uds --sessions` shows each session's variant, and marks untagged ones.
- `captures uds --set-variant` and `captures migrate-variants` for fixing or stamping tags.
- `import uds` and `import can` require `--variant` on a profile with more than one variant. This
  is the one new refusal in this phase: an import is someone else's car, so its tag can't be left
  to chance.

## Changes

### Data model

- `canlib/schema/captures_schema.json` and `canlib/captures/types.py`: optional session fields
  `variant` and `attribution` (`{variant: selected | migrated | asserted}` in this phase; phase 4
  adds `alias` and `only`).
- **Neither is an anchor field** (`captures/merge.py::session_anchor` stays unchanged), so editing
  a tag never breaks merge or contribute pairing.
- Same treatment for the raw-CAN index (`canlib/schema/can_index_schema.json`, a per-log
  `variant`) and `dtc_log.yaml` entries.

### The flag

- `canlib/cli.py`: `--variant` joins `_GLOBAL_OPTS_WITH_VALUE`, next to `--profile`; then
  `CANAIR_VARIANT`.
- It is validated against the profile's registry when the profile resolves. An undeclared variant
  is an error that lists the declared ones.
- It is carried on the resolved `Profile` as `variant` (no definitions depend on it yet).

### Writing tags

- Every session builder (`captures/__init__.py::build_query_session`, `build_scan_session`,
  `build_raw_session`, `build_discover_session`, `build_manual_session`) and the journal's
  `build_session_from_records` take the variant and attribution.
- `--save` with an explicit variant stamps `attribution.variant: selected`; without one, the
  session stays untagged, exactly as today.
- **`import uds`** (both forms) and **`import can`**: `--variant` is required when the profile
  declares more than one variant. The selected variant is never inherited, because an import is a
  different car.
- **`canair dtc`**: log entries record the variant when one is given.

### Stamping and fixing

- `canair captures migrate-variants --variant ID [scope]`, implemented in
  `canlib/migrations/variants.py`: stamps untagged sessions in scope with
  `attribution.variant: migrated`. `--dry-run` previews; it refuses a bare run with no scope or
  variant, and writes only the profile's own captures layer.
- `captures uds --set-variant ID` (`commands/captures/session_fields.py`, behind the
  `session_gate.py::refuse_write` gate): needs a scope filter, confirms, writes
  `attribution.variant: asserted`.
- `canair variants rename`/`remove` now rewrite (or refuse on) capture tags, the DTC log and the
  raw-CAN index, all or nothing, like `canlib/states/rename.py`.
- Run in this phase's commit, from provenance:

  | Profile | Stamp |
  |---|---|
  | `hyundai-ioniq-ae-ev` | every session → `28kwh` |
  | `hyundai-kona-os-ev` | every session → `64kwh` (two US Highlanders; US cars were 64 kWh only) |
  | `xpeng-g6-f30-ev` | every session → `87kwh` |
  | `nissan-leaf-ze1-ev`, `hyundai-ioniq-5-ne-ev` | left untagged (battery size never recorded) |

### Merging tags

In `canlib/captures/merge.py` (the merge driver and the contribute overlay) and the layered read in
`capture_store.load_all_captures`: a tagged and an untagged session with the same anchor and
otherwise identical content are one session, and the **tagged** copy wins. Without this, a user's
older untagged copy would "replace" the stamped upstream sessions in a contribution.

### Reading tags

- `captures/dates.py::add_scope_args`: the analysis scope honours the global `--variant`, which
  accepts `ID`, `all` or `untagged`. Without it, the scope is every session, as today.
- `validate captures` reports the untagged count for profiles with more than one variant. The CI
  ratchet arrives with phase 4, once the remembered choice keeps new sessions tagged.

### Skill and docs

- The skill gains **tagging discipline**: never import without `--variant`, fix tags with
  `--set-variant`, never by hand.
- `docs/concepts/captures-and-states.md` (tags, attribution, the migration),
  `docs/concepts/variants.md`, the regenerated CLI reference, `CHANGELOG.md` (the import
  requirement).

## Tests

- Builders and journal recovery carry the tag.
- Imports refuse without `--variant` on a multi-variant profile, and accept it on a single-variant
  one.
- `migrate-variants`: dry-run, scope required, idempotent, only writes the own layer.
- Tagged-wins rule: merge driver, contribute overlay, layered read.
- `variants rename` rewrites every reference or none.
- Analysis scope with `ID`, `all` and `untagged`.

## Acceptance

- The three stamped profiles have zero untagged sessions.
- With no `--variant`, every live and analysis command produces byte-identical output to phase 2,
  apart from the new `variant` column in `--sessions`.
- A `--save` with `--variant 28kwh` lands tagged, and `captures uds --sessions` shows it.

## Out of scope

Remembering a variant, the refusal, alias-implied variants and the CI ratchet (phase 4); ECU
revision tags (phase 5).

## Risks

- **A wrong migration stamp is worse than none.** Hence stamping only the three profiles whose
  provenance names the car, and recording `migrated` so the tag's source stays visible.
