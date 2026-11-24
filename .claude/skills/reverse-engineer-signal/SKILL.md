---
name: reverse-engineer-signal
description: "Generic, vehicle-agnostic reverse-engineering workflow for canair — the whole flow from orient/discover through capture, analyze, define, verify, integrate, for ANY signal (a PID/DID signal, a raw broadcast frame field, a routine, an IOControl actuator) on ANY car. Covers WiCAN Bnn / ISO-TP / PCI byte indexing, expression syntax, the analysis reasoning (signal types, physics/EE, statistics), and writing/validating definitions. Use when discovering, decoding, or verifying anything on a vehicle bus, writing or fixing an expression, working out a byte offset, or working a research: backlog — on the bundled Ioniq profile or a profile you built for another car. Examples use the Ioniq for concreteness; the method is generic."
---

# Reverse-engineering a vehicle signal (generic)

The **vehicle-agnostic, end-to-end** workflow for taking a signal from "unknown" to a
verified, decoded signal in a profile's `ecus/`. Not PID-specific: the same loop serves a
service-`22`/`21` PID/DID, a passively-sniffed broadcast frame field, a routine, or an IOControl
actuator. Examples use the bundled 2017 Ioniq because it is fully worked: the *method*
transfers, not the byte offsets. This skill is the *procedure*; the profile is the *data* (which
ECU carries which signal, marque quirks and addresses live in the active profile's `ecus/`).

Related skills: **`ioniq-reverse-engineering`** (bundled-car ignition lines, ECU gotchas and
factory module names; load it too when working the Ioniq), **`decode-bitfields`** (the
bit-level loop for discrete body signals), **`profile-variants`** (which profile a car belongs
in, and its name), **`contributing-profiles`** (sharing the result upstream).

Pass `--profile NAME` on every mutative command (`AGENTS.md` → Profiles). Examples below omit it.

## Safety first

The rules are in `AGENTS.md` → The car and `docs/concepts/safety.md`. Skill-specific points:

- Steps 3, 4 and any live check send requests to the car: propose the exact commands and wait
  for the user's "yes". Steps 1 and 5–9 work on existing captures and need no car.
- **NEVER** open a programming session (`10 02`) or do any firmware write/upload: ECUs can be
  bricked. canair blocks these. Never work around the block.
- A read (`22`) cannot harm an ECU in any DID range, but it still needs approval. `2E` writes
  and `2F` actuation can brick or move hardware: out of scope for signal decoding.
- One canair connection at a time, any transport (a `flock` mutex enforces it). Clear a stuck
  session with `--force` or `canair lock`, **never by rebooting the device**. Never reboot
  without asking (the WebSocket terminal overrides AutoPID; the reboot restores it).
- The first request after idle often fails. Do not conclude from one failure that a PID/ECU is
  dead: report it and ask the user before you retry.
- Device sleep should be off for a long session and on again after. That is **`wican-cli`** (a
  separate package), not canair: `wican sleep --disable`. Status: `canair status`. Both touch
  the device, so ask the user to run them or to approve them.

## Working principles

- **Evidence, not vibes.** Never accept a byte because it "looks about right": confirm it
  (range, distribution, correlation, physical plausibility across states) and state your
  confidence honestly.
- **Think like the ECU's systems engineer.** Ask what the module must measure, report and
  control. Signals cluster by the ECU's job, come in physically sensible units and ranges, sit
  in orderly blocks and are scaled to fit their field width. A decode no engineer would design
  is probably wrong.
- **NEVER mark a signal `verified: true` without proof.** `verified` claims the decode was
  checked against *real data from this vehicle*: a capture matching a known physical state, a
  scan-tool cross-check, or a definitive constant. Not "looks plausible", not "copied from
  another car's sheet". **No capture ⇒ no proof ⇒ stays `--unverified`**, even when porting a
  structure verified on a *different* profile (there it is a hypothesis). A false `verified`
  poisons every downstream user and every correlation that trusts it. Record the missing proof
  in `notes:`.

## The lifecycle

```
orient → prerequisites → discover → capture → inspect → hypothesize
       → define → decode/validate → verify → integrate
```

Progress is tracked per ECU in the `research:` block of `ecus/<ecu>.yaml` (schema
`src/canair/schema/pids_schema.yaml`): `pending → captured → done` (plus `nrc` for a dead
scan), ending in a real `parameters:` entry marked `verified: true`.

Two domains: **A** = diagnostic request/response (`ecus/`, WiCAN `Bnn`); **B** = broadcast CAN
frames (`captures/can/`, `signals/<bus>.yaml`). Detail in
`.claude/skills/reverse-engineer-signal/orient-discover.md`.

## Where to read next

| When you are...                                       | Read                                                                    |
|-------------------------------------------------------|-------------------------------------------------------------------------|
| orienting, checking access, discovering DIDs (1–3)    | `.claude/skills/reverse-engineer-signal/orient-discover.md`             |
| capturing payloads or inspecting bytes (4–5)          | `.claude/skills/reverse-engineer-signal/capture-inspect.md`             |
| forming, testing, defining an expression (6–8)        | `.claude/skills/reverse-engineer-signal/hypothesize-define.md`          |
| verifying, closing research leads, integrating (9–10) | `.claude/skills/reverse-engineer-signal/verify-integrate.md`            |
| picking the canair command for a question            | `.claude/skills/reverse-engineer-signal/tool-cheatsheet.md`             |
| converting a byte index, writing a `Bnn` expression   | `.claude/skills/reverse-engineer-signal/byte-indexing-expressions.md`   |
| reasoning about signal type, physics/EE, statistics   | `.claude/skills/reverse-engineer-signal/signal-reasoning.md`            |
| Hyundai/Kia DIDs (paging, record/IOControl/identity)  | `.claude/skills/reverse-engineer-signal/hyundai-kia-uds-conventions.md` |
