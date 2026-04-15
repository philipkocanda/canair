# IGPM Wake-Up & Remote Access Research

## Goal

Read BMS SoC (and other ECU data) remotely while the car is fully asleep and unplugged, without physical access to the key fob or vehicle.

## ECU Wake Hierarchy

| ECU          | Fob lock wake | CAN `1001` wake | Needs ACC power               |
|--------------|---------------|-----------------|-------------------------------|
| IGPM (0x770) | Yes           | Yes             | No — always partially powered |
| SKM (0x7A5)  | No            | **Yes**         | No — wakes from CAN activity  |
| BMS (0x7E4)  | No            | No              | Yes — needs ACC relay          |
| VCU (0x7E2)  | Not tested    | Not tested      | Yes                           |
| BCM (0x7A0)  | Not tested    | No (tested)     | Yes                           |

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

### Key Finding: SKM Also Wakes from Deep Sleep (2026-04-15)

**Contrary to earlier testing, the SKM DOES wake from `1001`** — even when the car is fully asleep (off + locked). The `--wake` flag sends `1001` to the target ECU, which wakes the SKM's CAN transceiver. After a brief delay, `1003` succeeds and IOControl works.

**CAVEAT: Key fob was in proximity during this test** (user was near the car with fob in pocket). The fob's passive LF/RFID signal may have kept the SKM's transceiver partially powered. Earlier tests without the fob nearby failed. **A retest with the fob far from the car is needed** to determine if `1001` alone is sufficient, or if fob proximity is required for SKM wake.

Tested sequence (car off, locked, deep sleep, **fob nearby**):
```
1001 → NO DATA  (wakes SKM transceiver)
1003 → 5003     (extended session established)
2FB108030A0A05 → ACC relay ON (clicking heard, infotainment powered on, doors unlocked)
```

**ACC does NOT latch** — it stays on only while the IOControl session is held (TesterPresent keepalive). When the session drops, the ACC relay opens and the car re-locks.

**Side effects of ACC ON via IOControl:**
- Doors unlock (normal ACC behavior)
- Infotainment boots
- Doors re-lock when session drops
- Infotainment may get stuck mid-boot if power is cut abruptly

### Updated Power Dependency Chain

```
CAN bus activity (any frame, e.g. 1001)
  ├─ IGPM (0x770) ← wakes from CAN activity (always partially powered)
  └─ SKM (0x7A5)  ← ALSO wakes from CAN activity
       └─ ACC relay ← IOControl 2FB108030A0A05 (hold with TesterPresent)
            └─ BMS (0x7E4), VCU, MCU, etc.
```

### Remaining Challenge: Multi-ECU Session

The remote BMS read path is now clear:
1. Wake SKM with `1001`
2. Hold ACC via `2FB108030A0A05` with TesterPresent keepalive
3. Query BMS (0x7E4) for SoC, voltages, etc.
4. Release ACC (drop session)

**BUT:** `can-request.py` currently supports only one ECU per session. Holding ACC on the SKM while querying the BMS requires either:
- Multi-ECU support in `can-request.py` (send AT header switches mid-session)
- A dedicated "remote read" script that handles the full sequence

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
| BC2D | `6FBC2D00`     | Unknown actuator. Requested as BC2E, responded as BC2D. Between brake lights (BC2B/BC2C) — possibly CHMSL or reverse light |
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

## SKM IOControl Test (Ready Mode — 2026-04-15)

### Test: ACC relay IOControl (`2FB108030A0A05`)

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

### 1. Test unknown IGPM IOControl DIDs

The following accepted-but-untested DIDs should be tested with `--hold` (one at a time, visually confirm what happens):

```sh
python3 can-request.py --raw 770:2FBC0A03 --hold   # Puddle/welcome light?
python3 can-request.py --raw 770:2FBC0C03 --hold   # Rear defogger relay
python3 can-request.py --raw 770:2FBC2503 --hold   # Unknown (NEW)
python3 can-request.py --raw 770:2FBC2D03 --hold   # Unknown (NEW) — CHMSL/reverse?
python3 can-request.py --raw 770:2FBC4203 --hold   # Unknown (NEW)
python3 can-request.py --raw 770:2FBC4303 --hold   # Unknown (NEW)
python3 can-request.py --raw 770:2FBC4403 --hold   # Unknown (NEW)
```

### 2. Test SKM relay ON when ACC is off

With the car off (but recently driven, so SKM is still powered — light sleep):
```sh
python3 can-request.py --raw 7A5:2FB108030A0A05 --session --hold
```

If this works, we confirm the ON command is valid and the NRC 0x22 was indeed "already active".

### 3. HVAC IOControl discovery

See HVAC section in IOControl CLI commands doc.

## Theories for Remote BMS Access

### Theory 1: IGPM has an ACC relay DID

One of the unscanned IGPM IOControl DIDs (BC1D-BC41 range) might directly control the ACC power relay, bypassing the SKM entirely. The IGPM is the power distribution module — it's plausible it has direct relay control.

### Theory 2: IGPM can wake SKM indirectly

An IGPM IOControl DID might energize a bus or relay that powers the SKM, even if it doesn't control the ACC relay directly. Once the SKM has power, we can use its own IOControl for ACC.

### Theory 3: Network Management (NM) frames

Hyundai/Kia uses OSEK NM. The IGPM might forward NM wake requests to other ECUs via internal relay logic. This wasn't testable in deep sleep (NM frames timed out on dead bus), but might work differently if the IGPM is already awake.

### Theory 4: Functional addressing (0x7DF)

UDS functional broadcast might reach ECUs that individual addressing doesn't, if the IGPM acts as a gateway and forwards requests. Not successful in deep sleep, but worth retesting with IGPM awake.

## Sleep State Observations

Three observed IGPM sleep states:

| State        | Trigger                    | IGPM Behavior                           |
|--------------|----------------------------|-----------------------------------------|
| Light sleep  | Recently charged/ACC off   | Everything works (IOControl, reads, session) |
| Medium sleep | ~15 min after last activity | Reads work, session may fail            |
| Deep sleep   | Extended time off/unplugged | Only `1001` wake works (may need retry) |

The IGPM always wakes from `1001` in any state — but the first attempt may return NO DATA while the transceiver powers up. A 0.5s delay between wake and session request is sufficient.
