---
name: contributing-profiles
description: Guidelines for contributing a vehicle profile or decoded PIDs/signals UPSTREAM to canair — the `canair contribute`/`share` PR flow, the PII/security-key scrubbing that gates it, profile-data editing discipline (edit-via-tool, never hand-edit), and the validation/quality bar a shared profile must clear. Load this when preparing, cleaning, or submitting a profile (or a partial signal/offset/research contribution) — NOT for changing canair's Python code (use contributing-code) or for the discover→decode→verify RE method itself (use reverse-engineer-signal / ioniq-reverse-engineering).
---

# Contributing a vehicle profile to canair

The highest-value contribution to canair needs **no code changes**: share a
profile (or a slice of one) so the next person with the same car starts ahead
instead of from zero. This skill is the *contribution* side — preparing,
scrubbing, and submitting profile **data** upstream. A partial lead (a few
`--verified` signals, one corrected offset, a `research:` entry) is welcome too.

Related skills — load the right one for the job:

- **`reverse-engineer-signal`** / **`ioniq-reverse-engineering`** — the RE
  *workflow* that produces the data. Do that work *first*.
- **`contributing-code`** — changing canair's own Python (`src/canair/`).
- **`profile-variants`** — whether a car needs its own profile, and how to name
  (or rename) one. Settle that before contributing a new profile.

## Critical rules (one-line summaries — details in the files below)

- **Never hand-edit profile data** — use `canair pids`/`signals`/`states`/`ecu` editors;
  captures are only recorded or imported. (YAML `#` comments are the one exception.)
- **`canair validate all` must pass** — no exceptions.
- **Never commit PII or location data** — VINs hide inside capture payloads; the tree and
  git history are public forever. Read `pii-location.md` before submitting.
- **Never share ECU security keys, seeds, or unlock algorithms** (`0x27` SecurityAccess) —
  illegal in many jurisdictions. Read `security-keys.md` before submitting.
- **Be honest about confidence** — `--verified` only with evidence; guesses `--unverified`.
- **Always pass `--profile NAME`** when submitting or authoring into a specific profile;
  otherwise `default_profile` decides where it lands.
- Already-leaked PII or keys are an **incident**: a delete commit doesn't purge history.

## Workflow

1. Do the RE work first (other skills), settle naming with `profile-variants`.
2. Prepare: validate, source/verify signals, set `car_model`, pick representative captures.
3. Scrub: check captures, labels, notes for PII and security-key material.
4. Preview: `uv run canair contribute --diff` shows the diff and stops. Read it for PII and
   security-key material: the reviewer (you) is the backstop.
5. Submit: `uv run canair contribute --profile NAME` (or a hand-rolled PR).

## Routing

| When you are...                                              | Read                                                                      |
|--------------------------------------------------------------|---------------------------------------------------------------------------|
| Deciding what counts as a contribution / preparing the bar   | `.claude/skills/contributing-profiles/contribution-scope-quality.md`      |
| Checking the full data rules, edit-via-tool, generated files | `.claude/skills/contributing-profiles/non-negotiables-data-discipline.md` |
| Running `canair contribute` (flow, modes, `--profile`)       | `.claude/skills/contributing-profiles/submit-contribute.md`               |
| Scrubbing captures/labels for PII or location data           | `.claude/skills/contributing-profiles/pii-location.md`                    |
| Anything touching `0x27` SecurityAccess keys/seeds           | `.claude/skills/contributing-profiles/security-keys.md`                   |

## Licence

Contributions under `profiles/` are licensed CC BY 4.0 (`profiles/LICENSE`), like
the rest of the profile data; code is MIT. Only submit material you have the right
to license that way.
