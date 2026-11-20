# ECU status and per-ECU gotchas

Scope: live-status commands and the per-ECU gotchas that a signal list does not show.

## ECU status

Live status: `canair ecu` (registry, per-ECU stats, identity confidence) and `canair research`
(open backlog). The registry has 30 ECUs. Decode findings are recorded in each `ecus/<ecu>.yaml`;
the table below carries only gotchas that a signal list does not show.

| ECU | TX ID | Gotcha |
|-----|-------|--------|
| IGPM | 0x770 | IOControl map BC00–BCFF (lights, horn, signals, DRL, brake, trunk, locks, charge-cable lock). Status reads (BC03/04/06) need no session; `2FBCxx` does. |
| BCM | 0x7A0 | Part 95400-G7470 (Ioniq AE BEV only). TPMS and body state share it: `22C00B`. IOControl B000–B072 scanned; B061 answers NRC 0x31 (charge door not supported here). |
| VCU | 0x7E2 | Speed in 2101 is MPH (`((S20*256)+B19)*1.609344/100`), about 3.6 % above true speed. 2102 B18 is the coolant temperature (`B18-100`), not the 12 V rail: that is `VCU_LDC_OUTPUT_VOLTAGE` (`((B20<<8)|B19)/1000`). Motor RPM and torque live in the MCU. The 12 V DC-DC converter (LDC) is integrated here (ecu_id `AEVLDC53`, "VLDC"); there is no separate LDC ECU. |
| MCU | 0x7E3 | Inverter (36600-0E250). Motor RPM = 2102 `(S10<<8)|B09` (signed, verified). Most other 2101/2102 signals are unverified. |
| OBC | 0x7E5 | On-board charger only; no DC-DC control. Every 2101 byte is mapped from the firmware. Never send `99`, or `30 06/07` / `31 00/01` in session 0x90 (OBC_FIRMWARE_FINDINGS.md §9). |
| SKM | 0x7A5 | ACC relay IOControl is accepted, but **the relay only closes with the fob nearby**. `skm-wake` verifies via IGPM BC03. The powertrain stays dead without the fob. |
| PSM, VESS | 0x7A3, 0x736 | No readable live data. Both are driven by IOControl only (the seat motors run until OFF is sent). |

