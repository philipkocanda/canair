# Variants and ECU revisions

How one profile serves several cars, and how the code resolves the definitions for exactly one of
them. Read this page before you change the variant selection, the revision gating, the capture
tags or any loader of `ecus/`. The user-facing view is [Variants](../concepts/variants.md). The
naming rules are in the `profile-variants` skill. The design, phase by phase, is in
`plans/2026-10-30-profile-variants-and-rename.md`.

## Two levels

- A **variant** is a version of the *car* of the profile that an owner can name: a battery size, a
  facelift or a market. `variants.yaml` lists the variants with typed metadata, the electronic
  control unit (ECU) revisions that each car can carry (`revisions:`) and the `profile.yaml`
  settings that differ for it (`profile_overrides:`, a whitelist).
- An **ECU revision** is a version of *one ECU* that differs in protocol or decoding. An ECU file
  declares revisions under `revisions:`. A parameter identifier (PID) or signal gates on them with
  `revisions: [..]`. `variants: [..]` on an ECU says in which cars the ECU exists at all.

One check validates `variants.yaml`: `variants/__init__.py::check_variants_doc`. The loader,
`validate variants` and the editor (`yaml_edit/variants.py`) share it.

- The structure comes from `schema/variants_schema.yaml`. Its `profile_overrides` values `$ref` the
  `profile.yaml` rules.
- Python adds only the ID and pair-order checks.
- The editor types each command-line value from its `VARIANT_FIELDS` and
  `PROFILE_OVERRIDE_FIELDS` tables.
- `variants rename` rewrites every tag and every ECU `variants:` list that uses the ID.
  `variants rm` refuses while one does (`yaml_edit/variant_refs.py`).

## Which car: `variants/selection.py::apply_variant`

canair chooses the variant in this order. There is **no default**:

1. `--variant`
2. `CANAIR_VARIANT`
3. `profile_settings.<profile>.variant` in the user config (`profile use NAME --variant ID`)
4. the variant that an old profile name implies (an `aliases:` entry, read as `Profile.alias`)
5. the only variant, when the profile declares exactly one

`all` and `untagged` are scope keywords for analysis. Only the flag and the environment variable
accept them. canair carries the result on the `Profile` as `variant` and `variant_source`. The CLI
applies it once, when it resolves the active profile (`cli/profile_context.py`). A bare
`resolve_profile` never applies it.

**`require_variant` refuses.** It lists the variants and how to pick one. It refuses on a profile
with several variants when none is named. `cli/car.py::require_car` calls it for:

- every command that talks to the car (`cli/live/runtime.py::run_live`, except `discover` and
  `identity` without `--save`)
- `sniff`
- AutoPID generation

The test suite pins its car in the isolated config of `tests/conftest.py`.

Imports (`import uds`, `import can`) on such a profile **require the flag, not the env var**,
because the data is someone else's car (`selection.require_named_car`). An import drops the
revisions that the config pinned or a detection remembered.

## Which revision: resolved once, at the load seam

`ecus/pids.py::load_pids(profile)` returns definitions that canair has **already filtered and
overridden** for the selected variant and for the revision of each ECU. `load_raw_pids(profile)`
returns them as written. Code that works on one car reads `load_pids`. Code that must see every
car reads `load_raw_pids`: `validate`, the editors and anything that handles variants and revisions
itself. A profile without revisions or presence gets its raw objects back untouched.

canair resolves the revision of an ECU in this order, most authoritative first
(`variants/revisions.py::resolve`):

1. a pin: `--revision ECU=ID`, or `profile_settings.<p>.revisions` (`profile use --pin`)
2. the only possible revision: the selected variant allows one, or the ECU declares one
3. a **detection**, remembered in user state
4. **undetermined**. canair does not guess. It skips the revision-gated PIDs and signals of the ECU
   with one stderr note. The ungated ones still read.

`Profile.cache_key` covers the root, the variant, the pins and the detections, so no cache is
shared between two cars of one profile. Anything that keys a cache on a profile uses it.

### Detection

`live/revision_probe.py::detect_revisions` runs from `run_session_guarded`, before dispatch.

- For each ambiguous ECU that the current `read` or `monitor` steps target, it sends the cheapest
  `responds:` fingerprint. The matcher is the I/O-free `variants/fingerprints.py`.
- canair remembers a single match in user state (`variants/detected.py`), carries it as
  `Profile.revision_detections`, and reloads the definitions.
- canair never probes a determined ECU.
- A fingerprint is evidence, not a rule. No match, or several matches, leave the ECU
  undetermined.
- `canair identity` reports the matching revision and the variants that allow it
  (`identity_report`).
- AutoPID generation refuses while a revision is undetermined, because the device polls a fixed
  list and cannot detect a revision.

### `per_revision:`

`per_revision:` on a PID or a signal replaces whitelisted fields for one revision
(`PID_OVERRIDE_FIELDS` and `SIGNAL_OVERRIDE_FIELDS` in `variants/revisions.py`). canair applies it
after gating.

**The `verified` and `response_frames` of a definition speak for its reference revision.** That is
the first declared revision that the definition applies to (`reference_of`). Another revision gets
only what its `per_revision` entry says. An undetermined ECU keeps no count and verifies nothing.
The frame-count write-back routes the same way (`ecus/response_frames.py::revision_target`). One
revision's count therefore never seeds a read of another revision and undercounts it. See
[response frame counts](frame-counts.md).

`pids upsert-param --revision R` writes `per_revision.R` on an existing signal and gates a new
signal to `[R]`.

## Captures carry the car

- **Tagging.** canair tags a recording with `variant`, `revisions: {ECU: id}` for the captured
  ECUs that have several revisions, and an `attribution` (`selected`, `alias`, `only`). Every save
  funnels through `captures/save.py::save_session` and the meta of the journal, both via
  `current_capture_tag` and `captures/tags.py::capture_tag`. The tag applies only to the own store
  of the active profile. The journal fixes the tag when it opens, so a later `--recover` cannot
  retag.
- **Scoping.** `captures/store.py::load_all_captures` scopes analysis when no explicit `--dir` is
  given.

    - With a named car, it returns the sessions of that car plus the untagged ones.
    - With no car, it returns every tagged session and no untagged one. A once-per-process stderr
      note says how many untagged sessions canair left out. It also names the fix: `captures
      migrate-variants`.

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
| `ecu <ECU> rename` | also renames the key of the ECU in `revisions:` of every variant and in the capture revision tags |

The regression fixture is `tests/fixtures/profiles/variants/`.
