# IOControl CLI Commands

All commands use extended diagnostic session (`--session` implied by `--hold`).
Actuators release when session drops (Ctrl+C or `--timeout`).

## Quick Reference

```sh
# Lights
python3 can-request.py --raw 770:2FBC0103 --hold   # Low beam ON
python3 can-request.py --raw 770:2FBC0203 --hold   # High beam ON
python3 can-request.py --raw 770:2FBC0403 --hold   # Tail/rear light ON
python3 can-request.py --raw 770:2FBC0803 --hold   # Rear fog light ON

# Turn signals
python3 can-request.py --raw 770:2FBC1503 --hold --timeout 10  # Left indicator
python3 can-request.py --raw 770:2FBC1603 --hold --timeout 10  # Right indicator

# Locks (have keyfob ready — may trigger alarm!)
python3 can-request.py --raw 770:2FBC1003 --hold   # Door LOCK all
python3 can-request.py --raw 770:2FBC1103 --hold   # Door UNLOCK all
python3 can-request.py --raw 770:2FBC0903 --hold   # Trunk unlock

# Charge cable (IGPM)
python3 can-request.py --raw 770:2FBC3F03 --hold   # Charge cable LOCK
python3 can-request.py --raw 770:2FBC4103 --hold   # Charge cable UNLOCK

# Charge port flap (BCM — no --session needed)
python3 can-request.py --raw 7A0:2FB06103 --hold   # Charge door OPEN
python3 can-request.py --raw 7A0:2FB06100 --hold   # Charge door CLOSE (release)

# Mirrors (BCM)
python3 can-request.py --raw 7A0:2FB05B03 --hold   # Mirrors FOLD
python3 can-request.py --raw 7A0:2FB05C03 --hold   # Mirrors UNFOLD

# SKM relay control (only works when car is charging or ACC on)
python3 can-request.py --raw 7A5:2FB108030A0A05 --session  # ACC ON
python3 can-request.py --raw 7A5:2FB10800 --session        # ACC OFF
python3 can-request.py --raw 7A5:2FB109030A0A05 --session  # IGN1 ON
python3 can-request.py --raw 7A5:2FB10900 --session        # IGN1 OFF

# VESS — vehicle exterior sound (pedestrian warning)
python3 can-request.py --raw 736:2FF011030001 --hold  # Play VESS sound 5 sec

# Replace 03 with 00 to turn OFF (e.g. 2FBC0100 = low beam OFF)
```

## BCM IOControl DIDs (0x7A0)

BCM does **not** require extended diagnostic session — commands work directly.

| DID  | Label (e-Niro)            | Status    | Ioniq 2017 Notes                                                         |
|------|---------------------------|-----------|--------------------------------------------------------------------------|
| B019 | Room lamp (interior)      | Untested  | Interior light on/off                                                    |
| B059 | Heated door handle ON     | Untested  | May not exist on Ioniq 2017 base trim                                    |
| B05A | Heated door handle LED    | Untested  | LED on door handle                                                       |
| B05B | Mirror fold               | Confirmed | Fold side mirrors in. From e-Niro                                        |
| B05C | Mirror unfold             | Untested  | Unfold side mirrors. From e-Niro                                         |
| B061 | Charge door open          | Failed    | NRC 0x7F (serviceNotSupportedInActiveSession). BCM won't enter extended session when car is asleep. May work with ACC on, or charge port may be on IGPM instead |

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
| BC18 | Courtesy/license plate?   | -         | Untested. In DID gap after turn signals — likely a lighting output           |
| BC1B | Reverse/side marker?      | -         | Untested. Another lighting output in the post-turn-signal DID range          |
| BC1C | Luggage lamp              | Rejected  | Got TesterPresent echo (timing artifact)                                     |
|      |                           |           | **— BC1D-BC2A: UNSCANNED —**                                                |
| BC2B | Rear left brake light     | Confirmed | Works!                                                                       |
| BC2C | Rear right brake light    | Confirmed | Works!                                                                       |
|      |                           |           | **— BC2D-BC3E: UNSCANNED — charge port flap likely here (near BC3F/41)**    |
| BC3F | Charge cable LOCK         | Confirmed | Works!                                                                       |
|      |                           |           | **— BC40: UNSCANNED — could be charge port flap (between lock/unlock)**     |
| BC41 | Charge cable UNLOCK       | Confirmed | Works, but likely does not stop charge session (DANGER! needs testing!)      |
|      |                           |           | **— BC42+: UNSCANNED —**                                                    |

