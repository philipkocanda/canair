# Reverse Engineering TODOs

- [ ] Decode BCM charge scheduling DIDs (e.g. preheat and charge schedules, scheduled charging on/off, rear defrost on/off, etc.) -> can we also write these somehow?
- [x] Scan various ECUs using --identity flag.
- [x] Scan HVAC for PIDs (e.g. blower speed, A/C status)
- [ ] Scan HVAC for IOControl (e.g. blower speed control, A/C on/off)
- [ ] Test VESS for IOControl (sound!)
- [x] Scan cluster for PIDs (range estimate, settings status)
- [ ] Scan SKM for PIDs (e.g. key status, start button status)
- [ ] Decode keyfob proximity state DID (IGPM or SKM — is fob nearby?)
- [ ] Capture IGPM BC03 B11 in all ignition states (Off/ACC/ON/Ready) to verify byte values
- [x] Scan VCU for PIDs (e.g. motor temps, RPM, torque)
- [x] Scan MCU for PIDs (e.g. motor temps, RPM, torque)
- [x] Scan EPS for PIDs (e.g. steering angle, torque assist)
- [ ] Scan ABS for PIDs (e.g. wheel speeds, brake pressure)
- [ ] Scan BCM for IOControl (e.g. door lock/unlock, light control)
- [ ] Scan BMS for IOControl (e.g. battery fan control)
- [ ] **Ioniq charge cable unlock IOControl** — test `2FBC4103` (charge cable UNLOCK) and `2FBC3F03` (charge cable LOCK) on IGPM 0x770. Both accepted in IOControl scan (BC20-BC41 range untested visually). Useful for remotely releasing stuck charge cables. Requires extended session (`--session --hold`). Test during active charging session with key fob nearby.
- [ ] **Ioniq remote climate start** — research HVAC (0x7B3) IOControl DIDs for compressor, blower, heater, A/C control. Goal: remote cabin pre-conditioning via CAN. Likely requires SKM IGN1 wakeup (HV system needed for compressor/PTC heater). Scan `--scan --tx 7B3 --service 2F --session` and cross-reference with e-Niro/Ioniq 5 HVAC actuator tables.
- [ ] **Ioniq BCM security access** — crack UDS `27 01`/`27 02` seed-key algorithm for BCM (0x7A0) and IGPM (0x770). 48 algorithms tried, all failed. Need a valid seed-key pair (sniff from Kingbolen/GDS scanner) or firmware dump. See `WiCAN Pro/docs/wakeup-research.md` Security Access section. Check mhhauto.com forum (requires account).
- [ ] **Ioniq IGPM undecoded DIDs** — decode remaining bytes in BC01–BC07 status registers. Known candidates: seatbelt status, ambient light sensor, window positions, mirror fold state, wiper/washer state, washer fluid level, bonnet/hood open, rear defogger active, hazard lights, interior lamps (room/map/trunk), key-in-ignition warning, vehicle speed pulse. Requires ignition-ON testing for most. BC03/BC04 can be further decoded with lock/unlock cycle while monitoring.

## Unverified PIDs

- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan control DID (likely on BMS)** — Kingbolen scanner can actuate fan via UDS, specific DID unknown
- [ ] **Remaining IOControl** — BCM `2f b0 xx`, SKM `2f b1 08 03` (wakeup).

## Discovery Scan Follow-up (2026-04-17)

Full address sweep found 30 alive ECUs (14 new). New PID files created for SAS, PTC, SCC, MFC.

- [ ] **Scan OBC-746 and PLC 0x733 during active charging** — both ECUs have no live data when idle. Need to scan service 21 01-0F and 22 E001-E020 while plugged in (AC or DC).
- [ ] **Decode SAS 0x725 steering angle** — 220100 has 48 bytes. B03-B04 likely signed 16-bit angle (0 = straight). Verify by turning wheel during monitor mode.
- [ ] **Decode PTC 0x7B6 heater data** — 220100 has 27 bytes, mostly zeros when off. Need captures with cabin heater ON to see changing bytes. Candidate temps at B13/B15/B16.
- [ ] **Decode SCC 0x7D0 cruise control** — 5 DIDs (0100-0103, 0105). Need driving captures with cruise control active to decode target speed, gap, radar distance.
- [ ] **Decode MFC 0x7C4 ADAS camera** — 3 DIDs (0100-0102). Need driving captures to see lane detection, speed sign recognition data.
- [ ] **Identify Unknown-783 and Unknown-7D2** — both respond to 1001 session control but have zero identity DIDs (no UDS F1xx, no KWP2000 1Axx). Try service 09 (vehicle info request) or broader DID ranges.
- [ ] **Identify Unknown-7D5** — only 2 identity DIDs (serial + "G7" app SW). Try service 22 broader ranges.
