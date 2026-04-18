# IOControl CLI Commands

All commands use extended diagnostic session (`--session` implied by `--hold`).
Actuators release when session drops (Ctrl+C or `--timeout`).

## Scanning

```sh
# Scan BCM IOControl DIDs — body/comfort range
# sends 2F <DID> 00 which is returnControl (OFF/release). That's the safest IOControl command — it tells the ECU to stop any external control and return to normal operation. No actuation risk.
canreq --scan --tx 7A0 --service 2F --range B000-B0FF --append 00 --session
```

## Quick Reference

Note: Append --wake flag if car is fully asleep (e.g. after 30 min idle) — this sends a wakeup message before the command, which may be necessary to get a response from the IGPM.

```sh
# Lights
python3 canreq.py --raw 770:2FBC0103 --hold   # Low beam ON
python3 canreq.py --raw 770:2FBC0203 --hold   # High beam ON
python3 canreq.py --raw 770:2FBC1803 --hold   # DRL ON
python3 canreq.py --raw 770:2FBC0403 --hold   # Tail/rear light ON
python3 canreq.py --raw 770:2FBC0803 --hold   # Rear fog light ON
python3 canreq.py --raw 770:2FBC1803 --hold --timeout 10 --wake # DRL ON for 10 sec, with wakeup if asleep

# Turn signals
python3 canreq.py --raw 770:2FBC1503 --hold --timeout 10  # Left indicator
python3 canreq.py --raw 770:2FBC1603 --hold --timeout 10  # Right indicator

# Locks (have keyfob ready — may trigger alarm!)
python3 canreq.py --raw 770:2FBC1003 --hold   # Door LOCK all
python3 canreq.py --raw 770:2FBC1103 --hold   # Door UNLOCK all
python3 canreq.py --raw 770:2FBC0903 --hold   # Trunk unlock

# Charge cable (IGPM)
python3 canreq.py --raw 770:2FBC3F03 --hold   # Charge cable LOCK
python3 canreq.py --raw 770:2FBC4103 --hold   # Charge cable UNLOCK

# Mirrors (BCM)
python3 canreq.py --raw 7A0:2FB05B03 --hold   # Mirrors FOLD
python3 canreq.py --raw 7A0:2FB05C03 --hold   # Mirrors UNFOLD

# SKM relay control (only works when car is charging or ACC on)
python3 canreq.py --raw 7A5:2FB108030A0A05 --session  # ACC ON
python3 canreq.py --raw 7A5:2FB10800 --session        # ACC OFF
python3 canreq.py --raw 7A5:2FB109030A0A05 --session  # IGN1 ON
python3 canreq.py --raw 7A5:2FB10900 --session        # IGN1 OFF

# VESS — vehicle exterior sound (pedestrian warning)
python3 canreq.py --raw 736:2FF011030001 --hold  # Play VESS sound 5 sec

# Replace 03 with 00 to turn OFF (e.g. 2FBC0100 = low beam OFF)
```

## BCM IOControl DIDs (0x7A0)

BCM requires extended diagnostic session (`1003`) AND SKM ACC+IGN1 power to respond to IOControl. For read-only data (service 0x22), no session needed — BCM responds directly.

**IOControl scan status:** B000-B072 fully scanned (2026-04-15, `--append 00` safe mode). B073-B0FF incomplete (SKM session dropped mid-scan).

