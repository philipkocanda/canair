# IGPM Wake-Up & Remote Access Research

## Goal

Read BMS SoC (and other ECU data) remotely while the car is fully asleep and unplugged, without physical access to the key fob or vehicle.

## ECU Wake Hierarchy

| ECU          | Fob lock wake | CAN `1001` wake | Needs ACC power               |
|--------------|---------------|-----------------|-------------------------------|
| IGPM (0x770) | Yes           | Yes             | No — always partially powered |
| SKM (0x7A5)  | No            | No              | Yes                           |
| BMS (0x7E4)  | No            | No              | Yes                           |
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

### Blocking Problem: SKM Power Dependency

The path to BMS requires the ACC relay:

```
IGPM (0x770) ← wakes from CAN bus activity
  └─ SKM (0x7A5) ← needs ACC power (fully unpowered in deep sleep)
       └─ ACC relay ← controlled by SKM IOControl (2FB108030A0A05)
            └─ BMS (0x7E4), VCU, MCU, etc.
```

The SKM's CAN transceiver is completely off in deep sleep — `1001` has no effect. Without the SKM, we can't close the ACC relay programmatically.

## IGPM DID Scan Results (Deep Sleep)

### Read Scan (Service 0x22) — All Responding DIDs

Scanned BC00-BC80 while car was in deep sleep (unplugged, locked). All responding DIDs listed:

| DID Range | Responding DIDs                                                              |
|-----------|------------------------------------------------------------------------------|
| BC00-BC0F | BC01, BC02, BC03, BC04, BC05, BC06, BC07                                     |
| BC10-BC1C | (all within IOControl range — not read-scanned in deep sleep)                |
| BC1D-BC41 | **BC21** (7E00), **BC33** (7E00)                                             |
| BC42-BC60 | **BC46** (7E00), **BC56** (7E00)                                             |
| BC61-BC80 | **BC65** (7E00), **BC77** (7E00), **BC80** (7E00)                            |

#### New DIDs (all return `7E00` in deep sleep)

| DID    | Value  | Binary           | Notes                                                    |
|--------|--------|------------------|----------------------------------------------------------|
| BC21   | `7E00` | `01111110 00000000` | Unknown — likely IOControl status register              |
| BC33   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |
| BC46   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |
| BC56   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |
| BC65   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |
| BC77   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |
| BC80   | `7E00` | `01111110 00000000` | Unknown — same pattern                                  |

All 7 new DIDs return identical `7E00`. These are likely IOControl actuator status registers — each one might correspond to a group of actuators, with `0x7E` = bits 1-6 set representing "off/inactive" state. The values need to be compared against ACC-on readings to determine which bits change.

### IOControl Scan (Service 0x2F) — Previous Results (BC00-BC20)

From earlier scanning in ACC mode:

| DID  | Status   | Description                  |
|------|----------|------------------------------|
| BC01 | Accepted | Low beam headlight           |
| BC02 | Accepted | High beam headlight          |
| BC03 | Accepted | Front fog light              |
| BC04 | Accepted | Tail light                   |
| BC05 | Accepted | Backlight flash              |
| BC07 | Accepted | **Horn**                     |
| BC08 | Accepted | Rear fog light               |
| BC09 | Accepted | Trunk unlock                 |
| BC0A | Accepted | Unknown (puddle/welcome?)    |
| BC0C | Accepted | Rear defogger relay          |
| BC10 | Accepted | Door LOCK all                |
| BC11 | Accepted | Door UNLOCK all              |
| BC12 | Accepted | Unknown (per-door unlock?)   |
| BC14 | Accepted | Unknown (per-door unlock?)   |
| BC15 | Accepted | Left turn indicator          |
| BC16 | Accepted | Right turn indicator         |
| BC18 | Accepted | Unknown (courtesy/license?)  |
| BC1B | Accepted | Unknown (reverse/marker?)    |
| BC00 | Rejected | (NRC 0x31)                   |
| BC06 | Rejected | (NRC 0x31)                   |
| BC0B | Rejected | (NRC 0x31)                   |
| BC13 | Rejected | (NRC 0x22 conditionsNotCorrect) |

**Unscanned IOControl ranges:** BC1D-BC41, BC42+

## Next Steps (ACC-On Session)

When the car is in ACC mode:

### 1. Confirm SKM IOControl works on Ioniq

```sh
python3 can-request.py --raw 7A5:2FB108030A0A05 --session --wican home
```

If accepted: ACC relay can be controlled via SKM. The remaining challenge is powering the SKM remotely.

### 2. Re-read new DIDs in ACC to compare

```sh
python3 can-request.py --raw 770:22BC21 --session --wican home
python3 can-request.py --raw 770:22BC33 --session --wican home
python3 can-request.py --raw 770:22BC46 --session --wican home
python3 can-request.py --raw 770:22BC56 --session --wican home
python3 can-request.py --raw 770:22BC65 --session --wican home
python3 can-request.py --raw 770:22BC77 --session --wican home
python3 can-request.py --raw 770:22BC80 --session --wican home
```

Compare values against deep-sleep baseline (`7E00`). Any bits that change indicate ACC/IGN-related status.

### 3. IOControl existence scan (safe, no actuation)

```sh
python3 can-request.py --scan --tx 770 --service 2F --range BC1D-BC41 --append 00 --session --wican home
```

Using `--append 00` (returnControlToECU) — only checks if the DID exists as an IOControl target, doesn't activate anything.

### 4. Broader read scan in ACC

```sh
python3 can-request.py --scan --tx 770 --service 22 --range BC81-BCFF --session --wican home
```

There may be more DIDs above BC80.

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
