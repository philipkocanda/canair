# OBC / LDC ECU Disambiguation

## Problem

Three CAN addresses (0x7E2, 0x7E5, 0x746) have confusing cross-references between OBC and LDC functions. The current naming in `pids/` and `ecus.yaml` is inconsistent — `pids/ldc.yaml` points at 0x7E5 which identifies as an OBC, while `pids/vcu.yaml` points at 0x7E2 which identifies as an LDC. Two additional Kia Soul addresses (0x7CD, 0x79C) were referenced in the Obsidian vault but never confirmed on the Ioniq.

## Identity Scan Results (2026-04-19, during AC charging)

| Address | KWP2000 1A8C (ecu_id) | 1A8D/8E (sw_id) | 1A92 (hw) | 2101 data?                       | Current file      |
|---------|------------------------|------------------|-----------|----------------------------------|--------------------|
| 0x7E2   | `AEVLDC53`             | —                | —         | Yes — VCU data (gear, speed, drive mode) | `pids/vcu.yaml`   |
| 0x7E5   | `AEEOBC51`             | —                | —         | Yes — mixed OBC+LDC params       | `pids/ldc.yaml`   |
| 0x746   | `1.10`                 | `Z311` / `Z311`  | `3.5`     | NRC 0x12 on 2101/2102/2103       | (none)             |
| 0x7CD   | —                      | —                | —         | NO DATA                          | —                  |
| 0x79C   | —                      | —                | —         | NO DATA                          | —                  |

**0x7CD and 0x79C do not exist** on the Ioniq 2017 — those are Kia Soul-only addresses.

## Hyundai ECU ID Naming Convention

The KWP2000 identity string (DID `1A8C`) follows a pattern for Hyundai's own powertrain ECUs:

```
AE  +  X  +  MODULE  +  VER
│      │      │          │
│      │      │          └── Version digits (e.g. 53, 83, 51)
│      │      └── 3-char module code (LDC, MCU, OBC, BMS...)
│      └── Single variant letter
└── AE platform (Ioniq 2016-2019)
```

Known examples from the Ioniq 2017:

| Address | ecu_id       | Platform | Variant | Module | Ver | Notes                                      |
|---------|-------------|----------|---------|--------|-----|---------------------------------------------|
| 0x7E2   | `AEVLDC53`  | AE       | V       | LDC    | 53  | VLDC — serves VCU + LDC data                |
| 0x7E3   | `AEEMCU83`  | AE       | E       | MCU    | 83  | Motor Control Unit (inverter, part 36600-0E250) |
| 0x7E5   | `AEEOBC51`  | AE       | E       | OBC    | 51  | On-Board Charger — also reports LDC metrics |

The variant letter varies: `E` for MCU and OBC, `V` for LDC. Meaning unclear (Electric vs Vehicle? Supplier division?). The module code is the reliable identifier.

**This pattern does NOT apply to all ECUs.** Third-party supplied ECUs use their own conventions:
- BMS (0x7E4): `CGBMSE305AE1000A` — LG Chem supplier format
- Gateway (0x7E6): `EAE16AAFB30001` — different structure entirely
- Unknown-746 (0x746): `1.10` — just a version number, no Hyundai naming

## Interpretation

### 0x7E2 — VLDC (currently named VCU)

ECU firmware identifies as **VLDC** (Vehicle LDC). Despite this, its PID 2101 returns VCU-type data: gear position, vehicle speed, drive mode, brake lamp, EV ready state. This is likely a **combined VCULDC** — the Vehicle Control Unit that also controls the Low-voltage DC-DC converter. Part number 36601-0E250.

On the Kia Soul, the LDC was a standalone ECU at 0x7CD. On the Ioniq 2017, it was integrated into the VCU.

### 0x7E5 — EOBC (currently named LDC)

ECU firmware identifies as **EOBC** (Electric On-Board Charger). Its PID 2101 returns a mix of OBC and LDC parameters: HV input voltage, 12V output voltage/current, LDC temperature, OBC charge voltage, AC current, pilot duty cycle. PID 2102 only responds during AC charging.

The LDC metrics here are likely the OBC reporting on the adjacent LDC hardware in the shared enclosure (part 36400-0E150), not a separate LDC controller.

On the Kia Soul, the OBC was at 0x79C. On the Ioniq 2017, it moved to 0x7E5.

### 0x746 — Unknown-746

Alive but dormant. Responds to KWP2000 1A8C with `1.10`, sw `Z311`, hw `3.5`. Rejects all data reads (2101/2102/2103 NRC 0x12). Most identity fields `FFFFFFFF`. The `Z311` naming doesn't follow Hyundai conventions — could be a third-party charge communication controller (PLC/pilot signal handler) or a legacy address.

## Disambiguation Tests (TODO)

These tests will confirm whether the mixed params in 0x7E5 are truly OBC+LDC or just OBC:

| Test | Description                                             | Expected result                                   | State     |
|------|---------------------------------------------------------|---------------------------------------------------|-----------|
| A    | Read 0x7E5 (EOBC) 2101 while **driving** (not charging) | OBC params (charge V, AC A, pilot) → 0; LDC params (12V V/A, temp) → active | Requires driving |
| B    | Read 0x7E5 2102 while **driving**                        | NRC (confirms 2102 is OBC/charge-only)             | Requires driving |
| C    | Read 0x7E2 (VLDC) broader DIDs (2102, 2103)             | May reveal LDC-specific params not in 2101         | Can test now      |
| D    | Scan 0x746 with UDS `22 xxxx` DIDs                      | May respond to different service than KWP2000 `21xx` | Can test now  |
| E    | Compare 0x7E5 OBC_CHARGE_V with BMS voltage during charging vs idle | If OBC_CHARGE_V = 0 when not charging, it's OBC-sourced | Requires both states |

## Proposed Renames (after disambiguation)

| Current                       | Proposed                      | Reason                                               |
|-------------------------------|-------------------------------|------------------------------------------------------|
| `pids/ldc.yaml` (0x7E5)      | `pids/obc.yaml`               | ECU identifies as EOBC, not LDC                      |
| `pids/vcu.yaml` (0x7E2)      | keep name, update notes       | VCU is the functional role; note VLDC identity        |
| `pids/ahb.yaml` (0x7D5)      | delete or rename              | AHB identification was wrong (see ecus.yaml notes)   |
| `ecus.yaml` LDC entry (0x7E5) | rename to OBC/EOBC            | Matches confirmed ecu_id                             |
| `ecus.yaml` VCU entry (0x7E2) | add VLDC alias                | ecu_id says VLDC, functional role is VCU             |

## Open Questions

- **What is 0x746?** PLC comm controller? Pilot signal handler? Legacy address? The `Z311` naming suggests a non-Hyundai supplier. No data PIDs work.
- **Does 0x7E2 expose LDC-specific params?** Its 2101 only shows VCU data. Try 2102/2103 for LDC voltage/current/temp.
- **Should OBC vs LDC params be grouped separately in `pids/obc.yaml`?** If Test A confirms LDC params stay active without charging, they could be split into a `# LDC metrics` section.
