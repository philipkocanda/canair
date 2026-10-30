# Profile variants, phase 2 — the variant registry (metadata only)

Status: **DONE** (2026-10-30). Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[Variants](2026-10-30-profile-variants-and-rename.md#variants-variantsyaml) section). Depends on
phase 1.

## As built

Where the implementation differs from the plan below:

- **No cache.** `load_variants` reads the file on each call, like `load_groups`; nothing derives
  state from it yet, so there is nothing to invalidate. Register a derived cache with
  `pids.register_derived_cache` once something does.
- **The field table lives in Python** (`canlib/variants/__init__.py::VARIANT_FIELDS`), with the
  schema YAML as its documented companion pinned by a test, following the signals precedent.
- **IDs are `-`-separated words** (`^[a-z0-9]+(-[a-z0-9]+)*$`, at least one letter): no leading,
  trailing or doubled `-`.
- **The battery check accepts gross capacity.** Makers quote either, so a battery-named ID is
  checked against `usable_kwh` when given, else `gross_kwh`.
- **`sources` takes one item per `sources=…`**, repeated for more, since sources contain commas;
  `markets` and `trims` stay comma-separated.
- **`profile list` shows no variant count**; `profile show` and the explorer's toolbar list the
  variants.

## Goal

Add `variants.yaml`: each variant's ID and typed metadata, editable through `canair variants`, and
shown by `profile show`, `profile list` and the explorer. **Purely additive.** Nothing selects a
variant, gates a definition, or tags a capture yet, so no command's behaviour changes.

## What users see

- `canair variants` (list / show) and its editors.
- `profile show` lists the variants: name, production window, battery, status.
- The explorer shows the same metadata on each profile's page.
- Nothing else.

## Changes

### The file and its loader

- Schema `canlib/schema/variants_schema.yaml`. The variant ID pattern is `^[a-z0-9][a-z0-9-]*$`
  with at least one letter. The typed metadata core is `name`, `model_code`, `designation`,
  `generation`, `production` (`YYYY[-MM]` pair), `model_years`, `markets`,
  `battery{usable_kwh, gross_kwh, series_cells, chemistry, cooling}`, `trims`, `status`
  (`verified | community | seed`), `sources`, `notes`.
- Keys a later phase owns (`revisions:`, `profile_overrides:`) are **rejected** here, so nobody
  writes data that nothing reads.
- Loader `canlib/variants/__init__.py::load_variants(profile=None)`, the standard
  `load_x(profile)` shape, memoised on `Profile.cache_key` and registered with
  `pids.register_derived_cache`.
- `BUNDLE_MEMBERS` gains `variants.yaml` (role `definition`), so contribute, the blind strip and
  `profile show` pick it up.
- `canair validate variants` (and in `validate all`): schema, ID pattern, ID uniqueness,
  `production` ordering, `usable_kwh` agreeing with the ID's battery number.

### Editing — `canlib/variants/edit.py` + `canair variants`

Same discipline as `canlib/states/edit.py`: comment-preserving, schema-validated,
auto-reverted, echoed through `edit_echo.echo_edit`, refused on a layered profile by
`require_writable_definitions`.

- `list`, `show ID`, `add ID --name … [--meta key=value …]`, `set-meta ID key=value`,
  `rename OLD NEW`, `remove ID`.
- In this phase `rename`/`remove` have no references to rewrite; later phases extend them (capture
  tags, revisions).
- **Never hand-edit `variants.yaml`.**

### Seed the five profiles

Every value cites a source; unknown values are left out, never guessed.

| Profile | Variants |
|---|---|
| `hyundai-ioniq-ae-ev` | `28kwh` (`verified`; model code `AEEV`; air-cooled 96S pack), `38kwh` (`seed`; designation "IONIQ EV PE"; production from ~2019-06) |
| `hyundai-kona-os-ev` | `64kwh` (`community`, 98S, from the seed's sources), `39kwh` (`seed`) |
| `hyundai-ioniq-5-ne-ev` | `58kwh`, `77kwh`, `84kwh` (all `seed`) |
| `nissan-leaf-ze1-ev` | `40kwh` (`community`), `62kwh` (`seed`) |
| `xpeng-g6-f30-ev` | `87kwh` (`community`, 150S by pack voltage), `66kwh` (`seed`) |

The battery comments that phase 1 kept in each `profile.yaml` move into this metadata.

### Display

- `profile show`: a `variants:` block.
- `profile list`: the variant count per profile.
- `canlib/explorer/export.py`: an allow-listed `variants` record per profile (metadata only, with
  `src` line references), rendered on the profile page by `docs/assets/explorer/explorer.js`. Add
  the field to `tests/test_explorer_export.py`.

### Skill and docs

- The `profile-variants` skill gains the **variant** rules: when a difference is a variant (the
  owner can name it, and it changes something), battery-based IDs, what metadata is for, never a
  year.
- New `docs/concepts/variants.md` (the registry part only); `docs/concepts/profiles.md` bundle
  layout; regenerated CLI reference; `CHANGELOG.md`.

## Tests

- Schema: good and bad IDs (bare number, uppercase input normalised, year-like ID allowed by the
  pattern but caught by the `usable_kwh` agreement), rejected later-phase keys.
- Editor round-trips preserve comments; failed edits revert.
- `validate all` on each bundled profile; explorer export snapshot.

## Acceptance

- Every bundled profile carries a `variants.yaml` that validates.
- Every command except `variants`, `profile show/list` and the explorer produces byte-identical
  output to phase 1.

## Out of scope

Selecting a variant, tagging captures (phases 3–4), `revisions:` and `profile_overrides:`
(phase 5).

## Risks

- **Metadata nobody reads drifts.** That is why the schema is fixed and `validate` checks the
  battery figure against the ID: the display surfaces are the readers.
