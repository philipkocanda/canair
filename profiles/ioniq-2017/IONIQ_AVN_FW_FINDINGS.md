# Findings from Ioniq AVN head-unit firmware RE (for canair to triage)

This document collects what reverse-engineering the 2017 Hyundai Ioniq (AE) **head-unit /
telematics firmware** in the sibling project `ioniq-avn-fw` found that is relevant to this canair
profile (`profiles/ioniq-2017/`). Items are folded into the profile one at a time, with canair's own
tooling, once confirmed on the car; so far the EPB at `0x7D5` came from §2, and the TPMS address in
§2 was tried and found not to apply.

## Provenance and how much to trust it

Source: `ioniq-avn-fw` (a repo of SD-card firmware dumps of the Ioniq AE head unit), decompiled to
this depth — Android/Dalvik bytecode, RH850 MICOM disassembly, and the flash CAN tables. Two
distinct head-unit platforms were dumped, **both the same 2017 Ioniq AE EV**:

- **EU** — LG AVN, Telechips TCC8931, builds `2022-05-19_SOP.014.6` and `2024-03-06_SOP.017.1`;
  RH850 MICOM. BlueLink/telematics **absent** (EU car never had it).
- **US** — Hyundai Mobis "DAUDIO" Gen5 unit, build `2020-11-02` (`AEEV.USA.SOP.V108.201102`, MICOM
  rev `292.190613`); a distinct RH850 MICOM image; Infobank "HSCAN" telematics middleware with the
  **full BlueLink stack present** (`com.hkmc.telematics.*`).

Confidence: this is decompiled-firmware evidence, which is a stronger basis than pure UDS
guesswork — but the **broadcast-frame bit layouts are firmware-derived** (from the head unit's own
`compose()`/`parse()` code and the MICOM packers) and are **mostly not yet confirmed against real
bus traffic**. Rule of thumb: **where canair has a real capture that disagrees, the capture wins.**
Treat everything below as a high-quality lead to confirm on the bus, not as ground truth.

Deeper detail lives in the source repo: `docs/micom/micom-can-findings.md`,
`docs/versions/2020-11-02_USA.SOP.V108.201102/telematics-findings.md`, and `docs/can-bus-wiring.md`.

## 1. Things canair already has right (mutual confirmation)

- **AVN diagnostic server** `tx 0x780` / `resp 0x788` — confirmed; EU firmware id string is
  `AE_EV EUR.SOP`. The RH850 runs the whole ISO-TP + UDS stack locally.
- **HVAC / FATC / DATC = `0x7B3`** — confirmed (the US telematics diag table maps `07b3` → FATC).
- **VCU = `0x7E2`, MCU = `0x7E3`** — confirmed by the US telematics diag table.
- **MM-CAN = 100 kbit/s** — the profile's `can_buses.yaml` already cites this repo's MICOM RS-CAN
  RE (prescaler 25, 16 tq, 40 MHz clock); reconfirmed.
- **Two charge windows + two preconditioning slots** — matches the head unit's data model exactly
  (two-slot `HSChargeVCU9` / `HSAirconVCU8`, and `Set3`/`Set4` in the EV C-CAN parser).

## 2. ECU → UDS diagnostic-address table (the main gift)

The US telematics runs a whole-vehicle DTC health poll for the BlueLink report. It does **not** open
a raw CAN socket — the RH850 MICOM owns the ISO-TP/UDS stack and the Android side maps each
diagnostic address to a `TP_DEVICE_*` tag (`DiagManager.changeMicomCanID`). The recovered map:

| ECU (device tag)         | Address | ECU (device tag)          | Address |
| ------------------------ | ------- | ------------------------- | ------- |
| ABS                      | `0x7D1` | MCU (motor)               | `0x7E3` |
| ECS (suspension)         | `0x7D3` | VCU (HCU on HEV)          | `0x7E2` |
| EMS (engine)             | `0x7E0` | LDC                       | `0x7C5` |
| TCU (transmission)       | `0x7E1` | BMS (OBC folded here)     | `0x7E4` |
| TPMS                     | `0x7D6` | FATC (climate / HVAC)     | `0x7B3` |
| EPB                      | `0x7D5` | AAF                       | `0x7E6` |
| LKAS                     | `0x7C4` | BCM (body)                | `0x7A0` |
| MDPS (steering)          | `0x7D4` | ACU (airbag)              | `0x59A` |
| SCC (smart cruise)       | `0x7D0` |                           |         |

Caveats before trusting any single row:

- The strings are labeled `_RESID` in the firmware, so **request vs response** is ambiguous — but
  the ones that overlap canair (`0x7E2` VCU, `0x7B3` HVAC) match canair's `tx_id`, so these are
  most likely **request/tx** addresses.
- This is a **multi-platform library** (many Hyundai/Kia models share it), so some rows may be
  variant aliases rather than this exact AE EV's bus config. `VCU`/`HCU` share `0x7E2` and
  `BMS`/`OBC` share `0x7E4` as powertrain-variant folds.
