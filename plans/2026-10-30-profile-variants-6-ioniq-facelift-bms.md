# Profile variants, phase 6 — the Ioniq facelift BMS as a real revision

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[research on the facelift](2026-10-30-profile-variants-and-rename.md#the-ioniq-facelift-a-variant-not-a-profile)).
Depends on phase 5.

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
