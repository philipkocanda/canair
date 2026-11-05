# Check the shape of profile files with JSON Schema

Status: **IN PROGRESS** (2026-11-05). Steps 1 and 2 are done. Step 3 is open.

## Problem

`canair validate` checks most profile files with hand-written Python: one `isinstance` test, one
enum test and one "unknown field" loop per field. Three kinds of "schema" sit next to that code:

| Kind | Files | Who checks the data |
|---|---|---|
| JSON Schema, used to validate | `captures_schema.json`, `can_index_schema.json` | `jsonschema` |
| JSON Schema, used only by editors | `profile_schema.json` | `validation/meta.py` checks the same rules again |
| YAML field lists with comments | `pids_schema.yaml`, `signals_schema.yaml`, `states_schema.yaml`, `groups_schema.yaml`, `can_buses_schema.yaml`, `variants_schema.yaml` | hand-written Python |

The result:

- **Two descriptions of one file.** `profile_schema.json` and `validate_meta` say the same thing,
  and only a pytest keeps them in agreement. They already disagree: `aliases: {old: null}` passes
  the schema and fails `validate`.
- **Companions that nothing reads.** The YAML field lists document the files for humans. The
  validators re-state every list in Python, and tests pin the two copies together.
- **Repeated boilerplate.** `bool` is an `int` subclass, so every integer check carries an
  `isinstance(x, bool)` guard. The code repeats that guard dozens of times.
- **No editor help** for any file but `profile.yaml`.

## Decisions

1. **JSON Schema checks the shape, Python checks the meaning.** The schema owns types, enums,
   required fields, unknown fields, ranges, patterns and the rules between two fields of one
   mapping (`type: enum` needs `values:`). Python keeps what a schema cannot say:
   - references to another file (state vocabulary, `can_bus` codes, ECU names, predicate signals);
   - rules that depend on the profile (the 11-bit or 29-bit id limit follows the addressing mode);
   - parsing (`when:` predicates, selectors, WiCAN expressions, support maps);
   - graph rules (`implies:` cycles, `excludes:` conflicts) and order rules (`low < high`, a
     `[from, to]` pair that ends before it starts);
   - YAML traps that the parsed data no longer shows (duplicate keys, octal PID keys);
   - errors for a legacy field that name the migration command to run.
2. **One helper in `core/`:** `core/json_schema.py::schema_errors(name, data)` returns readable
   findings and never raises. It caches one validator per schema and resolves `$ref`s between
   canair's schemas through a `referencing` registry built from their `$id`s. Its phrasing:
   - `path: unknown field 'x' (allowed: a, b)`, `path: missing required field 'x'`,
     `path: must be an integer (got 'abc')`, `path: must be one of a, b (got 'c')`;
   - a path is dotted, with `[i]` for a list index (`messages.0x220.signals.GEAR.length`);
   - a boolean where text is expected says that YAML reads an unquoted `ON`/`OFF`/`YES`/`NO` as a
     boolean, because that is nearly always the cause;
   - a subschema may carry `errorMessage` (any failure in it) or `patternErrorMessage` (a failed
     `pattern`). These are the extension keywords VS Code and yaml-language-server already show,
     so canair and the editor give the same message.
3. **Draft-07 for every schema an editor reads.** yaml-language-server supports draft-07 best.
   The capture schemas are not editor-facing and stay on 2020-12. The helper picks the validator
   from each schema's `$schema`.
4. **A schema with comments is written in YAML.** The human documentation that the old companion
   files carry stays as comments in the same file, next to the rules. `profile_schema.json` stays
   JSON. The docs hook (`scripts/mkdocs_hooks/schemas.py`) publishes every editor-facing schema
   as JSON at `schema/<name>.json`. Each schema's `$id` is that URL, so a cross-file `$ref`
   (`profile.json#/properties/quirks`) resolves the same way in canair and in the editor.