- **Already in the profile**, some under other names: ABS is ESC (`0x7D1`), MDPS is EPS
  (`0x7D4`), LKAS is MFC (`0x7C4`); SCC, MCU, VCU, BMS, AAF, BCM, FATC (HVAC) and EPB are
  registered.
- **Not in the profile:** LDC (`0x7C5`) and ACU (`0x59A`; the profile's airbag module SRS answers at
  `0x7D2`) are worth probing with `canair discover` / `canair scan`. EMS, TCU and ECS are combustion
  or suspension units this car does not have.
- **TPMS `0x7D6` does not apply to this car:** nothing answers there, and this car reads tire
  pressures from the BCM (`0x7A0`, DID `22C00B`).

## 3. Preconditioning and charge schedules are BROADCAST, not a UDS page

The head unit does **not** expose the schedules as a readable service-21/22 page. It **transmits**
them as broadcast frames the moment the user edits them in the UI:

- `0x57B` (`HU_EV_P_02`) — departure-climate / AC-charge schedule, two slots (day-mask + hour/min +
  6-bit temp + defrost). Twin `0x57A` (`HU_EV_P_01`).
- `0x1C0` (`HU_DATC_PE_00`) — DATC climate control frame.
- `0x1AA` — charge-power reduction (see §4).

This explains canair's open "where is preconditioning stored?" question. canair already ruled out
**VCU `21F2`** (a precondition-only reprogram didn't move it) and **HVAC `0x7B3`** (rejects service
`0x21`, NRC `0x11`) — both consistent with this. The pending **AVN `21F2` / HVAC `21F2` sweeps are
expected to come back empty**; the real observation path is a **passive broadcast capture**
(`canair sniff`, or `canair import can` of a SavvyCAN/candump log) of
`0x57A`/`0x57B`/`0x1C0`/`0x1AA` on MM-CAN and/or P-CAN, then a before/after diff around a single UI
schedule change.

Where the VCU or any other ECU stores the windows after receiving them is not established: the
settings recorded for the earlier VCU `21F2` before/after are known to be invalid.

## 4. Charge-power-reduction axis (station vs portable) semantics

- `0x1AA` **byte 3** is the charge-current-reduction command, with the two chargers on separate bit
  pairs: **bits [3:2] `CfObcEvseInCurModeState`** (station / wallbox EVSE) and **bits [1:0]
  `CfObcIccbInCurModeState`** (portable "granny" charger, ICCB). Both enums are
  **0 = MAX, 1 = DECREASING, 2 = MIN, 3 = INVALID**. (No ICCU on the AE platform — the OBC is the
  diagnostic ECU behind this.) Its **result** is exactly what canair already observes in OBC
  telemetry (pilot duty/amps, `AC_INPUT_A`).
- `0x57A` **byte 4 bits [1:0] `CrHuChgCtrMode`** = `MANUAL` / `PROGCHG_NONECON` / `PROGCHG_ECON` /
  `PROGCHG_ECONONLY`. This is the **ECON / off-peak scheduling axis — NOT a power knob** (an easy
  thing to mislabel).
- The US telematics expresses the reduction as a two-level `CHARGE_RATE {_80, _100}` percent,
  carried in the TMU1 command frame (§6), consistent with the `0x1AA` MAX/DECREASING/MIN axis.

## 5. The US car uses all three buses (MM-CAN + P/C-CAN + B-CAN)

Unlike the EU LG firmware (which drove **only MM-CAN** and reached the powertrain via gateway
frame-repeat), the US Mobis/Infobank telematics genuinely reads/writes three segments:

- **P-CAN / C-CAN (powertrain):** `Can2ACCANCsmEV` decodes VCU/BMS/MCU/OBC/DATC signals directly
  (`CF_Vcu_EvMod`, `CR_Bms_BatTotV_V`, `CF_OBC_AC/DCChargingStat`, `VCU9 ProgChar Set3/4`, …).
- **B-CAN (body):** `BodyCanApi` exposes `getIGNStatus` / `getACCStatus` / `getEngineStartUp` /
  `getOuterTemp`, plus writes `setIGNStatus` / `setEngineStartUp` — used by the remote-start /
  wakeup path (this is the IGN/ACC sensing behind the remote-start ignore-guards).

Bus-naming reminder for this profile: **firmware `C-CAN` ≡ the diagram's `P-CAN`** (the 500 kbit/s
powertrain bus). Caveat: this is proven at the Android telematics layer (which consumes
MICOM-forwarded frames); whether the head-unit hardware physically terminates all three buses, or
the gateway forwards them, is not yet disassembled.

## 6. Remote-start / IG3 command and status frames (US)

- **`0x587` (`TMU1`) — telematics → bus command.** `CF_TMU_IG3RLY` at **byte 4 bits [1:0]** =
  `NONE`/`IMMEDIATE`/`MONITORING`/`RESERVATION` (0/1/2/3). Same frame also carries `AIRCONCTR`
  (B0.4), `CHARGECTR` (B0.7), `DEFROSTCTR` (B0.6), `TEMPSET` (B1–B2, 16-bit), `IMMECHAR_MODE`
  (B3[1:0]), `SEPZONEDISP` (B4.7), `AIRCONCTR_CANCEL` (B4.6), `CHAR_CANCEL` (B4.4), `TMU_RDY`
  (B4.2). This layout is **cross-derived** from the EU `HU_GW_P_00` frame (identical signal set) —
  bus-unconfirmed.
