---
name: ioniq-reverse-engineering
description: Facts about the bundled 2017 Hyundai Ioniq Electric that no other skill holds — ignition lines and how they map to canair's vehicle states, per-ECU gotchas, factory module names versus the profile's ECUs, the research findings documents, wake behaviour. Load this when working on the Ioniq reverse engineering project, together with reverse-engineer-signal.
---

# Ioniq CAN Reverse Engineering Skill

**This skill is the Ioniq-specific *context*; `reverse-engineer-signal` is the generic
*procedure*: load both for Ioniq reverse-engineering work.** This one carries the vehicle's facts
(ignition lines, per-ECU gotchas, factory module names). `reverse-engineer-signal` is the
vehicle-agnostic decode lifecycle, byte-index and expression syntax, and signal-analysis
reasoning. Decoding, adding, fixing or verifying any PID/DID on the Ioniq needs both. For a
different car, load that generic skill plus that car's profile; this skill is then an example of
a finished profile. Other skills own the rest:

- `wican-hardware-and-protocol`: the dongle, transports, the device API, sleep and power.
- `contributing-profiles`, `profile-variants`, `pid-cleanup`: profile data and its upkeep.
- `AGENTS.md`, `docs/reference/cli/` and `canair <cmd> --help`: commands, flags, config.

## Safety (non-negotiable)

The rules are in `AGENTS.md` (section "The car") and in the `reverse-engineer-signal` skill
(section "Safety first"). In short:

- Never touch the car, the dongle or the bus without the user's explicit "yes" for that exact
  action. Propose the exact commands, then wait.
- Never open a programming session (`10 02`, `10 05`) or write firmware. This car cannot be
  un-bricked.
- One canair connection at a time. Never reboot the WiCAN without asking.


## Critical gotchas (details in the sub-files)

- The OBD-II port is gateway-isolated: passive `canair sniff` sees almost nothing; use UDS.
- `canair io --poll` can actuate relays and solenoids (release `2F{DID}00` every 3 s).
- The SKM ACC relay closes only with the fob nearby; BMS and MCU answer only while IG3 is on.
- OBC: never send `99`, or `30 06/07` / `31 00/01` in session 0x90.
- Ignition-line wiring is US-market; treat fuse names and pins as hypotheses for this EU car.
- AutoPID goes stale when the 12 V battery hits `sleep_volt`, though the WiCAN stays reachable.

## Orientation

1. Load this skill and `reverse-engineer-signal`; the car is a 2017 Ioniq Electric, 28 kWh.
2. Pick a target from the backlog: `canair research --summary` / `--gaps`.
3. Triage the findings documents before a capture session.
4. Check the ECU gotchas and the power state (ignition lines) the ECU needs to answer.
5. Propose exact commands, wait for the user's "yes", then run them.
6. Record findings in `ecus/<ecu>.yaml`; keep `docs/profiles/hyundai-ioniq-ae-ev.md` current.

## Routing

| When you are...                                                     | Read                                                                      |
|---------------------------------------------------------------------|---------------------------------------------------------------------------|
| Picking a backlog item or triaging firmware/spec findings           | `.claude/skills/ioniq-reverse-engineering/research-documents.md`          |
| Needing car, dongle, AT init, AutoPID or `--session`/`--wake` facts | `.claude/skills/ioniq-reverse-engineering/vehicle-and-gotchas.md`         |
| Working with ACC/IGN1/IGN2/IG3, power states, vehicle states        | `.claude/skills/ioniq-reverse-engineering/ignition-lines.md`              |
| Decoding or actuating a specific ECU (IGPM, BCM, VCU, MCU, OBC...)  | `.claude/skills/ioniq-reverse-engineering/ecu-gotchas.md`                 |
| Mapping a factory module name to a profile ECU, EDR, CLIMATE-CAN    | `.claude/skills/ioniq-reverse-engineering/factory-modules.md`             |
| Interpreting BCM part numbers, or looking for references and docs   | `.claude/skills/ioniq-reverse-engineering/part-numbers-and-references.md` |