5. **Map keys are text.** YAML reads `values: {0: off}` with an integer key, and JSON Schema
   expects string keys. The helper turns every key into text before it validates, and a key rule
   uses `propertyNames`.
6. **Python tables that drive code stay.** `SIGNAL_FIELDS` and `VARIANT_FIELDS` set the order an
   editor writes fields in, and `ISOTP_FIELDS`, `KNOWN_QUIRKS` and `PROFILE_OVERRIDE_FIELDS` drive
   the runtime and the editors. A test pins each schema to the table it mirrors.
7. **Unknown top-level keys are errors** in `vehicle_states.yaml`, `can_buses.yaml` and
   `groups.yaml`, as they already are in `variants.yaml` and `signals/`. No bundled or fixture
   profile has one. `profile.yaml` stays open at the top level: it is extensible by design, and
   the format-version proposal (`plans/2026-11-02-profile-format-version-and-validation.md`)
   decides when that changes.
8. **Every file names its schema.** A `yaml-language-server` modeline is line 1 of each
   `vehicle_states.yaml`, `can_buses.yaml`, `groups.yaml`, `variants.yaml` and `signals/*.yaml`,
   as it already is for `profile.yaml`. The templates write it, and so does each editor that
   creates such a file (`Target.header`). The modelines and URLs live in `core/paths.py`
   (`EDITOR_SCHEMAS`).

## Step 1 — `profile.yaml` (done)

- `validate_meta` runs `profile_schema.json` through the helper. The hand-written checks of
  `car_model`, `init`, `response_timeout_ms`, `can_bitrate`, `multi_did_*`, `addressing`, `isotp`,
  `quirks`, `physical_bands` and `unit_guess_candidates` are gone.
- Python keeps: `physical_bands` low < high, and `addressing.rx_offset` as an integer (the schema
  also allows the string `"-0x20"`, which is how a YAML 1.2 editor reads `-0x20`; PyYAML reads an
  int, so a string means the value was quoted).
- Schema changes:
  - The root says `car_model` and `init` are required unless the file has `extends:` (an
    overlay), with `if`/`then`/`else`. That gives the plain "missing required field" message, so
    `required_meta_fields` leaves `pids_schema.yaml` and `validate_meta` loses its argument.
  - `aliases:` values must be mappings with only `variant` (the validator always required that).
  - `also_known_as` items must hold a non-space character, and must not repeat.
  - `quirks` items accept a retired quirk (`RETIRED_QUIRKS`), marked deprecated: `validate`
    warns about one, it does not fail.
- `profile_names.py` drops the type checks of `also_known_as` and `aliases`, which the schema now
  makes. It keeps the duplicate-name, declared-variant and alias-conflict checks.

## Step 2 — the smaller profile files (done)

Each companion file becomes a real draft-07 schema, written in YAML, and keeps its comments:

- **`can_buses_schema.yaml`**: the mapping form (code → name, description, positive `bitrate`,
  boolean `private`, or a bare string) and the legacy list form. Codes must not be empty or
  repeat. Python keeps only the count for the summary line.
- **`groups_schema.yaml`**: a group is a non-empty list of selectors or a mapping with
  `description` and a required non-empty `members`. A member cannot start with `@`. Python keeps
  the lower-cased duplicate check, selector parsing and the ECU registry check.
- **`states_schema.yaml`**: each state needs a non-empty `name`. `implies`/`excludes` are a list
  of names or one comma-separated string. Python keeps the duplicate check, predicate parsing, the
  relation rules and the predicate references.
- **`signals_schema.yaml`**: top level, messages and signals, including the 29-bit id limit (a
  pattern), the legal data lengths, `scale` not 0, a float's 32 or 64 bits and the typed-decoding
  pairs (`values` only with `type: enum`, `bits` only with `type: bitmask`, keys 0-63). Python
  keeps the frame-layout rules: an 11-bit id above `0x7FF`, a classic frame over 8 bytes and the
  signal span against the length. `check_signals_doc` keeps its signature, so the editor, `import
  dbc` and `export dbc` are unchanged.
