# Code organization, part 7: package `__init__.py` bodies

Status: **DONE** (2026-11-06; see "As built"). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 3 for
`profile/` only. The other packages can start after sub-plan 1.

## Goal

Move the code out of the six `__init__.py` files that are over 500 lines, into modules named
for what they do. Each `__init__.py` then holds its charter, and a reader who opens a package
sees its parts in the file list.

## Findings (at `0ce4f742`)

| `__init__.py` | Lines | Top-level names | References to the package root (imports and `monkeypatch` strings, `src/` and `tests/`) |
|---|---:|---:|---:|
| `states/` | 987 | 38 | 87 |
| `contribute/` | 935 | 45 | 11 |
| `captures/` | 845 | 30 | 160 |
| `profile/` | 705 | 26 | 198 |
| `config/` | 554 | 26 | 74 |
| `ecus/` | 502 | 24 | 94 |

These files became package `__init__.py` files in phase 4 of the layout plan, when a loose
module (`profile.py`, `config.py`, `contribute.py`) became a package. That kept every import
and `monkeypatch` string working at the time. The `captures/` charter says that saving is "here",
in the `__init__.py`.

Two more `__init__.py` files hold code that is not package-wide:

- `tui/__init__.py` (131 lines) holds the arrow-key list picker of `canair profile`.
- `actions/`, `variants/` and `query/` hold 258 to 349 lines. They are under the limit, and
  their code is the package's main job. This plan leaves them.

## Target modules

The split follows the groups that the names already form. Check each group against the code
before the move, and adjust it where a function belongs with another group.

| Package | New modules |
|---|---|
| `states/` | `vocabulary.py` (load, names, allowed states, parse, join, order, the YAML boolean words), `predicates.py` (`StateRule`, the predicate parser and the Kleene evaluator), `implies.py` (the `implies:` map, closure, cycle check, exclusive pairs, `most_specific_states`), `suggest.py` (`satisfied_states`, `suggest_state`, `suggest_states`), `selectors.py` (`state_matcher`, `format_state_selector`, `state_bucket_key`, `unknown_state_tokens`, `ecu_states`, `ecus_in_state`) |
| `contribute/` | `git.py` (the `git` runner, upstream ref, branch, commit, `has_changes`), `workspace.py` (ensure, reset, leftovers, backup, discard), `copy.py` (copy the profile, the capture overlay, `CaptureReplacement`), `preflight.py` (`Step`, `Preflight`, `ContributionPlan`, the rollback and collision checks), `github.py` (viewer permission, direct push, the PR lookup) |
| `captures/` | `build.py` (the `build_*_session` functions, `non_answer_text`, the `suggest_*_label` functions), `save.py` (`save_session`, `save_session_journaled`, the file write, `saved_banner`, the capture tag), `session_edit.py` (the `set_session_*` functions, `set_capture_note`, `delete_capture`, `annotate_session_spans`) |
| `profile/` | `bundle.py` (`BundleMember` and the member registry), `model.py` (`Profile`, `ProfileError`, `read_meta`, `looks_like_profile`), `discovery.py` (roots, discovery, layers, aliases, `resolve_profile`, `profile_for_path`), `writable.py` (`require_writable_definitions`) |
| `config/` | `file.py` (the config path and directory, get, set, unset, load), `devices.py` (`DeviceEntry`, `wican_devices`, `fallback_settings`, the WiCAN model), `transport.py` (`reconnect_max_wait`, `ws_ping_interval`, `stale_cycles_before_reconnect`, `expected_responses`, `firmware_check`) |
| `ecus/` | `registry.py` (`EcuRegistryEntry`, `load_ecus`, identity confidence), `names.py` (canonical names, aliases, the name indices, `EcuNameCollision`, `ecu_display`), `rx.py` (`rx_for_tx`, the RX indices, `resolve_tx`), `protocol.py` (`ecu_protocol`, `split_ecus_by_protocol`) |
| `tui/` | `picker.py` (`select_from_list` and the key reader) |