| DID  | Label (e-Niro)            | Status    | Ioniq 2017 Notes                                                         |
|------|---------------------------|-----------|--------------------------------------------------------------------------|
| B003 | Unknown                   | Accepted  | Low DID range — possibly status/config register                          |
| B006 | Unknown                   | Accepted  |                                                                          |
| B008 | Unknown                   | Accepted  |                                                                          |
| B009 | Unknown                   | Accepted  |                                                                          |
| B00A | Unknown                   | Accepted  |                                                                          |
| B00F | Unknown                   | Accepted  |                                                                          |
| B019 | Room lamp (interior)      | Accepted  | DID exists. Untested for visible effect                                  |
| B01A | Unknown                   | Accepted  | Adjacent to room lamp — may be reading lamp or trunk lamp                |
| B025 | Unknown                   | Accepted  | Possibly window or wiper motor                                           |
| B028 | Unknown                   | Accepted  | Possibly window or wiper motor                                           |
| B029 | Unknown                   | Accepted  | Possibly window or wiper motor                                           |
| B030 | Unknown                   | Accepted  | Possibly wiper/washer pump                                               |
| B038 | Unknown                   | Accepted  |                                                                          |
| B039 | Unknown                   | Accepted  |                                                                          |
| B03A | Unknown                   | Accepted  |                                                                          |
| B03B | Unknown                   | Accepted  |                                                                          |
| B04D | Unknown                   | Accepted  |                                                                          |
| B057 | Unknown                   | Accepted  | Mirror/handle region                                                     |
| B059 | Heated door handle ON     | Accepted  | DID exists. May not have heated handles. Or may refer to heated steering wheel? (more likely IMO!) |
| B05A | Heated door handle LED    | Accepted  |                                                                          |
| B05B | Mirror fold               | Confirmed | Fold side mirrors in. From e-Niro                                        |
| B05C | Mirror unfold             | Confirmed | Unfold side mirrors. From e-Niro                                         |
| B061 | Charge door open          | Rejected  | **NRC 0x31 — NOT supported on Ioniq 2017.** Tested both with and without ACC power. DID doesn't exist on this model |
| B071 | Unknown                   | Accepted  |                                                                          |
| B072 | Unknown                   | Accepted  |                                                                          |

### Unscanned range

B073-B0FF not yet scanned (SKM session dropped during scan). Re-scan needed with more aggressive keepalive.

### BCM Read DIDs (service 0x22)

BCM has extensive readable data — no session needed for reads:

| DID  | Size | Content                                                           |
|------|------|-------------------------------------------------------------------|
| B001 | 20 B | Config/status flags                                               |
| B002 | 13 B | Minimal data                                                      |
| B003 | 27 B | Dense bitfields — BCM feature configuration                       |
| B004 | 13 B | Possible voltage/current calibration                              |
| B005 | 13 B | Unknown                                                           |
| B006 | 13 B | Mostly zeros                                                      |
| B007 | 13 B | Contains 0x01E0 (480 = 08:00 in minutes) — **preheat time?**     |
| B008 | 13 B | Unknown                                                           |
| B009 | 13 B | Repeating FEEE pattern — config word                              |
| B00A | 13 B | Unknown                                                           |
| B00B | 2 B  | Status register (7E00)                                            |
| B00C | 13 B | **Preheat schedule** — day-of-week bitmask (0x3F = 6 days)        |
| B00D | 13 B | **Preheat timer config** — FCFC E0, may encode departure time     |
| B00E | 13 B | Charge port flap status (B10:5 = open/closed) — **VERIFIED**     |
| B00F | 13 B | Unknown                                                           |
| C001 | 34 B | TPMS config — pressure limits, sensor setup                       |
| C002 | 27 B | TPMS pressure readings (4 wheels)                                 |
| C003-C007 | 34 B | Per-wheel TPMS sensor IDs (DO01, NA01, NTPM, DO02, NA02)   |
| C008-C00A | 34 B | Spare TPMS slots (all FF)                                   |
| C00B | 27 B | TPMS live data (already in AutoPID)                               |
| C00C-C00F | 41 B | Extended per-sensor TPMS data with status flags              |

## IGPM IOControl DIDs (0x770)

**Sleep state caveat:** IGPM IOControl requires extended session (`1003`). This works when the car is in light sleep (e.g. recently stopped charging), but after entering deep sleep, the IGPM becomes fully unresponsive — `1003` returns NO DATA, IOControl fails with NRC 0x7F, and even basic reads (`22BCxx`) return NO DATA. The exact light-sleep → deep-sleep timeout is unknown — likely 5-30 minutes after last CAN bus activity. Being plugged in / charging keeps the bus active.

Warning: Attempt to open door/trunk (after unlocked using IOControl) will trigger the horn. Because alarm is not disarmed by IOControl, the horn will sound until the door/trunk is closed again or the keyfob is used to silence it. Test with keyfob in hand and be prepared to silence the alarm immediately after unlocking.

