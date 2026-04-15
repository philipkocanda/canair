# WiCAN TODOs

## IOControl

- [ ] **Charge cable unlock** — test `2FBC4103` (UNLOCK) and `2FBC3F03` (LOCK) on IGPM 0x770. Useful for remotely releasing stuck charge cables. Test during active charging with key fob nearby. Commands: `can-request.py --raw 770:2FBC4103 --hold` / `--raw 770:2FBC3F03 --hold`

## Unverified PIDs

- [ ] **VCU speed** — verify if formula is MPH or km/h (compare with GPS)
- [ ] **VCU CAR_READY / PARK_BRAKE** — `B26` exceeds 22-byte response. Wrong offset for Ioniq?
- [ ] **BMS byte offsets** — MODULE_3/5_TEMP read padding (-50C), CUMULATIVE_ENERGY implausibly large
- [ ] **VCU 2102 / MCU 2101/2102** — captured but undecoded (motor temps, RPM, torque)
- [ ] **Battery fan/EWP control DID** — Kingbolen scanner can actuate fan via UDS, specific DID unknown
- [ ] **Remaining IOControl** — BCM `2f b0 xx`, SKM `2f b1 08 03` (wakeup), all untested on Ioniq
