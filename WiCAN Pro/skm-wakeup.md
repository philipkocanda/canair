# SKM ECU Wakeup — Remote ACC/IGN Control via CAN

## Overview

The **SKM (Smart Key Module)** at ECU address `0x7A5` can remotely activate the car's ACC (Accessory), IGN1, IGN2, and Start relays via UDS IOControl (`0x2F`) commands. This wakes sleeping ECUs (IGPM, CLU, TPMS, ESC, BCM) that don't respond when the car is locked or only AC charging.

**Tested on:** Hyundai Ioniq Electric 2017 (28 kWh), via WiCAN Pro WebSocket ELM327 terminal  
**Source:** Kia Soul EV community documentation (projectgus.com)  
**Date first tested:** 2026-04-14

## Prerequisites

- WiCAN must be connected to WiFi and reachable (e.g. `http://10.0.2.86`)
- Use `can-request.py` interactive mode or direct WebSocket terminal
- **The CAN bus must already be active** — the SKM cannot wake the car from a fully powered-down state. The diagnostic CAN bus is completely off when the car is asleep; all commands return `NO DATA`. The CAN bus is active when:
  - The car is AC or DC charging
  - Within ~10 minutes of unlocking with the smart key remote
  - The car is in ACC or ignition-on mode

## Wakeup Procedure

### Step 1: Set ELM327 headers for SKM

```
ATSH7A5
ATFCSH7A5
```

Both should return `OK`.

### Step 2: Enter extended diagnostic session

```
1003
```

Expected response: `5003` (positive response for DiagnosticSessionControl — extendedDiagnostic).

### Step 3: Send ACC On command

```
2FB108030A0A05
```

This is a UDS IOControl (`0x2F`) command:

| Byte(s)     | Value      | Meaning                                              |
|-------------|------------|------------------------------------------------------|
| `2F`        | SID        | IOControlByIdentifier                                |
| `B1 08`     | DID        | ACC relay control                                    |
| `03`        | Control     | ShortTermAdjustment (ON)                             |
| `0A 0A 05`  | Magic bytes | Required control parameters (from Kia Soul)          |

Expected response sequence:
1. `7F2F78` — requestCorrectlyReceivedResponsePending (ECU is processing)
2. `6FB108030A0A05` — positive response confirming ACC activated

### Step 4: Verify

- Dashboard lights should turn on
- Previously sleeping ECUs (IGPM, TPMS, CLU, ESC) should now respond to queries
- IGPM 22BC03 byte 7 bit 5 will read `1` (ACC flag)

## Available Relay Commands

All commands use SID `0x2F` with DID prefix `B1`. Extended diagnostic session (`1003`) is required first.

| Function        | ON Command             | OFF Command   | DID    |
|-----------------|------------------------|---------------|--------|
| ACC (Accessory) | `2FB108030A0A05`       | `2FB10800`    | `B108` |
| IGN1 (Ignition) | `2FB109030A0A05`       | `2FB10900`    | `B109` |
| IGN2            | `2FB10A030A0A05`       | `2FB10A00`    | `B10A` |
| Start Relay     | `2FB10B030A0A05`       | `2FB10B00`    | `B10B` |

### Command byte structure

```
2F [DID_HI] [DID_LO] [CONTROL] [MAGIC...]
│   │        │         │         └── 0A 0A 05 (required for ON)
│   │        │         └── 03 = ON (ShortTermAdjustment), 00 = OFF (ReturnControlToECU)
│   │        └── 08=ACC, 09=IGN1, 0A=IGN2, 0B=Start
│   └── B1 (SKM DID range)
└── IOControlByIdentifier SID
```

## Known Issues

### ACC Off does not work reliably

On the Ioniq 2017, sending the ACC Off command (`2FB10800`) returns a **positive response** (`6FB10800`) but ACC **stays on**. The following were all attempted without success:

| Command    | Purpose                      | Response       | Result              |
|------------|------------------------------|----------------|---------------------|
| `2FB10800` | ACC Off                      | `6FB10800` (+) | Lights stayed on    |
| `2FB10900` | IGN1 Off                     | Positive       | No change           |
| `2FB10A00` | IGN2 Off                     | Positive       | No change           |
| `1001`     | Return to default session    | NO DATA        | No change           |
| `1101`     | ECU hard reset               | `5101FE` (+)   | No change           |
| `1103`     | ECU soft reset               | `5101FE` (+)   | No change           |

ACC eventually times out on its own or must be cleared by the user (press start button, open/close door, etc.). The exact auto-timeout duration is unknown.

### TesterPresent may be needed

For sustained IOControl sessions, UDS requires periodic TesterPresent (`3E00`) messages at ~1 Hz to prevent the ECU from reverting to default session. Without this, the diagnostic session may time out after a few seconds. For a one-shot wakeup this doesn't matter — the ACC relay stays latched regardless.

## Safety Warnings

- **Start Relay (`B10B`) can crank the motor** — do NOT use unless the car is in a safe state (Park, no one in front, etc.)
- IOControl commands actuate **real physical hardware** (relays, solenoids)
- The Ioniq is a keyless-start vehicle — the SKM validates the smart key proximity before allowing normal start. UDS IOControl **bypasses this check**
- These commands are from the Kia Soul community and **have not been exhaustively tested** on the Ioniq. Proceed with caution
- The magic bytes `0A 0A 05` work on both Kia Soul and Ioniq 2017. Alternative sequences `0A 05 0A` and `05 0A 0A` have been documented but not tested

## ECU Power Domains

The SKM is **not always powered**. It shares a power domain with the powertrain ECUs, not the body ECUs. This means:

- **Keyfob unlock** wakes the IGPM only — the SKM, BMS, VCU all remain asleep
- **AC charging** wakes BMS, VCU, LDC, Gateway, **and the SKM** — this is the only non-ignition state where SKM wakeup works
- The SKM wakeup command is therefore specifically useful during **charging sessions** to bring the body ECUs (IGPM, CLU, TPMS, ESC) online alongside the already-awake powertrain ECUs

## ECU Sleep/Wake Behavior

| Car State                | CAN Bus | SKM   | BMS/VCU/LDC | IGPM/CLU/TPMS/ESC |
|--------------------------|---------|-------|--------------|---------------------|
| Locked, not charging     | OFF     | No    | No           | No                  |
| Keyfob unlock (no ACC)   | ON      | No    | No           | IGPM only (~10 min) |
| AC charging              | ON      | Yes   | Yes          | No (sleeping)       |
| AC charging + SKM wakeup | ON      | Yes   | Yes          | Yes (ACC woke them) |
| ACC mode (button)        | ON      | Yes   | Yes          | Yes                 |
| Ignition ON              | ON      | Yes   | Yes          | Yes                 |

## Example Session (can-request.py)

```bash
$ python3 can-request.py --wican home

ioniq> ATSH7A5
OK
ioniq> ATFCSH7A5
OK
ioniq> 1003
5003
ioniq> 2FB108030A0A05
7F2F78
6FB108030A0A05

# Dashboard lights are now on — query IGPM
ioniq> ATSH770
OK
ioniq> ATFCSH770
OK
ioniq> 22BC03
62BC03FD...    # IGPM now responding

# When done, reboot WiCAN to restore AutoPID
ioniq> !reboot
```

## References

- [projectgus.com — Simplifying a bench Kona](https://www.projectgus.com/2024/10/simplifying-bench-kona/) — SKM hardware teardown, relay control commands
- Obsidian vault: `KB/EV/Hyundai Ioniq/Reverse engineering/PIDs by ECU/SKM or SMK (Smart Key Module) (0x7a5).md`
- Obsidian vault: `KB/Electronics/CAN bus/Protocols/UDS/Services/UDS 0x2F (IOControl).md`
- Obsidian vault: `KB/EV/Hyundai Ioniq/Reverse engineering/Tested scenarios/ECUs awake when AC charging.md`
