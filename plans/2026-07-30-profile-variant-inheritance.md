# Multi-variant profile support — sharing a profile across model variants without duplication

Status: **DESIGN / DECISION DOC — NOT STARTED for *definition* inheritance.**
There is no definition inheritance/composition/variant mechanism in `canlib/` or
the schemas. **Blocked on a decision before any code:** pick Option A/B/C/D (A
recommended), the ECU merge granularity (PID-level recommended), and the
write-target policy (the plan explicitly defers this one). Only pays off once a
real second variant exists — e.g. XPeng G6 SR vs LR.

**`extends:` now exists, but only for captures.**
`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md` §B shipped
*layered profiles*: a same-named user bundle whose `profile.yaml` carries
`extends: <name>` layers its `captures/` over a read-only base, while every
definition still resolves from the base alone. `canlib/profile.py::profile_layers`
walks the chain and `::_from_layers` deliberately **errors** when `extends:` names
a *different* profile — that case is this plan. So the key, the resolver walk and
the `Profile.overlays` model are already in place; what remains here is the
definition merge (which is the hard part, and the reason it was split off).

**A preparation pass has since landed** (`plans/2026-08-04-profile-variant-inheritance-prep.md`,
2026-08-04): the seams this plan would have to touch were consolidated so a chain
change lands in one place per concern instead of several. The "Background" and
"Implementation sketch" sections below have been updated to the post-prep code;
the options, granularity, write-target and open questions are untouched — they
remain the open decision.

## Goal

Support **multiple variants of one vehicle model** — e.g. the XPeng G6 SR
(66 kWh) vs LR (87.5 kWh) battery packs, model years, and market/firmware
differences — where a handful of PIDs diverge (most visibly the BMS
**cell/temperature counts**) while **90%+ of the profile is identical** (all
other ECUs, addressing, quirks, states, buses). Today the only way to express a
variant is to **copy the whole profile directory**, then keep the shared 90% in
sync by hand across the copies.

This is a recognised, currently-unsolved need:

- `profiles/xpeng-g6/KNOWN-ISSUES.md:34-37` — a "Variant note" spelling out the
  exact SR/LR case: *"The XPeng G6 ships in 66 kWh and 87.5 kWh packs with
  different cell counts. … build a separate profile (or a variant) for it rather
  than assuming 150."* — deferring to "separate profile" as the workaround.
- `docs/profiles/ioniq-2017.md:17-25` — PIDs/byte layouts "can differ across
  model years and markets — and even between two cars of the same year running
  different ECU firmware/part numbers."

This plan is a **design/decision document**: it lays out every option, the
trade-offs, and a recommended path, so the actual implementation can be scoped
from an agreed abstraction. No behaviour ships from this file.

## Background — current profile architecture (relevant seams)

There is **no** profile inheritance / composition / variant / include mechanism
anywhere in `canlib/` or the schemas today. A profile is a single self-contained
directory resolved to a flat `Profile` dataclass. The seams an inheritance layer
would touch (post-prep — each is now *one* place, not several):

- **`Profile(name, root)`** — frozen dataclass in `canlib/profile.py`. Every file
  location is a `root`-relative `@property`, and each one is declared in the
  **`BUNDLE_MEMBERS` registry** alongside its role (definition / evidence /
  external / local / generated) and whether it is contributable. `member_path` /
  `member_exists` resolve a member for a profile; a drift test asserts the
  properties and the registry agree. So "which components may a variant inherit,
  and how does `profile show` mark inherited vs overridden" is a table question.
  The **single-root assumption is still pervasive** — that is the actual work.
- **`resolve_profile`** — precedence chain (`--profile` > `CANAIR_PROFILE` >
  `default_profile` > single discovered) → `Profile`. `discover_profiles` does
  **shadowing only**, never merges. **`profile_for_path`** resolves the profile
  owning any path (root, `ecus/`, or a file inside) by walking up to the nearest
  bundle — the one function that must learn to read `extends:`.
