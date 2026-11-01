# Variants

A [profile](profiles.md) describes one model generation with one drivetrain — say, the
Hyundai Ioniq Electric (AE). Within it, cars still differ in ways an owner can name: a
28 kWh pack or the 38 kWh facelift, a market with different diagnostics. Those are the
profile's **variants**, declared in its `variants.yaml`.

!!! note "What variants do so far"
    Variants record *what* versions of the car exist and what is known about each; you
    [name yours](#choosing-your-car) once, and [captures are
    tagged](#tagging-captures-with-the-car) with the one they came from. An ECU can declare
    [revisions](#ecu-revisions), and a variant can say which of them its car carries, which
    ECUs it has and which `profile.yaml` settings differ for it. Recognising an ECU's revision
    from the car itself is not built yet: pin it. The design for what follows is in
    [`plans/2026-10-30-profile-variants-and-rename.md`](https://github.com/philipkocanda/canair/blob/main/plans/2026-10-30-profile-variants-and-rename.md).

## What is a variant, and what is not

A variant is a version of the car **the owner can name** — from the brochure, the door-pillar
label or the battery size — **and** that matters to the diagnostics or to keeping captures apart.

| Difference | Where it belongs |
|---|---|
| Battery size, facelift, a market with different diagnostics | a **variant** |
| Model year | never by itself — makers change parts on production dates, not model years |
| Trim and equipment (BSD, heat pump, …) | **neither**: an ECU that isn't fitted just doesn't answer |
| Another generation, drivetrain or model | a **separate profile** (see [Naming a profile](profiles.md#naming-a-profile)) |

## IDs

A variant ID is local to its profile, so it names **only what differs**, never the profile:
`28kwh`, not `ioniq-ev-28`. It is lower-case words joined by `-`, with at least one letter (a bare
`28` would read back as a number), and never a year.

The convention is the **battery capacity the car is sold by**, as a whole number of kWh:
`28kwh`, `38kwh` for 38.3 kWh, `87kwh` for 87.5 kWh. When two variants share a pack, add the next
difference (`38kwh-us`). `canair validate` checks a battery-named ID against the battery it states.

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
| `model_code` | The maker's model code, often reported by the car's own ECUs (`AEEV`) |
| `designation` | The maker's name for this version (`IONIQ EV PE`, Hyundai's facelift name) |
| `generation` | Generation code (`AE`, `OS`, `ZE1`) |
| `production` | Build window, `"YYYY"` or `"YYYY-MM"` — the meaningful window, since running changes follow production dates |
| `model_years` | Informational; markets disagree on model year |
| `markets` | Where it was sold |
| `battery` | `usable_kwh`, `gross_kwh`, `nominal_voltage` (V), `series_cells`, `parallel_cells`, `modules`, `chemistry`, `cell_format`, `cell_supplier`, `cooling` |
| `trims` | Informational only |
| `revisions` | ECU → the [revisions](#ecu-revisions) of that ECU this car can carry (`BMS: [uds]`); an unlisted ECU may be any |
| `profile_overrides` | `profile.yaml` settings that differ for this car, replaced whole: `car_model`, `physical_bands`, `response_timeout_ms`, `init`, `multi_did_max`, `quirks` |
| `status` | `verified` (proven on a car), `community` (from community sources), `seed` (placeholder) |
| `sources` | Where the facts came from |
| `notes` | Free text |

The authoritative field list is `canlib/variants/__init__.py`; `canlib/schema/variants_schema.yaml`
documents it.

## Editing

Edit `variants.yaml` with [`canair variants`](../reference/cli/variants.md). Every edit is
checked and reverted on failure, and a rename also rewrites the captures tagged with the old ID.

```bash
canair variants                                   # list
canair variants show 38kwh
canair variants add 38kwh --name "Ioniq Electric 38 kWh (facelift)" \
    production=2019-06,2022 battery.usable_kwh=38.3 status=seed
canair variants set-meta 28kwh markets=EU,US,KR \
    "sources=first source" "sources=second source, with a comma"
canair variants unset-meta 28kwh trims
canair variants set-meta 38kwh profile_overrides.car_model="Ioniq Electric 38 kWh"
canair variants set-revisions 38kwh BMS uds
canair variants rename 28kwh 28kwh-eu
canair variants rm 38kwh
```

Short lists (`markets`, `trims`) are comma-separated; `sources` takes one source per
`sources=…`, so a source can contain a comma; a `profile.yaml` override is set one at a time as
`profile_overrides.<setting>=…`. Setting a field replaces it.

`canair profile show` lists a profile's variants, and the
[profile explorer](../profiles/explorer.md) shows them in its toolbar.

## Choosing your car

Name your car once per profile, and canair remembers it:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # on a terminal, omit --variant to pick
canair --variant 38kwh read BMS                          # or for one command
canair profile                                           # picker: re-select the current profile
                                                         # to change only the car
```

The variant in effect is the first of:

1. the global `--variant ID` flag;
2. `CANAIR_VARIANT`;
3. `profile_settings.<profile>.variant` in your config (what `profile use … --variant` writes);
4. the variant an **old profile name** implies, when you used one — `ioniq-2017` names the 28 kWh
   car, so a setup still using it keeps working unchanged;
5. the only variant, when the profile declares just one.

There is **no default**. On a profile with several variants and none chosen, every command that
talks to the car, records a capture or generates the AutoPID profile does nothing, and says how to
pick:

```text
error: hyundai-ioniq-ae-ev describes 2 variants and none is selected, so canair won't talk to the car.

  28kwh   Ioniq Electric 28 kWh (pre-facelift) · built 2016 – 2019-05 · 28 kWh usable · verified
  38kwh   Ioniq Electric 38 kWh (facelift) · built 2019-06 – 2022 · 38.3 kWh usable · seed

  Pick yours once:     canair profile use hyundai-ioniq-ae-ev --variant 28kwh
  Or for one command:  canair --variant 28kwh <command> …
  Not sure which you have?  canair identity BMS   (names the match)
```

`discover` and `identity` still run without a choice (they're how you find out), as long as they
don't `--save`. Editing definitions, `status`, `config` and read-only analysis don't need one
either. `canair status` and `canair profile show` report the variant in effect and where it came
from.

## Tagging captures with the car

A capture is evidence about one car. In a profile with several variants, a session says which
car it came from with a `variant` tag, plus an `attribution` recording how that was decided.

Every recording is tagged with [the car you chose](#choosing-your-car). A chosen variant must be
one of the profile's, and an unknown one is an error listing the valid ones.

| Where | What the tag does |
|---|---|
| `--save` on any live command | the session is tagged; its attribution is `selected` (you named it), `alias` (implied by an old profile name — shown as unconfirmed) or `only` (the profile's one variant) |
| `canair import uds` / `import can` | on a multi-variant profile the global `--variant` is **required** (an import is someone else's car, so `CANAIR_VARIANT` doesn't count) |
| `canair dtc` | each logged scan records the variant |
| analysis (`decode`, `captures`, `correlate`, …) | scoped to your car's sessions, plus untagged ones read as your car (with a note); with no car chosen, every tagged session and no untagged ones; `--variant all` / `--variant untagged` override that |

The tag is part of the recording, so a session recovered after a crash keeps the car it was
recorded with, whatever a later run selects.

`canair captures uds --sessions` shows each session's tag, and marks untagged sessions on a
profile with several variants. `canair validate captures` counts the untagged sessions (CI
fails if that count grows: `--max-untagged N`) and warns about a tag naming a variant the
profile doesn't declare.

### Tagging existing history

History recorded before a profile declared its variants is untagged. Stamp the sessions you
know came from one car, once per car:

```bash
canair captures migrate-variants 28kwh --dry-run          # preview
canair captures migrate-variants 28kwh --since 2026-01-01 --label commute
```

It only tags sessions that have no tag yet (attributed `migrated`) and only writes the
profile's own captures. To correct one session's tag afterwards, use
`canair captures uds --set-variant ID` with a scope filter (attributed `asserted`;
`--set-variant ''` removes it). The global `--variant untagged` selects every untagged
session.

### Tags survive merges

A copy of a session that lacks the tag is the same recording with less information. The capture
merge driver, `canair contribute` and a layered profile's read all keep the tagged copy, so an
older, untagged copy of your history can never strip the tags from it.

`canair variants rename` rewrites every tag that uses the variant (captures, the raw-CAN log index
and the DTC log) and every ECU fitted to it, and `canair variants rm` refuses while any of them
still does.

## ECU revisions

A variant describes the car; a **revision** describes one ECU. ECUs change on their own
production dates, and a dealer-replaced ECU follows the newer rules in an older car, so a
running change — a VCU that went from KWP2000 to UDS mid-year — is two revisions of that ECU,
not two variants. A revision exists only if some definition differs: a new part number with the
same diagnostics is another *fingerprint* of an existing revision, not a new one.

```yaml
VCU:
  tx_id: 0x7E2
  revisions:
    kwp:                                   # listed first: the reference revision
      identity: {id_protocol: KWP2000}
      fingerprints:
        - {responds: "1A90"}               # a safe read only this revision answers
    uds:
      identity: {id_protocol: UDS}
      fingerprints:
        - {identity: {part_number: "36601-0E1*"}}
  pids:
    "2101":
      revisions: [kwp]                     # only the kwp VCU answers it
      parameters: {...}
    "22E001":
      revisions: [uds]
      parameters: {...}
```

- **IDs** follow the variant rule and name what differs (`kwp`/`uds`, `150s`), local to the ECU.
- A revision may **override** the ECU's `sessions`, `wake`, `multi_did`, `multi_did_max`,
  `response_timeout_ms`, `addressing`, `rx_id` and `identity.id_protocol`.
- **Recorded identity (part number, calibration, notes, …) belongs to the first declared
  revision**, the one it was read from. On a car whose ECU is another revision, `canair ecu`
  shows none rather than the other revision's values; only `description`, `alias` and
  `id_protocol` carry over.
- **Gating:** `revisions:` on a PID or signal lists the revisions it applies to; absent means
  every revision. A signal's list must be within its PID's. Two PIDs of one ECU may ship the
  same signal name when their revisions are disjoint — that is how `VCU.VEHICLE_SPEED` stays one
  name, for state predicates and Home Assistant, across a protocol change.
- **Presence:** `variants: [60kwh]` on an ECU says only those cars have it.
- **Fingerprints** are recorded and checked, but canair does not match them against a car yet.

The bundled Ioniq is the worked case: its BMS has a `kwp` revision (every page the 28 kWh car
answers, `2101`…`21F2`) and a `uds` one (the Kona-family `220101`/`220105` pages the 38 kWh
facelift most likely answers, ported from the Kona profile and all unverified). `28kwh` allows
only `kwp`, `38kwh` only `uds`.

### What differs per revision, and what was verified where

A PID or signal shared by two revisions can differ field by field with `per_revision:`:

```yaml
SOC_BMS:
  expression: "B09/2"
  verified: true                  # verified on the reference revision
  per_revision:
    uds: {expression: "B10/2"}    # unverified on uds until it says verified: true
```

- A PID may override `period`, `response_frames`, `verified`, `notes`, `source` and
  `source_links`; a signal `expression`, `unit`, `min`, `max`, `type` (with `values`/`bits`/
  `fields`), `verified`, `notes`, `source` and `source_links`. Gates and `parameters:` can't be
  overridden: override each signal instead.
- **Evidence belongs to a revision.** A definition's own `verified` and `response_frames`
  describe its *reference* revision — the first declared one it applies to. On any other
  revision it is unverified, and has no frame count, unless its `per_revision` entry says
  otherwise. So a signal verified on one car never counts as verified on another, and the
  verified-only AutoPID output for a non-reference revision ships only what was verified there.
- When a live session learns a PID's frame count, it is written for the revision that session
  read: the PID's own `response_frames` on its reference revision, `per_revision.<id>` on
  another, and nowhere when the revision is undetermined. A count from one layout seeding a read
  of another would be an undercount, which desynchronises an ELM327 adapter.
- `canair pids upsert-param ECU PID NAME EXPR --revision uds …` writes `per_revision.uds` on an
  existing signal, and creates a new one gated to `[uds]`.

### Which revision your car has

1. a **pin**: `canair profile use NAME --pin VCU=uds` (remembered, as
   `profile_settings.<profile>.revisions`) or `--revision VCU=uds` for one command;
2. the **only** revision your variant allows (its `revisions:` entry), or the ECU's only one;
3. a **detected** revision: the one the ECU itself identified as (below), remembered from an
   earlier session;
4. otherwise **undetermined**: canair does not guess. That ECU's revision-specific PIDs and
   signals are skipped, with one note naming the fix; its other PIDs still read.

**Detection.** When a `read` or `monitor` is about to read an ECU that is still ambiguous,
canair first sends it its revisions' cheapest `responds:` fingerprint — a safe identity read,
one per ECU and never per PID — and settles the revision when exactly one matches. A KWP2000
ECU answers `1A90` and refuses a UDS `22F187`, so a protocol change is told apart without
knowing a single part number. The result is remembered per profile and variant in
`~/.config/canair/state/detected_revisions.yaml` (local, never contributed; safe to delete),
so the next session sends nothing. A sleeping ECU answers nothing and stays undetermined; pin it
if it can't be woken. An ECU that has one possible revision on your car is never probed.

**`canair identity ECU`** reports which revision its reading matches and which variants allow
it, with or without a car chosen — the way to find out which car you have. A reading that
contradicts a remembered detection replaces it; one that matches no revision is reported as a
question (possibly a revision the profile doesn't know yet), never filed as a new one.

Every command sees the definitions resolved this way, so a 40 kWh car never polls the UDS
VCU's PIDs. `canair status`, `canair profile show` and `canair ecu revisions VCU` report each
ECU's revision and where it came from.

### Captures and analysis

A recording tags each captured ECU that declares several revisions with the one it was on
(`revisions: {VCU: uds}`, attributed `pinned` or `only`). Analysis then reads only the
sessions that agree with your car's resolved revisions (an untagged session is read as yours).
**Byte-level analyses** — `investigate`, `hunt`, `correlate`, `coverage`, and `decode
--dump-bytes`/`--try` — refuse a scope that spans two revisions of an ECU they analyse, since a
byte offset means nothing across two layouts; scope with `--revision ECU=ID`. With no car
chosen (or `--variant all`), an ECU with several possible revisions is undetermined, so its
revision-specific definitions are left out of analysis too, and the note says so: name your car
(`--variant ID`), or add `--revision ECU=ID`.

### Editing

```bash
canair ecu revisions VCU                              # list
canair ecu revisions VCU add uds --protocol UDS
canair ecu revisions VCU set-fingerprint uds --responds 22F187
canair ecu revisions VCU rename uds uds-2018          # rewrites gates, variants, capture tags
canair ecu revisions VCU rm uds-2018                  # refused while anything uses it
canair pids set-revisions VCU:22E001 uds              # gate a PID (ECU:PID:SIGNAL: a signal)
canair ecu set-variants HEATER 60kwh                  # presence
canair variants set-revisions 40kwh VCU kwp
```

`canair validate` checks every reference, the subset rule, and that each state predicate
resolves on every variant and revision the profile describes — a predicate naming a signal
only one revision has would otherwise turn its state dark on the other car alone.
