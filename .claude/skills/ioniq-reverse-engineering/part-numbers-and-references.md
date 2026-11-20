# Part numbers and key references

Scope: BCM part-number meaning, external references and the user-facing docs policy.

## Part numbers

### BCM 95400-G7470 is Ioniq AE Electric (BEV) only (2026-04-19)

The `Gx` code in Hyundai part numbers identifies the model; `G7` is exclusive to
the Ioniq AE Electric (BEV) — G2=Ioniq HEV, G5=Kia Niro, G6=Picanto all use a
different BCM. So IOControl DIDs that accept but do nothing are **not** explained
by cross-vehicle sharing; more likely **market variants** (the same G7 BCM ships
across EU/US/KR with features like heated handles or auto-dimming mirrors not
fitted on every trim).

## Key references

- [wican-fw repo](https://github.com/meatpiHQ/wican-fw) ·
  [WiCAN docs](https://meatpihq.github.io/wican-fw/)
- local `profiles/hyundai-ioniq-ae-ev/references/` spreadsheets (Kia Soul and Kia e-niro
  XLSX-format spreadsheets pulled from Google Docs):
  - [Kia Niro 64 kWh sheet](https://docs.google.com/spreadsheets/d/1eT2R8hmsD1hC__9LtnkZ3eDjLcdib9JR-3Myc97jy8M)
  - [Kia Soul EV sheet](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI)
  - Both contain a lot of raw CAN logs and PID decoding information, SKM commands and ECU mappings. 
- **User-facing docs:** `docs/` (task-first, for humans) — `docs/profiles/hyundai-ioniq-ae-ev.md`
  documents the bundled profile. Keep it and the README current when you change a
  user-facing capability; see the README↔`docs/` policy in `AGENTS.md`.
