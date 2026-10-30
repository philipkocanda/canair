# Profile variants, phase 9 — AutoPID, the explorer and coverage per variant

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md). Depends on
phase 8.

## Goal

Make the remaining consumers variant-aware, so each car gets its own device profile, explorer view
and coverage report.

## What users see

- `wican autopid write` produces `out/<variant>/`.
- The explorer has a variant and revision switcher.
- `coverage` and `research` can be scoped to a revision.

## Changes

### AutoPID

- `wican autopid write`, `upload` and `diff` generate from the resolved definitions for the
  selected variant (phase 4 already refuses without one) into `out/<variant>/`.
- They refuse while any ECU's revision is unresolved, naming the ECU and the pin. The device
  polls a fixed list and can't detect, and shipping both revisions' PIDs would double the poll list
  and publish one signal name from two PIDs.
- The verified-only default reads the per-revision `verified` value from phase 7.
- `send_to` uniqueness (`canlib/wican/autopid_send_to.py`) is checked per variant.

### Explorer

- `canlib/explorer/export.py` exports raw definitions with their gates and per-revision overrides,
  allow-listed as today.
- `docs/assets/explorer/explorer.js` adds a variant/revision switcher that filters the view.
- The capture plots (`canlib/explorer/series.py`) decode each session with its own tags and label
  series by variant.
- Size budgets in `tests/test_explorer_series.py` are re-checked.

### Coverage and research

- `coverage` reports gaps per revision, never mixing two layouts in one byte map (phase 5's
  refusal).
- `research` entries gain an optional `revisions:` field, so a "confirm on a 38 kWh car" task is
  filterable.

### Docs and AGENTS.md

- `docs/profiles/hyundai-ioniq-ae-ev.md` describes both variants, what is verified on each, and how
  a 38 kWh owner can help.
- AGENTS.md Profiles and Key Files sections get their final form: `variants.yaml`, `revisions:`,
  `Profile.variant`/`.revisions`, the load seam, aliases, `--variant`.
- A status note on `plans/2026-07-30-profile-variant-inheritance.md` points at the design.

## Tests

- AutoPID: output per variant, the refusal when unresolved, per-variant verified filtering,
  `send_to` uniqueness per variant.
- Explorer export snapshot with gates; series labelling.
- Coverage per revision.

## Acceptance

- `out/28kwh/` is byte-identical to today's `out/` for the Ioniq.
- The explorer shows the `38kwh` BMS as unverified `uds` PIDs.

## Out of scope

Cross-profile sharing of ECU revisions (Kona ↔ Ioniq facelift BMS), which belongs to the
inheritance plan.

## Risks

- **AutoPID is what reaches a car.** The byte-identical `out/28kwh/` check is the guard.
