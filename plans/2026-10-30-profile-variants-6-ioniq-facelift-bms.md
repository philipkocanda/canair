# Profile variants, phase 6 — the Ioniq facelift BMS as a real revision

Status: **DONE** (2026-10-31). Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[research on the facelift](2026-10-30-profile-variants-and-rename.md#the-ioniq-facelift-a-variant-not-a-profile)).
Depends on phase 5.

## As built

- **Ported pages:** `220101` (24 signals) and `220105` (7: SOH, deterioration, dashboard SOC,
  cell deviation, heater temperature). The Kona's cells 97/98 at the end of `220105`, and its
  `220102`–`220104`/`220106` cell pages, are not ported: the plan names `220101`/`220105`, and the
  38 kWh pack's cell layout is unknown.
- **Offsets** are the Kona profile's (themselves OBDb's, via `canair bix`), cross-checked against
  the Ioniq `2101`/`2105` layout: each is that page's offset moved one data byte by the wider DID
  echo (skipping the PCI byte where it lands on one), the same relation the two cars' pages show.
- **Names follow the `kwp` pages** where the quantity is the same: the Kona's
  `NORMAL_CHARGE_PORT`/`RAPID_CHARGE_PORT` are `AC_CHARGER_CONNECTED`/`CHARGING_DC` (the two
  `vehicle_states.yaml` predicates read them), `SOC_DISPLAY` is `SOC_DISP`, `HEATER_TEMP` is
  `BATTERY_HEATER_1_TEMP`, the module temperatures are `MODULE_n_TEMP`, the fan signals and the
  `220101` charge-power limits take the `2101` names. The Kona's `CELL_VOLTAGE_DEVIATION` keeps its
  name: its scaling differs from `BATTERY_VOLTAGE_DEVIATION`.
- Both pages are `status: active` with PID-level `verified: false`, so the verified-only AutoPID
  default ships none of them.
- **The goldens were re-pointed:** the two golden modules' own config now names the suite's car
  (`28kwh`), as conftest does. They used to run with no car selected, which now leaves the BMS
  undetermined. No golden was regenerated.
- Boy Scout: `pids upsert-param` now adds a missing `parameters:` block after the PID's own fields
  instead of straight after its header.

## Goal

Give `hyundai-ioniq-ae-ev`'s BMS its two revisions, so the machinery runs on a real profile:

- `kwp` — every existing PID;
- `uds` — the Kona's BMS definitions, ported and unverified.

This is a data-only phase, done entirely through the `canair` editors, reviewable on its own and
reversible by reverting one commit.

## What users see

- `28kwh` users see no change at all.
- `38kwh` users get the Kona-family BMS PIDs (`220101`, `220105`, …), all marked unverified,
  instead of 28 kWh PIDs their car doesn't answer.

## Changes

- `ecu BMS revisions add kwp` and `add uds`:
  - `kwp`: `id_protocol: KWP2000`; fingerprints `responds: 1A90` (verified on the 28 kWh car: it
    returns `AEEV__ BMS`) and the ROM IDs from the identity notes (`4604`/`4804`/`4824`).
  - `uds`: `id_protocol: UDS`; fingerprint `responds: 22F187`, unverified.
- `pids set-revisions BMS:2101 … BMS:2105 kwp` — every existing BMS PID.
- **Port the Kona BMS** (`hyundai-kona-os-ev` `220101`/`220105`) into PIDs gated to `uds`:
  - re-derive every offset through `canair bix`, never by hand;
  - keep signal names identical to the `kwp` ones where the quantity is the same (`SOC_BMS`,
    `BATTERY_VOLTAGE`, …);
  - mark everything `verified: false`;
  - cite OVMS `vehicle_kianiroev` and the Kona profile in `source`/`source_links`;
  - keep cell counts the 38 kWh pack's, never the Kona's.
- `variants set-revisions 28kwh BMS kwp` and `variants set-revisions 38kwh BMS uds`.
- **Every BMS signal a `vehicle_states.yaml` predicate references** must exist under `uds` too, or
  `validate states` fails. That check is the point of doing this on a real profile.
- The `38kwh` variant's `sources:` and `notes` record the OVMS evidence, and the test that would
  turn the facelift into a separate profile.

## Tests

- `validate all` green, including the cross-revision predicate check.
- The fixture tests stay green; no code changes in this phase.

## Acceptance

- **Invariant:** with `--variant 28kwh`, every decode, coverage, investigate and AutoPID output is
  byte-identical to phase 5. The goldens are not regenerated.
- With `--variant 38kwh`, `ecu BMS pids` lists only the `uds` PIDs, and `decode` of a fabricated
  `620101…` payload, imported with `--variant 38kwh` into a scratch copy only, decodes.

## Out of scope

The VCU or any other facelift ECU. There is no evidence for them yet, and the VCU running change in
the design is hypothetical.

## Risks

- **It rests on OVMS's word and a port.** If a real 38 kWh capture shows the BMS answers `2101`
  after all, this phase is reverted or its gates widened. Every ported signal is unverified, so
  nothing ships to a device by default.