`profile/` waits for sub-plan 3, which moves `set_active`, `replace_active` and `active` to
`cli/` and removes `Profile.meta` and `Profile.revisions`.

The layout plan's section 4 already proposed `config/devices.py`, for the device registry that
both `transport/` and `device/` read.

## Decision D: re-export, or rewrite the callers

Option 1: the `__init__.py` re-exports every public name (`from .save import save_session`).
The 624 references to the package roots keep working, and nothing else changes.

Option 2: the `__init__.py` holds only the charter, and every caller imports from the module
that defines the name. A codemod rewrites the imports and the `monkeypatch` strings.

**Recommendation: option 2.**

- A re-exporting `__init__.py` imports every module of the package whenever any one of them is
  imported. Sub-plan 5 forbids that for `live/` and `tui/apps/`, for start-up time and to keep
  Textual out of the view models. One rule for every package is simpler.
- A module inside the package cannot import a sibling through the package root without a
  cycle. With re-exports, that mistake is easy to make and hard to see.
- The layout plan's principle 7 rejects compatibility aliases for moved code. A re-export of a
  moved function is such an alias.
- A patch of `canair.captures.save_session` does not affect code that imported the name from
  `canair.captures.save`. With option 2, every `monkeypatch` string names the module that
  defines the name, so a patch cannot miss.

Option 2 costs one codemod per package. The layout plan used the same codemod for phase 4.

## Steps

One package per commit, in this order: `config/`, `ecus/`, `states/`, `captures/`,
`contribute/`, `tui/` (the picker), then `profile/` after sub-plan 3.

In each commit:

1. Cut whole top-level definitions from `__init__.py` into the new modules. Do not edit a
   function body.
2. **Check that the move is mechanical.** A script compares the source of every top-level
   definition before and after (an AST scan, by name). Every definition must exist once, with
   the same source. Only import lines may change.
3. Rewrite the imports and the `monkeypatch` strings in `src/` and `tests/` with a codemod.
4. Keep the charter in `__init__.py`. Update its "Belongs" line to name the new modules.
5. Rename the `oversized_modules` key in `baseline.json` (for example `canair.captures` to
   `canair.captures.save`) before `test_structure --write`, or remove it when every new module
   is under 500 lines.
6. Regenerate the architecture page.

## Acceptance

- No `__init__.py` under `src/canair/` is over 100 lines, apart from `actions/`, `variants/`
  and `query/`.
- The mechanical-move check of step 2 passes for every commit.
- `--help` and every golden output are unchanged.

## Risks

- **`AGENTS.md` paths.** `AGENTS.md` names many `src/canair/<package>/__init__.py::name`
  references (for example `profile/__init__.py::profile_layers`). The path check of sub-plan 1
  fails on each one after the move. Correct them in the same commit. Sub-plan 10 then cuts
  most of them.

## As built

Decision D is taken: option 2. Each `__init__.py` keeps its charter and imports nothing.

The move tooling (outside the repo, kept for a rebase) is `split.py` (cut the top-level
definitions into modules per a spec), `verify.py` (the step 2 check, by name, against the old
`__init__.py` at the base commit) and `rewrite.py` (the callers, including `from canair import
pkg` aliases, `import canair.pkg` and the dotted strings).

- **`config/`** (done): `file.py`, `devices.py`, `transport.py`. 554 lines to 23. The check
  passes for 39 definitions. A deferred import that needs two of the new modules is two
  statements, which raises the `deferred_internal_imports` count, so
  `cli/commands/config/edit.py` imports `config.file` at the top (a layer below everything, loaded
  on every start-up anyway): the count falls 1104 to 1103.
- **`ecus/`** (done): `registry.py`, `names.py`, `protocol.py`. 502 lines to 10. The check passes
  for 27 definitions. The plan's `rx.py` is merged into `registry.py`: callers import
  `load_ecus` with `rx_for_tx` or `build_rx_index`, and a deferred import that names two modules
  is two statements, which raises the deferred-import count. Two files import `ecus.registry` at
  the top instead (1103 to 1101).
