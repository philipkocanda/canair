# Profile variants, phase 4 — choosing a variant, and refusing without one

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (see its
[Selection](2026-10-30-profile-variants-and-rename.md#selection) section). Depends on phase 3.

## Goal

Remember each user's variant per profile, and make every command that needs a variant **do
nothing** until one is chosen. This is the one deliberately breaking phase of the series, so it
carries nothing else.

## What users see

- `canair profile use NAME --variant ID`, or a picker on a TTY, sets the variant once. It is
  remembered per profile.
- On a profile with several variants and no choice, a live command, `--save` or AutoPID
  generation exits with status 2 and an explanation:

  ```text
  error: hyundai-ioniq-ae-ev describes 2 variants and none is selected.

    28kwh   Ioniq Electric 28 kWh (pre-facelift)   built 2016 – 2019-05
    38kwh   Ioniq Electric 38 kWh (facelift)       built 2019-06 – 2022

    Pick yours once:     canair profile use hyundai-ioniq-ae-ev --variant 28kwh
    Or for one command:  canair --variant 28kwh <command> …
  ```

  The "not sure which you have? `canair identity BMS`" line arrives with detection (phase 8),
  when `identity` can actually answer it.
- Users of `ioniq-2017`, `kona-electric-2019` and `xpeng-g6` are not interrupted: their old
  name implies the variant.

## Changes

### Remembering the choice

- User config `profile_settings.<profile>.variant`. Validated in `canlib/config.py` (an
  enum-checked `config set` key) and shown by `config show`.
- `canair profile use NAME [--variant ID]` (`commands/profile.py::_cmd_use`): sets
  `default_profile` and the variant. On a TTY with several variants and no `--variant`, it opens a
  picker showing each variant's name, production window and battery, built in the style of the
  existing profile picker (`_cmd_pick`).
- **Precedence**: `--variant` > `CANAIR_VARIANT` > `profile_settings.<profile>.variant` > the
  alias-implied variant > the only variant, when there is exactly one.
- `Profile.variant` is set from this resolution, together with its source (flag / env / config /
  alias / only).

### Aliases that imply a variant

- `aliases:` entries gain `variant:`:
  - `ioniq-2017: {variant: 28kwh}`
  - `kona-electric-2019: {variant: 64kwh}`
  - `xpeng-g6: {variant: 87kwh}`
  - `ioniq-5-2022` and `nissan-leaf-ze1` imply none, because their old `car_model` named no single
    pack.
- Phase 1's breadcrumb `profile_settings.<profile>.renamed_from` is turned into an explicit
  `variant` by a one-time step in the phase-1 migration module. This covers users whose config was
  already rewritten, so they get the variant without being asked.
- Session tags from an implied variant record `attribution.variant: alias`, and are shown as
  unconfirmed.

### The refusal — one gate

- A single function, `canlib/variants/__init__.py::require_variant(profile, purpose)`, raises
  `ProfileError` with the message above. It is called from:
  - `commands/_live/runtime.py::run_live` (every live command);
  - the `--save` paths;
  - `canair sniff`;
  - `wican autopid write`/`upload`/`diff`.
- **Exempt:**
  - `identity` and `discover` without `--save`;
  - `status`, `lock`, `logs`, `config`, `profile`;
  - the authoring commands;
  - read-only analysis.
- `status` and `profile show` report "no variant selected" and the fix.

### Captures

- Every `--save` is now tagged (`selected`, `alias` or `only`), since an untagged recording can
  no longer be made on a multi-variant profile.
- Analysis default scope becomes the selected variant if there is one. Untagged sessions are
  decoded as that variant, with a footer saying so. With no variant, untagged sessions are skipped,
  with a footer giving the count and the fix.
- **CI ratchet:** `validate captures --max-untagged N` fails when the untagged count grows, with
  per-profile baselines in `.github/workflows/ci.yml` beside the untimed ones.
- On the first live run, if an overlay holds untagged sessions, print one hint pointing at
  `captures migrate-variants`. It is a hint, not a prompt: the owner knows which car those were.

### Tests and fixtures

`tests/conftest.py` sets `CANAIR_VARIANT=28kwh` alongside `CANAIR_PROFILE`, so the suite keeps
exercising today's behaviour.

### Skill and docs

- The skill gains **selection**: how to pick, why canair never guesses.
- `docs/concepts/variants.md` (selection, the refusal), `docs/concepts/profiles.md`,
  `docs/getting-started/` (pick your variant as a first-run step), `docs/reference/config.md`
  (`profile_settings`), the regenerated CLI reference.
- `CHANGELOG.md`, **as a breaking change**: what refuses, why, and the one-command fix.

## Tests

- The precedence chain, each step on its own.
- An undeclared variant is an error.
- The refusal fires on each gated path and not on any exempt one.
- The picker writes config.
- `renamed_from` becomes `variant`.
- Alias-implied tags carry `alias` attribution.
- The ratchet passes at baseline and fails on growth.

## Acceptance

- On a fresh config, `uv run canair --profile hyundai-ioniq-ae-ev read BMS:2101` refuses with the
  message above, and nothing is sent to the device.
- After `profile use … --variant 28kwh`, the same command behaves exactly as in phase 3.
- `CANAIR_PROFILE=ioniq-2017` works with no further setup, via the alias.

## Out of scope

Anything that uses the variant to change definitions (phase 5 onwards).

## Risks

- **Every existing user of every bundled profile is affected once,** scripts and CI included. The
  mitigations are aliases for three of the five, the self-explaining refusal, `CANAIR_VARIANT` for
  non-interactive setups, and the changelog. Landing it alone keeps the blast radius
  understandable.
