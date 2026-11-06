# Variants and ECU revisions

How one profile serves several cars, and how the code resolves the definitions for exactly one of
them. Read this page before you change the variant selection, the revision gating, the capture
tags or any loader of `ecus/`. The user-facing view is [Variants](../concepts/variants.md). The
naming rules are in the `profile-variants` skill. The phase-by-phase design is
`plans/2026-10-30-profile-variants-and-rename.md`.

## Two levels

- A **variant** is a version of the profile's *car* that an owner can name: a battery size, a
  facelift, a market. `variants.yaml` lists the variants with typed metadata, the ECU revisions
  each car can carry (`revisions:`) and the `profile.yaml` settings that differ for it
  (`profile_overrides:`, a whitelist).
- An **ECU revision** is a version of *one ECU* that differs in protocol or decoding. An ECU
  file declares them under `revisions:`. A PID or signal gates on them with `revisions: [..]`.
  `variants: [..]` on an ECU says in which cars the ECU exists at all.

One check validates `variants.yaml`: `variants/__init__.py::check_variants_doc`. The loader,
`validate variants` and the editor (`yaml_edit/variants.py`) share it. The structure comes from
`schema/variants_schema.yaml`. Its `profile_overrides` values `$ref` the `profile.yaml` rules.
Python adds only the ID and pair-order checks. The editor types each command-line value from its
`VARIANT_FIELDS` and `PROFILE_OVERRIDE_FIELDS` tables. `variants rename` rewrites every tag and
every ECU `variants:` list that uses the ID, and `variants rm` refuses while one does
(`yaml_edit/variant_refs.py`).

## Which car: `variants/selection.py::apply_variant`

The variant is chosen in this order, and there is **no default**:

1. `--variant`
2. `CANAIR_VARIANT`
3. `profile_settings.<profile>.variant` in the user config (`profile use NAME --variant ID`)
4. the variant that an old profile name implies (an `aliases:` entry, read as `Profile.alias`)
5. the only variant, when the profile declares exactly one

`all` and `untagged` are scope keywords for analysis. They are valid from the flag or the env var
only. The result is carried on the `Profile` as `variant` and `variant_source`. The CLI applies
it once, when it resolves the active profile (`cli/profile_context.py`). A bare
`resolve_profile` never applies it.

**`require_variant` refuses.** It lists the variants and how to pick one. `cli/car.py::require_car`
calls it for every command that talks to the car (`cli/live/runtime.py::run_live`, except
`discover` and `identity` without `--save`), for `sniff` and for AutoPID generation, on a profile
with several variants and none named. The test suite pins its car in the isolated config of
`tests/conftest.py`.

Imports (`import uds`, `import can`) on such a profile **require the flag, not the env var**,
because the data is someone else's car (`selection.require_named_car`). The revisions that the
config pinned or a detection remembered are dropped for an import.

## Which revision: resolved once, at the load seam

`ecus/pids.py::load_pids(profile)` returns definitions that are **already filtered and
overridden** for the selected variant and for each ECU's revision. `load_raw_pids(profile)`
returns them as written. The variant-aware readers use the raw loader: `validate`, the editors
and anything that must see every revision. A profile without revisions or presence gets its raw
objects back untouched.

The revision of an ECU is, most authoritative first (`variants/revisions.py::resolve`):

1. a pin: `--revision ECU=ID`, or `profile_settings.<p>.revisions` (`profile use --pin`)
2. the only possible revision: the selected variant allows one, or the ECU declares one
3. a **detection**, remembered in user state
4. **undetermined**. canair does not guess. It skips the ECU's revision-gated PIDs and signals
   with one stderr note, and the ungated ones still read.

`Profile.cache_key` covers the root, the variant, the pins and the detections, so no cache is
shared between two cars of one profile. Anything that keys a cache on a profile uses it.

### Detection

`live/revision_probe.py::detect_revisions` runs from `run_session_guarded`, before dispatch. For
each ambiguous ECU that the current `read` or `monitor` steps target, it sends the cheapest
`responds:` fingerprint. The matcher is the I/O-free `variants/fingerprints.py`. A single match
is remembered in user state (`variants/detected.py`), carried as `Profile.revision_detections`,
and the definitions are reloaded. A determined ECU is never probed. A fingerprint is evidence,
not a rule: no match, or several matches, leave the ECU undetermined. `canair identity` reports
the matching revision and the variants that allow it (`identity_report`). AutoPID generation
refuses while a revision is undetermined, because the device polls a fixed list and cannot
detect one.

### `per_revision:`

`per_revision:` on a PID or a signal replaces whitelisted fields for one revision
(`PID_OVERRIDE_FIELDS` and `SIGNAL_OVERRIDE_FIELDS` in `variants/revisions.py`). It is applied
after gating.

**A definition's own `verified` and `response_frames` speak for its reference revision**: the
first declared revision that it applies to (`reference_of`). Another revision gets only what its
`per_revision` entry says. An undetermined ECU keeps no count and verifies nothing. The
frame-count write-back routes the same way (`ecus/response_frames.py::revision_target`), so one
revision's count never seeds, and undercounts, a read of another. See
[response frame counts](frame-counts.md).

`pids upsert-param --revision R` writes `per_revision.R` on an existing signal and gates a new
signal to `[R]`.

## Captures carry the car

- **Tagging.** A recording is tagged with `variant`, `revisions: {ECU: id}` for the captured
  ECUs that have several revisions, and an `attribution` (`selected`, `alias`, `only`). Every save
  funnels through `captures/save.py::save_session` and the journal's meta, both via
  `current_capture_tag` and `captures/tags.py::capture_tag`. The tag applies only to the active
  profile's own store. The journal fixes the tag when it opens, so a later `--recover` cannot
  retag.
- **Scoping.** `captures/store.py::load_all_captures` scopes analysis when no explicit `--dir`
  is given. With a named car it returns that car's sessions plus the untagged ones. With no car it
  returns every tagged session and no untagged one, and a once-per-process stderr note says which.
  Analysis drops sessions that are tagged with another revision of a determined ECU
  (`variants/tags.py::revisions_in_scope`). The byte-level analyses refuse a scope that spans two
  revisions (`tags.require_one_revision`, through `load_signal_captures(one_revision=…)`).
- **Untagged copies.** An untagged copy of a tagged session is the same session with less
  information. The merge driver, the contribute overlay and the layered read keep the tagged copy
  (`variants/tags.py::strip_tag`).
- **Ratchet.** `validate captures --max-untagged N` fails when the untagged count grows.
  `captures migrate-variants ID` tags old history, attributed `migrated`, and never retags.

## Editors

| Command | Module |
|---|---|
| `variants …` | `yaml_edit/variants.py` |
| `ecu revisions`, `ecu set-variants` | `yaml_edit/revisions.py` (its `rename` rewrites gates, variants and capture tags) |
| `pids set-revisions` | the `pids` editors in `yaml_edit/` |
| `ecu <ECU> rename` | also renames the ECU's key in `revisions:` of every variant and in the capture revision tags |

The regression fixture is `tests/fixtures/profiles/variants/`.