| DID  | Label (e-Niro/Soul)       | Status    | Ioniq 2017 Notes                                                             |
|------|---------------------------|-----------|------------------------------------------------------------------------------|
| BC01 | Low beam                  | Confirmed | Headlights on                                                                |
| BC02 | High beam                 | Confirmed | High beams on                                                                |
| BC03 | Front fog light           | Confirmed | Nothing visible — Ioniq 2017 base trim has no fog lights (no bulb installed) |
| BC04 | Tail/rear light           | Confirmed | Tail lights on                                                               |
| BC05 | Red backlight flash       | -         | Nothing visible — may be CHMSL (center high-mount stop lamp) flash           |
| BC07 | Horn                      | Confirmed | **HORN!!!** Not labeled in Soul/e-Niro tables but confirmed on Ioniq         |
| BC08 | Rear fog light            | Confirmed | Rear fog light on                                                            |
| BC09 | Trunk unlock              | Confirmed | Triggers alarm if car is locked! Releases latch only (no lift motor)         |
| BC0A | Welcome/approach light?   | -         | Untested. Likely puddle lights or DRL greeting on unlock                     |
| BC0C | Rear defogger relay       | -         | Untested. Well-documented on e-Niro/Soul — defrosts rear window              |
| BC10 | Door LOCK all             | Confirmed | Locks all doors - does not ARM alarm! |
| BC11 | Door UNLOCK all           | Confirmed | Unlocks all doors — does NOT disarm alarm!                                   |
| BC12 | Individual door unlock?   | -         | Untested. Soul uses 6F prefix — may unlock driver door only. Alarm risk!     |
| BC14 | Individual door unlock?   | -         | Untested. Soul uses 6F prefix — may unlock a specific door. Alarm risk!      |
| BC15 | Left turn indicator       | Confirmed | Left indicator on                                                            |
| BC16 | Right turn indicator      | Confirmed | Right indicator on                                                           |
| BC18 | DRL (daytime running lights) | Confirmed | DRL on — confirmed on Ioniq 2017                                         |
| BC1B | Reverse/side marker?      | -         | Untested. Another lighting output in the post-turn-signal DID range          |
| BC1C | Luggage lamp              | -         | TODO test                                                                    |
| BC25 | Unknown                   | No effect | Accepted but no visible/audible effect. Tested 2026-04-15 (Ready mode)       |
| BC2B | Rear left brake light     | Confirmed | Works!                                                                       |
| BC2C | Rear right brake light    | Confirmed | Works!                                                                       |
| BC2D | CHMSL (center brake light)| Confirmed | Center high-mount stop lamp. Confirmed 2026-04-15                            |
| BC3F | Charge cable LOCK         | Confirmed | Works!                                                                       |
| BC41 | Charge cable UNLOCK       | Confirmed | Works, but likely does not stop charge session (DANGER! needs testing!)       |
| BC42 | Unknown                   | No effect | Accepted but no visible/audible effect. Tested 2026-04-15 (Ready mode)       |
| BC43 | Unknown                   | No effect | Accepted but no visible/audible effect. Tested 2026-04-15 (Ready mode)       |
| BC44 | Unknown                   | No effect | Accepted but no visible/audible effect. Tested 2026-04-15 (Ready mode)       |

### Rejected DIDs (NRC 0x31 — requestOutOfRange)

BC00, BC06, BC0B, BC0D, BC0E, BC17, BC19, BC1A

All other DIDs in BC1D-BCFF not listed above (scanned 2026-04-15 in Ready mode).

### Conditional reject (NRC 0x22 — conditionsNotCorrect)

BC13 — may need ignition on or other precondition.
BC1B — may be reverse light (only accepts command when car is in reverse gear?)

## Safety Notes

- **IOControl (0x2F) is inherently reversible** — all actuators release when the diagnostic session ends
- **No write (0x2E) or flash (0x34/36/37) commands** — no bricking risk
- BC12/BC14 may trigger alarm — test with keyfob in hand
- BC0A, BC0C, BC18, BC1B are safe to test blind — worst case is an unexpected light/relay that releases on session drop

## SKM IOControl DIDs (0x7A5)

Requires extended session (`1003`). SKM wakes from **rapid-fire `1001`** (50x at 150ms intervals with `ATST10`) — no fob needed. A single `1001` is not enough; sustained CAN traffic wakes the transceiver. SKM falls asleep after ~60s of bus inactivity.

Magic bytes `0A 0A 05` are from Kia Soul and confirmed working on Ioniq 2017.

**CRITICAL: Fob proximity required.** SKM returns a positive UDS response (`6FB10803`) even without the keyfob nearby, but the relay **physically doesn't close**. The `skm-wake` command verifies engagement by reading IGPM BC03 ignition byte after the IOControl command.

