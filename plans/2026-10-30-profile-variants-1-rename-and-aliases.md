# Profile variants, phase 1 — make-model-code names and old-name aliases

Status: **NOT STARTED.** Part of
[`2026-10-30-profile-variants-and-rename.md`](2026-10-30-profile-variants-and-rename.md) (the
design; read its [Names](2026-10-30-profile-variants-and-rename.md#names-and-ids) and
[Rename and aliases](2026-10-30-profile-variants-and-rename.md#rename-and-aliases) sections first).
Depends on nothing.

## Goal

Rename the five bundled profiles to `make-model-code-drivetrain` names, keeping every old name
working. **No variants yet**: nothing about decoding, captures or selection changes.

| Old name | New name |
|---|---|
| `ioniq-2017` | `hyundai-ioniq-ae-ev` |
| `kona-electric-2019` | `hyundai-kona-os-ev` |
| `ioniq-5-2022` | `hyundai-ioniq-5-ne-ev` |
| `nissan-leaf-ze1` | `nissan-leaf-ze1-ev` |
| `xpeng-g6` | `xpeng-g6-f30-ev` |

## What users see

- New names in `profile list`, docs and the explorer.
- Old names keep resolving, with a one-line deprecation note naming the new one.
- On first run, `default_profile` in config and overlay directories under
  `~/.config/canair/profiles/` are migrated to the new name, each with a printed notice.
- **No command refuses, and no output changes** beyond the profile name.

## Changes

### Alias resolution — `canlib/profile.py`

- `profile.yaml` gains `aliases:`, a mapping of old name → `{}`. A mapping, not a list, so that a
  later phase can add the implied variant (`{variant: 28kwh}`) without a schema change.
- An alias index built by `discover_profiles`, over the same roots and precedence.
- `resolve_profile`: a real profile directory always wins (any root); else an alias resolves to its
  target, with the deprecation note on stderr; two profiles claiming one alias is a
  `ProfileError`.
- `profile_layers` / `_from_layers`: an overlay found under an *old* name whose `extends:` names
  that old name is layered onto the renamed base, so layering works even before its migration runs.

### First-run migration — new `canlib/migrations/profile_names.py`

Modelled on `migrations/devices.py::maybe_auto_migrate`: best-effort, never raises,
self-clearing, one notice per action.

- **Config:** `default_profile: <old>` → the new name. Also record
  `profile_settings.<new>.renamed_from: <old>`, a breadcrumb that lets phase 4 apply the alias's
  implied variant to users whose config this phase already rewrote.
- **Overlays:** `~/.config/canair/profiles/<old>/` with `extends: <old>` → rename the directory,
  rewrite `extends:`. If the target directory already exists, refuse and print why; never merge,
  never delete.
- **Adopted full copies** (no `extends:`) are left alone: rule 1 keeps them working as their own
  profile, and `profile list` notes the bundled successor.

### Commands

- `profile list`: aliases shown as `ioniq-2017 → hyundai-ioniq-ae-ev (renamed)`, never as
  profiles. Adopted copies under an old name note their bundled successor.
- `profile use <old>` writes the *new* name.
- `profile create`: warns (does not refuse) when a name doesn't match the convention, and points at
  the skill.
- `canair contribute` (`canlib/contribute.py`, `commands/contribute.py`): a source whose name is
  an alias is contributed to its target's directory, and says so. Never recreate
  `profiles/<old>/`.
- `canair update` (`install_context.py::snapshot_profile_risks`): files at risk in a snapshot's
  old-named profile are rescued into the renamed directory of the clone.

### Validation

- `validate`: bundled profile names must match
  `^[a-z0-9]+(-[a-z0-9]+)*-(ev|erev|phev|hev|ice|fcev)$`; aliases must be unique across discovered
  profiles and must not equal any real profile name.

### Repo-side rename

- `git mv` each bundle. The LFS and merge-driver patterns are `profiles/*/…`, so they still match.
- `car_model` values: "Hyundai Ioniq Electric (AE)", "Hyundai Kona Electric (OS)", "Hyundai Ioniq
  5 (NE)", "Nissan Leaf (ZE1)", "XPeng G6 (F30)". Battery-specific wording moves to phase 2's
  metadata, and is kept as a profile.yaml comment until then.
- Live references only: code docstrings/examples (`canlib/profile.py`, `quirks.py`,
  `commands/profile.py`, `config.py`), `config.example.yaml`, `tests/conftest.py`, goldens and
  every test naming a profile, `.github/workflows/ci.yml` (untimed baseline key),
  `scripts/mkdocs_hooks/profiles_index.py` (`DEDICATED_PAGES`), `mkdocs.yml` nav,
  `scripts/gen_screenshots.py` and the screenshot tapes, README, AGENTS.md, skills. **Not
  `plans/`**: dated history.
- Docs pages: `docs/profiles/hyundai-ioniq-ae-ev.md` and `docs/profiles/nissan-leaf-ze1-ev.md`,
  with stub pages at the old paths linking to the new ones.

### Skill — new `.claude/skills/profile-variants/SKILL.md`

Created now, carrying only what is true in this phase: the naming convention (where to find a
model code, the drivetrain list, never a year) and when a car needs a **new profile** (model,
generation, drivetrain, E/E architecture, profile-wide vocabularies). Write it with the
**skill-authoring** skill loaded. Add one-line pointers from `contributing-profiles`,
`reverse-engineer-signal`, `ioniq-reverse-engineering` and AGENTS.md.

## Tests

- Alias resolution: real directory wins; alias resolves with the note; duplicate alias errors;
  alias equal to a real name is rejected by `validate`.
- Migration: config rewrite plus breadcrumb; overlay rename plus `extends:` rewrite; existing
  target refused; idempotent; never raises.
- Layering an un-migrated old-named overlay onto the renamed base.
- Contribute maps an alias-named source; snapshot rescue lands in the renamed directory.
- The existing goldens pass with only the profile name changed. Any other diff in their output is
  a regression.

## Docs

`docs/concepts/profiles.md` (naming convention, aliases), `docs/bring-your-own-car/
01-create-profile.md` (naming), the two renamed profile pages + stubs, `docs/profiles/index.md`,
regenerated CLI reference, `CHANGELOG.md` (renames, aliases, the migration notices), AGENTS.md
(profile names in examples and the Profiles section).

## Acceptance

- `validate all` green for every renamed profile; CI's per-profile validate loop uses the new
  names.
- `CANAIR_PROFILE=ioniq-2017 uv run canair decode BMS 2101` produces the same output as before
  the rename, plus the deprecation note.
- A config with `default_profile: ioniq-2017` and an overlay `ioniq-2017/` is migrated on first
  run, and captures in that overlay still appear in `captures uds --sessions`.

## Out of scope

Variants of any kind (phase 2), implied variants on aliases (phase 4).

## Risks

- **The rename touches ~330 references.** Mechanical but wide; a grep for each old name in
  live files (excluding `plans/`) must come back empty.
- **Migration on someone else's machine.** It only renames and rewrites, never deletes, and refuses
  on a collision. A broken migration must never break a command.
