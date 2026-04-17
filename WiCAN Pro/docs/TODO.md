# Reverse Engineering TODOs

- [ ] Decode BCM charge scheduling DIDs (e.g. preheat and charge schedules, scheduled charging on/off, rear defrost on/off, etc.) -> can we also write these somehow?
- [ ] Scan various ECUs using --identity flag.
- [ ] Scan HVAC for PIDs (e.g. blower speed, A/C status)
- [ ] Scan HVAC for IOControl (e.g. blower speed control, A/C on/off)
- [ ] Test VESS for IOControl (sound!)
- [ ] Scan cluster for PIDs (range estimate, settings status)
- [ ] Scan SKM for PIDs (e.g. key status, start button status)
- [ ] Decode keyfob proximity state DID (IGPM or SKM — is fob nearby?)
- [ ] Capture IGPM BC03 B11 in all ignition states (Off/ACC/ON/Ready) to verify byte values
- [ ] Scan VCU for PIDs (e.g. motor temps, RPM, torque)
- [ ] Scan MCU for PIDs (e.g. motor temps, RPM, torque)
- [ ] Scan EPS for PIDs (e.g. steering angle, torque assist)
- [ ] Scan ABS for PIDs (e.g. wheel speeds, brake pressure)
- [ ] Scan BCM for IOControl (e.g. door lock/unlock, light control)
- [ ] Scan BMS for IOControl (e.g. battery fan control)
- [ ] **Ioniq charge cable unlock IOControl** — test `2FBC4103` (charge cable UNLOCK) and `2FBC3F03` (charge cable LOCK) on IGPM 0x770. Both accepted in IOControl scan (BC20-BC41 range untested visually). Useful for remotely releasing stuck charge cables. Requires extended session (`--session --hold`). Test during active charging session with key fob nearby.
- [ ] **Ioniq remote climate start** — research HVAC (0x7B3) IOControl DIDs for compressor, blower, heater, A/C control. Goal: remote cabin pre-conditioning via CAN. Likely requires SKM IGN1 wakeup (HV system needed for compressor/PTC heater). Scan `--scan --tx 7B3 --service 2F --session` and cross-reference with e-Niro/Ioniq 5 HVAC actuator tables.
- [ ] **Ioniq BCM security access** — crack UDS `27 01`/`27 02` seed-key algorithm for BCM (0x7A0) and IGPM (0x770). 48 algorithms tried, all failed. Need a valid seed-key pair (sniff from Kingbolen/GDS scanner) or firmware dump. See `WiCAN Pro/docs/wakeup-research.md` Security Access section. Check mhhauto.com forum (requires account).
- [ ] **Ioniq IGPM undecoded DIDs** — decode remaining bytes in BC01–BC07 status registers. Known candidates: seatbelt status, ambient light sensor, window positions, mirror fold state, wiper/washer state, washer fluid level, bonnet/hood open, rear defogger active, hazard lights, interior lamps (room/map/trunk), key-in-ignition warning, vehicle speed pulse. Requires ignition-ON testing for most. BC03/BC04 can be further decoded with lock/unlock cycle while monitoring.

## Unverified PIDs

- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU CAR_READY / PARK_BRAKE** — `B26` exceeds 22-byte response. Wrong offset for Ioniq?
- [ ] **BMS byte offsets** — MODULE_3/5_TEMP read padding (-50C), CUMULATIVE_ENERGY implausibly large
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan/EWP control DID** — Kingbolen scanner can actuate fan via UDS, specific DID unknown
- [ ] **Remaining IOControl** — BCM `2f b0 xx`, SKM `2f b1 08 03` (wakeup), all untested on Ioniq