- **`0x57B` (`VCU10`) — bus → telematics status.** `CF_Vcu_IG3RlyMd` at **byte 2 bits [7:4]**
  (observed values 2 and 10).

## 7. Direction nuance to resolve on the bus: `0x57B`

The two head-unit vendors use `0x57B` in **opposite directions**: EU LG firmware **transmits** it
(`HU_EV_P_02` schedule, head-unit → bus), while the US Infobank firmware **receives** it (`VCU10`
status, bus → head-unit). On a real capture, confirm which direction `0x57B` (and `0x57A`) actually
carries on this car before committing a `signals/` definition.

**Resolved for P-CAN, see §9:** on the powertrain bus `0x57A` and `0x57B` are transmitted by the
VCU. The EU head unit's `0x57A`/`0x57B` exist only on MM-CAN.

## 8. OBC address: settled on the car

The US telematics diag table never references `0x7E5` and folds OBC onto **`0x7E4`** with BMS. On
this car the OBC (part `36400-0E150`) answers at **`0x7E5`**, with thousands of captured OBC
responses there, and `0x7E4` answers as the BMS. The table's fold is a multi-platform alias.

## Suggested next actions (canair agent to decide)

1. Probe LDC (`0x7C5`) and ACU (`0x59A`) from §2 with `canair discover` / `canair scan`, and
   register them with `canair ecu add` only if they answer.
2. Redirect the preconditioning hunt: mark the AVN/HVAC `21F2` sweeps "expected empty" and add a
   **broadcast-capture** research entry — `canair sniff` (or `canair import can`) of
   `0x57A`/`0x57B`/`0x1C0`/`0x1AA`, diffed around a single UI schedule change (§3).
3. On a broadcast capture, define `0x57A`/`0x57B`/`0x1C0`/`0x1AA`/`0x587` in `signals/<bus>.yaml`
   with the firmware-derived layouts in §4/§6, then verify each field by before/after diff.
4. Resolve the `0x57B` direction (§7) by capture.

## 9. Checked against real P-CAN traffic (2026-10-29)

Source: the uhi22 Ioniq28Investigations P-CAN logs (a 2018 EU Ioniq 28 kWh, recorded Saturday
2025-02-01), its EPCU-on-a-bench log (the VCU and MCU running alone, nothing else on the bus) and
uhi22's test of which EPCU transceiver sends each frame. None of this is the owner's car.

Who sends what on P-CAN was settled by timing: frames from one controller leave in a fixed order a
fraction of a millisecond apart each cycle. Every frame below follows the gateway's `CGW1` (`0x541`)
by 0.3-4 ms in every cycle of both logs, and none is on the VCU's transceiver.

| Head-unit command (MM-CAN) | P-CAN frame | Evidence |
| --- | --- | --- |
| `0x54F` `HU_GW_P_05` clock | gateway `0x567` | identical layout; weekday bit reads Saturday |
| `0x57B` `HU_EV_P_02` departure schedule | gateway `0x55C` | decodes as two plausible slots; VCU echoes it |
| `0x57A` `HU_EV_P_01` off-peak window | gateway `0x588` | first four bytes echoed by the VCU |
| `0x1AA` `HU_EV_PE_00` EV settings | gateway `0x5DF` or `0x5B3` | both gateway-timed; not told apart |
| `0x54D`/`0x54E` nav road type/distance | gateway `0x55F`/`0x561`? | gateway-timed; all zero, no route active |
| `0x1C0` `HU_DATC_PE_00` climate comfort | gateway `0x5D3`? | only the AQS 'no change' nibble matches |

The VCU keeps the schedules and re-broadcasts them: departure on `0x57A` (bytes 0-6 identical to
`0x55C`, byte 7 bit 7 set) and the off-peak window on `0x579` (bytes 0-3 identical to `0x588`). The
EPCU alone on the bench still sends both with its own stored content, which is why a schedule runs
with the head unit unpowered. The VCU's `0x57B` is its `VCU10` status frame.

Consequences for the claims above:

- §3 and §7: the gateway does **not** repeat `0x57A`/`0x57B` onto P-CAN under the same IDs; on
  P-CAN those IDs belong to the VCU. The Soul EV `C_CAN` sheet rows for `0x57A`/`0x57B` describe the
  VCU's frames.
- Gateway translations change the ID but keep the bytes, so the head unit's own layouts decode the
  P-CAN copies. Definitions: `signals/body.yaml` (`CGW_CLOCK`, `GW_DEPART_*`, `GW_ECON_*`) and
  `signals/powertrain.yaml` (`VCU_DEPART_*`, `VCU_ECON_*`).
- The departure temperature field reads 63 and 7 in uhi22's car with preconditioning off, so its
  encoding is still unknown.
