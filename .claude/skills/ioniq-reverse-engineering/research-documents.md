# Research findings documents and backlog

Scope: the research backlog commands and the findings documents under
`profiles/hyundai-ioniq-ae-ev/research/`.

## Backlog and research documents

Backlog: `canair research --summary` (per-ECU `research:` sections), plus `canair research
--gaps` for actuator DID families never read with `22` (PSM `B4xx`, SKM `B1xx`, DDM `B1xx`,
VESS `F0xx`).

Findings documents in `profiles/hyundai-ioniq-ae-ev/research/` collect what firmware,
third-party logs and Hyundai/Kia's own specs say, for triage before a capture session. Each opens
with YAML frontmatter: dates, status, per-section status (integrated, superseded, invalidated,
open), relations to the other documents and plans, and sources:

- `IGPM_FINDINGS.md` — the gateway (IGPM): hardware, gateway behaviour, diagnostics.
- `IONIQ_AVN_FW_FINDINGS.md` — what the head-unit/telematics firmware says about other ECUs.
- `FUNCTIONAL_ADDRESSING_FINDINGS.md` — `0x7DF` broadcast requests: routing, ECU behaviour,
  timing.
- `HKMC_UDS_SPEC_FINDINGS.md` — Hyundai/Kia's UDS spec (ES95486): session aliases, power-state
  rules behind NRC 0x22, identification DIDs, record-DID families and support maps, IOControl
  and routine id ranges, NRC and failure-type tables. The DID conventions for scanning are in the
  `reverse-engineer-signal` skill's `hyundai-kia-uds-conventions.md`.
- `VCU_FIRMWARE_FINDINGS.md`, `OBC_FIRMWARE_FINDINGS.md`, `MCU_FIRMWARE_FINDINGS.md` — static
  reads of the VCU, OBC and MCU flash images: service sets and session matrices, every record
  byte's source, CAN frame layouts, and (OBC section 9) requests that must never be sent.
- `DTC_MEANINGS_FINDINGS.md` — what Hyundai's service manual says each stored DTC means for the
  units with no `dtcs:` section yet (ESC, BCM, IGPM, smart key, TPMS).

