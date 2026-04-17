# IGPM Wake-Up & Remote Access Research

## Goal

Read BMS SoC (and other ECU data) remotely while the car is fully asleep and unplugged, without physical access to the key fob or vehicle.

## ECU Wake Hierarchy

| ECU          | Single `1001` | Rapid-fire `1001` (64ms) | After SKM ACC+IGN1 | Power domain             |
|--------------|---------------|--------------------------|---------------------|--------------------------|
| IGPM (0x770) | Yes           | Yes (attempt 1)          | Alive               | Always on (body)         |
| BCM (0x7A0)  | Yes (side effect) | Yes                  | Alive               | Body (CAN bus wake)      |
| SKM (0x7A5)  | No            | **Yes (attempt 2)**      | Alive               | Body (2s sleep timer)    |
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

### Key Finding: SKM Wake Mechanism (2026-04-15, refined 2026-04-16)

The SKM wakes on the **second** `1001` frame — it needs exactly one CAN frame to trigger its transceiver hardware wake-up. No broadcast, no IGPM involvement needed — sending `1001` directly to `0x7A5` works.

**Critical detail:** The SKM has an aggressive **sleep timer (~2 seconds)**. After waking, it falls back asleep if CAN traffic drops below a threshold. With the default ELM327 timeout (600ms), the gap between frames is too long — the SKM responds to attempts 2-4, then goes back to sleep. With reduced timeout (64ms, `ATST10`), frames flow fast enough to sustain the wake state.

**Wake sequence:**
```
ATST10          (set 64ms timeout — essential for keeping SKM awake)
1001 → NO DATA  (attempt 1 — triggers transceiver wake)
1001 → 5001     (attempt 2 — SKM awake and responsive)
...             (keep sending to maintain wake state)
ATST96          (restore normal timeout)
1003 → 5003     (extended session + TesterPresent keepalive takes over)
2FB108030A0A05 → ACC relay ON (6FB10803)
```

**Evidence (2026-04-16 step-by-step re-test):**