### Rejected DIDs (NRC 0x31 — requestOutOfRange)

BC00, BC06, BC0B, BC0D, BC0E, BC17, BC19, BC1A

### Conditional reject (NRC 0x22 — conditionsNotCorrect)

BC13 — may need ignition on or other precondition.

### TODO: scan unscanned ranges

The initial scan only covered BC00-BC20. Charge port flap is likely in BC20-BC41 (near charge cable lock/unlock).

```sh
# Targeted scan for charge port flap and other unknowns
python3 can-request.py --scan --tx 770 --service 2F --range BC1D-BC41 --append 03 --session

# If not found, try wider range
python3 can-request.py --scan --tx 770 --service 2F --range BC42-BCFF --append 03 --session
```

**Safe to scan** — IGPM IOControl actuators release on session drop. Only risk is BC10/BC11 (lock/unlock) which are already known. The BC20-BC41 range should be all relay/motor outputs.

## Safety Notes

- **IOControl (0x2F) is inherently reversible** — all actuators release when the diagnostic session ends
- **No write (0x2E) or flash (0x34/36/37) commands** — no bricking risk
- BC12/BC14 may trigger alarm — test with keyfob in hand
- BC0A, BC0C, BC18, BC1B are safe to test blind — worst case is an unexpected light/relay that releases on session drop

## SKM IOControl DIDs (0x7A5)

Requires extended session (`1003`). **Only works when car is charging or ACC/IGN already on** — SKM is unpowered when car is fully asleep.

Magic bytes `0A 0A 05` are from Kia Soul and confirmed working on Ioniq 2017. Alternative byte orders (`0A 05 0A`, `05 0A 0A`) documented but untested.

| DID  | Label (Soul)    | Status    | Ioniq 2017 Notes                                                                |
|------|-----------------|-----------|---------------------------------------------------------------------------------|
| B108 | ACC relay       | Confirmed | Turns on accessory power (dash lights, wakes most ECUs)                         |
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

Not yet researched. The HVAC ECU (0x7B3) responds to read requests (`22 01 00`) and provides temperature/humidity data. IOControl commands for compressor, blower, heater, and A/C are unknown.

**Blower fan (fan-only mode) may work without ACC/IGN** — it's a 12V safety feature (ventilation for someone locked in the car). Compressor and PTC heater require HV system (IGN1+).

### Discovery scan commands

```sh
# HVAC actuators — E0xx range (Hyundai/Kia powertrain/HVAC convention)
python3 can-request.py --scan --tx 7B3 --service 2F --range E000-E0FF --append 03 --session

# Body/comfort range — blower fan control might be here
python3 can-request.py --scan --tx 7B3 --service 2F --range B000-B0FF --append 03 --session
```

These scans are safe — worst case is a blast of air or fan spin-up. No alarm, no locks.

### Research plan

1. Run discovery scans above to find accepted DIDs
2. Cross-reference with Kia Niro / e-Niro / Ioniq 5 HVAC actuator tables
3. Test blower fan control first (low risk, 12V only)
4. Determine if compressor/heater need SKM IGN1 wakeup
5. Build full remote climate sequence: SKM wakeup → HVAC actuators → monitor temp → shutdown

**Potential approach:** SKM IGN1 ON → HVAC blower/compressor/heater ON → monitor cabin temp via `22 01 00` → HVAC OFF → SKM IGN1 OFF. Car must be charging (SKM is unpowered when fully asleep).
