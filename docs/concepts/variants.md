# Variants

A [profile](profiles.md) describes one model generation with one drivetrain — say, the
Hyundai Ioniq Electric (AE). Within it, cars still differ in ways an owner can name: a
28 kWh pack or the 38 kWh facelift, a market with different diagnostics. Those are the
profile's **variants**, declared in its `variants.yaml`.

!!! note "Metadata only, for now"
    Variants currently record *what* versions of the car exist and what is known about
    each. Nothing selects a variant, limits a definition to one, or tags a capture with
    one yet; every definition in the profile still applies to every car. The design for
    what follows is in
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
