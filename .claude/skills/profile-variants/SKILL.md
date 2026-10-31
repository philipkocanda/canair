---
name: profile-variants
description: Where a car belongs in canair and what to call it — whether a different car needs its own profile, is a variant of an existing one (battery, facelift, market), or only has a different revision of one ECU; how to name a profile (make-model-code-drivetrain, never a year), a variant and a revision; and how a renamed profile keeps its old name working. Load when creating, naming or renaming a profile, adding or editing a variant (variants.yaml, canair variants) or an ECU revision (canair ecu revisions, pids set-revisions, --pin/--revision), tagging or importing captures from a particular car (--variant), or deciding whether a car fits an existing profile. NOT for decoding signals (reverse-engineer-signal) or submitting a profile upstream (contributing-profiles).
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
- **Battery size, facelift or market** within one generation. These are *variants* of the same
  profile (below).

Two profiles of related cars (Kona and Niro, a model's EV and EREV) still duplicate their shared
ECUs today. Sharing across profiles is the open inheritance design in
`plans/2026-07-30-profile-variant-inheritance.md`; don't merge two cars into one profile to avoid
the duplication.

## Variants: versions of the car within one profile

A **variant** is a version of the car the owner can name (battery size, facelift, a market with
different diagnostics) **and** that matters to the diagnostics or to keeping captures apart. It
is declared in the profile's `variants.yaml` with an ID and metadata, and captures are tagged with
the variant they came from (below). A variant can also say which ECUs its car has and which
revision of an ECU it carries (next section), and override a few `profile.yaml` settings
(`profile_overrides:`).

- **Model year alone is never a variant.** Makers change parts on production dates, and owners
  can't tell which side of a mid-year change their car is on. Record `production` (a build
  window), and treat `model_years` as informational.
- **Trim is never a variant.** Equipment differences only decide which ECUs answer.
- **IDs are local and name only what differs**: the battery capacity the car is sold by, in whole
  kWh (`28kwh`, `38kwh` for 38.3 kWh), plus the next difference when two variants share a pack
  (`38kwh-us`). Never repeat the profile's make, model or code in an ID, and never use a year.
- **Metadata is evidence.** Every value cites a source (`sources:`); leave a field out rather than
  guess it. `status:` says how much to trust the variant as a whole: `verified` (proven on a car),
  `community` (from community sources), `seed` (a placeholder for a car nobody has captured).
  Record whether a battery figure is usable or gross; makers quote either.
- **Edit with `canair variants`**, never by hand (`--help` for the syntax). Pass `--profile`.

## ECU revisions: when one ECU changed, not the car

A **revision** is a version of one ECU that differs in protocol or decoding — a VCU that went
from KWP2000 to UDS, a BMS whose cell count changed the layout. Decide bottom-up: revision before
variant before new profile.

- **The ECU decides, not the car.** ECUs change on their own production dates, and a dealer
  replacement follows the newer rules in an older car. A running change inside one variant is two
  revisions of the ECU, never two variants (that multiplies: `2^n` variants for `n` changes, and
  asks the owner a question they can't answer).
- **A revision exists only if a definition differs.** A new part number with the same diagnostics
  is another *fingerprint* of an existing revision (`identity:` globs, or `responds:` — a safe
  identity read only that revision answers). An identity that matches no fingerprint is a
  question to investigate, not a new revision.
- **Name it by what differs** (`kwp`/`uds`, `150s`), local to the ECU, never a year. The first
  declared revision is the reference.
- **Gate what differs, nothing more.** `pids set-revisions ECU:PID uds` (or `ECU:PID:SIGNAL`) on
  the PIDs and signals only one revision has; leave shared ones ungated. Keep a signal's *name*
  across revisions (two PIDs may ship one name when their revisions are disjoint), so predicates,
  groups and Home Assistant keep working — `validate states` checks every predicate on every car
  and revision.
- **Let the variant narrow it**: `variants set-revisions 38kwh BMS uds` when a car can only have
  one; an ECU only some cars have gets `ecu set-variants`.
- **Never guess a revision** to make PIDs appear. An undetermined ECU skips its gated PIDs on
  purpose; ask the user, read the ECU's identity, and pin it (`profile use NAME --pin ECU=ID`, or
  `--revision ECU=ID` once). A seeded revision's definitions stay `verified: false`.
- **Evidence belongs to a revision.** A definition's own `verified` and `response_frames` speak
  for its reference revision (the first it applies to); never assume a signal verified there holds
  on another. Record a difference with `pids upsert-param … --revision R` (it writes
  `per_revision.R`; a new signal is gated to `[R]`), and mark it verified only from a capture of
  that revision. Ported or seeded definitions stay `verified: false`.
- Edit with `canair ecu revisions ECU …` (its `rename` rewrites gates, variants and capture
  tags), never by hand. `docs/concepts/variants.md#ecu-revisions` has the model; the synthetic
  fixture `tests/fixtures/profiles/variants/` shows every case.

## Choosing the car you're working on

On a profile with several variants, canair refuses to talk to the car, record or generate the
AutoPID profile until a variant is named — and there is no default, by design. Name it with
`canair profile use NAME --variant ID` (remembered per profile) or the global `--variant` for one
command; `docs/concepts/variants.md` has the precedence.

- **Never pick a variant to make a refusal go away.** Ask the user which car they have, or run
  `discover`/`identity` (allowed without a choice) and read the evidence. A wrong choice tags
  every recording with the wrong car.
- **An old profile name may imply a variant** (`ioniq-2017` is the 28 kWh car). Those tags are
  attributed `alias` and shown as unconfirmed; replace them with an explicit choice when the user
  confirms.

## Tagging captures with the car they came from

A capture is evidence about one car, so on a profile with several variants every session should
say which: its `variant` tag, set by the global `--variant ID` flag (see
`docs/concepts/variants.md` for the mechanics).

- **Every recording is tagged with the chosen car**, so the choice above is what makes the tag
  right.
- **Every import names the car on the command line.** A forum paste or a third-party log is
  someone else's car; `import uds`/`import can` refuse without the global `--variant`, and
  `CANAIR_VARIANT` doesn't count. Never pick a variant just to make the command run: if the
  source doesn't say which car it was, ask, or don't import it into a multi-variant profile.
- **Tag existing history only from evidence** about where it came from (a capture's notes, who
  recorded it, a battery size readable in the data). `captures migrate-variants ID`, scoped by
  date and label, stamps sessions as `migrated`; leave a profile's history untagged rather than
  guess.
- **Fix a wrong tag with `captures uds --set-variant`**, never by hand. Renaming a variant goes
  through `canair variants rename`, which retags everything.

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
