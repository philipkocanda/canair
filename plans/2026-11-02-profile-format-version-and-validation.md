# Profile format version, and validating every profile YAML file properly

Status: **PROPOSED** (2026-11-02).

## Problem

Two related gaps in how canair treats a profile bundle on disk.

**A profile does not say which format it is in.** Every format change so far (YAML→JSON
captures, `ecu`→`rx`, `ha_class`→`category`, `availability`→`vehicle_states`, `states.yaml`→
`vehicle_states.yaml`, folded captures) is detected by *shape*: each loader or validator knows the
legacy key names and either migrates them or complains. That has worked while canair owned every
profile, but profiles now live in user config, in overlays and in other people's forks. Shape
detection cannot tell canair that a profile is from a *newer* canair than the one reading it, it
spreads the knowledge of past formats across many loaders, and each new legacy shape adds another
check that runs forever.

**Validation is uneven and has real bugs.** An audit of `canair validate` against every YAML kind
in a bundle found:

- **Crashes.** A YAML syntax error in `vehicle_states.yaml`, `can_buses.yaml`, `groups.yaml`,
  `actions.yaml`, `signals/*.yaml` or `captures/can/index.yaml` raises a traceback (their targets
  call `safe_load` unguarded and `cli.py` does not catch `yaml.YAMLError`), and `validate all`
  skips every target after it. Reproduced on a copy of `xpeng-g6-f30-ev`.
- **Parked ECU files are never validated.** `_run_pids` asks for `_name.yaml` files
  (`include_disabled=True`, with a comment saying they are "still reported on"), but
  `collect_pids_validation` skips every `_`-prefixed file.
