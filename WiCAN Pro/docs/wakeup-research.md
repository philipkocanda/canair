# IGPM Wake-Up & Remote Access Research

## Goal

Read BMS SoC (and other ECU data) remotely while the car is fully asleep and unplugged, without physical access to the key fob or vehicle.

## ECU Wake Hierarchy

| ECU          | Single `1001` | Rapid-fire `1001` (50x) | After SKM ACC+IGN1 | Power domain           |
|--------------|---------------|-------------------------|---------------------|------------------------|
| IGPM (0x770) | Yes           | Yes (attempt 1)         | Alive               | Always on (body)       |
| SKM (0x7A5)  | No            | **Yes (~2-17 attempts)**| Alive               | Body (partial standby) |
| BCM (0x7A0)  | No            | Not tested              | **Alive**           | Body (ACC-switched)    |
| CLU (0x7C6)  | No            | No (50 tries)           | Dead                | IGN-switched           |
| BMS (0x7E4)  | No            | —                       | Dead                | Powertrain (ACC relay) |
| VCU (0x7E2)  | No            | —                       | Dead                | Powertrain (ACC relay) |
| MCU (0x7E3)  | No            | —                       | Dead                | Powertrain (ACC relay) |
| LDC (0x7E5)  | No            | —                       | Dead                | Powertrain (ACC relay) |
| GW (0x7E6)   | No            | No                      | Dead                | Powertrain (ACC relay) |
| HVAC (0x7B3) | No            | No (50 tries)           | Dead                | IGN-switched           |
| ESC (0x7D1)  | No            | No                      | Dead                | Powertrain             |

### Key Finding: IGPM Wakes from Deep Sleep

The IGPM's CAN transceiver stays partially powered even when the car is fully asleep and unplugged. Sending `1001` (DiagnosticSessionControl defaultSession) wakes it:

```
1001 → NO DATA  (first attempt — wakes transceiver)
1001 → 5001     (second attempt — ECU now responsive)
1003 → 5003     (extended session established)
```

Sometimes the first `1001` gets a response immediately; sometimes it takes a retry. Once awake, the full IGPM feature set works:
- Read DIDs (`22BCxx`) — door status, lock status, lights, ignition, seatbelt
- IOControl (`2FBCxx`) — low/high beam, turn signals, horn, door lock/unlock, trunk

### Key Finding: SKM Wakes from Rapid-Fire `1001` Without Fob (2026-04-15)

A single `1001` is NOT enough to wake the SKM — it returns NO DATA. But **sustained rapid CAN traffic** (repeated `1001` frames at ~150ms intervals) wakes the SKM transceiver from deep sleep without fob proximity. Consistently succeeds at attempts 2-17 across multiple cold-start tests.

**Rapid-fire wake sequence (car off, locked, deep sleep, fob far away):**
```
ATST10          (set 64ms timeout for speed)
1001 → NO DATA  (attempt 1)
1001 → NO DATA  (attempt 2-16)
1001 → 5001     (attempt 17 — SKM transceiver awake!)
ATST96          (restore normal timeout)
1003 → 5003     (extended session established)
2FB108030A0A05 → ACC relay ON (6FB10803)
```

**Previous misconception:** Earlier tests used only 1-2 `1001` attempts and concluded "fob proximity required." The SKM's CAN transceiver is in ultra-low-power standby — it needs ~2-3 seconds of sustained CAN bus activity to trigger its hardware wake-up circuit. This is different from the IGPM, which wakes on the first frame.

**Reproducibility:** Confirmed across 4 separate cold-start tests with WiCAN rebooted between each. Attempt count varies from 2 to 17. The SKM falls back asleep after ~60s of CAN bus inactivity.

**ACC does NOT latch** — it stays on only while the IOControl session is held (TesterPresent keepalive). When the session drops, the ACC relay opens and the car re-locks.

**Side effects of ACC ON via IOControl:**
- Doors unlock (normal ACC behavior)
- Infotainment boots
- Doors re-lock when session drops
- Infotainment may get stuck mid-boot if power is cut abruptly

### Key Finding: BCM Wakes with SKM ACC+IGN1 (2026-04-15)

