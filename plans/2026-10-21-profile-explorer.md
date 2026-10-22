# Profile explorer: browse a profile's ECUs, PIDs and IO controls in the docs

Status: **PROTOTYPE BUILT** (2026-10-22). Phases 1-3 exist in a first, quick form; see
"Prototype status" at the end for what is and isn't done.

The bundled-profile pages (`docs/profiles/`) describe a profile in prose and a few counts. To
see what the Ioniq's BMS actually exposes, a reader has to open `ecus/bms.yaml` on GitHub and
scroll through 30 ECU files (about 100 PIDs and 390 signals). The same is true of the
Leaf, and of every future profile. We want a page where a reader can pick a profile, see roughly
how its ECUs sit on the CAN buses, click an ECU, and browse its PIDs, signals, IO controls and
research leads, with a link from everything to the YAML it came from.

## Goals and non-goals

Goals:

- One **Explorer** page in the docs, in the "Bundled profiles" section, that works for every
  bundled profile with no per-profile page work.
- A **bus diagram**: CAN segments as lanes, ECUs placed on them, gateways visible.
- Drill-down: ECU, then PID, then signal, plus an IO controls view and a global signal search.
- A **source link** on every ECU, PID, signal and actuator, to the exact line in the YAML.
- Always current: generated at docs build time from `profiles/*/`, never committed.

Non-goals:

- Talking to a device. The page is static and read-only; it shows the `canair io` command to
  run instead of running anything.
- Live or captured values. Captures hold real readings (odometer, VIN-bearing identity
  responses) and are large. Definitions only in this plan; plotting decoded captured values is
  proposed in `plans/2026-10-22-explorer-capture-plots.md`.
- Editing a profile. Authoring stays in `canair pids`/`ecu`.

## Design

### Static, no framework

The site is MkDocs Material on GitHub Pages and CI builds it with `mkdocs build --strict`, so
the explorer is a data file plus a small vanilla-JS page, with no bundler and no new Python or
Node dependency. It follows the pattern `scripts/mkdocs_hooks/profiles_index.py` already set:
derive from `profiles/` at build time instead of committing generated output.

| Piece | Where | Job |
|-------|-------|-----|
| Export | `canlib/explorer_export.py` | Profile in, plain JSON-able dict out. Pure, unit-tested. |
| Hook | `scripts/mkdocs_hooks/explorer.py` | Writes `explorer/<profile>.json` as virtual files. |
| Page | `docs/profiles/explorer.md` | Mount point, `<div id="explorer">`, no-JS fallback text. |
| Assets | `docs/assets/explorer/explorer.{js,css}` | Rendering, filtering, URL state. |

Keeping the logic in the export module (and out of the JS) is deliberate: the JS only renders,
so what matters can be tested in pytest, where CI already runs.

### Export schema

An **allow-list**, not a dump: each field is copied by name, so a new profile field never
reaches the public site by accident.

- `profile`: name, car model, maturity, the stats the index already computes.
- `buses`: code, name, description, bitrate (from `can_buses.yaml`).
- `ecus[]`: name, tx/rx ids, protocol, bus codes, identity description and confidence, wake
  ritual, research items, DTC names.
- `ecus[].pids[]`: id, status, period, response frames, vehicle states.
- `pids[].signals[]`: name, expression, unit, min/max, type, verified, notes, source links.
- `ecus[].iocontrol[]`: did, label, on/off frames, session, hold, verified, notes.
- `broadcast[]`: arbitration id, sender, signals from `signals/<bus>.yaml`.
- Every record carries `src: {file, line}`.

**Identity values are excluded.** The `identity:` block can hold a live VIN, serial numbers and
part numbers (see the PII gate in `canlib/pii.py`). Only the free-text `description` and the
confidence flag are exported. A test asserts the exported JSON contains none of the profile's
identity fields.

Source lines: a plain `yaml.safe_load` drops line numbers, so the export reads the same files
with ruamel (already used by `canlib/yaml_rt.py`) and takes `lc.line` from each node. Links
point at the commit being built (`GITHUB_SHA`, falling back to `main` for a local build), so a
line number can't drift when the file is edited later.

### The bus diagram

The data says which segment each ECU is on (`can_bus:` codes) and nothing about physical
wiring, so the diagram is explicitly an **approximation** and says so in a caption.

- One horizontal lane per segment, labelled with name and bitrate. The Ioniq's `ALL` code (the
  gateway bridging every segment) is drawn as the gateway node, not as a lane.