- **`variants_schema.yaml`**: the entry fields, the battery block and the shapes of
  `production`/`model_years`/`revisions`. A `profile_overrides` value now uses the
  `profile.yaml` rule for that key (`$ref: profile.json#/properties/<key>`), so an override of
  `quirks` is as strict as `profile.yaml` itself. Python keeps: variant and revision IDs (they are
  lower-cased before the check, and the rule is shared with ECU revisions), pair order and the
  battery-named ID against its kWh.
- The `validation/` checks that read these files report schema findings first, then run the
  Python checks on the parts that are well-formed.
- The editors' own post-write checks (`yaml_edit/states.py`, `yaml_edit/groups.py`) are not
  changed here: making them stricter would refuse an edit to a file that already holds an old
  unknown field. The format-version proposal (its Phase 4 "edit guard") is where that is decided.

## Step 3 — the ECU files (open)

`pids_schema.yaml` becomes a draft-07 schema for one ECU file. This is the largest step:

- Severity: an unknown ECU, PID or signal field is a warning today. The helper needs a way to
  report a keyword as a warning (`additionalProperties` → warning), unless the format-version
  plan makes unknown fields errors first.
- `SchemaFields` and `schema_fields.py` go. The Python copies of the vocabularies
  (`expression/decode_value.py` signal types, `ecus/wake.py` wake methods, the identity fields in
  `ecus/__init__.py` and `yaml_edit/ecu.py`) are pinned to the schema by tests, or read from it.
- PID keys need care: YAML reads `0105` as the octal integer 69, and that check must stay in
  Python before the keys become text.
- The per-ECU `addressing:` block shares its rules with `profile.yaml` through a `$ref`.
- Python keeps the mode-dependent id limit, state and bus vocabularies, expressions, support
  maps, categories, revisions and the cross-file checks.

## Relation to the format-version proposal

`plans/2026-11-02-profile-format-version-and-validation.md` (status PROPOSED) covers part of the
same ground:

- Its Phase 2 ("`profile.yaml` checked against its schema") is Step 1 here, without the root
  `additionalProperties: false`, the overlay `profile.yaml` rule and the merged variant overrides.
  Step 2 gives the variant overrides the schema's per-key rules.
- Its Phase 4 says the `states`, `can_buses` and `groups` companions should get drift tests
  rather than become machine schemas. This plan replaces that bullet: once the companions are the
  schemas, there is no second copy to drift from.
- Its Phase 4 "unknown keys are errors" is done for the three small files (Decision 7).

## Tests

- `tests/core/test_json_schema.py`: the phrasing of each keyword, `errorMessage` and
  `patternErrorMessage`, text keys, the boolean hint, a cross-file `$ref`, and that every schema
  in `src/canair/schema/` is a valid schema of its draft.
- `tests/contracts/test_profile_schema.py` keeps pinning `profile_schema.json` to
  `KNOWN_QUIRKS`, `RETIRED_QUIRKS`, `ISOTP_FIELDS` and the addressing modes.
- The signals and variants pin tests read the new schemas.
- `tests/contracts/test_profile_file_schemas.py`: every bundled and fixture file of each kind
  conforms to its schema and names it on line 1, and so do the files `profile create` writes.
  `tests/yaml_edit/test_yaml_edit_targets.py` pins each editor's `Target.header` to its modeline.
- The existing validator tests are updated to the new messages and still cover each rule.
- `validate all` on every bundled and fixture profile gives the same result before and after.

## Docs

- `docs/concepts/profiles.md`: the editor schemas and the modelines.
- `AGENTS.md`: the `canair signals`/`variants` entries name the schemas, and the `profile.yaml`
  Key Files entry stops calling the schema editor-only.
- The `core/` charter lists `json_schema`.
