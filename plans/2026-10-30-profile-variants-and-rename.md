# Profile variants, ECU revisions, make-model-code names, and old-name aliases

Status: **DESIGN — APPROVED IN PRINCIPLE, NOT STARTED.** Every decision is taken (see
[Decisions](#decisions)); the whole of it is in scope, including revisions, overrides and detection,
so the machinery is exercised from the start. Delivered in nine [phases](#phases), each its own
plan. Supersedes the *motivating case* of
`plans/2026-07-30-profile-variant-inheritance.md` (XPeng G6 SR vs LR is a variant, not a second
profile). That plan stays open for **cross-profile** sharing between siblings (Kona OS / Niro DE /
Soul SK3 / the Ioniq facelift BMS; a G6 EV and G6 EREV pair), which variants deliberately do not
solve.

## Goal

1. Rename every bundled profile to a **make-model-code-drivetrain** name (`ioniq-2017` →
   `hyundai-ioniq-ae-ev`). A year was already wrong for the 2016–2019 28 kWh car, and is plainly
   wrong for the 38 kWh facelift the profile should be able to describe.
2. Add an optional per-profile **variant registry** (`variants.yaml`): one stable ID per variant of
   the car, plus typed metadata (production window, model years, markets, battery, …).
3. Add optional per-ECU **revisions**: the versions of one ECU that differ in protocol or
   decoding, each recognisable from the ECU's own identity. PIDs and signals are gated to
   revisions (absent = all), and a revision can override fields of a shared definition.
4. Select the variant with a **separate setting** (`--variant`, remembered per profile). When a
   profile has several variants and none is selected, commands that need one **do nothing** and say
   how to pick.
5. **Tag every capture** with the variant (and ECU revisions) it came from.
6. Keep every old profile name working as an **alias**.
7. Capture the judgement — revision, variant, or new profile; how to name one — in a new
   **`profile-variants`** skill.

## Decisions

| Topic | Decision |
|---|---|
| Model | **Two layers**: variants describe the car, revisions describe an ECU |
| Profile names | `make-model-code-drivetrain`, every part always present, never a year |
| IDs | Local, lowercase, name only what differs: variants by battery (`28kwh`), revisions as `ECU:id` (`VCU:uds`) |
| Vocabulary | `variants.yaml`; `revisions:` / `per_revision:` in ECU files (see [Key names](#key-names)) |
| Selection | Global `--variant` / `CANAIR_VARIANT`, remembered per profile in `profile_settings` |
| No variant selected | Nothing runs; the error lists the variants and how to pick one |
| Aliases | Every old name; implies a variant only when the old profile described exactly one |
| Ioniq facelift | A variant, unless a facelift capture shows the profile-wide pieces don't hold |
| Scope | Everything, now, exercised by a synthetic fixture and a seeded Ioniq facelift BMS |
| Skill | New `profile-variants` skill |

## The worked example

The Ioniq Electric (AE), simplified but representative:

| Built | What changed |
|---|---|
| 2016 … | 28 kWh air-cooled pack; BMS reads over KWP2000-style service `21`; VCU speaks KWP2000 |
| ~2018 (running change, hypothetical) | VCU speaks UDS. Same pack, same car, same model-year brochure |
| ~2019-06 (facelift, "IONIQ EV PE") | 38 kWh pack; the BMS and drive-train ECUs follow the Kona/Niro diagnostic family (see [The Ioniq facelift](#the-ioniq-facelift-a-variant-not-a-profile)) |

Two things are true here that a car-level-only model gets wrong:

- **Changes are per ECU and land on production dates, not model years**, and different ECUs change
  at different dates. A "2018" car may have either VCU. Encoding each running change as a car
  variant multiplies (`2^n` for `n` independent changes) and asks the owner a question they can't
  answer ("was your car built before or after the VCU change?").
- **The car can contradict its own build date.** A dealer-replaced or reflashed ECU follows the
  newer rules in an older car. Only the ECU itself knows which revision it is.

So the design has **two layers**: *variants* describe the car (and constrain which ECU revisions
are possible); *revisions* describe an ECU, and are resolved from what that ECU reports.

## Vocabulary — five axes, each with exactly one home

| Axis | Example | Modelled as |
|---|---|---|
| **Model / generation** | Ioniq (AE) vs Ioniq 5 (NE); Kona OS vs Kona SX2 | **separate profile** |
| **Drivetrain** | Ioniq AE EV vs AE PHEV; G6 EV vs G6 EREV | **separate profile** |
| **Variant** — the car changes in a way the owner can name | 28 kWh vs 38 kWh facelift; G6 SR vs LR; a market with different diagnostics | **`variants.yaml` entry** |
| **ECU revision** — one ECU changes, often mid-variant | VCU KWP2000 → UDS; BMS ROM `4604` → `4824` if the layout changed | **`revisions:` in the ECU file** |
| **Equipment / option** — an ECU is fitted or not | BSD, SCC, heat pump, trim level | **neither** (critique §5); absence is harmless today |

Rules:

- **A revision exists only if some definition differs.** A new part number with identical
  diagnostics is another fingerprint of an existing revision, not a new one.
- **A variant exists only if the owner can name it** (pack size, facelift, market) **and** it
  changes something: which ECUs are fitted, which revisions are possible, a profile-wide setting,
  or captures that must be kept apart in analysis. Model year is never by itself a variant.
- Metadata-only differences (trim, colour, a market with identical diagnostics) are attributes of
  an existing variant (`markets: [EU, KR]`).

In the example: **two variants** (`28kwh`, `38kwh`), and the VCU split is **two VCU revisions**
inside `28kwh`, not two variants.

## Names and IDs

### Profile names

`make-model-code-drivetrain`, lowercase, hyphenated, **every part always present**:

- **make** — `hyundai`, `nissan`, `xpeng`. Groups `profile list` and the explorer by make as
  community profiles accumulate, and matches how comparable catalogues name cars (OBDb
  `Hyundai-IONIQ-Electric`, wican-fw `vehicle_profiles/<make>/`, OVMS).
- **model** — the nameplate (`ioniq`, `ioniq-5`, `kona`, `leaf`, `g6`).
- **code** — the maker's model code for the generation (`ae`, `ne`, `os`, `ze1`, `f30`).
- **drivetrain** — from a closed list: `ev`, `erev`, `phev`, `hev`, `ice`, `fcev`. These tokens are
  uniform across makes, unlike marketing words ("Electric", "e-").
- **Never a year.**

Always including the code and drivetrain is what makes a name permanent. A shorter "only when
ambiguous" rule needs a rename the moment a model gets a successor or a new drivetrain, and both
happen mid-life: the Kona already has two EV generations (OS, SX2), and XPeng filed a G6 EREV in
2025, partway through the F30 generation. A rename keeps the old name as an alias, but that alias
then points new owners at the *older* car, the one they didn't mean.

The codes are opaque to most owners, so `profile list` and the picker show each profile's
`car_model` and its variants' production windows next to the name. `validate` enforces the pattern
for bundled profiles (in CI); `profile create` warns, but does not refuse, for user profiles.

| Today | New name | Model code source |
|---|---|---|
| `ioniq-2017` | `hyundai-ioniq-ae-ev` | `AEEV` in the car's own ECU identity strings |
| `kona-electric-2019` | `hyundai-kona-os-ev` | community/OVMS naming of the first-generation Kona |
| `ioniq-5-2022` | `hyundai-ioniq-5-ne-ev` | Hyundai's `NE` code for the Ioniq 5 |
| `nissan-leaf-ze1` | `nissan-leaf-ze1-ev` | already in the name |
| `xpeng-g6` | `xpeng-g6-f30-ev` | `F30` (Wikipedia, CarNewsChina) |

### Variant and revision IDs

**Every ID is local and names only what differs.** A variant ID never appears without its profile
(`variants.yaml` and the captures live inside the profile directory, and every selection names the
profile), and a revision ID never appears without its ECU. Repeating the profile or ECU inside the
ID is noise. So `28kwh`, never `IONIQ-EV-28`, and `uds`, never `BMS-UDS`.

- **Pattern** `^[a-z0-9][a-z0-9-]*$`, containing at least one letter. A bare `28` would read as an
  integer when used as a YAML key, and would not say what it counts. Input is case-insensitive and
  normalised to lowercase.
- **Variants** are named by **usable battery capacity**, as the integer part of the marketed figure
  (`28kwh`, `38kwh` for 38.3 kWh, `87kwh` for 87.5 kWh). It is the one thing owners reliably know
  about their car. When two variants share a pack, append the next difference (`38kwh-us`).
- **Revisions** are named by what differs about the ECU: protocol (`kwp`/`uds`), cell count
  (`150s`), or software line — whichever is the actual distinction. Written `ECU:id` (`VCU:uds`),
  following the existing `ECU:PID` convention.
- **Never a year**, for either.
- Unique within their scope; renamed only via `canair variants rename` / `canair ecu <ECU>
  revisions rename`, which rewrite every reference, capture tags included (as `states rename`
  does).

### Key names

| Concept | Name | Why this one |
|---|---|---|
| Variant registry | `variants.yaml` | One word everywhere: `--variant`, `canair variants`, the session field `variant`. "Generation" is wrong: battery size and market aren't generations, and a facelift is the same generation in Hyundai's own naming |
| An ECU's versions | `revisions:` (declaration mapping, at ECU level) | The automotive term for a part's hardware/software revision. `versions` collides with identity's `sw_version`/`hw_version`/`fw_version` and with a capture session's `version` (the canair version); `variants` would merge the two layers |
| Gating | `variants:` on an ECU (presence); `revisions:` on a PID or signal (applicability list) | One gating key per level. At ECU level `revisions:` is always the declaration mapping, below it always a list — the schema tells them apart by level and shape, as it already does for `vehicle_states:` |
| Overrides | `per_revision:` | Names the axis it varies along. A bare `overrides:` wouldn't, and `when:` is taken by state predicates |
| A variant's profile-wide changes | `profile_overrides:` inside the variant entry | Kept with the variant it belongs to, not in a parallel top-level map |
| Recognising a revision | `fingerprints:` (`identity:` / `responds:`) | Describes evidence, not a rule |
| Capture tags | `variant`, `revisions`, `attribution` | |
| User config | `profile_settings.<profile>.variant` / `.revisions` | Per-car user state, keyed by profile |

## Data model

Both layers are optional. A profile without `variants.yaml`, and an ECU without `revisions:`, has
one implicit variant/revision and **nothing changes**: every code path, file and capture stays
byte-identical. A `variants.yaml` with exactly one variant needs no selection either.

### ECU revisions (`revisions:` in `ecus/<ecu>.yaml`)

```yaml
VCU:
  tx_id: 0x7E2
  revisions:
    kwp:                                 # listed first = the reference revision
      identity: {id_protocol: KWP2000}
      fingerprints:
        - {responds: "1A90"}             # a safe identity read this revision answers
    uds:
      identity: {id_protocol: UDS}
      fingerprints:
        - {identity: {part_number: "36601-0E1*"}}   # hypothetical
  pids:
    2101:
      revisions: [kwp]
      parameters:
        VEHICLE_SPEED: {expression: "B12"}
    22E001:
      revisions: [uds]
      parameters:
        VEHICLE_SPEED: {expression: "B14", verified: false}
```

- A revision carries **ECU-level overrides** (whitelisted: `identity.id_protocol`, `sessions`,
  `wake`, `multi_did`, `multi_did_max`, `response_timeout_ms`, `addressing`, `rx_id`) and
  **fingerprints**:
  - `identity:` — glob match on identity fields (part number, SW ID, ROM ID). The known part
    numbers move here from the ECU's single `identity:` block, which today records one car.
  - `responds:` — a safe read (identity DID / `1A` LID) that only this revision answers. This is
    how a protocol change is detected without knowing any part numbers.
- **Applicability** — `revisions:` on a PID or signal. Absent = every revision of that ECU. No
  `ALL` token, no negation.
- **Overrides** — `per_revision:` on a PID or signal, shallow field replace over a whitelist:

  ```yaml
  SOC_BMS:
    expression: "B09/2"
    verified: true
    per_revision:
      uds: {expression: "B10/2", verified: false}
  ```

  No `revisions`/`per_revision` inside an override; a PID override cannot carry `parameters:`
  (divergent signals are overridden at the signal level, so there is one place to look); an
  override key must be within the entity's applicability (dead data otherwise).
- **Duplicate shipped signal names** (today a `validate pids` error) become legal **iff** the
  owning PIDs' revision sets are disjoint. This is the point: `BMS.SOC_BMS` stays one name across a
  protocol change, so state predicates, groups, Home Assistant entities and MQTT topics keep
  working.
- **Subset rule** (validate error): a signal's revisions must be within its PID's.

Two ECU entries (`VCU` / `VCU-UDS`) were rejected: the ECU name is the identity used by queries,
predicates, groups and the cross-ECU misfile check.

### Variants (`variants.yaml`)

```yaml
# profiles/hyundai-ioniq-ae-ev/variants.yaml
variants:
  28kwh:
    name: "Ioniq Electric 28 kWh (pre-facelift)"
    model_code: AEEV                     # what the car's own ECUs report ("AEEV__ BMS")
    generation: AE
    production: ["2016", "2019-05"]      # YYYY[-MM] build window — the real cut line
    model_years: [2017, 2019]            # informational; markets disagree on model year
    markets: [EU, US, KR]
    battery: {usable_kwh: 28, series_cells: 96, chemistry: Li-polymer, cooling: air}
    revisions:                           # which ECU revisions this car can have
      BMS: [kwp]
    status: verified
    sources: ["…"]
  38kwh:
    name: "Ioniq Electric 38 kWh (facelift)"
    model_code: AEEV                     # unchanged by the facelift
    designation: "IONIQ EV PE"           # Hyundai's facelift name, e.g. in recall campaigns
    generation: AE
    production: ["2019-06", "2022"]
    battery: {usable_kwh: 38.3}
    revisions:
      BMS: [uds]
    profile_overrides:                   # whitelisted profile.yaml keys
      car_model: "Hyundai Ioniq Electric 38 kWh"
    status: seed                         # metadata + ported definitions, nothing verified yet
    sources: ["OVMS vehicle_kianiroev docs", "…"]
```

- **`revisions:`** maps an ECU to the revisions this variant can carry. An unlisted ECU allows all
  of its revisions. A variant listing two (`VCU: [kwp, uds]`) is a running change: the ECU decides.
  This is how a car-level difference reaches inside an ECU — the XPeng SR/LR cell lists, for
  example, would be BMS revisions, each allowed by one variant.
- **ECU presence** — `variants:` on an ECU, for an ECU only some variants have. Absent = all.
- **Typed metadata** is a fixed, schema-validated core (`name`, `model_code`, `designation`,
  `generation`, `production`, `model_years`, `markets`, `battery{…}`, `trims` (informational
  only), `status`, `sources`, `notes`), not a free-form bag. The explorer, docs page, picker and
  `profile show` render it, and an untyped bag drifts (`battery_kwh` vs `battery: 64` vs
  `"64 kWh"`).
- **`production`, not `model_years`, is the meaningful window.** Model year differs by market (a
  US MY2020 can be built in 2019) and running changes ignore it. `production` is also what an
  owner can read off the door-pillar label without exposing a VIN.
- **`status:`** `verified | community | seed`, so an unproven variant is visibly a stub.
- **`profile_overrides`** is a whitelist (`car_model`, `physical_bands`, `response_timeout_ms`,
  `init`, `multi_did_max`, `quirks`), not a second `profile.yaml`.

## Selection

### Choosing a variant

A profile is one directory; a variant is a fact about **your car**. They are selected separately,
so the profile name stays a directory name for every bundle-level command (`adopt`, `overlay`,
`contribute`, `path`) and every docs URL.

```bash
canair profile use hyundai-ioniq-ae-ev --variant 38kwh   # set once, remembered per profile
canair --variant 28kwh monitor @driving                  # one-off override
```

- **Global flag `--variant ID`**, next to `--profile` (before the subcommand), and
  `CANAIR_VARIANT`.
- **Remembered per profile** in user config, beside the other per-car user state. Keyed by
  profile, so switching profiles can never carry a stale variant across:

  ```yaml
  default_profile: hyundai-ioniq-ae-ev
  profile_settings:
    hyundai-ioniq-ae-ev:
      variant: 38kwh
      revisions: {VCU: uds}              # optional pins (see below)
  ```

- **Precedence**: `--variant` > `CANAIR_VARIANT` > `profile_settings.<profile>.variant` > the
  variant implied by an old-name alias (see [Aliases](#aliases)) > the only variant, when there is
  exactly one. Nothing else: **there is no default variant**.
- A variant the active profile doesn't declare is an **error naming the profile's variants**. That
  is what a mismatched `CANAIR_PROFILE`/`CANAIR_VARIANT` pair produces.
- `profile use NAME` on a TTY with several variants opens a picker showing each variant's name,
  production window and battery — the things an owner *can* answer.

### When none is chosen: nothing happens

On a profile with several variants and no selection, every command that needs one exits with
status 2 and does nothing else:

```text
error: hyundai-ioniq-ae-ev describes 2 variants and none is selected.

  28kwh   Ioniq Electric 28 kWh (pre-facelift)   built 2016 – 2019-05
  38kwh   Ioniq Electric 38 kWh (facelift)       built 2019-06 – 2022

  Pick yours once:          canair profile use hyundai-ioniq-ae-ev --variant 28kwh
  Or for one command:       canair --variant 28kwh <command> …
  Not sure which you have?  canair identity BMS   (reports which variant it matches)
```

**Needs a variant:**

- every command that talks to the car (`read`, `monitor`, `scan`, `io`, `routines`, `dtc`, `raw`,
  `repl`, `sniff`);
- every write of a capture (`--save` on any of them);
- device-config generation (`wican autopid write`/`upload`/`diff`).

**Exempt:**

- `identity` and `discover` *without* `--save` — they are how you find out. They match fingerprints
  against every variant and report which fits.
- `status`, `lock`, `logs`, `config` and `profile` — they don't decode anything. `status` reports
  "no variant selected" and the fix.
- Authoring (`pids`, `signals`, `states`, `groups`, `ecu`, `variants`) — the target is chosen by
  `--revision`, not by the car.
- Read-only analysis — it decodes each session with its own tag (see
  [Reading tags back](#reading-tags-back)).

`import uds`/`import can` never use the selected variant at all: an import is someone else's car,
so it always takes its own `--variant` (see [Every write path tags](#every-write-path-tags)).

### Which revision, per ECU

Most to least authoritative:

1. **Pin** — `profile_settings.<profile>.revisions.<ECU>` (set with `profile use … --pin
   VCU=uds`), for when detection can't work (a revision with no fingerprint yet) or the user knows
   better.
2. **Only one possible** — the variant allows a single revision, or the ECU has one. No probe.
3. **Detected** — when several are possible, match fingerprints on first contact with that ECU.
   The session open already happens; add one safe identity read per *ambiguous* ECU, never per PID.
   The result is cached per profile and variant in user state (local, never contributed) and
   re-checked when a later identity read contradicts it.
4. **Undetermined** — the same principle as an unchosen variant: **no guess**. That ECU's
   revision-gated PIDs are skipped with one message naming the pin; its ungated PIDs, valid on
   every revision, still read.

A fingerprint is a **read**: identity DIDs and `1A` LIDs only, through the existing session/safety
policy. A UDS identity read sent to a KWP2000 ECU just draws silence or an NRC.

`canair status`/`profile show` print the profile, the variant, and each ambiguous ECU's revision,
each with its source (flag / env / config / alias / only / pin / detected).

### Resolution happens once, at the load seam

`Profile` gains `variant: str | None` and a resolved `revisions: Mapping[str, str]`; `cache_key`
covers both. `load_pids(profile)` returns definitions **already filtered and overridden** for that
variant and those revisions, so the ~95% of consumers reading `load_pids()` need no change.
Variant-*aware* code reads raw documents: `validate`, `coverage`/`research` across revisions, the
explorer and the editors (which already work on ruamel text, not the resolved dict). The same seam
applies `profile_overrides` to `Profile.meta`.

Live detection happens *after* the first load (it needs the bus), so a session starts on the
pinned/only-one resolution and re-resolves an ECU when detection settles it. That is one cache
invalidation per ambiguous ECU per session, through the existing `clear_cache()` hook.

## Captures — every session says which car it came from

A capture is evidence about **one car**. Once a profile describes more than one, a session that
doesn't say which is worse than useless: it gets decoded with the wrong layout, and its values
"confirm" signals on a variant they were never seen on. So tagging is **explicit data, written at
record time**. Code never infers it when reading.

### What a session records

- **`variant`** — the variant ID, on every session of a profile that has a `variants.yaml`.
- **`revisions: {ECU: id}`** — for each captured ECU with more than one revision (only those, so
  the common case adds nothing).
- **`attribution`** — how each was decided, per field: `selected` (flag, env or config),
  `pinned`, `detected` (fingerprint matched), `only` (the only possibility), `alias` (implied by an
  old profile name), `migrated`, `asserted` (set afterwards with `--set-variant`). A tag is only as
  good as its source, and `alias` is shown as **unconfirmed** wherever the tag is displayed.
- One session is one car: a session never changes variant part-way. A revision settled during a
  session (detected late, or an ECU that only now answered its fingerprint) splits the session,
  the same way a reconnect re-homes one.

None of these are **anchor fields**, so `--set-variant`/`--set-revision` edits keep merge and
contribute pairing intact.

### Every write path tags

- **`--save`** (`read`/`monitor`/`scan`/`raw`/`discover`/`identity`) stamps the resolved variant
  and revisions, journaled like the rest of the session, so a crashed session's recovered data is
  tagged too. With no variant selected it refuses, like every other command that needs one.
- **`import uds`** and **`import uds --from-log`** require `--variant` (and `--revision ECU=id`
  where needed) on a multi-variant profile, and never inherit the selected one — a community paste
  or a third-party log is exactly the case where the car is someone else's.
- **`import can`**: the raw broadcast log index (`captures/can/index.yaml`) gets the same `variant`
  field, under the same rule. A broadcast map can differ by facelift just as a PID can.
- **`canair dtc`**: `dtc_log.yaml` entries record the variant, since a code's meaning can differ.

### Existing captures — migrated explicitly, never guessed

`canair captures migrate-variants` is a one-time stamp of sessions that have no tag. It needs an
explicit `--variant` (plus optional `--revision`, and the usual scope filters), so a profile whose
captures come from several cars is stamped in several scoped runs. It writes
`attribution: migrated`, and `--dry-run` previews it. It runs:

- **in the tagging phase's commit**, for each bundled profile, from evidence about where the
  captures came from (below);
- **by the owner**, for a user's overlay. Their recordings are of *their* car — which may well be
  a 38 kWh car that used `ioniq-2017` for want of anything better — so canair hints at the command
  and never stamps them itself.

| Profile | Captures from | Stamp |
|---|---|---|
| `hyundai-ioniq-ae-ev` | the profile author's own 28 kWh car, every session (incl. the one `import`) | `28kwh` |
| `hyundai-kona-os-ev` | projectgus CC0 logs of two US Kona EV Highlanders, a 2019 and a 2021 (US cars were 64 kWh only) | `64kwh` |
| `xpeng-g6-f30-ev` | one car, 150S by pack voltage (87.5 kWh) | `87kwh` |
| `nissan-leaf-ze1-ev` | 2026-10-21 device sweeps; battery size not recorded | **untagged until confirmed** |
| `hyundai-ioniq-5-ne-ev` | one odometer reading (wican-fw#478); battery size not recorded | **untagged until confirmed** |

An **untagged** session in a multi-variant profile is a counted state, the way untimed captures
are:

- `validate captures` reports the count, and CI ratchets it with `--max-untagged N`, which may
  only fall.
- `captures uds --sessions` marks such sessions.
- Analysis skips them, with a footer giving the count and the fix, unless a variant is selected.
  Then they are decoded as that variant, with a footer saying so.

**Merge and contribute must not let a stale copy strip a tag.** A tagged and an untagged session
with the same anchor and otherwise identical content are the **same** session plus added
information. The merge driver, the contribute overlay and the layered read keep the **tagged** one.
Otherwise, after the migration, every untagged session in a user's older adopted copy would
"replace" its tagged upstream twin.

### Reading tags back

- In analysis commands, `--variant ID|all|untagged` scopes sessions, and `--revision ECU=id` (in
  `add_scope_args`) narrows further. The default is the selected variant if there is one, else
  every tagged session.
- Each session decodes with **its own** stamped variant and revisions, so signal-level views
  (`decode` ranges, `align`, `captures --step`) can span cars without mis-decoding.
- **Byte-level analyses** (`investigate`, `hunt`, `correlate`, `coverage`,
  `decode --dump-bytes`/`--try`) refuse a scope that spans two revisions of the ECU being analysed,
  and name `--variant`/`--revision`. A byte offset means nothing across two layouts.
- A protocol-change revision usually **self-identifies in the data**: a KWP `61 01` reply cannot be
  a UDS `62 01 01` reply. `validate captures` warns when a session holds replies to PIDs outside
  its stamped revision, and when its own identity reads contradict its tag. This is the check that
  catches a wrong `alias` or `migrated` stamp.
- `captures uds --set-variant`/`--set-revision` (scope filter required, the same gate as
  `--set-state`) write `attribution: asserted`.

## Evidence fields — where writes land

Three fields are *measurements of one ECU*, not definitions, so they belong to a revision, not a
car. An ECU without revisions has one, which is why a body ECU verified on the 28 kWh car counts as
verified on the 38 kWh car: it is the same ECU revision. If it isn't, that is a second revision.

| Field | Today | With revisions |
|---|---|---|
| `verified` | bool, implicitly "on the one car" | the base value means **on the reference (first-listed) revision**; another revision resolves to `false` unless `per_revision.<r>.verified: true`. No mass migration of thousands of `verified: true` |
| `response_frames` (auto-written) | per PID | written to the base for the reference revision, else to `per_revision.<r>`. **Correctness, not tidiness**: an undercount carried over from another revision's layout desyncs the ELM327 pipe |
| `identity:` (from `canair identity`) | one block per ECU | an identity matching a revision's fingerprints is filed as that revision's evidence; one matching none is reported as a **possible new revision** instead of overwriting the base |

`wican autopid write`'s verified-only default therefore ships, for `38kwh`, only what someone
verified on that ECU revision — stricter than today, where a 38 kWh owner using `ioniq-2017`
silently gets 28 kWh offsets. Call this out in the changelog.

**AutoPID can't detect.** The device polls a fixed list and never runs canair's fingerprinting, so
`wican autopid write` needs every ambiguous ECU resolved (pin or cached detection) and otherwise
refuses, naming the ECU and the pin to set. Output goes to `out/<variant>/`. Shipping both
revisions' PIDs would double the poll list and publish one signal name from two PIDs.

## Research — model codes and the Ioniq facelift

Gathered 2026-10-30; recorded because the names and the facelift decision rest on it.

### Model codes

- **Ioniq Electric: `AEEV`, both variants.** The profile's own captures read `AEEV__ BMS` (BMS
  `1A90`) and `AEEV  97250-G7060 … AE_EV DATC` (HVAC); the US telematics firmware build is
  `AEEV.USA.SOP.V108`; the IGPM findings list `AE EV` part numbers for EU, US and the facelift.
  Hyundai's battery recall text, as quoted on SpeakEV, names the facelift *"IONIQ EV PE (AEEV)"*,
  built from 2019-06-01. That is a secondhand quote, since the thread is paywalled to fetch.
  So `28kwh` ends around 2019-05; neither boundary is confirmed to the month. The model code cannot
  tell the variants apart, and neither can `ecu_id` fingerprints such as `AEEV__ BMS`.
- **XPeng G6: `F30`** (Wikipedia; CarNewsChina, March 2023). The 2025 refresh is `F30b`, with a
  China-only cost-down `F30be` (CnEVPost, July 2025) — refreshes of one generation, so candidate
  *variants* of `xpeng-g6-f30-ev`, not new profiles. XPeng filed a **G6 EREV** with MIIT in October
  2025 (CnEVPost): a different drivetrain, so a future `xpeng-g6-f30-erev` profile.
- **Kona Electric: `OS`** for the 2018–2023 car, **`SX2`** for its 2023 successor.
  **Ioniq 5: `NE`.** **Leaf: `ZE1`** (2017–2025), between `ZE0` and the current generation.
  Confirm each from the profile's own ECU identity strings before stamping metadata, as was done
  for the Ioniq.

### The Ioniq facelift: a variant, not a profile

- **Powertrain follows the Kona.** OVMS (Open Vehicle Monitoring System) supports the 28 kWh car
  with its own "Hyundai Ioniq vFL" module, and the facelift with its **"Kia e-Niro / Hyundai Kona
  / Hyundai Ioniq FL"** module. That module's docs say the Ioniq FL *"differs from the e-Niro and
  Kona only by the lower battery capacity of 38 kWh"*. So the facelift's BMS most likely answers
  the Kona's UDS `220101…` where the 28 kWh car answers `2101`. That fits the "38kWh profile"
  speed formula the VCU research cites. OVMS reads only about a dozen battery and charging signals,
  so this says nothing about body ECUs.
- **Body follows the Ioniq.** The facelift IGPM is part G7532, in the same G75xx family as the
  28 kWh car's (G7510; US G7530). OVMS remote locking via IGPM `BC11` works across the Kona,
  e-Niro and Ioniq 38 kWh alike. The Kona profile shares the Ioniq's bus vocabulary and 11-bit,
  `+0x08` addressing.

So the facelift is Kona-family battery and drive-train ECUs inside an Ioniq body. Either choice
duplicates one side:

- **As a variant**, the body ECUs — most of the 30 files and nearly all the verified work — are
  shared, and only the battery and drive-train definitions are ported from `hyundai-kona-os-ev`
  into `uds` revisions. Those are small and mostly unverified.
- **As a separate profile**, the body ECUs are copied, then drift, and every fix has to be made
  twice.

Capture tags make the variant choice reversible: splitting `38kwh` sessions into their own profile
later is mechanical. Merging two drifted profiles back together is not.

**It would become a separate profile** if one discovery-and-identity session on a facelift car
shows any of these: a different set of ECU addresses or a gateway in front of the OBD port; body
ECUs (IGPM, BCM, CLU, HVAC) that don't answer the 28 kWh PIDs with the same layouts; or
power-state behaviour that needs different vehicle states. A community capture imported with
`--variant 38kwh` would serve as well as our own.

The lasting fix for the ported battery definitions is sharing *across* profiles. The Kona, Niro,
Soul 2 and Ioniq facelift BMS are one ECU family, which belongs to the inheritance plan, possibly
narrowed to sharing single ECU revisions between profiles.

## Rename and aliases

### Aliases

Every renamed profile keeps its old name. An alias implies a variant **only when the old profile's
`car_model` named exactly one**; otherwise it resolves to the profile alone, and the user picks,
like anyone else:

| Old name | Resolves to | Implied variant |
|---|---|---|
| `ioniq-2017` | `hyundai-ioniq-ae-ev` | `28kwh` ("Hyundai Ioniq 2017"; the 28 kWh car is the only 2017) |
| `kona-electric-2019` | `hyundai-kona-os-ev` | `64kwh` ("… (64 kWh)") |
| `xpeng-g6` | `xpeng-g6-f30-ev` | `87kwh` ("Xpeng G6 (87.5 kWh)") |
| `ioniq-5-2022` | `hyundai-ioniq-5-ne-ev` | none — the old profile named no pack |
| `nissan-leaf-ze1` | `nissan-leaf-ze1-ev` | none — the old profile named both packs |

Aliases are data, declared in the target's `profile.yaml`:

```yaml
# profiles/hyundai-ioniq-ae-ev/profile.yaml
aliases:
  ioniq-2017: {variant: 28kwh}
```

Resolution for a profile name:

1. A **real** profile directory of that name (any root) **always wins**. An adopted user copy
   still called `ioniq-2017` must not be hijacked.
2. Else an alias → the target profile, its implied variant ranked below every explicit choice, and
   a one-line deprecation note: `ioniq-2017 is now hyundai-ioniq-ae-ev (variant 28kwh); run canair
   profile use hyundai-ioniq-ae-ev --variant 28kwh`.
3. Two profiles claiming one alias → `ProfileError` (`validate all` checks bundled ones).

`profile list` shows aliases as `ioniq-2017 → hyundai-ioniq-ae-ev (28kwh, renamed)`, never as
profiles. `profile use ioniq-2017` writes the *new* name and variant, not the alias.

### User-side breakage matrix

| User state | What breaks without care | Handling |
|---|---|---|
| `default_profile: ioniq-2017` in config | not found | alias; resolves silently. canair never rewrites the config, which older checkouts sharing it would then fail to read |
| `CANAIR_PROFILE=ioniq-2017`, scripts, `--profile ioniq-2017` | not found | alias, with the deprecation note |
| Any bundled profile with no implied variant (Leaf, Ioniq 5) | first live command refuses | the refusal *is* the handling: it lists the variants and the one command to pick |
| Overlay `~/.config/canair/profiles/ioniq-2017/` (`extends: ioniq-2017`) | dangling-overlay error; their captures vanish from view | it layers onto the renamed profile in place; never renamed for the user |
| Adopted full copy `ioniq-2017` | keeps working as its own profile (rule 1); stops shadowing the bundled one | `profile list` notes the bundled successor; nothing auto-merged |
| `canair contribute` from that copy | would recreate `profiles/ioniq-2017/` upstream next to the renamed one | contribute maps an alias-named source onto its target (and says so), or refuses with the fix |
| Install snapshot with writes in `profiles/ioniq-2017/` | `snapshot_profile_risks` reports it under a dead name | rescue into the renamed directory |
| Doc links to `/profiles/ioniq-2017/` or `/profiles/nissan-leaf-ze1/` | 404 | keep a stub page linking to the new one (no `mkdocs-redirects` dependency today) |

### Repo-side rename

- `git mv` each bundle; LFS/merge-driver patterns are `profiles/*/…`, so they still match.
- Update live references only: code docstrings/examples, `config.example.yaml`, tests (conftest
  sets `CANAIR_PROFILE=hyundai-ioniq-ae-ev` **and** `CANAIR_VARIANT=28kwh`; goldens), CI's untimed
  baseline key, `mkdocs.yml`, the docs-hook map, screenshot tapes, README/AGENTS.md, skills.
  **Do not rewrite `plans/`**: they are dated history, and a blanket replace would make them claim
  things that were never true.
- `car_model` → "Hyundai Ioniq Electric (AE)". Profile-wide comments that are really per-variant
  (the air-cooled 28 kWh pack, the 96S band) move into `variants.yaml` metadata, and the BMS ROM ID
  list by production month becomes BMS fingerprints.

## Exercising it now

Every phase ships with data that exercises it, rather than waiting for a real second-revision
capture.

- **Synthetic fixture profile** `tests/fixtures/profiles/variants/`:
  - two variants;
  - one ECU with `kwp`/`uds` revisions plus `per_revision` overrides, and one shared ECU;
  - captures tagged per variant, plus one untagged legacy session;
  - a state predicate that must resolve under both revisions.

  It drives the unit and golden tests, the mock-bus detection tests on the raw path, and the opt-in
  ELM327-emulator test, where the emulator answers `1A90` or `22F187` depending on the scenario.
  It is the only place the VCU running change exists: that change is hypothetical, so it is never
  authored in a bundled profile.
- **The bundled Ioniq, for real.** BMS gets two revisions:
  - `kwp` — every existing PID. Fingerprint: `responds: 1A90`, already verified on this car.
  - `uds` — the Kona's `220101`/`220105` BMS definitions, ported through `canair bix`, all
    `verified: false`. Fingerprint: `responds: 22F187`, unverified.

  `38kwh` allows only `uds`. Every BMS signal a `vehicle_states.yaml` predicate references must
  exist under both revisions, or `validate states` fails, so the cross-revision predicate check is
  exercised on the real profile.
- **The other profiles** get `variants.yaml` with metadata and seed variants, but no revisions:
  there is no evidence of per-ECU differences there yet.
- **Acceptance invariant, checked at every phase:** with `--variant 28kwh`, every decode, coverage,
  investigate and AutoPID output is **byte-identical** to today's `ioniq-2017` output. The existing
  goldens are re-pointed, not regenerated. This proves the machinery changes nothing for the car it
  was built on.

## Authoring CLI

- `canair variants` — `list`, `show ID`, `add`, `set-meta`, `set-revisions ID ECU REVS`,
  `rename`, `remove` (refuses while referenced). Same discipline as `canair states`:
  comment-preserving, validated, auto-reverted. **Never hand-edit `variants.yaml`.**
- `canair ecu <ECU> revisions` — `list`, `add ID`, `set-fingerprint`, `rename` (rewrites PID/signal
  gates, `variants.yaml` and capture stamps), `remove`.
- `pids set-revisions ECU:PID[:SIGNAL] REVS|--clear`; `ecu <ECU> set-variants VARIANTS|--clear`.
- `pids upsert-param … --revision R`: on an **existing** signal writes `per_revision.R`; on a
  **new** one creates it with `revisions: [R]`. The opposite default would silently claim a signal
  seen on one ECU revision exists on all of them.
- `canair identity ECU` reports which revision the reading matches (or that it matches none), and
  which variants that leaves possible.
- `profile use NAME [--variant ID] [--pin ECU=REV …]`, writing `default_profile` and
  `profile_settings`.
- `BUNDLE_MEMBERS` gains `variants.yaml` (role `definition`), so contribute/blind/`profile show`
  pick it up at once.

## Phases

Nine phases, each its own plan, each landing green on its own and shippable before the next starts.
They are ordered so that every additive step lands, and can be used and corrected, before the one
step that refuses (phase 4). The byte-identical `28kwh` invariant is checked at every phase.

| # | Plan | What it does | Behaviour change |
|---|---|---|---|
| 1 ✓ | [Rename and aliases](2026-10-30-profile-variants-1-rename-and-aliases.md) | Five renames, `aliases:` (old names, old-named overlays, contribute), the skill (naming + new-profile rules) | names only |
| 2 ✓ | [Variant registry](2026-10-30-profile-variants-2-variant-registry.md) | `variants.yaml` (metadata), `canair variants`, display in `profile show` and the explorer | none |
| 3 ✓ | [Capture tags](2026-10-30-profile-variants-3-capture-tags.md) | `variant` tags, global `--variant` (opt-in), `migrate-variants` on bundled history, tagged-wins merge, analysis scope | imports need `--variant` |
| 4 ✓ | [Selection and refusal](2026-10-30-profile-variants-4-selection-and-refusal.md) | `profile_settings`, `profile use --variant` + picker, alias-implied variants, the refusal, untagged ratchet | **breaking**: no variant, nothing runs |
| 5 ✓ | [ECU revisions](2026-10-30-profile-variants-5-ecu-revisions.md) | `revisions:`, gating and resolution at the load seam, pins, validation, byte-level refusal, the synthetic fixture | none for bundled profiles |
| 6 | [Ioniq facelift BMS](2026-10-30-profile-variants-6-ioniq-facelift-bms.md) | The real-profile exercise: BMS `kwp`/`uds`, Kona definitions ported unverified | `38kwh` only |
| 7 | [Overrides and evidence](2026-10-30-profile-variants-7-overrides-and-evidence.md) | `per_revision:`; `verified`, `response_frames`, identity routed per revision | stricter on non-reference revisions |
| 8 | [Detection](2026-10-30-profile-variants-8-detection.md) | Fingerprint matching, the per-session probe, `identity` names your variant | ambiguous ECUs read instead of skip |
| 9 | [Consumers](2026-10-30-profile-variants-9-consumers.md) | AutoPID per variant, explorer switcher, coverage/research per revision, final docs | `out/<variant>/` |

The skill and docs grow with each phase rather than landing ahead of the features they describe.

## The `profile-variants` skill

A new skill at `.claude/skills/profile-variants/SKILL.md`, written with the **skill-authoring**
skill loaded (frontmatter conventions and lint).

**Why new, not an extension.** The judgement it carries arises far more often than contributing
upstream: whenever someone creates a profile for their own car, imports a forum paste from a
different car, or finds a signal that differs between two owners' cars. Folding it into
`contributing-profiles` would make those people load a PR-and-PII workflow they don't need.
Folding it into `reverse-engineer-signal` would mix a structural decision into a decoding method.
Each of those skills gets a one-line pointer instead.

**Description** (the trigger):

> Where a car's data belongs in canair — a new profile, a variant, or an ECU revision; how to name
> a profile (make-model-code-drivetrain) and its variant/revision IDs; tagging captures with the car
> they came from; and authoring revisions, per-revision overrides and fingerprints. Load when
> creating or naming a profile, adding a variant or ECU revision, importing captures from another
> car, or when a signal differs between cars of one model. NOT for decoding a signal
> (reverse-engineer-signal) or submitting upstream (contributing-profiles).

**Content** — intent, not flag lists. It points at `canair variants --help`, the schema and
`docs/concepts/variants.md`, following the house rule that skills don't copy what drifts:

1. **Decide bottom-up**: revision → variant → new profile → neither, with the rules from
   [Vocabulary](#vocabulary--five-axes-each-with-exactly-one-home). The test is whether the
   profile-wide pieces still hold (buses, addressing, vehicle states, drivetrain), not how many
   ECUs changed.
2. **Naming**: the profile convention, where to find a model code (the car's own identity strings
   first), the ID rules.
3. **Evidence discipline**: verification belongs to a revision; never assume a signal holds on
   another revision; seeds stay `verified: false`; an identity matching no fingerprint is a
   question, not a new revision.
4. **Tagging discipline**: never import without `--variant`; never guess a variant to make a
   command run; fix a wrong tag with `--set-variant`, not by hand.
5. **Worked cases**: the Ioniq facelift (variant, with the Kona-family BMS as a revision); a VCU
   running change (revision); the G6 EREV (new profile); Kona OS vs SX2 (new profile); trim level
   (neither).

Pointers to it: `contributing-profiles`, `reverse-engineer-signal`, `ioniq-reverse-engineering`,
the AGENTS.md Profiles section, and `docs/bring-your-own-car/01-create-profile.md`.

## Critique

1. **This is a large change.** The rename, the variant registry, revisions, overrides and detection
   touch the loader, every write path, the analysis scoping and the evidence write-back that guards
   against ELM327 desync. Mitigation: nine phases that each land green, additive steps before the
   single refusing one, and the byte-identical `28kwh` invariant at every phase.
2. **Revisions are being built before any real second-revision capture exists.** The Ioniq
   `uds` BMS rests on OVMS's word and a port from the Kona. The synthetic fixture covers the edge
   cases, but it was designed by the same people who designed the feature. Expect details
   (fingerprint kinds, override whitelists) to change when the first real 38 kWh capture lands, and
   keep every seeded definition `verified: false`.
3. **"No variant, nothing runs" breaks every existing user once.** Every bundled profile becomes
   multi-variant, so after upgrading, the first live command refuses until a variant is chosen,
   including for scripts and CI. Users still on one of the three old names that imply a variant
   are not interrupted. For everyone else the refusal is a one-command fix that names itself. It needs a
   prominent changelog entry, and `CANAIR_VARIANT` for non-interactive setups.
4. **Two layers cost more than one.** The simple case (XPeng SR/LR cell lists) takes two edits — a
   BMS revision plus a variant mapping — where `variants:` on a PID would take one. That is
   deliberate: allowing both would mean two ways to gate the same PID, and the car-level shortcut
   is exactly the one dealer-replaced ECUs and mid-year changes break. If the indirection hurts in
   practice, add CLI sugar (`pids set-revisions --for-variant`) rather than a second schema key.
5. **Trim level as a variant is a trap.** Gen × battery × market × trim is combinatorial, and trims
   mostly change *which ECUs are fitted*, which canair already tolerates. `trims:` is informational
   only. If equipment presence ever needs modelling (e.g. `discover` expectations), that is a
   separate `equipment:` tag on the ECU.
6. **Applicability is not evidence.** Ungated means "believed to apply to every revision", not
   "verified on every revision" — hence `verified` resolving per revision. Without that split,
   every 28 kWh observation would silently become a 38 kWh claim.
7. **Detection has failure modes.** A sleeping ECU answers no fingerprint; a revision may have no
   known fingerprint; the extra identity read costs a round trip on a high-latency link. Hence:
   only ambiguous ECUs are probed, the result is cached, and an undetermined ECU skips its
   revision-gated PIDs rather than guessing. Pins are the escape hatch.
8. **Fingerprints can be wrong in both directions.** Part numbers get superseded with identical
   firmware (a false "new revision"), and a reflash can change behaviour under the same part number
   (a false match). `responds:` fingerprints are robust for protocol changes; layout changes under
   one part number need a SW-ID fingerprint or a pin. `canair identity` reporting "matches none"
   makes the first case visible instead of silently filing it.
9. **Inline overrides make files denser.** The alternative — per-revision overlay files next to the
   ECU file — keeps base files pristine but hides divergence in a second file and reintroduces the
   write-target question the layered-profile work avoided. Inline keeps the difference next to what
   it differs from.
10. **Attribute predicates were rejected** (`variants: {battery_kwh: ">=38"}`,
    `production: ">=2018-03"`): stringly-typed, hard to validate, and editing metadata would
    silently re-gate definitions. A build-date cut line is also exactly the rule a replaced ECU
    violates.
11. **Aliases are forever, and now there are five.** Every alias is permanent resolver surface.
    Keep them data (declared in the target), never code, and never alias a name that was not
    released.
12. **Full names are long and partly opaque.** `hyundai-ioniq-5-ne-ev` is 21 characters, and `ne`
    means nothing to most owners. They are typed once (`profile use`), and the picker and
    `profile list` explain the codes. The alternative is guaranteed renames: the Kona already needs
    its code, and the G6 needed its drivetrain within two years of launch.
13. **A separate variant setting can disagree with the profile.** `CANAIR_PROFILE` and
    `CANAIR_VARIANT` can be set independently. Keying the remembered choice by profile removes the
    stale-config case. An explicit variant the profile doesn't declare is a hard error naming the
    valid ones, never a fallback. The alias-implied variant ranks below every explicit choice, so an
    old name can't override what a user set deliberately.
14. **A capture tag can be wrong, and then it's worse than no tag.** `migrated` stamps rest on
    provenance notes, and `alias` stamps on an old name. Hence `attribution` on every tag, `alias`
    shown as unconfirmed, two profiles left untagged rather than guessed, and the
    self-identification checks. Those checks catch protocol revisions, but not a layout change
    behind the same DID. A careless `--set-variant` on a broad scope is the other risk; it shares
    `--set-state`'s scope gate and confirmation.
15. **Per-revision predicate resolution can turn a state dark on one revision only.**
    `validate states` must check each `when:` reference resolves under *every* reachable
    variant×revision combination, or it reintroduces the "renamed signal disabled a state while
    `validate all` was green" bug class on a new axis. The combinations are bounded by each
    variant's `revisions:` map, so this stays small.
16. **One more skill.** Skill sprawl is a real cost, since each one has to be found, triggered and
    kept current. This one is justified by how often the decision comes up outside a contribution;
    it stays short by pointing at docs and `--help` rather than restating them.
17. **The existing inheritance plan reserves `extends:`.** Variants and revisions are
    intra-profile, inheritance is inter-profile; they compose later (a sibling inheriting a base's
    ECU revisions, or a G6 EREV sharing the EV's body ECUs, is a question for that plan).
18. **Broadcast `signals/` and `dtcs:` stay revision-agnostic** here. Both could plausibly differ
    (a facelift gateway, new DTCs); defer until evidence shows it. The `ioniq-reverse-engineering`
    skill keeps its name — it names a project, not a profile.

## Docs

- `docs/concepts/variants.md` (new): variants, revisions, selection, the refusal, resolution
  order, pins, tagging.
- `docs/concepts/profiles.md`: naming convention, `--variant`, `profile_settings`, `variants.yaml`
  in the layout, the writes table.
- `docs/concepts/ecu-protocols.md`: an ECU's protocol can differ by revision.
- `docs/bring-your-own-car/01-create-profile.md`: the naming and boundary rules, pointing at the
  skill's worked cases.
- Profile pages: `docs/profiles/hyundai-ioniq-ae-ev.md` and `docs/profiles/nissan-leaf-ze1-ev.md`,
  with stubs at the old paths.
- `docs/reference/config.md`: `profile_settings`.
- `README.md` profile highlights (one line).
- `AGENTS.md` Profiles and Key Files: `variants.yaml`, `revisions:`, `Profile.variant`/`.revisions`,
  the load seam, aliases, `--variant`, and the skill in the Tools map.
- The CLI reference regenerated.
- `CHANGELOG.md`: the renames, aliases, the variant requirement, stricter per-variant AutoPID.
- A status note on `plans/2026-07-30-profile-variant-inheritance.md` pointing here.
