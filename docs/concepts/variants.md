# Variants

A [profile](profiles.md) describes one model generation with one drivetrain. An example is the
Hyundai Ioniq Electric (AE). Cars inside one profile still differ in ways that an owner can name.
Examples are a 28 kWh pack or the 38 kWh facelift, and a market with different diagnostics. These
differences are the **variants** of the profile. The profile declares them in its `variants.yaml`.

!!! note "What variants do"
    Variants record *which* versions of the car exist and what canair knows about each version.
    You [name yours](#choosing-your-car) once. canair
    [tags captures](#tagging-captures-with-the-car) with the variant they came from.

    An electronic control unit (ECU) can declare [revisions](#ecu-revisions). A variant can say
    which of these revisions its car carries. It can also say which ECUs the car has and which
    `profile.yaml` settings differ for it.

    canair [detects](#which-revision-your-car-has) the revision of an ECU from the car itself.
    You can pin the revision when detection cannot settle it.

    The design and the record of each build phase are in
    [`plans/2026-10-30-profile-variants-and-rename.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-10-30-profile-variants-and-rename.md).

## What is a variant, and what is not

A variant is a version of the car that **the owner can name**. The owner finds the name in the
brochure, on the door-pillar label or from the battery size. The version must **also** matter to
the diagnostics or to keeping captures apart.

| Difference | Where it belongs |
|---|---|
| Battery size, facelift, a market with different diagnostics | a **variant** |
| Model year | never by itself — makers change parts on production dates, not model years |
| Trim and equipment (blind-spot detection, heat pump, …) | **neither**: an ECU that is not fitted does not answer |
| Another generation, drivetrain or model | a **separate profile** (see [Naming a profile](profiles.md#naming-a-profile)) |

## IDs

A variant ID is local to its profile. It names **only what differs** and never names the profile.
Write `28kwh`, not `ioniq-ev-28`. The ID is lower-case words joined by `-`. It has at least one
letter, because a bare `28` would read back as a number. It never contains a year.

The convention is the **battery capacity that the car is sold by**, as a whole number of kWh.
Examples are `28kwh`, `38kwh` for 38.3 kWh, and `87kwh` for 87.5 kWh. When two variants share a
pack, add the next difference (`38kwh-us`). `canair validate` checks a battery-named ID against the
battery that it states.

## The file

```yaml
variants:
  28kwh:
    name: Ioniq Electric 28 kWh (pre-facelift)
    model_code: AEEV
    generation: AE
    production: ["2016", "2019-05"]
    markets: [EU, US, KR]
    battery: {usable_kwh: 28, series_cells: 96, chemistry: Li-polymer, cooling: air}
    status: verified
    sources: [this profile's own captures (a 2017 car, NL market)]
  38kwh:
    name: Ioniq Electric 38 kWh (facelift)
    designation: IONIQ EV PE
    production: ["2019-06", "2022"]
    battery: {usable_kwh: 38.3}
    status: seed
```

| Field | Meaning |
|---|---|
| `name` | Human-readable name (required) |
| `also_known_as` | Other names that owners call this version (`Ioniq 28`, `Ioniq vFL`). The [profile pages](profiles.md#other-names-for-a-car) show them. Labels only |
| `model_code` | The maker's model code. The ECUs of the car often report it (`AEEV`) |
| `designation` | The maker's name for this version (`IONIQ EV PE`, the facelift name of Hyundai) |
| `generation` | Generation code (`AE`, `OS`, `ZE1`) |
| `production` | Build window, `"YYYY"` or `"YYYY-MM"`. This is the meaningful window, because running changes follow production dates |
| `model_years` | Informational. Markets disagree on the model year |
| `markets` | Where the maker sold the car |
| `battery` | `usable_kwh`, `gross_kwh`, `nominal_voltage` (V), `series_cells`, `parallel_cells`, `modules`, `chemistry`, `cell_format`, `cell_supplier`, `cooling` |
| `trims` | Informational only |
| `revisions` | ECU → the [revisions](#ecu-revisions) of that ECU that this car can carry (`BMS: [UDS]`). An unlisted ECU may have any revision |
| `profile_overrides` | `profile.yaml` settings that differ for this car. The override replaces the whole setting: `car_model`, `physical_bands`, `response_timeout_ms`, `init`, `multi_did_max`, `quirks` |
| `status` | `verified` (proven on a car), `community` (from community sources), `seed` (placeholder) |
| `sources` | Where the facts came from |
| `notes` | Free text |

The authoritative field list is `src/canair/variants/__init__.py`.
`src/canair/schema/variants_schema.yaml` documents it.

## Editing

Edit `variants.yaml` with [`canair variants`](../reference/cli/variants.md). canair checks every
edit and reverts it on failure. A rename also rewrites the captures that carry the old ID as a tag.

```bash
canair variants                                   # list
canair variants show 38kwh
canair variants add 38kwh --name "Ioniq Electric 38 kWh (facelift)" \
    production=2019-06,2022 battery.usable_kwh=38.3 status=seed
canair variants set-meta 28kwh markets=EU,US,KR \
    "sources=first source" "sources=second source, with a comma"
canair variants unset-meta 28kwh trims
canair variants set-meta 38kwh profile_overrides.car_model="Ioniq Electric 38 kWh"
canair variants set-revisions 38kwh BMS UDS
canair variants rename 28kwh 28kwh-eu
canair variants rm 38kwh
```

Use these rules to set fields:

- Separate the items of a short list (`markets`, `trims`, `also_known_as`) with commas.
- Give `sources` one source for each `sources=…`. A source can then contain a comma.
- Set a `profile.yaml` override one at a time, as `profile_overrides.<setting>=…`.
- Setting a field replaces it.

`canair profile show` lists the variants of a profile. The
[profile explorer](../profiles/explorer.md) shows them in its toolbar.

## Choosing your car

Name your car once for each profile. canair remembers the name:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # on a terminal, omit --variant to pick
canair --variant 38kwh read BMS                          # or for one command
canair profile                                           # picker: re-select the current profile
                                                         # to change only the car
```

canair uses the first of these that applies:

1. The global `--variant ID` flag.
2. `CANAIR_VARIANT`.
3. `profile_settings.<profile>.variant` in your config. The command `profile use … --variant`
    writes it.
4. The variant that an **old profile name** implies, when you used one. `ioniq-2017` names the
    28 kWh car, so a setup that still uses it keeps working unchanged.
5. The only variant, when the profile declares just one.

There is **no default**. A profile can have several variants and you may choose none. Then these
commands do nothing and say how to pick:

- every command that talks to the car
- every command that records a capture
- every command that generates the AutoPID profile

The words after `so canair won't` name the blocked action. This example is for a command that
talks to the car:

```text
error: hyundai-ioniq-ae-ev describes 2 variants and none is selected, so canair won't talk to the car.

  28kwh   Ioniq Electric 28 kWh (pre-facelift) · built 2016 – 2019-05 · 28 kWh usable · verified
  38kwh   Ioniq Electric 38 kWh (facelift) · built 2019-06 – 2022 · 38.3 kWh usable · seed

  Pick yours once:     canair profile use hyundai-ioniq-ae-ev --variant 28kwh
  Or for one command:  canair --variant 28kwh <command> …
  Not sure which you have?  canair identity BMS   (names the match)
```

`discover` and `identity` still run without a choice, because they are how you find out. They must
not use `--save`. Editing definitions, `status`, `config` and read-only analysis also need no
choice. `canair status` and `canair profile show` report the variant in effect and where it came
from.

## Tagging captures with the car

A capture is evidence about one car. In a profile with several variants, a session says which car
it came from. It carries a `variant` tag and an `attribution` that records how canair decided.

canair tags every recording with [the car you chose](#choosing-your-car). A chosen variant must be
one of the variants of the profile. An unknown variant is an error that lists the valid ones.

| Where | What the tag does |
|---|---|
| `--save` on any live command | canair tags the session. The attribution is `selected` (you named it), `alias` (an old profile name implied it — shown as unconfirmed) or `only` (the one variant of the profile) |
| `canair import uds` / `import can` | on a multi-variant profile the global `--variant` is **required** (an import comes from someone else's car, so `CANAIR_VARIANT` does not count) |
| `canair dtc` | each logged scan records the variant and the ECU revisions. `canair dtc log`, the open codes in `canair ecu` and the totals count only the scans of your car |
| analysis (`decode`, `captures`, `correlate`, …) | scoped to the sessions of your car, plus untagged sessions, which canair reads as your car (with a note). With no car chosen, canair reads every tagged session and no untagged session. `--variant all` and `--variant untagged` override this |

The tag is part of the recording. A session that canair recovers after a crash keeps the car that
it was recorded with, whatever a later run selects.

`canair captures uds --sessions` shows the tag of each session. On a profile with several variants,
it also marks untagged sessions. `canair validate captures` counts the untagged sessions. CI fails
if that count grows (`--max-untagged N`). The command also warns about a tag that names a variant
the profile does not declare.

### Tagging existing history

History that canair recorded before a profile declared its variants is untagged. Stamp the sessions
that you know came from one car. Do this once for each car:

```bash
canair captures migrate-variants 28kwh --dry-run          # preview
canair captures migrate-variants 28kwh --since 2026-01-01 --label commute
```

The command tags only sessions that have no tag yet (attributed `migrated`). It writes only the
captures of the profile itself.

To correct the tag of one session afterwards, use `canair captures uds --set-variant ID` with a
scope filter. canair attributes this tag as `asserted`. `--set-variant ''` removes the tag. The
global `--variant untagged` selects every untagged session.

### Tags survive merges

A copy of a session that lacks the tag is the same recording with less information. These parts of
canair all keep the tagged copy:

- the capture merge driver
- `canair contribute`
- a read of a layered profile

An older, untagged copy of your history can therefore never strip the tags from it.

`canair variants rename` rewrites every tag that uses the variant (captures, the raw-CAN log index
and the DTC log). It also rewrites every ECU fitted to the variant. `canair variants rm` refuses
while any of them still uses the variant.

## ECU revisions

A variant describes the car. A **revision** describes one ECU. ECUs change on their own production
dates. A dealer-replaced ECU also follows the newer rules in an older car. A running change is
therefore two revisions of that ECU and not two variants. An example is a Vehicle Control Unit
(VCU) that changed from Keyword Protocol 2000 (KWP2000) to Unified Diagnostic Services (UDS)
mid-year.

A revision exists only if some definition differs. A new part number with the same diagnostics is
another *fingerprint* of an existing revision. It is not a new revision.

```yaml
VCU:
  tx_id: 0x7E2
  revisions:
    KWP:                                   # listed first: the reference revision
      identity: {id_protocol: KWP2000}
      fingerprints:
        - {responds: "1A90"}               # a safe read only this revision answers
    uds:
      identity: {id_protocol: UDS}
      fingerprints:
        - {identity: {part_number: "36601-0E1*"}}
  pids:
    "2101":
      revisions: [KWP]                     # only the KWP VCU answers it
      parameters: {...}
    "22E001":
      revisions: [UDS]
      parameters: {...}
```

- **IDs** follow the variant rule and name what differs (`KWP`/`UDS`, `150s`). They are local to
  the ECU. Unlike a variant ID, a revision ID may use capitals. canair keeps its case. A pin or
  command argument matches it in any case (`--revision BMS=uds` finds `UDS`).
- A revision may **override** these fields of the ECU: `sessions`, `wake`, `multi_did`,
  `multi_did_max`, `response_timeout_ms`, `addressing`, `rx_id` and `identity.id_protocol`.
- **The first declared revision owns the recorded identity** (part number, calibration, notes, …).
  canair read the identity from that revision. On a car whose ECU is another revision, `canair ecu`
  shows no identity. It does not show the values of the other revision. Only `description`, `alias`
  and `id_protocol` carry over.
- **Gating:** `revisions:` on a PID or signal lists the revisions that it applies to. If the list
  is absent, the PID or signal applies to every revision. A signal can apply only to revisions
  that its PID also lists. `canair validate` reports an error when a signal lists a revision that
  is not in the `revisions:` list of its PID (`a signal can only apply where its PID does`).
- Two PIDs of one ECU may ship the same signal name when their revisions are disjoint. This is how
  `VCU.VEHICLE_SPEED` stays one name across a protocol change, for state predicates and Home
  Assistant.
- **Presence:** `variants: [60kwh]` on an ECU says that only those cars have it.
- **Fingerprints** decide which revision an ECU is. `canair identity` matches a full reading.
  `read` and `monitor` send the cheapest `responds:` probe (see
  [detection](#which-revision-your-car-has)).

The bundled Ioniq is the worked case. Its BMS has a `KWP` revision and a `UDS` revision:

- `KWP` has every page that the 28 kWh car answers, `2101`…`21F2`.
- `UDS` has the Kona-family `220101` and `220105` pages. The 38 kWh facelift most likely answers
  them. They come from the Kona profile and are all unverified.

`28kwh` allows only `KWP`. `38kwh` allows only `UDS`.

### What differs per revision, and what was verified where

A PID or signal that two revisions share can differ field by field with `per_revision:`:

```yaml
SOC_BMS:
  expression: "B09/2"
  verified: true                  # verified on the reference revision
  per_revision:
    uds: {expression: "B10/2"}    # unverified on uds until it says verified: true
```

- A PID may override `period`, `response_frames`, `verified`, `notes`, `source` and `source_links`.
- A signal may override `expression`, `unit`, `min`, `max`, `type` (with `values`, `bits` or
  `fields`), `verified`, `notes`, `source` and `source_links`.
- Gates and `parameters:` cannot be overridden. Override each signal instead.
- **Evidence belongs to a revision.** The `verified` and `response_frames` of a definition describe
  its *reference* revision. This is the first declared revision that the definition applies to.
  On any other revision, the definition is unverified and has no frame count. A `per_revision`
  entry can say otherwise. A signal that is verified on one car therefore never counts as verified
  on another car. The verified-only AutoPID output for a non-reference revision ships only what
  canair verified there.
- A live session can learn the frame count of a PID. canair writes the count for the revision that
  the session read:
    - on the reference revision, as the `response_frames` of the PID itself
    - on another revision, as `per_revision.<id>`
    - nowhere, when the revision is undetermined

    A count from one layout would be an undercount if it seeded a read of another layout. An
    undercount desynchronises an ELM327 adapter.
- `canair pids upsert-param ECU PID NAME EXPR --revision UDS …` writes `per_revision.UDS` on an
  existing signal. On a new signal, it creates the signal gated to `[uds]`.

### Which revision your car has

canair picks the revision of an ECU from the first of these that applies:

1. A **pin**. Run `canair profile use NAME --pin VCU=uds` (canair remembers it as
    `profile_settings.<profile>.revisions`). Or pass `--revision VCU=UDS` for one command.
2. The **only** revision that your variant allows (its `revisions:` entry), or the only revision
    of the ECU.
3. A **detected** revision. This is the revision that the ECU identified itself as (see below).
    canair remembers it from an earlier session.
4. Otherwise, the revision is **undetermined**. canair does not guess. It skips the
    revision-specific PIDs and signals of that ECU, with one note that names the fix. The other
    PIDs of the ECU still read.

**Detection.** Sometimes `read` or `monitor` is about to read an ECU that is still ambiguous. canair
then first sends the ECU the cheapest `responds:` fingerprint of its revisions. This is a safe
identity read. canair sends one for each ECU and never one for each PID. canair settles the
revision when exactly one revision matches.

A KWP2000 ECU answers `1A90` and refuses a UDS `22F187`. canair can therefore tell a protocol
change apart without knowing a single part number.

canair remembers the result for each profile and variant in
`~/.config/canair/state/detected_revisions.yaml`. The file is local, canair never contributes it,
and you can safely delete it. The next session then sends nothing.

A sleeping ECU answers nothing and stays undetermined. Pin the revision if you cannot wake the ECU.
canair never probes an ECU that has one possible revision on your car.

**`canair identity ECU`** reports which revision its reading matches and which variants allow that
revision. It works with or without a chosen car. Use it to find out which car you have.

- A reading that contradicts a remembered detection replaces the detection.
- A reading that matches no revision is reported as a question. The profile may not know that
  revision yet. canair never files such a reading as a new revision.

Every command sees the definitions that canair resolved this way. A 40 kWh car therefore never
polls the PIDs of the UDS VCU. `canair status`, `canair profile show` and
`canair ecu revisions VCU` report the revision of each ECU and where it came from.

### Captures and analysis

A recording tags each captured ECU that declares several revisions with the revision that the ECU
was on (`revisions: {VCU: UDS}`). The attribution is `pinned`, `only` or `detected`. The values
`migrated` and `asserted` mark tags that a migration or an explicit assertion added.

Analysis then reads only the sessions that agree with the resolved revisions of your car. It reads
an untagged session as yours.

**Byte-level analyses** are `investigate`, `hunt`, `correlate`, `coverage`, and
`decode --dump-bytes`/`--try`. They refuse a scope that spans two revisions of an ECU that they
analyse, because a byte offset means nothing across two layouts. Scope them with
`--revision ECU=ID`.

With no car chosen (or with `--variant all`), an ECU with several possible revisions is
undetermined. Analysis then leaves out its revision-specific definitions, and the note says so.
Name your car (`--variant ID`), or add `--revision ECU=ID`.

### Editing

```bash
canair ecu revisions VCU                              # list
canair ecu revisions VCU add UDS --protocol UDS
canair ecu revisions VCU set-fingerprint UDS --responds 22F187
canair ecu revisions VCU rename UDS UDS-2018          # rewrites gates, variants, capture tags
canair ecu revisions VCU rm UDS-2018                  # refused while anything uses it
canair pids set-revisions VCU:22E001 UDS              # gate a PID (ECU:PID:SIGNAL: a signal)
canair ecu set-variants HEATER 60kwh                  # presence
canair variants set-revisions 40kwh VCU KWP
```

`canair validate` checks every reference and the subset rule. It also checks that each state
predicate resolves on every variant and revision that the profile describes. A predicate that names
a signal which only one revision has would otherwise turn its state dark on the other car alone.
