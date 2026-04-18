# Reverse Engineering TODOs

- [ ] Scan HVAC for IOControl (e.g. blower speed control, A/C on/off)
- [ ] Test BCM IOControl with car asleep (unlocked)
- [ ] Test BCM IOControl with ACC ON
- [ ] Decode BCM charge scheduling DIDs (e.g. preheat and charge schedules, scheduled charging on/off, rear defrost on/off, etc.) -> can we also write these somehow?
- [x] Test BCM IOControl with car asleep (locked) (most are NRC 0x22 conditionsNotCorrect
- [ ] Decode keyfob proximity state DID (IGPM or SKM — is fob nearby?)
- [ ] Decode HVAC pids (e.g. blower speed, A/C status, temperature settings)
- [ ] Test VESS for IOControl (sound!)
- [ ] Scan SKM for PIDs (e.g. key status, start button status)
- [ ] Capture IGPM BC03 B11 in all ignition states (Off/ACC/ON/Ready) to verify byte values
- [x] Scan VCU for PIDs (e.g. motor temps, RPM, torque)
- [x] Scan MCU for PIDs (e.g. motor temps, RPM, torque)
- [x] Scan EPS for PIDs (e.g. steering angle, torque assist)
- [ ] Scan ESC for PIDs (e.g. wheel speeds, brake pressure)
- [ ] Scan BMS for IOControl (e.g. battery fan control) (should be safe as dangerous operations are likely protected by security access)
- [x] Scan cluster for PIDs (range estimate, settings status)
- [ ] Scan cluster for IOControl
- [x] Scan various ECUs using --identity flag.
- [x] Scan HVAC for PIDs (e.g. blower speed, A/C status)
- [ ] **Ioniq remote climate start** — research HVAC (0x7B3) IOControl DIDs for compressor, blower, heater, A/C control. Goal: remote cabin pre-conditioning via CAN. Likely requires SKM IGN1 wakeup (HV system needed for compressor/PTC heater). Scan `--scan --tx 7B3 --service 2F --session` and cross-reference with e-Niro/Ioniq 5 HVAC actuator tables.
- [ ] **Ioniq BCM security access** — crack UDS `27 01`/`27 02` seed-key algorithm for BCM (0x7A0) and IGPM (0x770). 48 algorithms tried, all failed. Need a valid seed-key pair (sniff from Kingbolen/GDS scanner) or firmware dump. See `WiCAN Pro/docs/wakeup-research.md` Security Access section. Check mhhauto.com forum (requires account).
- [ ] **Ioniq IGPM undecoded DIDs** — decode remaining bytes in BC01–BC07 status registers. Known candidates: seatbelt status, ambient light sensor, window positions, mirror fold state, wiper/washer state, washer fluid level, bonnet/hood open, rear defogger active, hazard lights, interior lamps (room/map/trunk), key-in-ignition warning, vehicle speed pulse. Requires ignition-ON testing for most. BC03/BC04 can be further decoded with lock/unlock cycle while monitoring.
- [ ] Add a "quality score" or "confidence level" to each PID based on factors like how many sources confirm it, how well it matches known data, etc. This can help prioritize which PIDs to focus on next and which ones are more likely to be correct.
- [ ] For each unverified PID, add a "verification plan" that outlines the specific steps needed to confirm its meaning. This can include things like what conditions to test under (e.g. ignition on, driving, charging), what other data to compare it against (e.g. GPS speed for VCU speed).

## Unverified PIDs

- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan control DID (likely on BMS)** — Kingbolen scanner can actuate fan via UDS, specific DID unknown
- [ ] **Remaining IOControl** — BCM `2f b0 xx`, SKM `2f b1 08 03` (wakeup).

## Untested PIDs — now in per-ECU `pids/*.yaml` files

As of 2026-04-18, all untested/undecoded PID research items have been migrated from
`untested-pids-index.yaml` into `research:` sections in the per-ECU YAML files under `pids/`.
Each ECU file now contains its own research backlog alongside its PID definitions.

See `pids/_schema.yaml` for the research entry format (type, target, status, priority,
prerequisite, notes, sources, what_to_test).

### Session plan

**Keyfob wake (deep sleep, no ACC) — ~5 min:**
- BMS 0x7E4: scan 22 BC01-BC0B, B002-B005, E003-E005
- OBC/LDC 0x7E5: test 22E011 (LDC aux battery monitoring)

**ACC on — ~15 min:**
- VCU 0x7E2: scan 22 E001-E010, decode 2102
- MCU 0x7E3: scan 22 E001-E010, re-decode 2101
- HVAC 0x7B3: scan 22 0100-010B
- CLU 0x7C6: scan 22 B001-B010
- AVN 0x7C0/7C1: probe (NO DATA during sleep, retry with ACC)
- ESC 0x7D1: scan 22 C101-C10F, 0101-010F
- EPS 0x7D4: scan 22 0101-0105
- 0x730: test 22 F010 (Ioniq 5 ESC address)

**ACC IOControl — ~10 min:**
- BCM 0x7A0: scan 2F B073-B0FF (incomplete from 2026-04-15)
- BCM 0x7A0: test accepted unknown DIDs (B019 room lamp, etc.)
- BMS 0x7E4: scan 2F E000-E0FF (battery fan DID)
- IGPM 0x770: test BC3F/BC41 (charge cable lock/unlock)

**During charging — ~10 min:**
- OBC/LDC 0x7E5: scan 22 E001-E011
- IGPM 0x770: test charge cable lock/unlock during active charge (verify the state actually propagates to the BMS and the rest of the car, and that it actually stops charging!)

**While driving — ~10 min:**
- VCU speed formula: compare with GPS (MPH vs km/h)
- ESC 0x7D1: verify REAL_SPEED_KMH (B12), test 220104 wheel speeds
- MCU 0x7E3: capture 2101/2102 under load

## Discovery Scan Follow-up (2026-04-17)

Full address sweep found 30 alive ECUs (14 new). New PID files created for SAS, PTC, SCC, MFC.

- [x] **Scan OBC-746 and PLC 0x733 during DCFC charging** — OBC-746: only 21F2 responds (6 bytes). PLC: all 255 NRCs, dead during AC charging (DC CCS only?).
- [ ] **Identify Unknown-783 and Unknown-7D2** — both respond to 1001 session control but have zero identity DIDs (no UDS F1xx, no KWP2000 1Axx). Try service 09 (vehicle info request) or broader DID ranges.
- [ ] **Identify Unknown-7D5** — only 2 identity DIDs (serial + "G7" app SW). Try service 22 broader ranges.

## Sharing when done

- [ ] Rename canreq to udscan
- [ ] Rename repository to wican-tools ("A set of WiCAN reverse engineering tools") 
- [ ] Make more generic (not Ioniq-specific) and share on GitHub with open source license (MIT/Apache)
- [ ] Write blog post about reverse engineering process, findings, and how to use the tool?
- [ ] Share with Gathering of Tweakers community
- [ ] Share on various Hyundai/Kia forums (ioniqforum.com)
