---
name: reverse-engineer-signal
description: "Generic, vehicle-agnostic reverse-engineering workflow for canair — the whole flow from orient/discover through capture, analyze, define, verify, integrate, for ANY signal (a PID/DID parameter, a raw broadcast frame field, a routine, an IOControl actuator) on ANY car. Covers WiCAN Bnn / ISO-TP / PCI byte indexing, expression syntax, the analysis reasoning (signal types, physics/EE, statistics), and writing/validating definitions. Use when discovering, decoding, or verifying anything on a vehicle bus, writing or fixing an expression, working out a byte offset, or working a research: backlog — on the bundled Ioniq profile or a profile you built for another car. Examples use the Ioniq for concreteness; the method is generic."
---

# Reverse-engineering a vehicle signal (generic)

The **vehicle-agnostic, end-to-end** workflow for taking a signal from "unknown" to a
verified, decoded parameter in a profile's `ecus/`. Not PID-specific: the same loop
serves a service-`22`/`21` PID/DID, a passively-sniffed broadcast frame field, a
routine, or an IOControl actuator. Examples use the bundled 2017 Ioniq because it is
fully worked — the *method* transfers, not the byte offsets. This skill is the
*procedure*; the profile is the *data* (which ECU carries which signal, marque quirks
and addresses live in the active profile's `ecus/`).

Related skills: **`ioniq-reverse-engineering`** (bundled-car ignition lines, ECU gotchas and
factory module names — load it too when working the Ioniq), **`decode-bitfields`** (the
bit-level loop for discrete body signals), **`contributing-profiles`** (sharing the
result upstream).

> **Target the right profile.** The repo ships several (`hyundai-ioniq-ae-ev`,
> `hyundai-ioniq-5-ne-ev`, …) and **none auto-selects**. **Pass `--profile NAME` on every mutative
> command** — `pids …`, `hunt --promote`, `import uds`, `signals upsert`, `--save` reads — otherwise
> they write to whatever `default_profile`/`CANAIR_PROFILE` resolves to,
> *not necessarily the car you mean* (exactly how signals once landed in the wrong
> profile). Prefer it on read-only commands too. Examples below omit it — add it.
> Unsure which profile a car belongs in, or what to name a new one? See the
> **profile-variants** skill.

## Safety first (non-negotiable)

Full policy: `docs/concepts/safety.md`. The essentials:

- **NEVER interact with the car without the user's explicit approval** (`AGENTS.md` → The car).
  Approval is never implied by the task or by this skill. Before the first step that sends
  something to the car (discover, scan, capture, a live check), propose the exact commands and
  wait for an explicit "yes". That "yes" then covers the session, as `AGENTS.md` says. Steps 1
  and 5–9 work on the existing captures and need no car.
- **NEVER** open a programming session (`10 02`) or do any firmware write/upload —
  ECUs can be bricked. canair blocks these; never work around it. The same guard refuses
  ECU resets (`11`), silencing an ECU's traffic (`28`), memory writes (`2E`/`3B`/`3D`) and
  reprogramming routines (`31 01 FF00`, Hyundai/Kia's `10 05` alias); the list is in
  `docs/concepts/safety.md`.
- A read (`22`) cannot harm an ECU in any DID range, but it still needs approval. `2E` writes
  and `2F` actuation can brick or move hardware — out of scope for signal decoding.
- **One canair connection at a time, any transport** (a `flock` mutex enforces it); no
  concurrent requests to one ECU. Clear a stuck session with `--force` or
  `canair lock` — **never by rebooting the device**, and never reboot without asking
  (the WebSocket terminal overrides AutoPID; the reboot is what restores it).
- Be gentle: the first request after idle often fails. Do not conclude from one failure that
  a PID/ECU is dead: report it and ask the user before you retry.
- Device sleep should be off for a long session and on again after. That is **`wican-cli`** (a
  *separate* package), NOT canair: `wican sleep --disable`. Status: `canair status`. Both touch
  the device, so ask the user to run them or to approve them.

## Working principles

- **Evidence, not vibes.** Never accept a byte because it "looks about right": confirm
  it (range, distribution, correlation, physical plausibility across states) and state
  your confidence honestly.
- **NEVER mark a parameter `verified: true` without proof.** `verified` claims the
  decode was checked against *real data from this vehicle* — a capture matching known
  physical state, a scan-tool cross-check, or a definitive constant. Not "looks
  plausible", not "copied from another car's sheet". **No capture ⇒ no proof ⇒ stays
  `--unverified`**, even when porting a structure verified on a *different* profile
  (here it is a hypothesis). A false `verified` poisons every downstream user and every
  correlation that trusts it; record the missing proof in `notes:`.

## The lifecycle

```
orient → prerequisites → discover → capture → inspect → hypothesize
       → define → decode/validate → verify → integrate
```

Progress is tracked per-ECU in the `research:` block of `ecus/<ecu>.yaml` (schema
`src/canair/schema/pids_schema.yaml`): `pending → captured → done` (plus `nrc` for a dead
scan), ending in a real `parameters:` entry marked `verified: true`.


Two domains: **A** = diagnostic request/response (`ecus/`, WiCAN `Bnn`); **B** = broadcast CAN
frames (`captures/can/`, `signals/<bus>.yaml`). Detail in
`.claude/skills/reverse-engineer-signal/orient-discover.md`.

Steps: **1 Orient** (`research`, `coverage`, `--support-map`) · **2 Prerequisites** (power,
access) · **3 Discover** (which DIDs respond) · **4 Capture** (payloads per state) · **5 Inspect**
(bytes) · **6 Hypothesize** · **7 Test** (`decode --try`) · **8 Define** (`ecus/`) · **9 Verify** ·
**10 Integrate**. Steps 3–4 touch the car: ask first.

## Where to read next

| When you are...                                       | Read                                                                    |
|-------------------------------------------------------|-------------------------------------------------------------------------|
| orienting, checking access, discovering DIDs (1–3)    | `.claude/skills/reverse-engineer-signal/orient-discover.md`             |
| capturing payloads or inspecting bytes (4–5)          | `.claude/skills/reverse-engineer-signal/capture-inspect.md`             |
| forming, testing, defining an expression (6–8)        | `.claude/skills/reverse-engineer-signal/hypothesize-define.md`          |
| verifying, closing research leads, integrating (9–10) | `.claude/skills/reverse-engineer-signal/verify-integrate.md`            |
| picking the canair command for a question             | `.claude/skills/reverse-engineer-signal/tool-cheatsheet.md`             |
| converting a byte index, writing a `Bnn` expression   | `.claude/skills/reverse-engineer-signal/byte-indexing-expressions.md`   |
| reasoning about signal type, physics/EE, statistics   | `.claude/skills/reverse-engineer-signal/signal-reasoning.md`            |
| the ECU-engineer mindset, writing notes               | `.claude/skills/reverse-engineer-signal/working-principles.md`          |
| Hyundai/Kia DIDs (paging, record/IOControl/identity)  | `.claude/skills/reverse-engineer-signal/hyundai-kia-uds-conventions.md` |