- **`pids.load_pids` / `_load_dir` / `merge_ecu_documents`** — the ECU merge seam.
  `merge_ecu_documents` is now a named function taking `(path, doc)` pairs and
  merging them first-wins with a duplicate-key warning: the place a PID-level
  variant merge slots in. Memoized on **`Profile.cache_key`** (not a member path),
  and `clear_cache()` drops **registered derived caches** too, so chain
  invalidation has one entry point.
- **Per-component loaders** — uniform shape `load_x(profile: Profile | None)`:
  `load_states` (`canlib/states.py`), `load_can_buses` (`canlib/can_buses.py`),
  `load_groups` (`canlib/ecu_groups.py`), `load_signals` (`canlib/signals.py`,
  added by the prep — previously four copies of a glob), capture readers
  (`canlib/capture_io.py`, path-based).
- **ECU file location** — `canlib/ecu_files.py`: `ecus_dir`, `iter_ecu_files`,
  `find_by_name`, `find_by_tx`. Single home for "which file owns this ECU",
  i.e. where the **write-target policy** belongs.
- **`profile.yaml` validation** is imperative — `validate_meta`
  (`canlib/commands/validate/pids.py`) — and **tolerates unknown top-level
  keys** (deliberately extensible), so an `extends:` key already parses
  harmlessly. There is **no `profile.yaml` schema file**.
- **`canair profile`** — `canlib/commands/profile.py` — `list/show/path/use/create`
  (`create_profile` scaffolds the bundle from `templates/`). `show` iterates
  `BUNDLE_MEMBERS`. No notion of a base/parent.

**Existing override precedents to lean on** (the "child overrides base" mental
model already in the tree):

- Per-ECU → profile-default precedence for `addressing.*` / `multi_did_max` /
  `wake` (`canlib/addressing.py:_addressing_block`).
- Value-level built-in → profile inherit/override in `resolve_physical_bands`
  (`canlib/physical_bands.py`, `plans/2026-07-29-configurable-physical-bands.md:101`).
- State-vocabulary "extend the shared base" via `states.allowed_states`
  (`canlib/states.py`).

## Background — where the duplication actually is

The divergence is **fine-grained**: a single ECU's PID param list differs, not a
whole different car. Cell count is **fully hardcoded as a one-param-per-cell
enumeration** — no loop, count field, or templating:

- **Ioniq BMS** (`profiles/ioniq-2017/ecus/bms.yaml:625-1610`): 96 cells spelled
  out `CELL_01_VOLTAGE`…`CELL_96_VOLTAGE` across DIDs `2102`/`2103`/`2104`. Byte
  offsets are **non-contiguous** (skip ISO-TP PCI bytes B8/16/24/…).
- **XPeng BMS** (`profiles/xpeng-g6/ecus/bms.yaml:95-856`): DID `221122`
  (`variable_length: true`) with `HV_C_V_001`…`HV_C_V_150` and `HV_T_1`…`HV_T_35`
  spelled out. Notes (`:699-707`) record it was **seeded with 192 cells** from
  upstream, then **trimmed to 150** after a real capture (bytes 151→ read `0xFF`;
  575.3 V / ~3.835 V/cell = 150S). A 66 kWh car would need a **different
  `HV_C_V_*` list in a second profile** — the exact duplication to avoid.

So the unit that diverges in practice is **a whole DID (the cell/temp block)**,
inside an ECU otherwise identical to the base.

## Options

### Option A — Base profile + declarative overlay (RECOMMENDED)

A variant profile declares a base and contributes only its differences:

```yaml
# profiles/xpeng-g6-sr/profile.yaml
extends: xpeng-g6                 # base name (via discover_profiles) or a path
car_model: "XPeng G6 SR (66 kWh)"
```

Merge semantics at the two seams:

