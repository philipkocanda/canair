# WiCAN TODOs

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


## Unverified PIDs

- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU CAR_READY / PARK_BRAKE** — `B26` exceeds 22-byte response. Wrong offset for Ioniq?
- [ ] **BMS byte offsets** — MODULE_3/5_TEMP read padding (-50C), CUMULATIVE_ENERGY implausibly large
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan/EWP control DID** — Kingbolen scanner can actuate fan via UDS, specific DID unknown
- [ ] **Remaining IOControl** — BCM `2f b0 xx`, SKM `2f b1 08 03` (wakeup), all untested on Ioniq
