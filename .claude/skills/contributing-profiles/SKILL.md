---
name: contributing-profiles
description: Guidelines for contributing a vehicle profile or decoded PIDs/signals UPSTREAM to canair — the `canair contribute`/`share` PR flow, the PII/security-key scrubbing that gates it, profile-data editing discipline (edit-via-tool, never hand-edit), and the validation/quality bar a shared profile must clear. Load this when preparing, cleaning, or submitting a profile (or a partial signal/offset/research contribution) — NOT for changing canair's Python code (use contributing-code) or for the discover→decode→verify RE method itself (use reverse-engineer-signal / ioniq-reverse-engineering).
---

# Contributing a vehicle profile to canair

The highest-value contribution to canair needs **no code changes**: share a
profile (or a slice of one) so the next person with the same car starts ahead
instead of from zero. This skill is the *contribution* side — preparing,
scrubbing, and submitting profile **data** upstream.

Related skills — load the right one for the job:

- **`reverse-engineer-signal`** / **`ioniq-reverse-engineering`** — the RE
  *workflow* that produces the data (orient → discover → capture → analyze →
  define → verify). Do that work *first*; this skill is what to do with the
  result.
- **`contributing-code`** — changing canair's own Python (`canlib/`). If you're
  editing the tool rather than a profile, that's the skill.

Always run `canair` as `uv run canair …` from the repo root (never a
globally-installed bare `canair`). See `AGENTS.md` for why.

## First principle — describe intent, not specifics that will drift

**Avoid baking concrete code, flag lists, or byte offsets into this skill.**
Command flags, schema fields, and PII-detector internals go stale silently.
Point at where to look now (`canair contribute --help`, `canlib/pii.py`, the
schemas in `canlib/schema/`) and verify against the tree — a path is cheap to
re-check, a copied specific rots invisibly. If this skill has drifted, fix it
(Boy Scout rule). This applies to the skill itself: keep it intent-level.

## What a contribution is

- **A whole new car** — a `profiles/<your-car>/` bundle you built with the
  bring-your-own-car journey.
- **More of an existing profile** — newly-decoded params, a corrected byte
  offset, a fixed expression, added captures as evidence.
- **A partial lead** — even a handful of `--verified` signals, one corrected
  offset, or a new `research:` entry is welcome. You do *not* need a "finished"
  profile.

Whichever it is, the data lives in a **profile bundle** (`ecus/`, `profile.yaml`,
`captures/`, `vehicle_states.yaml`, `can_buses.yaml`, optional `signals/` +
`captures/can/`, `references/`, generated `out/`). Inspect the active one with
`canair profile show`.

## Non-negotiables (data edition)

0. **Never hand-edit profile *data* — go through the tool.** The `ecus/` files are
   the source of truth and are edited *only* through the surgical, validated,
   comment-preserving `canair pids` / `canair signals` / `canair states` /
   `canair ecu` editors. Captures are *only* recorded (`--save`) or imported
   (`canair import`), never typed by hand. Hand-editing *data* silently breaks
   schema, formatting, or invariants a reviewer can't see.
   **The one exception is YAML comments** — the `#` explanatory lines carry no
   schema meaning and no tool edits them (the editors only *preserve* them), so
   editing/adding/removing a comment by hand is fine. Touch only the comment
   text, leave the surrounding data keys/values alone, and re-run
   `canair validate all` afterward to confirm you didn't disturb the structure.
1. **It must validate.** `canair validate all` must pass — no exceptions. A
   profile that doesn't validate can't be trusted or merged.
2. **Never commit PII or location data.** Profiles, captures, and git history are
   public forever. A VIN hides *inside* capture payloads. See "No PII or
   location data" — this is what the `canair contribute` pre-flight scans for,
   but the reviewer (you) is the backstop.
3. **Never share ECU security keys, seeds, or unlock algorithms.** Distributing
   `0x27` SecurityAccess material is illegal in many jurisdictions. See "Never
   share security keys".
4. **Be honest about confidence.** Ship `--verified` only when the evidence
   supports it; mark genuine guesses `--unverified`. A wrong "verified" signal is
   worse than an honest unknown.

## Prepare the profile — the quality bar

Before submitting, make the profile clean and honest:

- **`canair validate all` passes.** This covers schema + the duplicate
  shipped-signal-name check + soft warnings (out-of-vocabulary states, misfiled
  captures, non-hex payloads, degraded-transport sessions). Read the warnings —
  they often point at a real defect. Use `--strict` to also fail on untimed
  payload captures if you want the CI-grade gate.
- **Prefer verified params with a `--source`.** A verified parameter should carry
  the evidence that verified it (`--source`, `--notes`); an honest `--unverified`
  candidate is fine and welcome, just labelled as such. Author these with
  `canair pids upsert-param` (never hand-edit `ecus/`).
- **`car_model` pins down the vehicle** — model / year / market / battery — so
  another owner can tell whether the profile matches *their* car. Set it via the
  profile scaffold or `canair config`/profile tooling, not by hand-editing if a
  tool reaches it.
- **Include representative captures as evidence, not everything.** Captures back
  up the decode, but they can be large — a representative subset is enough.
  `canair contribute --no-captures` ships definitions only when the evidence is
  too bulky or sensitive to include.
- **Keep free-text technical.** `--label`/`--notes`, `research:` notes, DTC-log
  labels, and profile descriptions must stay technical — they're a common PII
  leak vector (see below).

## Submit — `canair contribute` (alias `canair share`)

**`canair contribute` opens the pull request for you** — no manual
fork/clone/branch/push, and it works regardless of where the profile is stored
(bundled repo, `~/.config/canair/profiles/`, or a `--path` bundle). Check
`canair contribute --help` for the current flags; the shape of the flow:

1. Runs `canair validate all` and **refuses a broken profile**.
2. Runs the **PII pre-flight** (`canlib/pii.py`) — VIN identity DIDs, VIN-shaped
   payloads, the curated `ecus/` `identity.vin`, and PII-looking free text in
   labels/notes/`car_model` — and requires you to confirm before continuing.
   Per-unit ECU serials are deliberately *not* flagged, in captures or in
   `identity:`: they name a module, not a person.
3. Runs the **staleness / self-collision guards** and asks you to confirm past
   each: an *installed-snapshot* warning when the profile was read from a frozen
   `site-packages`/`uv tool` copy (a bare `canair`, not `uv run canair` from a
   checkout), a *rollback* warning when the contribution would remove committed
   upstream lines from curated definitions (which normally only grow — a rollback
   usually means your source is behind, so sync it first), and a *workspace
   self-collision* refusal when you are running from **inside** the managed
   contribution clone canair stages into (its bundled copy of the profile is the
   copy destination, so there is nothing to contribute — re-run from your own
   checkout). Captures are append-only and are overlaid onto upstream, so a
   behind source never proposes deleting upstream sessions, and a capture log
   your contribution adds nothing to is left untouched.
4. Copies the profile into a managed fork checkout, commits, pushes to your fork,
   and opens the PR via the **GitHub CLI (`gh`)**. When `gh` is missing or
   unauthenticated it prints install + `gh auth login` steps (and the manual
   equivalent) and changes nothing.

Useful modes (verify against `--help`): `--no-captures` (definitions-only PR),
`--dry-run` (prepare branch+commit, no push/PR), `--yes` (non-interactive, for
agents/CI), `--json` (emit the PR URL + findings), `--profile NAME` to target a
specific profile.

**Prefer doing it by hand?** A normal PR putting the bundle under
`profiles/<your-car>/` works just as well — but you still owe the same
validation + PII/security scrub the tool would have run.

> **When submitting a *specific* profile, always pass `--profile NAME`** (and on
> any authoring command that writes into a profile). Without it, writes/PRs
> target whatever `default_profile` resolves to — not necessarily the car you
> mean. This is exactly how signals once landed in the wrong profile.

## No PII or location data — the tree is public

Everything contributed is **shareable/public** and git history is forever.
Nothing that could identify or geolocate the author — or any car owner — may land
in the tree, in **data, captures, docs, commit messages, or history**. When in
doubt, leave it out and ask. (The same rule constrains *code* contributions — see
**`contributing-code`**.)

Watch for these leaks (reason about the *class*, not just the list):

- **Vehicle identity:** VINs, license plates, insurance/registration numbers —
  and remember **these hide inside captured payloads.** Identity DIDs (UDS `F190`,
  KWP2000 record `90`) return the VIN in raw bytes; a capture is *not* safe just
  because it "is just hex". The `canair contribute` PII scan flags these, but
  redact them yourself. A per-unit **ECU serial** (`F18C`/`F18B`,
  `identity.serial`) is *not* in this class — it names a module, not a person, and
  the project treats it as shareable diagnostic data.