- An ECU with several codes (the Leaf's VCM is on EV-CAN and CAR-CAN) is drawn once, with a
  tick on each lane, and is treated as a bridge. ECUs with no `can_bus:` go in a grey
  "unplaced" lane. `canair bus` already reports the same two gaps.
- The OBD-II port is drawn on the lane(s) it reaches, because it explains the gateway
  behaviour that the profile pages describe in prose.
- Node colour shows maturity (no PIDs, some signals, any verified). Clicking a node selects
  the ECU; hovering shows tx/rx ids.

The layout (lane order, node positions, bridge connectors) is computed in Python and shipped as
data, so it is deterministic and testable. The JS emits SVG from it. Mermaid was considered
and rejected: it is already on the site, but it can't filter, select or resize nodes, and
automatic layout of a bus-and-bridge picture is poor.

### Browsing

Left rail: profile switcher, ECU list with a text filter and a "verified only" toggle. Main
pane tabs per ECU: **Overview** (addressing, protocol, wake, bus), **PIDs** (expandable rows
into signals, with the expression and the `canair read ECU:PID` command to copy), **IO
control**, **Research**. A header search matches signal names across the whole profile.

State lives in the URL hash (`#ioniq-2017/BMS/2101`), so any view is linkable. Material's
`navigation.instant` swaps pages without reloading, so the script must mount from
`document$.subscribe(...)`, not on `DOMContentLoaded`, or it only works on a hard refresh.

IO controls carry a warning banner (they drive real hardware) and link to the Safety concepts
page (`docs/concepts/safety.md`). Frames marked unverified are labelled as such.

### Size

The Ioniq's definitions are about 370 KB as JSON before dropping `scan_log` and unused fields.
That compresses well, and GitHub Pages serves it compressed. One file per profile, fetched when that
profile is selected. If it gets too slow, split per ECU. Do not do that up front.

## Phases

1. **Export + data page.** `explorer_export.py`, the hook, tests, and a plain ECU/PID/signal
   browser with source links. Already useful on its own.
2. **Bus diagram.** Python layout, SVG rendering, selection linked to the browser.
3. **Search, deep links, IO controls, research tab.**
4. **Optional:** `canair profile explore [NAME]` writes the same page as one self-contained
   HTML file, so someone with an unpublished profile (the bring-your-own-car audience) gets
   it too. Only the profile-discovery wiring would be new; the export is shared.

## Changes outside the new files

- `mkdocs.yml`: hook, `extra_css`/`extra_javascript`, nav entry under "Bundled profiles".
- `.github/workflows/docs.yml`: add `canlib/explorer_export.py` to the path filter, so a change
  to the exporter redeploys (profile data paths are already covered).
- `docs/profiles/`: link to the explorer from the profiles index and each dedicated page.
- AGENTS.md: one line in the code map for the new module; docs-site notes if any.

## Testing

- pytest: the export for each bundled profile validates against a small JSON schema; it is
  deterministic (two runs are identical); no identity values or capture data appear; every
  `src.line` points at a line that contains the record's key; the layout puts every ECU on
  exactly the lanes its `can_bus:` lists.
- CI already runs `mkdocs build --strict`, which covers the hook and the page.
- The JS is checked by hand in a browser for each phase (instant navigation, hash links, dark
  mode, narrow screens). There is no JS test runner in CI and adding one for ~300 lines is not
  worth it yet; revisit if the script grows.

## Open questions

- Should the page also show a per-ECU "latest decoded state" like `canair ecu <ECU> pids`?
  That needs captures, so it depends on the privacy decision above.
- A profile-level toggle for tentative identities (the Leaf marks most ECUs tentative) may be
  worth a badge in the diagram rather than the list only.
- Whether `src` links should target the commit or `main` once release tags exist.

## Prototype status

Built (2026-10-22): `canlib/explorer_export.py`, `canlib/explorer_layout.py`,
`scripts/mkdocs_hooks/explorer.py`, `docs/profiles/explorer.md`,
`docs/assets/explorer/explorer.{js,css}` and `tests/test_explorer_export.py`, plus the
`mkdocs.yml`, workflow and docs links. `mkdocs build --strict` passes and the page was
exercised in headless Chrome (load, ECU select, PID expand, search deep link, IO tab, profile
switch, broadcast view, instant navigation).

Covered: all of phases 1 and 2, and most of phase 3 (search, hash deep links, IO control and
research tabs, plus a broadcast frames view and a verified-only filter).

Not done yet:

- Phase 4 (`canair profile explore`, a self-contained HTML file).
- The Leaf's tentative-identity badge in the diagram (the list shows it on the Overview tab only).
- The exporter's headline counts duplicate `profiles_index.profile_stats`; a test pins them equal,
  but one of the two should call the other.
- Reserving the lanes a bridge crosses (so no node is drawn on its connector) makes the Ioniq
  diagram wider than the page on a phone; it scrolls sideways.