- **Settings files that are never checked.** An overlay's own `profile.yaml` (`extends:`) is never
  read by `validate` (`Profile.meta_file` is the base's). A variant's `profile_overrides:` replace
  `profile.yaml` settings but are only loosely typed (`quirks` need only be strings,
  `physical_bands` only a mapping), and the merged settings are never checked.
- **Duplicate-key errors dropped.** `_run_states`, `_run_can_buses` and `_run_groups` return early
  on a structural error before printing the duplicate-key errors they already collected (or before
  computing them).
- **Unknown keys tolerated.** A typo at the top level of `profile.yaml` (`respone_timeout_ms`),
  `vehicle_states.yaml`, `can_buses.yaml` or `groups.yaml` passes silently, and an unknown field in
  `ecus/` (ECU, PID, signal, iocontrol, research, scan_log, dtcs, sessions, hit sections) is only a
  warning. No bundled profile has such a field today (checked across all six), so an error costs
  them nothing.
- **Two descriptions of `profile.yaml`.** `validate_meta` hand-checks types, and the editor schema
  `canlib/schema/profile_schema.json` says the same thing again; only a pytest keeps them aligned,
  and they already disagree (`aliases: {old: null}` passes the schema and fails `validate`).
- **Schema files the code does not read.** `pids_schema.yaml`'s `iocontrol_fields`,
  `research_fields`, `routines_fields`, `iocontrol_discoveries_fields`, `revision_fields` and
  `valid_fingerprint_kinds` are re-hardcoded in Python and have drifted (the validator accepts
  iocontrol `vehicle_states` and hit-section `session`, the schema lists neither).
  `states_schema.yaml`, `can_buses_schema.yaml` and `groups_schema.yaml` are documentation with no
  test pinning them to the code.
- **Cross-references that disagree about the car.** `validate states` resolves predicates on every
  car; `validate groups` and `validate actions` check only the selected car's registry, and groups
  swallows a registry load failure (`except Exception`). `signals/` never checks `tx_ecu`. The CAN
  log index never checks that a listed file exists or that its `vehicle_states`/`variant` are in
  vocabulary, and only the write layer's index is checked.
- **Coverage outside CI.** The fixture profiles `single-frame` and `elm327-emulator` never get
  `validate all`, the `profile create` scaffold's `profile.yaml` never goes through `validate_meta`,
  and the `profile.yaml` modeline is checked only by pytest. `make check` claims to validate but
  does not run `canair validate`.
- **Hand-wired targets.** `validate all` lists its targets by hand, so a new `BUNDLE_MEMBERS` entry
  can land with no validator and nothing notices.

## Decisions

1. **One integer, `format_version`, in `profile.yaml`.** It names the on-disk format of the *whole
   bundle* (every `BUNDLE_MEMBERS` member: `profile.yaml`, `ecus/`, the state, bus, group, action
   and variant files, `signals/`, the capture JSON and the CAN log index), not a per-file number.
   One number keeps "is this profile readable?" a single comparison.

   **Not `version`**: next to `car_model`, `version: 1` reads as "release 1 of this car's data", and
   contributors would bump it for a new signal. Bumping the format number makes every older canair
   refuse the profile, so a content-flavoured name is a trap. (Content versioning is a separate
   question, out of scope here.)

2. **`format_version: 1` is the format as of this change**, after every existing shape-based
   migration (`captures migrate`, `migrate-rx`, `migrate-discover`, `split-batched`, `compact`,
   `pids migrate-categories`). Those stay as they are; only *future* format changes go through the
   new mechanism.

3. **A missing `format_version` means 1, never "current".** Every unversioned profile predates
   versioning, so it is format 1 by definition; reading it as current would skip the migrations
   once format 2 exists.

4. **What bumps it.** A change that means a profile in the old format would be read *wrongly* or
   must be rewritten: a key renamed, removed or moved; a value whose shape or meaning changes; a
   file moved or renamed; a change to the capture encoding. **An added optional field does not
   bump it** (an older canair's `validate` names the unknown field, which is the right message, and
   its loaders ignore it). A bump ships, in one commit: the step (one upgrade function per member
   it changes), the bundled profiles migrated, the old format frozen as
   `tests/fixtures/profiles/format-N/`, a decision on the merge driver's output if captures change,
   and a `!` subject (a profile-schema break, per `RELEASING.md`).

5. **Every format from 1 on stays readable.** A newer canair reads an older-format profile by
   upgrading it *in memory* as it loads, so upgrading canair never stops a profile from working.
   Dropping a format is itself a breaking change (`!`), announced one release ahead.

6. **Writes need the current format, and canair never rewrites a profile on its own.** An editor
   or a recording writes current-format data, and an old-format bundle holding some new-format
   data would be neither format. So a write into an older-format profile is refused, naming
   `canair profile migrate`. That migration is always explicit, because user profiles are often
   git-tracked or shared between machines.

7. **A newer format than canair knows is refused.** An older canair cannot know what a later format
   means, so it must not guess. Additive changes do not bump the number (Decision 4), which keeps
   this rare. Releases from before this change ignore `format_version`, and that cannot be fixed
   after the fact; the refusal starts with the release that introduces the field.

8. **Enforcement.** `validate` errors on a bundled profile with no `format_version` (CI) and warns
   on a user profile. Writers (`profile create`, `profile overlay`) always write it.

9. **Strictness.** Unknown keys become errors everywhere, and JSON Schema becomes the structural
   check for `profile.yaml`, so the editor schema and `validate` cannot disagree.

## Design

### Phase 1 — validation that never crashes and checks what it claims

- **One document loader for validators**: `validate/_common.py::load_doc(path, label)` returns the
  parsed data plus its errors (parse error, empty file, duplicate keys) and never raises. Every
  `_run_*` target uses it, and computes duplicate keys *before* any structural early return.
  `validate all` therefore always runs every target. `cli.py` stays unchanged: a parse error is a
  finding, not an exception.
- **Parked ECU files**: `collect_pids_validation` validates `_name.yaml` files per file, labelled
  `(parked)`, and leaves them out of the cross-file uniqueness checks (they are not loaded, so a
  name they share is not a conflict yet). The false comment goes.
- Tests: a syntax error in each file kind gives exit 1 with a `file: YAML parse error` line, and the
  targets after it still run; a parked file's error is reported.

### Phase 2 — `profile.yaml` checked against its schema

- `validate_meta` runs `canlib/schema/profile_schema.json` (Draft-07, `jsonschema` is already a
  dependency) for structure: types, ranges, enums and unknown keys. Root `additionalProperties`
  becomes `false`. Python keeps only what JSON Schema cannot express: `physical_bands` low < high,
  `rx_offset` int-only (the schema allows the YAML 1.2 `"-0x20"` string for editors, PyYAML does
  not), alias variants declared in `variants.yaml`, the naming convention and `dtcs:`. The
  hand-written type checks the schema now covers are removed. `tests/test_profile_schema.py` keeps
  pinning the schema's enums to `KNOWN_QUIRKS`, `ISOTP_FIELDS` and the addressing modes.
- Schema fix: an `aliases:` value must be a mapping (drop `null`), matching the validator.
- **Overlay `profile.yaml`**: each layer in `Profile.overlays` is validated with the overlay rule.
  It may hold only `extends` and `format_version`, `extends` must name the base, and the
  modeline is required. The schema's `extends` branch becomes that rule.
- **Variant overrides**: for every variant with `profile_overrides:`, the merged settings
  (`{**meta, **overrides}`) go through the same schema and semantic checks, reported as
  `variants.yaml: <id>: profile_overrides: …`. This replaces the loose `_overrides_problem` value
  checks; the whitelist of overridable fields stays in `canlib/variants/__init__.py`.
- **Modeline**: `validate` checks line 1 against `PROFILE_SCHEMA_MODELINE`, as an error for bundled
  profiles (like the naming rule) and a warning otherwise.

### Phase 3 — the format version

- **`canlib/profile_format.py`** (leaf, no `canlib.commands` imports): `CURRENT = 1`,
  `declared(meta) -> int | None`, `effective(meta) -> int` (missing → 1), and the migration registry
  `STEPS: dict[int, Step]`, one step per `N → N+1`. It is empty at format 1.
- **One step, two uses.** A `Step` holds a summary, per-member upgrade functions and any file
  renames. Each upgrade function mutates a document through the `MutableMapping`/`MutableSequence`
  interface only. That makes the *same* function work on a plain dict from PyYAML or JSON (the
  in-memory read) and on a ruamel `CommentedMap` (the on-disk migration, which keeps comments). So
  reading an old profile and migrating it cannot drift apart. A file rename is resolved by the read
  seam (as `Profile.states_file` already falls back to `states.yaml`) and performed by `migrate`.
- **The read seam.** `profile_format.upgraded(profile, member, doc)` applies
  `STEPS[effective] … STEPS[CURRENT-1]` for that member and is a no-op when the profile is current.
  Each loader passes through it: `read_meta`, `load_raw_pids`, `load_states`, `load_can_buses`,
  `load_groups`, `load_actions`, `load_variants`, `load_signals`, `capture_io.parse_capture_text`
  (the only capture parser) and `can_logs.load_index`. Code that reads profile YAML for some other
  purpose falls into one of three groups:
  - **Validators** validate the upgraded document, so an old profile is judged by current rules.
    They also report "format N, current M; `canair profile migrate` rewrites it" as information.
  - **Editors and anything else that rewrites a file** (`pids_edit/`, `ecus/`, `states/`, `groups`,
    `actions`, `variants`, `signals` editors, capture writes, `import`, `discover --register`, the
    captures `--set-*`/`--delete`/migrations) call `require_current_format(profile)` in their
    existing funnel (next to `require_writable_definitions`). A `--save` session checks it before
    connecting, so a recording is refused up front rather than lost at the end.
  - **Bundled-only readers** (the explorer export, the docs hooks) require the current format;
    CI keeps bundled profiles current.
- **Cost.** An upgrade runs on every load, which matters for a large capture store. Each step is
  expected to be cheap, `Profile.cache_key` includes the effective format, and the once-per-process
  note ("profile X is format N, read through an in-memory upgrade; `canair profile migrate X`
  rewrites it") points at the fix.
- **Schema**: `format_version` is an integer, `minimum: 1`, `maximum: CURRENT` (a test pins the
  maximum to `CURRENT`), and required in both the vehicle and the overlay branch. Being required
  in the editor schema nudges user profiles too, while `validate` only *warns* for them
  (Decision 8).
- **Gate at the one choke point**: `set_active` checks every layer of the resolved profile.
  - `format_version > CURRENT`: `ProfileError` — "profile X is format N; this canair reads up to M;
    run `canair update`". Commands that must work on any format opt out: `validate` (reports it as
    a finding), `profile list/show/path`, `status`, `config`, `update`. The opt-out is a module
    attribute on the command (`PROFILE_FORMAT_EXEMPT = True`) that `cli.py` reads, not a list in
    `cli.py`.
  - `< CURRENT`, or missing on a user profile: reads proceed through the seam, with the
    once-per-process note; writes are refused as above.
  - Layers are upgraded independently, so an overlay on a base of another format still reads.
    Writes go to the write layer, so only that layer must be current.
- **`canair profile migrate [NAME] [--dry-run] [--yes]`** (library code in
  `canlib/profile_format.py`, CLI in `commands/profile.py`):
  - With no `format_version`: before stamping `1`, runs the existing legacy-shape detectors (YAML
    captures, legacy `ecu` capture keys, retired ECU fields) and refuses, listing the commands to
    run, if any remain. Stamping `1` on a profile that is not format 1 would be a lie.
  - Otherwise it applies the same `STEPS` the read seam uses, in order, to the files on disk, and
    rewrites `format_version` last, so an interrupted run is retried from the right step. Each
    step lists the files it rewrites, those files are snapshotted first, and they are restored if
    the step or the following `validate all` fails.
  - Refuses an installed snapshot (`install_context`) and a base layer (an overlay migrates only its
    own layer).
  - The `profile.yaml` edit is comment-preserving (ruamel, `canlib/yaml_rt.py`), inserting the
    field after the header comment block.
  - `--json` reports the from/to format and the files touched.
- **Writers**: `templates/profile.yaml.tmpl` gains `format_version: $format_version` as its first
  key; `overlay_profile` writes it; `adopt` copies it unchanged.
- **Bundled data**: stamp the six bundled profiles and the three fixture profiles with
  `format_version: 1`, using `profile migrate`.
- **`contribute`**: its pre-flight requires `format_version == CURRENT` (the profile becomes a
  bundled one upstream), with `profile migrate` as the remedy.
- **Display**: `profile show` prints the format; `profile list` marks a profile that needs a
  migration or a newer canair.

#### Proving that old formats keep working

The read seam only helps if every reader goes through it. A reader that parses a file on its own
would quietly misread an old shape, and nothing would fail. So the guarantee is enforced by tests,
not by convention:

- **A frozen fixture per format**: `tests/fixtures/profiles/format-N/`, a small but complete
  profile (every `BUNDLE_MEMBERS` member, captures included) at format `N`. It is created when
  format `N` is superseded and never edited after that. A manifest of its file hashes is checked
  in, and a test fails if any file in it changes.
- **Equivalence**: for each frozen fixture, a set of read-only commands (`validate all`,
  `ecu`, `decode`, `captures uds --summary` and `--sessions`, `states`, `groups`, `variants`,
  `signals`, `coverage`, `investigate`, `wican autopid write` into a temporary directory) gives
  the same `--json`/text output on the fixture as read in place and on a copy migrated with
  `profile migrate`. A reader that bypasses the seam shows up as a difference.
- **Writes refused**: every write funnel refuses on the frozen fixture. This is a parametrized
  test over the editors and the capture writers.
- **Merge driver**: `captures merge-driver` parses both sides through `parse_capture_text`, so a
  step that changes the capture encoding must state which format the merged file is written in.
  That question is on the bump checklist (Decision 4).

Until format 2 exists the seam has nothing to do. So the first implementation also ships a
**test-only `STEPS[1]`** (registered by a fixture, never in the product) that renames a key in each
member. That proves the seam, the equivalence harness and the write refusal before any real
format change depends on them.
### Phase 4 — unknown keys are errors everywhere; schemas are read, not restated

- Unknown top-level keys are errors in `vehicle_states.yaml`, `can_buses.yaml` and `groups.yaml`.
- Unknown fields in `ecus/` (ECU, PID, signal, iocontrol, research, scan_log, dtcs, sessions,
  routines, iocontrol_discoveries) move from warning to error.
- **Edit guard**: `canlib/ecus/edit.py` and `commands/pids.py` revert an edit on *any* error in
  the file. With unknown fields now errors, a user file holding one old unknown field would block
  every edit to that ECU. So the guard compares errors before and after the edit, and reverts only
  on an error the edit *introduced*. While there: `canlib/ecus/edit.py` and
  `canlib/states/rename.py` import `canlib.commands.validate`, a library module importing up into
  the command layer. Move `validate_pids_file`/`collect_pids_validation` down to
  `canlib/validation/` (the home `signals.py` already uses) and re-export them from the command
  package.
- **`pids_schema.yaml` becomes the single source** for the sections the code re-hardcodes: the
  validator reads `iocontrol_fields`, `research_fields` (types, statuses, priorities),
  `routines_fields`, `iocontrol_discoveries_fields`, `revision_fields` and
  `valid_fingerprint_kinds` through `_SchemaFields`. The schema gains the two drifted fields
  (iocontrol `vehicle_states`, hit-section `session`).
- `states_schema.yaml`, `can_buses_schema.yaml` and `groups_schema.yaml` get drift tests pinning
  their field lists to the validators, as `variants`/`signals` already have. They are documentation
  in a schema-like layout rather than machine schemas, so pinning beats rewriting them.

### Phase 5 — cross-references, the registry, coverage

- `validate groups` and `validate actions` check members and requests against **every car's**
  registry (`load_raw_pids()` plus the per-variant resolution `states` uses), and stop swallowing a
  registry load failure.
- `signals/`: `tx_ecu` must name a registered ECU or alias — a **warning**, because imported DBCs
  carry node names of ECUs no profile registers. (The file stem is a logical bus name by design, per
  `signals_schema.yaml`, so it is *not* checked against `can_buses.yaml`.)
- CAN log index: a listed `file` must exist (an LFS pointer counts); its `vehicle_states` and
  `variant` must be in vocabulary; every capture layer's index is checked, as JSON captures are.
- `ecus/`: a warning when a file's stem does not match its ECU key (all bundled files match).
- **Registry-driven `validate`**: `BundleMember` gains `validator: str | None` (the target name).
  `validate all` and the `target` choices derive from `BUNDLE_MEMBERS`. A test requires every
  `definition` and `evidence` member to name one, so a new member cannot land unvalidated.
  `references` and `out` are explicitly `None`.
- **Coverage**: a parametrized test runs `validate all` on every profile under
  `tests/fixtures/profiles/` and on a fresh `profile create` scaffold (which brings the scaffold's
  `profile.yaml` under `validate_meta`). Bundled profiles stay in the existing CI loop.
- `make check`: fix its help text (or add the bundled-profile `validate` loop); CI keeps the
  ratchet flags.

## Docs

- `docs/concepts/profiles.md` ("`profile.yaml` settings"): `format_version`, what it means, the
  compatibility promise (older formats read, newer refused, writes need `profile migrate`).
- `docs/development/`: a "changing the profile format" page (the bump rule and checklist from
  Decision 4, how to write an upgrade function against the mapping interface, freezing the
  fixture), linked from the **contributing-code** skill.
- `RELEASING.md`: a format bump is a profile-schema break.
- `AGENTS.md`: the `validate` target list (it omits `actions` and `variants`), the `profile.yaml`
  Key Files entry, and the Profiles section (the gate and `profile migrate`).
- `validate --help` epilog ("pids + captures + states" is stale) and `profile migrate --help`;
  regenerate `docs/reference/cli/`.
- `templates/profile.yaml.tmpl` header comment.

## Tests

- `tests/test_profile_format.py`: missing → 1; refusal of a newer format, and the exempt commands
  still running on one; an older format read through the seam, layer by layer; `profile migrate`
  stamps, refuses legacy shapes, refuses snapshots and base layers, is idempotent, and `--dry-run`
  writes nothing. The test-only `STEPS[1]` exercises ordering, the snapshot/restore, the "version
  written last" rule and the dict/ruamel equivalence of one upgrade function.
- The frozen-fixture equivalence and write-refusal tests above.
- Schema `maximum` pinned to `CURRENT`; every bundled and fixture profile and the scaffold at
  `CURRENT`.
- The regression tests from Phases 1, 2, 4 and 5 listed above.

## Out of scope

- A YAML lint or 100-column check in CI (566 overlong lines today, mostly `signals/` and `ecus/`).
- Content versioning of profile data.
- A freshness check for the committed `out/` AutoPID files.
- Moving the existing shape-based migrations into `STEPS`.
- `profiles/hyundai-ioniq-ae-ev/research/` and `profiles/xpeng-g6-f30-ev/KNOWN-ISSUES.md` are not
  `BUNDLE_MEMBERS`, so `adopt`, `contribute` and the blind strip ignore them. That is worth deciding
  on, but it is separate from this plan.