| Test                                       | Result                                    |
|--------------------------------------------|-------------------------------------------|
| Single `1001` to SKM (600ms timeout)       | Dead                                      |
| 3x `1001` to SKM (600ms)                  | Dead                                      |
| 5x IGPM wake + sleep 1s + 3x SKM (600ms)  | Dead (IGPM traffic doesn't help)          |
| 10x `1001` to SKM (600ms)                 | Woke at #2, fell asleep at #5             |
| 10x `1001` to SKM (64ms)                  | Woke at #2, stayed awake through #10      |

**Previous misconception:** Earlier tests reported needing 2-17 attempts because the rapid-fire script varied in timing. The wake actually happens on attempt 2 consistently — the variable attempt count was an artifact of the test harness timing, not the ECU's behaviour.

**CRITICAL: Fob proximity required for relay engagement.** The SKM returns a positive UDS response (`6FB10803`) to the ACC IOControl command even without fob nearby, but the relay **physically doesn't close**. Verified by reading IGPM BC03 B11 (ignition byte = `0x00`, should be `0x20`+ for real ACC) and BC04 B7 (also stays `0x00` = deep sleep). Earlier observations of "ACC relay clicked, doors unlocked" **required fob proximity** — this was not recognized at the time.

The `skm-wake` command now includes a verification step (step 4/4) that reads IGPM BC03 after the IOControl command and checks the ignition byte to confirm the relay actually engaged.

**ACC does NOT latch** — it stays on only while the IOControl session is held (TesterPresent keepalive). When the session drops, the ACC relay opens and the car re-locks.

**Side effects of ACC ON via IOControl (with fob nearby):**
- Doors unlock (normal ACC behavior)
- Infotainment boots
- Doors re-lock when session drops
- Infotainment may get stuck mid-boot if power is cut abruptly

### Key Finding: BCM Wakes from CAN Bus Activity Alone (2026-04-16)

The BCM (0x7A0) wakes from CAN bus activity without needing SKM ACC power. Simply waking the IGPM with `1001` generates enough CAN traffic to wake the BCM as a side effect. No SKM session, no ACC relay, no extended session needed.

```bash
# From deep sleep — no SKM involved:
./canreq.py --multi "raw 770:1001" "raw 770:1001" "sleep 1" "raw 7A0:22C00B"
```

```
770:1001 → NO DATA     (wakes IGPM transceiver)
770:1001 → 5001        (IGPM responsive)
7A0:22C00B → 62C00B... (BCM responds — TPMS data)
```

**Previous misconception:** Earlier tests always had SKM ACC active when querying BCM, so it appeared that ACC power was required. In reality, BCM has the same CAN-bus-wake capability as IGPM — its transceiver stays in standby and wakes on bus activity.

**Practical implication:** BCM data (TPMS pressures, charge port status, preheat schedules) is accessible remotely without any relay activation — just wake IGPM and query BCM directly.

### Power Dependency Chain

```
CAN bus activity (any frame, e.g. 1001 to IGPM)
  └─ IGPM (0x770) ← always-on, wakes on first CAN frame
  └─ BCM (0x7A0) ← wakes on CAN bus activity (no ACC needed)
  └─ SKM (0x7A5) ← wakes after ~2-17 sustained rapid-fire CAN frames
       └─ ACC + IGN1 relays ← IOControl 2FB108/B109 (hold with TesterPresent)
            └─ BMS (0x7E4), VCU, MCU, LDC, GW ← STILL DEAD (powertrain relay doesn't latch)

NOT reachable without physical ACC/IGN:
  CLU (0x7C6), HVAC (0x7B3), ESC (0x7D1)
```

### Blocking Problem: Powertrain ECUs Not Reachable

The SKM wakes and ACC/IGN1 IOControl is accepted, but the powertrain ECUs (BMS, VCU, MCU, LDC, GW) remain dead. The IOControl energizes the relay coil but the relay may not latch without the proper handshake (e.g. immobilizer validation, or the relay requires sustained current that the IOControl pathway doesn't provide).

**Reachable without fob:**
- IGPM (0x770) — doors, locks, lights, horn, turn signals, trunk
- SKM (0x7A5) — relay IOControl (UDS response positive, but relay doesn't physically close without fob)
- BCM (0x7A0) — TPMS, charge port status (wakes from CAN bus activity alone)

**Requires fob proximity:**
- SKM ACC/IGN relay latch — positive UDS response without fob is misleading; relay only closes with fob nearby

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

## BCM Power Sequencing Theory (2026-04-15)

### Hypothesis

The BCM orchestrates the vehicle power mode state machine (OFF → ACC → IGN1 → IGN2 → Ready). Instead of directly controlling relays like the SKM IOControl does, the BCM manages the *logical* power state — coordinating the SKM (relay hardware), IGPM (circuit enables), CLU (dashboard wake), and HVAC.

### Evidence

1. **BCM contains preheat departure time (DID B007)** — The BCM knows when to power up the HVAC for cabin preheating, which means it must be able to initiate a power-up sequence autonomously.
2. **Preheat works without key press** — The car preheats on schedule (daily 08:00). Something triggers ACC/IGN from sleep to run the HVAC. The BCM timer is the most likely trigger. **However, preheat only works when the car is plugged in — it draws power from the charger, not the HV battery.**
3. **B003 byte 8 reflects power mode** — Changes between ACC and ACC+IGN1 (see comparison below).
4. **BCM has IOControl DIDs for power-related functions** — 24 accepted DIDs in B0xx range, including some that could be power mode selectors.

### B003 Power State Comparison (2026-04-15)

Read BCM DID `22B003` in three states (all via SKM IOControl from deep sleep, no fob):

| Power State       | B003 stripped byte 8 | Binary       | Notes                        |
|-------------------|-----------------------|--------------|------------------------------|
| ACC only          | `0x09`                | `0b00001001` | Bits 0, 3 set                |
| ACC + IGN1        | `0x0A`                | `0b00001010` | Bits 1, 3 set                |
| ACC + IGN1 + IGN2 | (incomplete read)     | —            | Session dropped mid-capture  |

**Key observation:** Bits 0 and 1 swap between ACC and IGN1 states. This could be:
- A 2-bit power mode field: `01` = ACC, `10` = IGN1
- Or individual relay status bits: bit 0 = ACC relay, bit 1 = IGN1 relay

Full B003 payload (stripped, ACC only):
```
62 B003 BF 8B 80 00 97 3D 09 B8 F9 F8 F9 F7 3D F8 00 00 00 00 00 00 00 AA AA AA
```

Full B003 payload (stripped, ACC + IGN1):
```
62 B003 BF 8B 80 00 97 3D 0A B8 F9 F8 F9 F7 3D F8 00 00 00 00 00 00 00 AA AA AA
```

Only byte 8 (WiCAN B11) changed. B001 and B008 were identical across states.

### Charger Dependency

**Critical constraint:** The preheat schedule only works when the car is connected to a charger and can draw power from it. This means:
- The BCM timer-based power-up is designed for EVSE power, not standalone battery operation
- Remote climate without being plugged in is impossible through the BCM timer path
- The BCM probably commands the OBC/LDC to draw charger power → 12V → HVAC PTC heater

This limits the BCM power sequencing approach: even if we could trigger the BCM's power-up routine remotely, it would only work while plugged in.

### Testing Plan

1. **B003 power mode tracking** — read B003 in more states (OFF/deep sleep, Ready mode with physical key) to map all bit patterns
2. **Write test (0x2E) to B003** — attempt `2EB003` with modified byte 8 to request a power mode change. Start with current value + 1 bit to avoid drastic changes. **Requires extended session and possibly security access.**
3. **RoutineControl scan (0x31)** — check if BCM has routines for power mode transitions: `31 01 xxxx` range scan
4. **BCM DID B00C/B00D** — the preheat schedule DIDs. Writing to these might configure a one-time departure time and trigger immediate preheat
5. **Monitor B003 during preheat** — capture B003 while the car is plugged in and the preheat timer fires, to see the BCM's actual power-up sequence

## Next Steps

### 1. BCM power mode investigation

Test the BCM power sequencing theory — see BCM Power Sequencing Theory section above.

### 2. Test with fob nearby for comparison

Re-run the full ECU sweep with fob nearby to confirm whether the ACC relay actually latches (vs. just energizing the coil). If BMS/VCU respond with fob present but not without, the relay latch requires immobilizer validation.

### 3. HVAC IOControl discovery

See HVAC section in IOControl CLI commands doc. Needs ACC/IGN physically on (or plugged-in preheat state).

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

## IOControl — SKM (0x7A5)

- **ACC relay** (`2FB108030A0A05`) — UDS positive response confirmed. **However, relay only physically closes with fob nearby.** Without fob, SKM returns `6FB10803` but ignition byte stays `0x00`. The `skm-wake` command verifies this via IGPM BC03.
- **IGN1/IGN2** accepted but powertrain ECUs remain dead (relay doesn't latch even with fob — separate issue).
- **freezeCurrentState** (`2FB10802`) returns status bytes `6255`.

## ECU Wake from Deep Sleep (car off, unplugged, no fob)

- **IGPM wakes from single `1001`** — always partially powered.
- **SKM wakes from rapid-fire `1001`** (50x at 150ms intervals, `ATST10`). Consistently wakes at attempts 2-17. **No fob needed** (earlier "fob required" conclusion was wrong — single attempts aren't enough).
- **BCM wakes when SKM ACC+IGN1 active** — TPMS and charge port data confirmed.
- **Powertrain ECUs (BMS/VCU/MCU/LDC/GW) remain dead** even with ACC+IGN1+IGN2. Relay doesn't latch.
- **CLU, HVAC, ESC dead** — IGN-switched power domain.
- 10 alternative wake approaches all failed for non-body ECUs (functional broadcast, NM frames, brute-force per-ECU, etc.).

## BCM Power Sequencing Theory

- BCM may orchestrate vehicle power mode state machine. Evidence: B007 contains `0x01E0` (480 = minutes for 08:00 preheat time), B00C has day-of-week bitmask (`0x3F` = 6 days).
- **B003 byte 8 reflects live power state**: `0x09` in ACC, `0x0A` in ACC+IGN1. Bits 0-1 encode power mode.
- **Security access available** on BCM: `2701` returns 4-byte random seed. Key algorithm unknown. Writes (`0x2E`) return NRC 0x33 (securityAccessDenied) without completing handshake.
- **Preheat only works when plugged in** (draws from charger, not HV battery).

## Security Access Research (2026-04-16)

### Goal

Unlock BCM (0x7A0) write access via UDS Security Access (service 0x27) to enable writing power mode config (B003), preheat schedule (B00C/B007), and potentially other configuration DIDs.

### What We Know

- **BCM and IGPM** both support security access level 1 (`27 01`/`27 02`)
- Level 2 (`27 03`) returns NRC 0x12 (not supported) — only level 1 exists
- 2-byte key returns NRC 0x13 (wrong length) — **4-byte key required**
- Seeds are 4-byte, appear random (full range 0x00-0xFF in each position)
- Default session (`10 01`) returns NRC 0x7F — extended session (`10 03`) required
- **Lockout after ~2 wrong keys** — NRC 0x37, ~11s delay, session drops and must be re-established
- Security access works from deep sleep (with SKM ACC wake)

### Seed Samples (30 collected)

```
CECD67F1  928649CD  47E8A47E  7B2D3E20  6F85384D
9FC7506D  0C4F06B1  23AF9261  B377DA45  9A854DCD
423321A3  5109A90E  3DAA1F5F  B807DC8C  ECF37703
2EA817DF  2F101812  87554435  3F21201A  9A69CDBF
9B544E34  96854BCD  99254D1C  1E4E0FB0  862D43A1
85324323  A78ED450  DFFF7088  7D363F25  89ED4580
```

### Algorithms Tested (48 total, all failed)

**Simple transforms (17):** NOT, XOR (0x0D0B0507, 0x5A5A5A5A, 0xA5A5A5A5, 0x12345678, 0xDEADBEEF, 0x98765432, 0xFFFFFFFF, 0x6FD56FD5, 0xAAAAAAAA), byte-swap, swap16, ROL/ROR (4/8/16 bits), plus1, minus1, same (echo), zero, NOT+1, mul3+1, add/sub per-byte.

**Static keys (1):** Kia Soul `0x6FD56FD5` — the Soul sheet showed this as a static key for CAN ID 0x7DE (TPMS). The Soul's `27 01` returned no seed (zero), making it effectively a password. Our BCM returns random seeds, so it uses actual challenge-response.

**KI221Algo2 — XOR+ADD (2):** `key = (seed ^ 0x78253947) + 0x83249272` and reversed order. From UnlockECU project (Daimler instrument clusters).

**KI203Algo — swap/rotate/XOR with root key (9 root keys):** Byte-swap → ROL3 → XOR root → ROR(popcount(root)) → byte-swap. Tried roots: 0x30BACD45, 0x27FC2D10, 0x4902EF27, 0xBADEF289, 0x62FB90EF, 0x3EFA72D6, 0x3913B1FF, 0x4532F3EF, 0x2A58122F.

**KI221Algo1 — XOR/swap/rotate Feistel (6 root keys):** XOR root bytes with seed → byte-swap → ROR3 → XOR root → ROL7. Tried roots: 0x3913B1FF, 0x4532F3EF, 0x2A58122F, 0x78253947, 0x83249272, 0x30BACD45.

**Combination transforms (13):** ADD/SUB 0x6FD56FD5, byte-swap+NOT, NOT+byte-swap, XOR+byte-swap combos.

### Why These Failed

The UnlockECU algorithms (KI prefix) are for **Daimler/Mercedes Kombiinstrument** (instrument clusters), not Kia/Hyundai. The Hyundai Ioniq BCM (Mobis part 95400G7470) uses a proprietary Hyundai Mobis algorithm that has not been publicly reverse-engineered.

### Kia Soul Reference

The [Kia Soul TPMS spreadsheet](https://docs.google.com/spreadsheets/d/1YYlZ-IcTQlz-LzaYkHO-7a4SFM8QYs2BGNXiSU5_EwI/edit?gid=1506618410#gid=1506618410) shows CAN ID 0x7DE (BCM/TPMS):
```
02 27 01 00              → Seed request: "no key required" (zero seed?)
06 27 02 6F D5 6F D5     → Key: static 0x6FD56FD5
67 02 34                  → Positive response
```
The Soul may use a zero-seed (always-unlocked) scheme, unlike the Ioniq which returns random 4-byte seeds.

### DST80 / Immobilizer Connection

KU Leuven researchers found Hyundai/Kia used the proprietary DST80 encryption algorithm for immobilizer key fob authentication, with keys derivable from fixed constants or serial numbers. However, the immobilizer system (RF transponder ↔ engine start) is separate from UDS diagnostic security (CAN bus ↔ ECU write access). The security philosophy is similar though — "security through obscurity with hardcoded constants."

### Possible VIN-Based Key Derivation

Some OEMs use VIN or ECU serial number as input to the key derivation constant. If the algorithm incorporates VIN, the root key would be vehicle-specific. However, GDS dealer tools must compute keys for any vehicle, so the VIN → constant mapping would need to be in the tool's DLL/database, not truly per-vehicle secret.

### Community Resources

- **mhhauto.com** — automotive locksmith/diagnostic forum with a thread on "Security Access Algorithms Seed Key Algo All Brands All ECUs". Requires login, not publicly accessible. May contain Hyundai-specific seed-key calculators or DLLs.
- **UnlockECU** (jglim/UnlockECU on GitHub) — extensive seed-key algorithm database, but only Daimler/Continental/Bosch ECUs. No Hyundai/Kia entries.
- **OVMS** — Hyundai/Kia vehicle modules never use security access (read-only polling only).

### Next Steps

1. **Obtain a valid seed-key pair** — highest priority. Options:
   - Use Kingbolen scanner's TPMS programming function (if it authenticates to BCM)
   - Acquire a cheap ELM327-based TPMS tool that programs Hyundai sensors
   - Try GDS/KDS software (dealer tool) — may be obtainable from Korean automotive forums
   - Ask on mhhauto.com forum (create account, post in Hyundai/Kia section)
2. **Sniff the CAN bus during scanner authentication** — if any scanner tool authenticates, capture the `27 01` → `67 01 <seed>` → `27 02 <key>` → `67 02` exchange
3. **Firmware dump** — extract BCM firmware via JTAG/SWD debug port to find the algorithm in the binary. Requires physical access to the BCM board.
4. **Reverse the GDS security DLL** — if GDS software is obtainable, the `SecurityAccess.dll` can be analyzed with tools like jglim/SecurityAccessQuery
5. **Try other ECUs** — test security access on IGPM (0x770), SKM (0x7A5) with the same algorithms. If one ECU uses a simpler scheme, it might provide clues for the BCM.