When SKM ACC+IGN1 IOControl is active, the BCM (0x7A0) becomes responsive. It reads TPMS data (`22C00B`) and charge port status (`22B00E`) without needing extended session. The BCM shares the body electronics power domain with the IGPM.

```
# After SKM ACC+IGN1 active:
ATSH7A0; ATFCSH7A0;
22C00B → 62C00B... (full TPMS data — pressures, temps)
22B00E → 62B00E... (charge port status)
```

### Power Dependency Chain

```
CAN bus rapid-fire (50x 1001 at 150ms intervals)
  └─ IGPM (0x770) ← always-on, wakes on first CAN frame
  └─ SKM (0x7A5) ← wakes after ~2-17 sustained CAN frames
       └─ ACC + IGN1 relays ← IOControl 2FB108/B109 (hold with TesterPresent)
            └─ BCM (0x7A0) ← body electronics, TPMS, charge port
            └─ BMS (0x7E4), VCU, MCU, LDC, GW ← STILL DEAD (powertrain relay doesn't latch)

NOT reachable without physical ACC/IGN:
  CLU (0x7C6), HVAC (0x7B3), ESC (0x7D1)
```

### Blocking Problem: Powertrain ECUs Not Reachable

The SKM wakes and ACC/IGN1 IOControl is accepted, but the powertrain ECUs (BMS, VCU, MCU, LDC, GW) remain dead. The IOControl energizes the relay coil but the relay may not latch without the proper handshake (e.g. immobilizer validation, or the relay requires sustained current that the IOControl pathway doesn't provide).

**Reachable without fob:**
- IGPM (0x770) — doors, locks, lights, horn, turn signals, trunk
- SKM (0x7A5) — relay IOControl (accepted but relay doesn't latch for powertrain)
- BCM (0x7A0) — TPMS, charge port status (once ACC active)

**NOT reachable without physical key turn or fob button press:**
- BMS, VCU, MCU, LDC, GW (powertrain power domain)
- CLU, HVAC, ESC (IGN-switched power domain)

**Possible workarounds (none tested):**
1. **Leave a spare fob in the car** — would keep SKM wakeable with full relay latch, but security risk
2. **Direct ACC relay wiring** — bypass the SKM entirely with a relay module controlled by the WiCAN's GPIO output. Invasive but reliable.
3. **Charging state** — when plugged in, the CAN bus stays active and all ECUs are powered. BMS reads work without any wake sequence during charging.

### Alternative Wake Approaches Tested (All Failed)

The following were all tested on 2026-04-15 with the car fully asleep, unplugged, no fob:

| Approach                                    | Result                                  |
|---------------------------------------------|----------------------------------------|
| Functional broadcast `0x7DF` (3E00, 1001)   | 0 responders                           |
| NM wake frames (0x500-0x5FF range)          | No effect                              |
| IGPM mystery actuators BC25/BC42/BC43/BC44  | Accepted but didn't wake other ECUs    |
| SKM response ID 0x7AD direct                | NO DATA                                |
| CAN ID 0x000 (bus-dominant wake)            | No effect                              |
| Gateway forwarding via IGPM reads           | No effect                              |
| Brute-force 1001 to CLU (50x)              | Dead                                   |
| Brute-force 1001 to HVAC (50x)             | Dead                                   |
| Brute-force 1001 to GW/ESC                  | Dead                                   |

## IGPM DID Scan Results

### Deep Sleep Scan (car off, unplugged, locked)

#### Read Scan (Service 0x22) — BC00-BC80

| DID Range | Responding DIDs                                                              |
|-----------|------------------------------------------------------------------------------|
| BC00-BC0F | BC01, BC02, BC03, BC04, BC05, BC06, BC07                                     |
| BC10-BC1C | (all within IOControl range — not read-scanned in deep sleep)                |
| BC1D-BC41 | **BC21** (7E00), **BC33** (7E00)                                             |
| BC42-BC60 | **BC46** (7E00), **BC56** (7E00)                                             |
| BC61-BC80 | **BC65** (7E00), **BC77** (7E00), **BC80** (7E00)                            |

### Ready Mode Scan (car in Ready, ACC on — 2026-04-15)

#### Read Scan (Service 0x22) — BC1D-BC80

| DID Range | Responding DIDs                                                              |
|-----------|------------------------------------------------------------------------------|
| BC1D-BC41 | **BC21** (7E00), **BC34** (7E00)                                             |
| BC42-BC80 | **BC46** (7E00), **BC59** (7E00), **BC72** (7E00)                            |

**Note:** Different DIDs respond compared to deep sleep (BC33→BC34, BC56→BC59, BC77→BC72, BC65+BC80 not seen). Only BC21 and BC46 are consistent across both states. All values remain `7E00`. The responding DIDs may be timing-sensitive — the scan order or transceiver timing may affect which periodic register catches the response window.

#### IOControl Existence Scan (Service 0x2F, `--append 00`) — BC1D-BCFF

Full scan completed in Ready mode. `--append 00` = returnControlToECU (safe, checks DID existence without actuating).

**New actuator DIDs discovered:**

| DID  | Response       | Notes                                                                 |
|------|----------------|-----------------------------------------------------------------------|
| BC25 | `6FBC2500`     | Unknown actuator. Requested as BC26, responded as BC25 (off-by-one)   |
| BC2D | `6FBC2D00`     | Verified CHMSL light. Requested as BC2E, responded as BC2D.           |
| BC42 | `6FBC4200`     | Unknown actuator. Right after charge cable unlock (BC41)              |
| BC43 | `6FBC4300`     | Unknown actuator                                                      |
| BC44 | `6FBC4400`     | Unknown actuator                                                      |

**Re-confirmed existing DIDs via off-by-one responses:**

| Requested | Responded | Known DID                |
|-----------|-----------|--------------------------|
| BC2C      | BC2B      | Rear left brake light    |
| BC2D      | BC2C      | Rear right brake light   |
| BC41      | BC3F      | Charge cable lock        |

**Note on off-by-one:** Some DIDs respond with a different DID than requested (e.g. request BC26, response `6FBC2500`). This may be a firmware artifact where adjacent DIDs share a handler.

**Status registers (all `7E00`, periodic pattern):**

BC21, BC34, BC46, BC58, BC65, BC78, BC91, BCAB, BCC5, BCDF, BCF9

Spacing pattern: ~18-26 apart. Likely periodic watchdog/heartbeat registers in the IGPM firmware, not useful for actuation. No new actuator DIDs found above BC44.

**Rejected DIDs (NRC 0x31):** Everything else in BC1D-BCFF not listed above.

### IOControl Scan Summary — All Known IGPM DIDs (BC00-BCFF)

From combined ACC-mode scan (BC00-BC20, 2026-04-15) and Ready-mode scan (BC1D-BCFF, 2026-04-15):

| DID  | Status    | Description                  |
|------|-----------|------------------------------|
| BC01 | Confirmed | Low beam headlight           |
| BC02 | Confirmed | High beam headlight          |
| BC03 | Confirmed | Front fog light              |
| BC04 | Confirmed | Tail light                   |
| BC05 | Accepted  | Backlight flash              |
| BC07 | Confirmed | **Horn**                     |
| BC08 | Confirmed | Rear fog light               |
| BC09 | Confirmed | Trunk unlock                 |
| BC0A | Accepted  | Unknown (puddle/welcome?)    |
| BC0C | Accepted  | Rear defogger relay          |
| BC10 | Confirmed | Door LOCK all                |
| BC11 | Confirmed | Door UNLOCK all              |
| BC12 | Accepted  | Unknown (per-door unlock?)   |
| BC14 | Accepted  | Unknown (per-door unlock?)   |
| BC15 | Confirmed | Left turn indicator          |
| BC16 | Confirmed | Right turn indicator         |
| BC18 | Confirmed | DRL (daytime running lights) |
| BC1B | Accepted  | Unknown (reverse/marker?)    |
| BC25 | Accepted  | Unknown                      |
| BC2B | Confirmed | Rear left brake light        |
| BC2C | Confirmed | Rear right brake light       |
| BC2D | Accepted  | Unknown (CHMSL/reverse?)     |
| BC3F | Confirmed | Charge cable LOCK            |
| BC41 | Confirmed | Charge cable UNLOCK          |
| BC42 | Accepted  | Unknown                      |
| BC43 | Accepted  | Unknown                      |
| BC44 | Accepted  | Unknown                      |

**Rejected:** BC00, BC06, BC0B, BC0D, BC0E, BC17, BC19, BC1A (NRC 0x31)
**Conditional:** BC13 (NRC 0x22), BC1B (NRC 0x22 in some states)

## SKM IOControl Tests

### Deep Sleep Wake (car off, locked, no fob — 2026-04-15)

**SKM wakes from rapid-fire `1001` without fob.** 4 cold-start tests:

| Test | SKM wake attempt | ACC ON   | BMS response | Notes                          |
|------|-----------------|----------|--------------|--------------------------------|
| #1   | 17              | Accepted | —            | First discovery                |
| #2   | 16              | Accepted | NO DATA      | Tried immediately, no wait     |
| #3   | 2               | Accepted | NO DATA      | 5s wait, BMS still dead        |
| #4   | 1               | Accepted | NO DATA      | ACC+IGN1, 5s wait, BCM alive   |

### Ready Mode (car in Ready — 2026-04-15)

```
TX: 7A5:2FB108030A0A05 → NRC 0x22 (conditionsNotCorrect)
```

SKM responded (session established with `5003`), but rejected the ACC relay ON command. Likely because ACC is **already on** — IOControl refuses to actuate an already-active relay.

### Test: freezeCurrentState (`2FB10802`)

```
TX: 7A5:2FB10802 → 6FB1080262550000
```

DID B108 exists and returns status data. Response bytes:
- `0x62` = `01100010` — bits 1, 5, 6 set
- `0x55` = `01010101` — bits 0, 2, 4, 6 set (possibly a bitmask of relay states: ACC, IGN1, IGN2, Start?)

### Implications

The SKM B108 DID is confirmed working on the Ioniq 2017. The NRC 0x22 when ACC is already on is consistent with IOControl behavior (can't actuate to current state). The key question remains: **how to power the SKM when the car is fully asleep** — the SKM's CAN transceiver is completely off in deep sleep.

## Next Steps

### 1. Test with fob nearby for comparison

Re-run the full ECU sweep with fob nearby to confirm whether the ACC relay actually latches (vs. just energizing the coil). If BMS/VCU respond with fob present but not without, the relay latch requires immobilizer validation.

### 2. Read BCM data during SKM-held ACC

The BCM is alive — explore its full DID range for useful data beyond TPMS and charge port.

### 3. HVAC IOControl discovery

See HVAC section in IOControl CLI commands doc. Needs ACC/IGN physically on.

## Remaining Questions

### Why don't powertrain ECUs power up?

The SKM ACC/IGN1/IGN2 IOControl commands are all accepted (`6FBxxx03`), which means the SKM firmware processes the request. But the BMS/VCU/MCU remain dead. Possible explanations:

1. **Relay coil vs. latch** — the IOControl may energize the coil momentarily but the relay requires an immobilizer validation handshake to latch
2. **Two-stage power** — ACC IOControl may only control a secondary (diagnostic) power rail, not the main powertrain relay
3. **Different CAN bus** — BMS/VCU/MCU may be on a separate CAN bus (P-CAN) that's physically disconnected from the diagnostic bus (D-CAN) when the car is off

### What are BC25/BC42/BC43/BC44?

These IGPM IOControl DIDs were accepted but had no visible effect. Candidates:
- Internal diagnostic relays
- Power distribution test points
- Security-related actuators (immobilizer, alarm arming)
- Wiper-related (BC42-44 sequential range)

## Sleep State Observations

Three observed IGPM sleep states:

| State        | Trigger                    | IGPM Behavior                           |
|--------------|----------------------------|-----------------------------------------|
| Light sleep  | Recently charged/ACC off   | Everything works (IOControl, reads, session) |
| Medium sleep | ~15 min after last activity | Reads work, session may fail            |
| Deep sleep   | Extended time off/unplugged | Only `1001` wake works (may need retry) |

The IGPM always wakes from `1001` in any state — but the first attempt may return NO DATA while the transceiver powers up. A 0.5s delay between wake and session request is sufficient.