**ACC does NOT latch** — relay stays on only while the IOControl session is held (TesterPresent keepalive). When the session drops, ACC relay opens, doors re-lock.

| DID  | Label (Soul)    | Status    | Ioniq 2017 Notes                                                                |
|------|-----------------|-----------|---------------------------------------------------------------------------------|
| B108 | ACC relay       | Confirmed | ACC ON: dash lights, infotainment, doors unlock. **Requires fob proximity** — UDS positive response without fob is misleading (relay doesn't physically close). Verified via IGPM BC03 ignition byte. NRC 0x22 if ACC already on. `freezeCurrentState` (02) returns status bytes `6255`. |
| B109 | IGN1 relay      | Untested  | Ignition 1 — wakes all ECUs including HV system. **Use with extreme caution**   |
| B10A | IGN2 relay      | Untested  | Ignition 2 — purpose unclear on Ioniq EV. From Kia Soul (ICE start circuit)     |
| B10B | Start relay     | Untested  | Starter motor relay — **DO NOT USE on EV** (no starter motor, unknown behavior) |

## PSM IOControl DIDs (0x7A3)

Power seat module. From e-Niro — **untested on Ioniq 2017** (may not have power seats on base trim). Likely requires ACC/IGN on. Actuators are continuous motors — hold command active for desired duration, send OFF to stop.

| DID  | Label (e-Niro)               | Status   | Notes                         |
|------|------------------------------|----------|-------------------------------|
| B401 | Seat slide forward           | Untested | Continuous motor — send 00 to stop |
| B402 | Seat slide backward          | Untested |                               |
| B403 | Seat recline upright         | Untested |                               |
| B404 | Seat recline backward        | Untested |                               |
| B405 | Seat front height up         | Untested |                               |
| B406 | Seat front height down       | Untested |                               |
| B407 | Seat rear height up          | Untested |                               |
| B408 | Seat rear height down        | Untested |                               |

## VESS IOControl DIDs (0x736)

Vehicle Exterior Sound System (pedestrian warning buzzer). From online sources — **untested on Ioniq 2017**.

| DID  | Label              | Status   | Notes                                             |
|------|--------------------|----------|---------------------------------------------------|
| F011 | Play VESS sound    | Untested | `2FF011030001` — plays for ~5 sec. Payload bytes unclear |

## HVAC IOControl (0x7B3) — TODO

**Goal: remote climate pre-conditioning** (heat/cool cabin before driving).

BCM controls the charge/precondition timer schedule:
- **22B00C**: Day-of-week bitmask (0x3F = 6 days). Byte 0 = preheat days, rest = charging schedule (00 = disabled).
- **22B007**: Contains 0x01E0 = 480 = minutes from midnight for 08:00 — strong candidate for departure time.
- **22B00D**: Timer config bytes (FCFC E0) — encoding unclear, needs comparison test.

To decode: change preheat time in infotainment, then re-read B007/B00C/B00D to see which bytes change.

Not yet researched. The HVAC ECU (0x7B3) responds to read requests (`22 01 00`) and provides temperature/humidity data. IOControl commands for compressor, blower, heater, and A/C are unknown.

**Blower fan (fan-only mode) may work without ACC/IGN** — it's a 12V safety feature (ventilation for someone locked in the car). Compressor and PTC heater require HV system (IGN1+).

### Discovery scan commands

```sh
# HVAC actuators — E0xx range (Hyundai/Kia powertrain/HVAC convention)
python3 canreq.py --scan --tx 7B3 --service 2F --range E000-E0FF --append 03 --session

# Body/comfort range — blower fan control might be here
python3 canreq.py --scan --tx 7B3 --service 2F --range B000-B0FF --append 03 --session
```

These scans are safe — worst case is a blast of air or fan spin-up. No alarm, no locks.

### Research plan

1. Run discovery scans above to find accepted DIDs
2. Cross-reference with Kia Niro / e-Niro / Ioniq 5 HVAC actuator tables
3. Test blower fan control first (low risk, 12V only)
4. Determine if compressor/heater need SKM IGN1 wakeup
5. Build full remote climate sequence: SKM wakeup → HVAC actuators → monitor temp → shutdown

**Potential approach:** SKM IGN1 ON → HVAC blower/compressor/heater ON → monitor cabin temp via `22 01 00` → HVAC OFF → SKM IGN1 OFF. Car must be charging (SKM is unpowered when fully asleep).
