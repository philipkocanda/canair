# Layered profiles and write targets

How canair finds a profile, how a read-only base and the capture layer of a user combine, and how
canair keeps a write from landing where the next reinstall deletes it. Read this page before you
change profile discovery, a capture reader or writer, or any command that edits a profile. The
user-facing view is [Profiles](../concepts/profiles.md) and its
[layering section](../concepts/profiles.md#layering-your-captures-over-someone-elses-definitions).

## Finding a profile

Because more than one profile ships, canair selects none for you. The precedence is:

1. `--profile NAME|PATH` (a global flag, before or after the subcommand)
2. `CANAIR_PROFILE`
3. `default_profile` in the user config
4. a single discovered profile

`profile/discovery.py::profiles_roots` lists where to look, highest precedence first:

1. `--profiles-dir`
2. `$CANAIR_PROFILES_DIR`
3. `profiles_dir` in the config
4. `~/.config/canair/profiles/` (user profiles, uncommitted)
5. the `profiles/` directory of the repo

A user profile **shadows** a bundled one of the same name, unless it declares `extends:`. Then it
**layers**.

Names follow `make-model-code-drivetrain`, never a year (`profile/names.py`). A renamed profile
keeps its old names under `aliases:`.

- `resolve_profile` maps an old name to the new profile (`alias_index`). A real directory of the
  old name always wins.
- An overlay that is still filed under the old name layers onto the renamed profile
  (`profile_layers(..., old_names)`).
- Nothing rewrites the config or the directories of a user, because a config is often shared with
  older checkouts that only know the old name.
- `selectable_profiles` is discovery minus such old-named overlays. Use it, not
  `discover_profiles`, for anything that canair offers to a user.
- `also_known_as:` names are labels for people. They resolve nothing and have one reader:
  `profile/names.py::also_known_as`.

The member list of the bundle is **not** hand-written anywhere else. It is the `BUNDLE_MEMBERS`
registry in `profile/bundle.py`. The registry also decides what `contribute` ships and what the
blind-test strip withholds. A new bundle member is therefore one edit there.

## Adopt or overlay

Two commands make a profile writable. Both live in `profile/scaffold.py` and carry no `argparse`.

- **`profile adopt NAME`** copies a read-only profile into `~/.config/canair/profiles/`. The copy
  shadows the original. Choose it when you want to change definitions.

    - It skips members with `role: generated` (`out/` is regenerated, not inherited).
    - It **refuses** when a higher-precedence discovery root would keep winning (an explicit
      `--profiles-dir` or `$CANAIR_PROFILES_DIR`), because canair would never read the copy. The
      message names the blocking root.

- **`profile overlay NAME`** (`overlay_profile`) writes only a `profile.yaml` with
  `extends: NAME` and an empty `captures/`. The base keeps tracking upstream while every
  recording lands in the layer. Choose it when you only want to record.

`profile_layers` walks the roots and stops at the first bundle without `extends:`. `_from_layers`
assembles the `Profile`. It raises an error in two cases:

- a **dangling** overlay (nothing underneath)
- `extends:` that names a different profile. Today an overlay must extend the profile that has its
  own name (or an old name of that profile), and it layers only `captures/`. A profile that
  inherits the definitions of another profile with a different name is not built.
  `plans/2026-07-30-profile-variant-inheritance.md` is the design for that definition merge. It is
  not started, and it keeps the `extends:` key for this case.

## What a layered `Profile` looks like

`Profile` has `overlays: tuple[Path, ...]` (least specific first) with `layered`, `write_root`
and `capture_layers`.

- **Definitions** (`ecus_dir`, `states_file`, `signals_dir`, `can_buses_file`, `groups_file`,
  `meta_file`) stay on `root`, the base.
- **`captures_dir` and `out_dir`** derive from `write_root`, the layer of the user.
- **Definitions do not overlay.** `profile/writable.py::require_writable_definitions()` refuses an
  edit of a layered profile. The one edit funnel that every authoring command shares calls it
  (`cli/commands/_edit.py::edit_scope`). It raises `ProfileError`. `cli/__init__.py` reports that
  error as `error: …` with exit code 2, so no command handles it.

## Reads span layers, writes do not

- `captures/io.py::resolve_capture_layers` is the read seam. `captures/store.py::load_all_captures`
  iterates it.

    - `load_all_captures` dedupes sessions across layers by `captures/merge.py::session_key`. The
      base wins, and a session that was contributed and then pulled stays read-only.
    - It lets an overlay copy **supersede** the base when the copy shares the `session_anchor` of a
      base session but not its content. canair never counts an edited copy twice.
    - It sorts chronologically **only when layered**, so single-layer results stay byte-identical.

- **A reader that bypasses `load_all_captures` needs a decision per call site.**
  `validation/captures.py` and `cli/commands/coverage/` walk **both** layers, because a schema
  break or a payload in the base still matters. `contribute/pii.py` and `migrations/captures.py`
  intentionally stay on `captures_dir` (the overlay). The overlay is exactly what `contribute`
  ships, and the only layer that a migration may rewrite.
- **Base captures are read-only.** `captures/layers.py::refusal` enforces it for `--delete`, the
  `--set-*` editors, `--backfill-states` and the `captures uds --step` text user interface.
  `--dry-run` still previews. `read_only_files` returns `[]` when fewer than two layers resolve, so
  an ordinary profile is unchanged. `layers.py::owning_profile` judges an explicit `--dir`.
- **A session has no id. Its identity is its content,** plus an **anchor**
  (`captures/merge.py::session_anchor`: the date, the label, the version, and the time, `rx` and
  parameter identifier (PID) of the first capture). The anchor holds the fields that no editor or
  repair rewrites. It pairs an edited copy with its original in the merge driver, the contribute
  overlay and the layered read. **A new editor must keep the anchor fields intact.** The design
  rejected tombstones and copy-on-write
  (`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`).

## Never write into the install snapshot

A profile that resolves from `site-packages` (a bare `canair` from `uv tool install`, `pipx` or
`pip`) accepts writes that the next reinstall deletes. This cost two sessions' captures.

- Detection and the user-facing remedy: `install/context.py::installed_snapshot_kind` and
  `snapshot_write_note`.
- **Every write path emits that note.**

    - `captures/save.py::saved_banner` does it for captures.
    - `yaml_edit/echo.py::echo_edit` does it for every definition edit. `pids`, `signals`,
      `states`, `groups` and `ecu add` all route their `✓ …` confirmation through it. That also
      guarantees that the confirmation names a full path, not a bare `bms.yaml`.

- `canair update` refuses to be the thing that erases the data. `snapshot_profile_risks` diffs the
  profiles of the snapshot against the profiles of the clone. It lists what a reinstall would
  delete before it asks to proceed.
- `cli/first_run.py` offers to adopt a profile, instead of recording a `default_profile` that
  cannot work.
- To fix an existing setup, run `canair config set profiles_dir <clone>/profiles` (preferred:
  git tracks it and you can contribute it) or `canair profile adopt <name>`.

A new write path must emit the same note. Plan:
`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`.
