# Layered profiles and write targets

How a profile is found, how a read-only base and a user's capture layer combine, and how canair
keeps a write from landing where the next reinstall deletes it. Read this page before you change
profile discovery, a capture reader or writer, or any command that edits a profile. The
user-facing view is [Profiles](../concepts/profiles.md) and its
[layering section](../concepts/profiles.md#layering-your-captures-over-someone-elses-definitions).

## Finding a profile

Since more than one profile ships, none is selected for you. The precedence is:

1. `--profile NAME|PATH` (a global flag, before the subcommand)
2. `CANAIR_PROFILE`
3. `default_profile` in the user config
4. a single discovered profile

`profile/discovery.py::profiles_roots` lists where to look, highest precedence first:
`--profiles-dir`, `$CANAIR_PROFILES_DIR`, `profiles_dir` in the config,
`~/.config/canair/profiles/` (user profiles, uncommitted) and the repo's `profiles/`. A user
profile **shadows** a bundled one of the same name, unless it declares `extends:`. Then it
**layers**.

Names follow `make-model-code-drivetrain`, never a year (`profile/names.py`). A renamed profile
keeps its old names under `aliases:`. `resolve_profile` maps an old name to the new profile
(`alias_index`). A real directory of the old name always wins. An overlay that is still filed
under the old name layers onto the renamed profile (`profile_layers(..., old_names)`). Nothing
rewrites a user's config or directories, because a config is often shared with older checkouts
that only know the old name. `selectable_profiles` is discovery minus such old-named overlays.
Use it, not `discover_profiles`, for anything that is offered to a user. `also_known_as:` names
are labels for people, resolve nothing, and have one reader: `profile/names.py::also_known_as`.

The bundle's member list is **not** hand-written anywhere else. It is the `BUNDLE_MEMBERS`
registry in `profile/bundle.py`. It also decides what `contribute` ships and what the blind-test
strip withholds, so a new bundle member is one edit there.

## Adopt or overlay

Two commands make a profile writable. Both live in `profile/scaffold.py` and carry no `argparse`.

- **`profile adopt NAME`** copies a read-only profile into `~/.config/canair/profiles/`. The copy
  shadows the original. Choose it when you want to change definitions. It skips members with
  `role: generated` (`out/` is regenerated, not inherited). It **refuses** when a
  higher-precedence discovery root would keep winning (an explicit `--profiles-dir` or
  `$CANAIR_PROFILES_DIR`), because the copy would never be read. The message names the blocking
  root.
- **`profile overlay NAME`** (`overlay_profile`) writes only a `profile.yaml` with
  `extends: NAME` and an empty `captures/`. The base keeps tracking upstream while every
  recording lands in the layer. Choose it when you only want to record.

`profile_layers` walks the roots and stops at the first bundle without `extends:`.
`_from_layers` assembles the `Profile`. It raises an error for a **dangling** overlay (nothing
underneath) and for `extends:` that names a *different* profile (deferred to
`plans/2026-07-30-profile-variant-inheritance.md`, whose key `extends:` deliberately is).

## What a layered `Profile` looks like

`Profile` has `overlays: tuple[Path, ...]` (least specific first) with `layered`, `write_root`
and `capture_layers`.

- **Definitions** (`ecus_dir`, `states_file`, `signals_dir`, `can_buses_file`, `groups_file`,
  `meta_file`) stay on `root`, the base.
- **`captures_dir` and `out_dir`** derive from `write_root`, the user's layer.
- **Definitions do not overlay.** `profile/writable.py::require_writable_definitions()` refuses an
  edit of a layered profile. The one edit funnel that every authoring command shares calls it
  (`cli/commands/_edit.py::edit_scope`). It raises `ProfileError`, which
  `cli/__init__.py` reports as `error: …` and exit 2, so no command handles it.

## Reads span layers, writes do not

- `captures/io.py::resolve_capture_layers` is the read seam. `captures/store.py::load_all_captures`
  iterates it. It dedupes sessions across layers by `captures/merge.py::session_key`, so the base
  wins, and a session that was contributed and then pulled stays read-only. It lets an overlay
  copy that shares a base session's `session_anchor` but not its content **supersede** the base
  (an edited copy is never counted twice). It sorts chronologically **only when layered**, so
  single-layer results stay byte-identical.
- **A reader that bypasses `load_all_captures` needs a decision per call site.**
  `validation/captures.py` and `cli/commands/coverage/` walk **both** layers, because a schema
  break or a payload in the base still matters. `contribute/pii.py` and
  `migrations/captures.py` intentionally stay on `captures_dir` (the overlay). The overlay is
  exactly what `contribute` ships, and the only layer that a migration may rewrite.
- **Base captures are read-only.** `captures/layers.py::refusal` enforces it for `--delete`, the
  `--set-*` editors, `--backfill-states` and the `captures uds --step` TUI. `--dry-run` still
  previews. `read_only_files` returns `[]` when fewer than two layers resolve, so an ordinary
  profile is unchanged. An explicit `--dir` is judged by `layers.py::owning_profile`.
- **A session has no id: its identity is its content,** plus an **anchor**
  (`captures/merge.py::session_anchor`: the date, the label, the version and the first capture's
  time, `rx` and PID). The anchor holds the fields that no editor or repair rewrites. It pairs an
  edited copy with its original in the merge driver, the contribute overlay and the layered read.
  **A new editor must keep the anchor fields intact.** Tombstones and copy-on-write were rejected
  (`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`).

## Never write into the install snapshot

A profile that resolves from `site-packages` (a bare `canair` from `uv tool install`, `pipx` or
`pip`) accepts writes that the next reinstall deletes. This cost two sessions' captures.

- Detection and the user-facing remedy: `install/context.py::installed_snapshot_kind` and
  `snapshot_write_note`.
- **Every write path emits that note.** `captures/save.py::saved_banner` does it for captures.
  `yaml_edit/echo.py::echo_edit` does it for every definition edit. `pids`, `signals`, `states`,
  `groups` and `ecu add` all route their `✓ …` confirmation through it. That also guarantees that
  the confirmation names a full path, not a bare `bms.yaml`.
- `canair update` refuses to be the thing that erases the data. `snapshot_profile_risks` diffs
  the snapshot's profiles against the clone's and lists what a reinstall would delete before it
  asks to proceed.
- `cli/first_run.py` offers to adopt, instead of it recording a `default_profile` that cannot work.
- To fix an existing setup, run `canair config set profiles_dir <clone>/profiles` (preferred, it
  stays tracked by git and can be contributed) or `canair profile adopt <name>`.

A new write path must emit the same note. Plan:
`plans/2026-08-05-profile-write-targets-and-workspace-hygiene.md`.
