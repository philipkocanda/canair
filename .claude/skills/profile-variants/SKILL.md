---
name: profile-variants
description: Where a car belongs in canair and what to call it — whether a different car needs its own profile, how to name a profile (make-model-code-drivetrain, never a year), and how a renamed profile keeps its old name working. Load when creating, naming or renaming a profile, or when deciding whether a car fits an existing profile. NOT for decoding signals (reverse-engineer-signal) or submitting a profile upstream (contributing-profiles).
---

# Where a car belongs, and what to call its profile

A profile describes **one model generation with one drivetrain**. This skill is the judgement
behind that sentence: when a car needs a profile of its own, and how to name one so the name
never has to change. The design and its later stages are in
`plans/2026-10-30-profile-variants-and-rename.md`; read it before extending this skill.

Always run `canair` as `uv run canair …` from the repo root, and pass `--profile NAME` on every
mutative command (`AGENTS.md` explains both).

## Does this car need its own profile?

A **new profile** when any of these differ from an existing one:

- **Drivetrain** — EV, EREV, PHEV, HEV, ICE, FCEV. The ECU sets barely overlap (BMS/MCU/OBC vs
  ECM/TCM/HCU), and so do the vehicle states.
- **Model or generation**, even on a shared platform. Kona OS and Niro DE are separate profiles;
  so are Kona OS and its successor, Kona SX2.
- **Electrical architecture** — a different gateway or bus topology, CAN-FD, or a profile-wide
  `addressing` mode (11-bit vs 29-bit).
- **A profile-wide vocabulary** — when the car would need a different `vehicle_states.yaml`,
  `can_buses.yaml` or `groups.yaml`.

The test is whether those profile-wide pieces still hold, **not how many ECUs changed**. A
facelift that swaps several ECUs but keeps the buses, addressing, states and drivetrain is the
same profile.

Not a reason for a new profile:

- **Trim and equipment.** A car without BSD or a heat pump just has an ECU that doesn't answer.
- **Model year.** A year is never by itself a distinction.
- **Battery size, facelift or market** within one generation. These belong in the same profile;
  the plan describes how such differences are recorded.

Two profiles of related cars (Kona and Niro, a model's EV and EREV) still duplicate their shared
ECUs today. Sharing across profiles is the open inheritance design in
`plans/2026-07-30-profile-variant-inheritance.md`; don't merge two cars into one profile to avoid
the duplication.

## Naming a profile

`make-model-code-drivetrain`, lowercase, hyphenated, **every part always present**:

| Part | What it is | Examples |
|---|---|---|
| make | the manufacturer | `hyundai`, `nissan`, `xpeng` |
| model | the nameplate, which may be several tokens | `ioniq`, `ioniq-5`, `leaf`, `g6` |
| code | the maker's model code for this generation | `ae`, `ne`, `os`, `ze1`, `f30` |
| drivetrain | one of `ev`, `erev`, `phev`, `hev`, `ice`, `fcev` | `ev` |

- **Never a year.** A year was wrong for most of the cars it named; that is why the bundled
  profiles were renamed.
- **Never drop the code or the drivetrain** because the model "only has one". Both change during
  a model's life (a successor generation; an EREV added part-way through a generation), and the
  name then has to change too.
- **Finding the model code:** start with the car itself. ECUs often report it in their identity
  strings (`canair identity`, or `canair search` over the captures) — the Ioniq Electric's BMS
  answers `AEEV__ BMS`, which is the model code `AEEV` (Ioniq `AE`, `EV`). Then the maker's
  documentation, then a well-sourced reference, and record where it came from.
- The rule and its pattern live once, in `canlib/profile_names.py`; `canair validate` enforces
  them for bundled profiles, and `canair profile create` notes a non-conforming name.

## Renaming a profile

A rename must not break anyone using the old name: their `default_profile`, `CANAIR_PROFILE`,
scripts, and capture overlays under `~/.config/canair/profiles/<old name>/`.

1. `git mv profiles/<old> profiles/<new>`, and record the old name in the renamed profile's
   `profile.yaml`:

   ```yaml
   aliases:
     <old name>: {}
   ```

2. Update live references to the profile (code examples, tests, docs, CI, skills). Leave
   `plans/` alone: they are dated history. Leave capture files alone too: they never name their
   profile.
3. Keep a stub page at the old docs path, linking to the new one.
4. `uv run canair --profile <new> validate all`, and check the old name still resolves:
   `uv run canair --profile <old> profile show`.

How the old name resolves — and why a real directory of the old name (an adopted copy) always
wins — is in `docs/concepts/profiles.md` under *Renamed profiles*. Never write a migration that
rewrites a user's config or renames their directories: a config is often shared by canair
checkouts of different ages, and an older one would no longer find the profile.
