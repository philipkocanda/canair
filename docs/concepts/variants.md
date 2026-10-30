# Variants

A [profile](profiles.md) describes one model generation with one drivetrain — say, the
Hyundai Ioniq Electric (AE). Within it, cars still differ in ways an owner can name: a
28 kWh pack or the 38 kWh facelift, a market with different diagnostics. Those are the
profile's **variants**, declared in its `variants.yaml`.

!!! note "What variants do so far"
    Variants record *what* versions of the car exist and what is known about each; you
    [name yours](#choosing-your-car) once, and [captures are
    tagged](#tagging-captures-with-the-car) with the one they came from.
    Nothing limits a definition to one variant yet: every definition in the profile still
    applies to every car. The design for what follows is in
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
| `battery` | `usable_kwh`, `gross_kwh`, `series_cells`, `chemistry`, `cooling` |
| `trims` | Informational only |
| `status` | `verified` (proven on a car), `community` (from community sources), `seed` (placeholder) |
| `sources` | Where the facts came from |
| `notes` | Free text |

The authoritative field list is `canlib/variants/__init__.py`; `canlib/schema/variants_schema.yaml`
documents it.

## Editing

`variants.yaml` is edited with [`canair variants`](../reference/cli/variants.md), never by hand.
Every edit is checked and reverted on failure.

```bash
canair variants                                   # list
canair variants show 38kwh
canair variants add 38kwh --name "Ioniq Electric 38 kWh (facelift)" \
    production=2019-06,2022 battery.usable_kwh=38.3 status=seed
canair variants set-meta 28kwh markets=EU,US,KR \
    "sources=first source" "sources=second source, with a comma"
canair variants unset-meta 28kwh trims
canair variants rename 28kwh 28kwh-eu
canair variants rm 38kwh
```

Short lists (`markets`, `trims`) are comma-separated; `sources` takes one source per
`sources=…`, so a source can contain a comma. Setting a field replaces it.

`canair profile show` lists a profile's variants, and the
[profile explorer](../profiles/explorer.md) shows them in its toolbar.

## Choosing your car

Name your car once per profile, and canair remembers it:

```bash
canair profile use hyundai-ioniq-ae-ev --variant 28kwh   # on a terminal, omit --variant to pick
canair --variant 38kwh read BMS                          # or for one command
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
and the DTC log), and `canair variants rm` refuses while any of them still does.