- **Identity:** real names, emails, phone numbers, usernames.
- **Location:** home/work addresses, GPS, odometer-at-a-place, Wi-Fi SSIDs, and
  network coordinates (real LAN/VPN IPs, MACs, hostnames). Device addresses live
  in the **gitignored** `~/.config/canair/config.yaml` — keep real values out of
  anything committed.
- **Free-text that quietly accumulates PII:** capture `--label`/`--notes`,
  `research:` notes, DTC-log labels, profile descriptions. Keep them technical.

**Practices:**

- **Scrub before you submit.** Check every capture (and its notes/labels) drawn
  from a real car; redact identity DIDs. `canair validate` is not a privacy
  scanner and the `contribute` pre-flight is a heuristic net, not a guarantee —
  the reviewer is the backstop.
- **If something sensitive already reached the tree, treat it as an incident.**
  Deleting it in a new commit does *not* erase it from history — flag it to the
  user (history rewrite / secret rotation may be needed), don't just
  delete-and-move-on.

## Never share security keys — it's illegal

**Sharing the material that unlocks an ECU's SecurityAccess (UDS/KWP2000 `0x27`)
is illegal in many jurisdictions and dangerous — it must never land in a
contribution, not even by accident.** canair is a read/diagnostics tool; it
stays *aware* of `0x27` at the protocol level but never attempts to break it, and
ships no key-cracking / seed→key solver.

**Forbidden in a profile, its captures, notes, or commit history:**

- **Keys / seed→key pairs / unlock responses** — a captured `27 02 <key>` request
  or `67 01 <seed>` response, a seed paired with the key that unlocked it, or any
  recorded successful-unlock exchange. (If a capture accidentally recorded one,
  delete it before contributing.)
- **Unlock algorithms & secrets** — a seed→key transform for any ECU, PINs,
  passwords, certificates, manufacturer secrets.

**If a capability genuinely needs security access** (dealer-level reprogramming,
coding, an ECU-specific unlock), **do not source, derive, or share the key** —
direct the person to their **local authorized dealer or a licensed repairer**,
who are permitted to perform it. If key material already reached the tree, treat
it as a **security incident** (as with PII): flag it immediately — a plain delete
doesn't purge history, and the key may need rotating.

## Data & generated artifacts — the discipline

- **`profiles/*/ecus/`** is the source of truth — parameters, research,
  identity, addressing, buses, wake rituals. Edit the *data* *only* via
  `canair pids` / `canair ecu` / `canair states` / `canair signals` (validated,
  comment-preserving). If a field can't be reached by a tool, that's a
  canair-code bug to raise (see `contributing-code`), not a licence to hand-edit
  data. YAML **comments** are the exception — they carry no schema meaning and no
  tool edits them, so hand-editing a `#` comment is fine (leave the data keys
  alone and re-validate).
- **`profiles/*/captures/*.json`** are append-only session logs — recorded via
  `--save` or `canair import`, edited/removed via `canair captures uds --delete`,
  never hand-written. Pass `--label`/`--state`/`--notes` when recording.
- **`profiles/*/out/*.json`** is generated by `canair wican autopid write` —
  never hand-edit; regenerate.
- **`signals/` + `captures/can/`** (raw-CAN broadcast domain) follow the same
  discipline: signal maps via `canair signals`, frame logs via `canair import
  can`, indexed in `captures/can/index.yaml`. Mind the raw-CAN log
  **licensing/LFS policy** (a profile's *own* logs commit fully via Git LFS when
  large; third-party logs only when their licence permits redistribution;
  unlicensed corpora stay fetch-on-demand) — see
  `docs/concepts/broadcast-frames.md`.
- Schemas are tool-owned in `canlib/schema/`; `canair validate` is the gate.

## Before you submit

```bash
uv run canair validate all          # must pass — schema + checks
uv run canair contribute --dry-run  # prepare branch+commit locally; preview the PII pre-flight & diff
```

Then eyeball the diff for the PII/security classes above (the reviewer is the
backstop), confirm `car_model` is precise, and submit with
`uv run canair contribute` (or a hand-rolled PR under `profiles/<your-car>/`).

Contributions are released into the public domain under The Unlicense, like the
rest of the project.
