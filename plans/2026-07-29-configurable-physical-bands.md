# Configurable physical-value bands — vehicle profile + charging-grid region (`canair hunt --physical` / `investigate`)

## Goal

Make the **physical-band plausibility scan** — the reference-free heuristic that
flags a raw byte whose scaled value lands in a named physical range (HV pack
volts, mains RMS/peak, line frequency, 12 V rail) — **configurable** instead of
hardwired to a ~400 V EV on a 230 V / 50 Hz grid. Today the bands are a Python
constant (`canlib/xanalysis.py:740`) with a code comment that admits *"profile
overrides are future work."* This is the single highest-value residual gap for
running canair on non-Ioniq cars: on the wrong pack architecture or grid region
the scan **silently fails to flag the real signal** (no error, just no hit), so
it actively misleads rather than merely being unavailable.

Two config surfaces, one per axis (see Design): the **vehicle** bands (HV pack,
12 V rail) come from `profile.yaml`; the **grid** bands (mains, line frequency)
come from a **user-config region preset** (`grid_region: EU|UK|US|JP|CN|AU`),
since the same car charges on different grids depending on where its owner is —
with a one-time first-scan prompt to set the region.

Deliver additively so the bundled `ioniq-2017` profile behaves **identically**
(the built-in defaults reproduce today's five bands).

## Background — where the bands are used

`PHYSICAL_BANDS` (`canlib/xanalysis.py:740-746`) drives `physical_scan()`
(`:777`), consumed by exactly two commands:

- `canair hunt --physical` — `canlib/commands/hunt.py:521` (`physical_scan(lp, min_n=…, top=…)`)
- `canair investigate` — `canlib/commands/investigate.py:353` (`physical_scan(lp)`, the per-byte "physical-band flag" column)

`physical_scan()` takes a `LoadedPid` (`canlib/align.py:95`) and **no profile
context** today. The candidate *scalings* (`_PHYSICAL_SCALINGS`) and
*interpretations* (`_PHYSICAL_INTERPS`) are already generic — **only the band
*ranges* are the problem.**

## Why it breaks (the current constant)

```python
PHYSICAL_BANDS = [
    ("mains RMS V", 200.0, 250.0),   # assumes 230 V EU mains — misses NA 120 V
    ("mains peak V", 300.0, 340.0),  # 230 V peak — misses NA 170 V
    ("line freq Hz", 49.0, 51.0),    # assumes 50 Hz — misses 60 Hz Americas
    ("12V rail V", 11.0, 15.0),      # near-universal, fine
    ("HV pack V", 300.0, 450.0),     # ~400 V class — misses 800 V architectures
]
```

- **800 V architecture** (E-GMP 800 V, PPE, Taycan) runs ~450–850 V → never lands in `HV pack V` 300–450.
- **North American charging** is 120 V / 60 Hz → never lands in the 230 V / 50 Hz mains + line-freq bands.

## Motivating real case — from the WiCAN profiles reviewed

The upstream **VW EV MEB** profile
(`vehicle_profiles/vw/ev_meb.json`) is the clean proof: its `car_model` string
covers **both** 400 V cars (ID.3/ID.4/ID.5/Enyaq/Cupra Born) **and** 800 V PPE
cars (**Porsche Macan EV, Audi Q6 e-tron**) in one family. Its HV-voltage PID is

```
221E3B  HV_V = (((B4*256)+B5)/4)
```

On an ID.3 that decodes to ~360–420 (inside the current band); on a Macan
EV/Q6 e-tron it decodes to ~550–800 — **outside** canair's `HV pack V`
300–450 band, so `investigate`/`hunt --physical` would not flag it. Same profile
lineage, opposite outcome — exactly the case a code constant can't serve. See
**Appendix: WiCAN profile compatibility review** for the full survey (which also
surfaced *addressing* gaps that are **out of scope here** and filed against the
multi-vehicle plan).

## Design — two config surfaces, because the axes have different owners

A physical band belongs to one of **two independent axes**, and they are owned by
different things:

- **Vehicle axis** (`hv_pack`, `rail_12v`) — a property of the *car model*. An
  800 V pack is a fact about the vehicle, the same everywhere it's driven → lives
  in **`profile.yaml`**.
- **Grid axis** (`mains_rms`, `mains_peak`, `line_freq`) — a property of *where
  the car charges*, not the car. The same VW ID.4 charges from 230 V/50 Hz in
  Berlin and 240 V/60 Hz (split-phase L2) in Denver → lives in **user config**
  (`~/.config/canair/config.yaml`), as a **region preset**, so it's set once per
  user/location and applies across every profile.

This split is the core design decision: putting the grid bands in the profile
would force every user of a shared/community profile to re-edit it for their
region, and putting the pack voltage in user config would break when you switch
between a 400 V and an 800 V profile. Separating them makes each override live
where its truth lives.

### Vehicle axis — `profile.yaml` `physical_bands:`

Optional mapping of band name → `[low, high]` (natural unit). A name matching a
built-in band **overrides its range**; any other name **adds a custom band**;
unspecified built-ins keep their defaults. A profile declares only what *differs*.

```yaml
# profile.yaml — 800 V architecture EV
physical_bands:
  hv_pack: [450, 850]        # PPE / E-GMP 800 V pack
  # rail_12v inherits the built-in [11, 15]
  hv_pack_peak: [600, 900]   # custom band (added)
```

Canonical built-in band keys (stable identifiers, decoupled from the display
label). The **grid** keys are still *overridable* here for a one-off, but their
normal source is the region preset (below):

| key         | axis    | label (display) | default low | default high |
|-------------|---------|-----------------|-------------|--------------|
| `hv_pack`   | vehicle | HV pack V       | 300         | 450          |
| `rail_12v`  | vehicle | 12V rail V      | 11          | 15           |
| `mains_rms` | grid    | mains RMS V     | 200         | 250          |
| `mains_peak`| grid    | mains peak V    | 300         | 340          |
| `line_freq` | grid    | line freq Hz    | 49          | 51           |

### Grid axis — user config `grid_region:` preset

A single user-config key selects a **regional grid preset** that expands to the
`mains_rms` / `mains_peak` / `line_freq` bands. Regions with distinct grid
characteristics get distinct presets; some emit **multiple** mains bands (e.g. NA
Level 1 120 V *and* Level 2 240 V; Japan's dual 50/60 Hz split).

```yaml
# ~/.config/canair/config.yaml
grid_region: US        # EU | UK | US | JP | CN | AU  (case-insensitive)
```

Preset table (`canlib/grid_regions.py`). RMS ranges are nominal ±~10 %; peak =
RMS·√2 with a similar margin; frequency ±1 Hz:

| region | nominal        | `mains_rms` band(s)      | `mains_peak` band(s)     | `line_freq` |
|--------|----------------|--------------------------|--------------------------|-------------|
| `EU`   | 230 V / 50 Hz  | 207–253                  | 290–360                  | 49–51       |
| `UK`   | 230 V / 50 Hz  | 216–260                  | 300–370                  | 49–51       |
| `AU`   | 230 V / 50 Hz  | 207–253                  | 290–360                  | 49–51       |
| `CN`   | 220 V / 50 Hz  | 198–242                  | 280–345                  | 49–51       |
| `US`   | 120/240 V / 60 Hz | 108–132 **and** 216–264 | 150–190 **and** 300–375 | 59–61       |
| `JP`   | 100/200 V / 50+60 Hz | 90–110 **and** 190–220 | 130–160 **and** 270–315 | 49–51 **and** 59–61 |

The list is deliberately short and extensible — a region absent here is a
one-line addition to the table (or the user sets explicit bands in
`profile.yaml`). Non-listed regions fall back to the built-in EU-flavoured
defaults with a note.

### Precedence

For each band, first match wins:

1. **`profile.yaml` `physical_bands.<key>`** — explicit per-vehicle override (final say).
2. **user-config `grid_region` preset** — for the three grid bands only.
3. **built-in default** (`DEFAULT_PHYSICAL_BANDS`).

So a profile can still pin a grid band for a special case, but the normal path is
`grid_region` for grid bands + `physical_bands` for vehicle bands.

### First-scan region prompt

When `hunt --physical` / `investigate` runs the physical scan and **no
`grid_region` is set** (and the grid bands are still at default), print a
one-time, non-blocking hint to set the region — modelled on the conservative
`canlib/first_run.py` / `update_check` patterns:

- **TTY + interactive** → a short prompt: *"No charging-grid region set — mains /
  line-frequency band detection assumes 230 V / 50 Hz (EU). Set yours? [EU/UK/US/JP/CN/AU/skip]"*
  and persist the answer via `set_config_value("grid_region", …)`. `skip` writes a
  sentinel (`grid_region: unset` or a `grid_region_prompted: true` flag) so it
  **never asks again**.
- **Piped / non-interactive** → no prompt; a single stderr note the first time,
  gated by the same sentinel.
- Fully best-effort: any failure falls through to defaults (never blocks a scan).

Rejected (avoid overengineering): auto-detecting region from locale/timezone
(fragile, surprising); a `pack_voltage: 400|800` profile preset (the single
`hv_pack` override is clearer than a preset indirection — only one band).

## Phases

Three independently-shippable phases. **Phase 1** delivers the whole mechanism
and the vehicle axis (the higher-value 800 V case); **Phase 2** adds the grid
axis + region UX; **Phase 3** is docs/scaffold polish. Ioniq output is identical
after every phase.

### Phase 1 — Band resolver + vehicle-axis (`profile.yaml physical_bands:`)

The core plumbing: turn the hardcoded constant into a resolved, overridable list
and wire the profile (vehicle) axis end to end.

1. **Band resolver** — new `canlib/physical_bands.py` (keeps `xanalysis` lean):
   - `DEFAULT_PHYSICAL_BANDS: dict[str, tuple[str, float, float]]` keyed by
     canonical key → `(label, low, high)` (the current five).
   - `resolve_physical_bands(meta, *, grid_region=None) -> list[tuple[str, float, float]]`:
     start from defaults → (Phase 2) replace the three grid bands with the
     `grid_region` preset → apply `meta["physical_bands"]` overrides (override
     built-in by key, append unknown keys) → return the `(label, low, high)` list.
     Encodes the precedence above. In Phase 1 the `grid_region` arg is accepted but
     unused (defaults path).
2. **Thread bands into `physical_scan`** — add
   `bands: list[tuple[str, float, float]] | None = None` to `physical_scan()`
   (`canlib/xanalysis.py:777`); `None` → the default list (preserves current
   no-arg behaviour + existing `tests/test_xanalysis.py:628+`). Keep
   `PHYSICAL_BANDS` as the default source.
3. **Resolve at the command layer** — `canlib/commands/investigate.py:353` and
   `canlib/commands/hunt.py:521`: read `active().meta`, call
   `resolve_physical_bands(meta)`, pass `bands=`.
4. **Validation** — `canlib/commands/validate/pids.py::validate_meta` (`:857`),
   mirroring `_validate_addressing`/`_validate_isotp`: `_validate_physical_bands`
   — mapping of name → 2-element numeric `[low, high]` with `low < high`; unknown
   band keys allowed (custom), only shape checked. Wire alongside the existing
   `"isotp"`/`"addressing"` blocks (`:897-907`).

Phase-1 tests: `tests/test_xanalysis.py` (800 V payload missed by defaults, hit
with `hv_pack: [450, 850]`; custom band key; `bands=None` regression);
`tests/test_physical_bands.py` (`resolve_physical_bands` override/append/inherit
precedence); `tests/test_validate_meta.py` (malformed `physical_bands` rejected).

### Phase 2 — Grid axis (user-config `grid_region:` presets + first-scan prompt)

5. **Grid-region presets** — new `canlib/grid_regions.py`:
   - `GRID_PRESETS: dict[str, list[tuple[str, float, float]]]` keyed by region
     (`EU/UK/US/JP/CN/AU`), each a list of `(label, low, high)` for the grid bands
     (regions may emit >1 `mains_rms`/`mains_peak`/`line_freq` band).
   - `resolve_grid_bands(region: str | None) -> list[tuple[str, float, float]]` —
     case-insensitive lookup; unknown/`None` → EU-flavoured defaults.
6. **Wire the preset into the resolver + commands** — activate the `grid_region`
   arg in `resolve_physical_bands` (Phase 1 step 1); the two command call sites
   read `load_config()`'s `grid_region` and pass it in.
7. **User-config `grid_region` key** —
   - add `"grid_region"` to `_KNOWN_KEYS` (`canlib/commands/config.py:33`) and to
     the enum-validated keys with `valid: EU/UK/US/JP/CN/AU` (clear error on typo).
8. **First-scan region prompt** — a `canlib/first_run.py`-style helper (e.g.
   `canlib/grid_prompt.py`): TTY-only, one-shot via a `grid_region_prompted`
   sentinel, persists the answer through `set_config_value`; piped runs get a
   single stderr note (same sentinel); best-effort, never blocks a scan. Called
   from the two command sites before the scan when `grid_region` is unset.

Phase-2 tests: `tests/test_grid_regions.py` (each preset's shape; US/JP dual
bands; unknown region → EU default); extend `tests/test_physical_bands.py` for
the grid-preset precedence layer (profile override > grid preset > default);
`config set grid_region XX` enum-error test.

### Phase 3 — Scaffold + docs

9. **Scaffold surface** — commented `physical_bands:` example in
   `templates/profile.yaml.tmpl` (400 V-vs-800 V note); `grid_region` in
   `config.example.yaml`.
10. **Docs** — see the Docs section below (profiles concept, config doc,
    bring-your-own-car pointer, `AGENTS.md`, `CHANGELOG.md`).

`canair validate all` must keep passing for `ioniq-2017` + `xpeng-g6` at every
phase; add a fixture profile carrying `physical_bands:`.

- `docs/concepts/profiles.md` — `physical_bands:` (vehicle axis) next to
  `can_bitrate`/`isotp`/`addressing`/`quirks`; the 400 V-vs-800 V rationale +
  override-by-key semantics.
- `config.example.yaml` + a config doc — `grid_region` (grid axis), the preset
  table, and the "set once per location" rationale.
- `docs/bring-your-own-car/` — pointer: "800 V car → set `physical_bands.hv_pack`;
  not on a 230 V/50 Hz grid → set `grid_region` before `hunt --physical`".
- `AGENTS.md` — add `physical_bands:` to the `profile.yaml` field reference,
  `grid_region` to the `canair config` key list, and the axis split to the
  `hunt --physical` / `investigate` descriptions.
- `CHANGELOG.md` `[Unreleased]`. No `README.md` change (stays pointer-only).

---

## Appendix: WiCAN profile compatibility review

Surveyed the upstream `meatpiHQ/wican-fw` corpus (a focused six-profile deep-dive
plus a full 79-profile sweep) for how they'd map onto canair. **Only the
HV-voltage / grid observations belong to this plan**; the addressing findings are
recorded in `plans/2026-07-28-multi-vehicle-support.md` (they are **out of scope**
for the physical-bands change).

### Relevant to this plan (physical bands)

- **VW EV MEB** (`vw/ev_meb.json`) — HV_V `(((B4*256)+B5)/4)`; the profile family
  spans **400 V (ID.3/4/Enyaq/Born) and 800 V (Macan EV, Q6 e-tron)**. The
  motivating case above. Also reads `12V`-adjacent and temperature signals — the
  `rail_12v` default is fine.
- **800 V roster (full corpus).** A follow-up survey of all 79 upstream profiles
  confirms 800 V is common, not a corner case: **Porsche Taycan**, **Zeekr 001**,
  the **E-GMP** cars (Hyundai Ioniq 5/6/9, Kia EV6, Genesis GV60/GV70/G80), and
  800 V-capable **VW PPE / GM Ultium**. All exceed the built-in `HV pack V`
  300–450 band → `physical_bands.hv_pack` is a real in-catalog need.
- **BMW i3** (`bmw/i3.json`) — ~360 V pack; SOC only in the profile, but its class
  fits the default `hv_pack` band. No override needed (data point that the
  defaults are still right for many cars — overrides must stay opt-in).
- **BYD Dolphin, Nissan Ariya/Leaf, Renault Megane** — all EVs; HV/SOC signals.
  Ariya/Leaf/Renault are ~350–400 V (default band OK); confirms the default set
  is a reasonable baseline and overrides are the exception, not the rule.
- **Grid regionality** — several profiles are EU-authored (`ATST96`, 230 V/50 Hz
  assumptions) yet the cars sell in North America. Reinforces exposing
  `mains_rms`/`mains_peak`/`line_freq` as overridable, not just `hv_pack`.

### Out of scope — protocol / transport / addressing findings

The same six-profile survey surfaced a set of **protocol / transport / addressing**
findings that do **not** affect physical bands. To avoid duplication they live in
the multi-vehicle plan — see
`plans/2026-07-28-multi-vehicle-support.md` → *"Further compatibility findings
(2026-07-29 WiCAN profile survey)"*. Headline items: **ISO-TP extended (mixed)
11-bit addressing (BMW/PSA) [code gap]**, **functional-TX flow control
(Renault-Nissan) [code gap]**, non-`0x18` 29-bit priority (VW), non-`+8` 11-bit
RX offsets, non-`0xAA` padding, non-`22` service IDs, and the trailing
frame-count / multi-DID PID-key conventions.

**Net for this plan:** the survey confirms (a) the default HV/12 V bands are
correct for ~400 V EU cars, (b) 800 V packs and non-EU grids are real in-catalog
cases the overrides serve — so the two-axis configurability is warranted, and (c)
the addressing findings are independent multi-vehicle follow-ups.
