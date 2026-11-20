# Ioniq vehicle facts, AutoPID staleness and gotchas

Scope: the car, dongle and bus basics, AutoPID staleness, and Ioniq-specific operating gotchas.

## Vehicle

- **Car:** Hyundai Ioniq Electric AE EV 2017 (28 kWh, Premium trim, NL market).
  Not the HEV/PHEV. The 2017 model year (2016–2019) has a different CAN layout
  and fewer PIDs than the 2020+ facelift. The 28 kWh is air-cooled (fan); the
  38 kWh has a liquid-cooled battery, different BMS, and more cell-voltage PIDs. The profile
  describes both as variants (`variants.yaml`: `28kwh`, `38kwh`); this car is the 28 kWh.
- **Dongle:** WiCAN Pro (MeatPi), MAC `9888e006734d`.
- **CAN:** ISO 15765-4 (11-bit, 500 kbps) — ELM327 protocol `6`.
- **AT init** (`profile.yaml`): `ATSP6;ATS0;ATAL;ATST96;`, plus
  `response_timeout_ms: 614` (ECUs respond slowly; the first request after idle
  can time out — retry once; see the Gotchas in `profile.yaml`).


## AutoPID staleness

In AutoPID mode the latest values are at `http://<ip>/autopid_data` (cached, so they may be stale
if the car is off). **AutoPID stops polling when the 12 V battery hits the `sleep_volt`
threshold** while the WiCAN may stay WiFi-reachable. So `/autopid_data` and MQTT can go stale
(lights reported "on" after parking) even though the device answers. A direct `canair read` still
works and values self-correct on the next poll. The device API and the sleep logic are in
`wican-hardware-and-protocol`.

## Ioniq-specific gotchas

- **Passive sniffing sees almost nothing.** The OBD-II port is gateway-isolated: only diagnostic
  request/response reaches it. The raw-CAN value here is pipelined UDS, not `canair sniff`.
- **`--session` and `--wake`.** `--session` (extended session `10 03`) is required for IGPM
  `2FBCxx`; its `22BCxx` reads need none. `--wake` rouses a deep-sleeping ECU first. IGPM (0x770)
  and BCM (0x7A0) wake from CAN activity alone. BMS and MCU answer only while the IG3 rail is on
  (ignition ON, charging, the 12 V top-up), and the VCU a few minutes beyond, so ACC alone is not
  enough.
- **`canair io --poll` can actuate hardware.** The IOControl TUI's background status poll
  (opt-in) sends each DID's release (`2F{DID}00`, returnControlToECU) every 3 s. On relay and
  solenoid DIDs this can re-assert defaults and cause an audible click. A KWP2000 `0x30` entry is
  polled, and switched on, only once it declares a `release:`. None of the Ioniq's KWP ECUs has
  one yet (see the OBC `iocontrol_scan` research entry).
- **Remote climate is a broadcast frame, not a diagnostic service.** The telematics unit's `0x587`
  (`TMU11`) carries `CF_Tmu_AirconCtr` (a 0→1 edge starts a ~15-min precondition in P, not READY,
  plugged); the EU car has no TMU, so nothing competes with an injected copy. Test it with a
  `frames:` profile action (`slcan-tcp` only, gated by `safety.allow_frame_injection`, dangerous
  bits — VehImmo/VehSld/CHAR_CANCEL/IG3RLY — held 0 by the frame's `mask`). Open question:
  whether the OBD→P-CAN gateway even forwards it (probably not; see the VCU `research:` entry
  and `plans/2026-11-09-broadcast-frame-actions.md`).