- **A patch has to name the module that looks the name up.** A function that moved to another
  module keeps its own binding of every name it calls. The tool
  `codemods/p7/patch_guard.py` (a pytest plugin, outside the repo) wraps each such binding and
  logs a call made while the definition's module is patched. It found the `load_ecus` patches
  of `tests/dtc/test_dtc.py` and the `load_config` patches of
  `tests/transport/test_transport_config.py`; both now patch every module.
- **`states/`** (done): `tokens.py`, `predicates.py`, `implies.py`, `vocabulary.py`, `suggest.py`,
  `selectors.py`. 987 lines to 44. The check passes for 49 definitions. `tokens.py` is new:
  `load_states` needs `implies`, and `implies` needs `parse_states`, so `parse_states` cannot sit
  in `vocabulary`. The check also covers the comment blocks and the `TYPE_CHECKING` import. Seven
  command, validation and monitor modules import `states.*` at the top: 1101 to 1099.
- **`captures/`** (done): `build.py`, `save.py`, `session_edit.py`, and `decode.py` (new: it holds
  `_decode_payload`, which the plan did not place). 845 lines to 6. The check passes for 31
  definitions. Seven modules import `captures.*` at the top (`captures/journal.py` and six under
  `live/`): 1099 to 1094. `tests/live/test_dtc_snapshot_mode.py` once wrote into the bundled
  profile's `captures/` when a patch missed a hoisted binding; the patch guard found it.
- **`contribute/`** (done): `git.py`, `workspace.py`, `copy.py`, `checks.py`, `github.py`. 934 lines
  to 27. The check passes for 57 definitions. The plan's `preflight.py` is `checks.py`: the step
  and preflight types sit in `git.py` (the runner returns them), so a `preflight.py` holding the
  types and the checks would be a cycle. The tests that faked `contribute._run` fake it in every
  module that binds it (`_patch_run`), and the command package imports the names it uses instead
  of the `C` alias, so the tests that faked a `C.` function patch the command module.
- **`tui/`** (done): `picker.py` (`select_from_list`, `key_to_action`, `read_key_raw`,
  `PICKER_FOOTER`). 131 lines to 15. `__all__` is dropped.

Measured: the deferred-import ceiling fell from 1104 to 1094; `canair --help` takes the same
time as before (a median of about 236 ms under load, in alternating runs).

- **`profile/`** (done, after sub-plan 3 part B): `bundle.py`, `model.py`, `discovery.py`,
  `writable.py`. 610 lines to 34. The check passes for 27 definitions. `profile_for_path` is in
  `model.py`, not `discovery.py`: callers import it with `read_meta` or `Profile`, and it only
  builds a `Profile`. `BUNDLED_PROFILES_DIR`, which the old `__init__.py` re-exported, is read
  from `core.paths` by `install/context.py`; the tests patch it in `profile.discovery` (or
  `core.paths` for `install/context.py`). Three modules import `profile.*` at the top:
  the deferred-import ceiling falls 1025 to 1017.

Every package named in this plan is done. The other `__init__.py` files over 100 lines are
`actions/`, `variants/`, `query/` (left by this plan), and the command packages
`cli/commands/*/__init__.py`, `cli/__init__.py` and `yaml_edit/pids/__init__.py`.

Method notes for the next package: a deferred `from canair.pkg import a, b` that needs two new
modules becomes two statements, and the ceiling may not rise. Choose the groups by what callers
import together (`codemods/p7/count_splits.py` counts the splits for a spec before the move), and
import at the top in a module whose package already loads at `--help`
(`python -X importtime -m canair --help` lists what loads). A patch string has to name the module
that looks the name up, and `codemods/p7/patch_guard.py` finds the ones that miss.