- **`profile.yaml` meta** — shallow-merge child over base (child keys win). List
  fields (`quirks`, an ECU's `can_bus`) need an explicit union-vs-replace rule
  (see Open questions).
- **`ecus/`** — base's ECU set loaded first, then child's `ecus/*.yaml` merged on
  top at a chosen **granularity** (below).
- **Per-component files** (`vehicle_states.yaml`, `can_buses.yaml`, `signals/`,
  `captures/`) — child file present → used; absent → inherit base's.

**Pros:** smallest change that removes the duplication users actually hit; plain
standard-YAML `extends:` key (no exotic syntax); reuses the existing
child-overrides-base model. **Cons:** touches `resolve_profile`, `_load_dir` +
its cache key, every component loader, `validate_meta`/schema, the mutative
editors (write-target question), and `profile show/create`; needs multi-level
chain resolution + cycle detection.

### Option B — Separate profiles + symlinks

Keep fully independent profiles but symlink the shared ECU files
(`ecus/mcu.yaml` → base). **Zero code change**, but symlinks are fragile across
clones/OSes, invisible/hostile to git-naive contributors, and — critically — do
**not** solve the **within-file** cell-count divergence (the symlinked file is
identical, but the cell list is exactly what differs). **Not recommended.**

### Option C — Parametric cell generation

Add a PID-level generator so repeated cells derive from a template + count
instead of enumeration:

```yaml
221122:
  generate_params:
    template: "HV_C_V_{n:03d}"
    expression: "((B{5+2*(n-1)}*2)/100)"
    count: 150            # a variant overrides just this number
```

Orthogonal to inheritance — a variant could be base + a one-line `count`
override. **Pros:** attacks the actual duplication (150 hand-listed cells) most
directly; elegant. **Cons:** introduces an expression-templating layer with
byte-offset arithmetic in `{...}`; the Ioniq's **non-contiguous** offsets
(skipping ISO-TP PCI bytes) make the arithmetic gnarly and per-DID special-cased;
complicates every tool that expects concrete params (`coverage`, `decode`,
byte-notation, `pids` editors). Higher power, higher risk.

### Option D — Lightweight fork tooling

No inheritance; add `canair profile fork <base> <new>` + a variant diff view to
make duplicated variants cheap to create and audit. **Pros:** lowest risk, no
merge semantics. **Cons:** accepts the duplication and the ongoing manual
sync burden; doesn't actually solve the stated goal.

## ECU merge granularity (applies to Option A)

1. **File-level replace** — if the variant has `bms.yaml` it fully replaces the
   base's. Simplest, most predictable semantics; but the variant must re-list
   **all** of that ECU's PIDs even if only the cell block differs. Modest dedup.
2. **PID-level merge (RECOMMENDED)** — merge per ECU → per PID: the variant
   re-lists only the divergent DIDs (e.g. the cell-voltage block); unchanged PIDs
   are inherited. Good dedup, comprehensible. Deleting trailing cells (151→) is
   expressed by re-declaring that DID's param list — explicit and acceptable.
3. **Param-level merge + `null`-delete** — the variant can add/override/delete
   individual params (drop `HV_C_V_151`…`HV_C_V_192` one by one). Maximum dedup,
   but the hardest to reason about and to display in tooling.

Recommend **#2 (PID-level)**: the divergent unit in practice is a whole DID, and
it keeps merge semantics understandable.

## Write-target policy (applies to Option A)

When a mutative command runs against a variant (`canair pids upsert-param`,
`--save` captures, `canair signals upsert`, `discover --register`):

- **Always into the variant (RECOMMENDED)** — writes land in the variant's own
  dir, never the base; editing the base requires selecting the base profile
  explicitly. Safest and clearest; the surgical editors already assume one file
  owns a definition.
- **Variant + warn on base-shadow** — write to the variant but warn when the edit
  shadows/duplicates a base definition, so the user can decide whether it belongs
  in the base instead.
- **Defer** — resolve the policy during implementation once the merge seam
  exists.

## Base scope (applies to Option A)

- **All components (RECOMMENDED)** — meta, `ecus/`, `vehicle_states.yaml`,
  `can_buses.yaml`, `signals/`, `captures/` all inheritable; anything absent in
  the variant is inherited from the base.
- **Meta + ECUs only** — narrower and simpler, but states/buses are frequently
  shared across variants too, so this would force re-copying them.

## Overall recommendation

**Option A + PID-level merge + always-write-to-variant + all-components
inheritable**, with **Option C as an optional later layer** (a variant then
becomes base + a one-line `count` override). Option A is the smallest change that
removes the duplication users will actually hit (whole ECUs + most PIDs shared,
only battery DIDs diverge), keeps the YAML standard, and reuses the existing
precedence semantics. Option C is more elegant for the cell list specifically but
is a bigger, riskier tooling change and can follow once A lands.

## Implementation sketch (recommended path)

Each step now names the *single* function/table the prep pass consolidated it to.

1. **Schema / validation** — allow `extends:` (string name or path) in
   `profile.yaml` (`validate_meta`, `canlib/commands/validate/pids.py`); detect a
   missing base and reject inheritance **cycles**. Unknown top-level keys are
   already tolerated, so nothing rejects `extends:` in the meantime.
2. **`resolve_profile` / `profile_for_path`** — resolve the `extends` chain to an
   ordered base→…→variant list of roots (reuse `discover_profiles` + the
   path-like handling). Carry the chain on `Profile` (e.g. `bases: tuple[Path,
   ...]`) or a new resolved type; keep `root` = the variant root, and extend
   `Profile.cache_key` to cover the chain (it exists for exactly this).
3. **`merge_ecu_documents`** — already the named merge seam, taking `(path, doc)`
   pairs and resolving collisions first-wins. Layer base→variant at the chosen
   granularity here; `_load_dir` feeds it and needs no policy of its own.
4. **Component loaders** — `load_states`, `load_can_buses`, `load_groups`,
   `load_signals` and the capture readers resolve "variant file else base file"
   across the chain. All four vocabulary loaders already share one shape
   (`load_x(profile=None)`), and `Profile.member_path` is where a chain-aware
   resolution belongs.
5. **Mutative editors** — land writes in the variant root; the decision point is
   `canlib/ecu_files.py` (`find_by_name` / `find_by_tx`), the single answer to
   "which file owns this ECU", plus the per-member write resolvers in
   `states_edit` / `groups_edit` / `signals_edit`.
6. **`canair profile`** — `show` already iterates `BUNDLE_MEMBERS`, so marking a
   member inherited vs overridden is a per-member detail, not a rewrite;
   `profile create --extends <base>` scaffolds a variant (thin `profile.yaml` +
   empty `ecus/`).
7. **Cache invalidation** — register any chain-derived cache through
   `pids.register_derived_cache` so `clear_cache()` (and `set_active`) drops it;
   don't add another unmanaged module global.
8. **Docs** — a `docs/concepts/` variant page, README pointer, the AGENTS.md
   profile section, and update `profiles/xpeng-g6/KNOWN-ISSUES.md`.
9. **Tests** — merge semantics, override precedence, chain resolution, cycle
   rejection, cache correctness, and write-target behaviour.

## Open questions (resolve during implementation)

- **List fields on merge** — `quirks`, an ECU's `can_bus`: union or replace?
- **Captures across a chain** — does a variant *see* the base's captures for
  `decode`/`coverage`/`correlate`, or only its own? Affects analysis scope and
  capture provenance.
- **Contribution flow** (`canair contribute`) — ship the variant alone, or
  flatten the chain into a self-contained bundle for the PR?
- **`out/` generation** (`canair wican autopid write`) — generated from the merged
  view; confirm the output lands in the variant's `out/`.
