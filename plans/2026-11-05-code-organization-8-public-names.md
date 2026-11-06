# Code organization, part 8: public names across packages

Status: **DONE** (2026-11-06). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 7,
because that plan moves some of the modules named here.

## Goal

A name that another package uses is public: it has no leading underscore. A name with an
underscore is used only inside its own package. A gate keeps it that way.

## Findings (at `0ce4f742`)

An AST scan of `src/canair/` finds **17 underscore names that another package imports, at 29
import sites**. For `cli/`, the scan treats each command package (`cli/commands/<cmd>/`) as its
own package.

| Name | Defined in | Imported by |
|---|---|---|
| `_capture_key` | `captures/entries.py` | `cli/commands/captures/step.py`, three modules of `tui/apps/captures_step_*` |
| `_dedupe_payloads` | `captures/entries.py` | `cli/commands/captures/diff.py`, `tui/apps/captures_step_model.py` |
| `_entry_to_dict` | `captures/entries.py` | `cli/commands/captures/listing.py`, `tui/apps/captures_step_model.py` |
| `_group_by_key` | `captures/entries.py` | `cli/commands/captures/diff.py` |
| `_is_hex_payload` | `captures/entries.py` | `cli/commands/captures/query.py`, `tui/apps/captures_step_model.py` |
| `_key_ordinals`, `_prev_same_index` | `captures/entries.py` | `tui/apps/captures_step_model.py` |
| `_set_session_field` | `captures/__init__.py` | `migrations/variants.py` |
| `_parse_query` | `cli/commands/captures/query.py` | `cli/commands/decode/query.py`, `cli/commands/search/uds.py` |
| `_tcp_open` | `device/wican/mode.py` | `live/raw_ops.py`, `transport/fallback.py` |
| `_is_newer` | `install/update_check.py` | `cli/commands/update.py` |
| `_looks_like_pid`, `_query_selectors` | `query/steps.py` | `live/multi.py`, `live/multi_repl.py` |
| `_HIGHLIGHT_STYLE`, `_bytes_to_ascii` | `render/formatting.py` | `live/monitor.py` |
| `_render_hex_line` | `render/formatting.py` | `cli/commands/captures/diff.py`, `live/_monitor_render.py`, `live/monitor.py`, `tui/apps/captures_step_render.py` |
| `_order_states` | `states/__init__.py` | `captures/state_inference.py`, `cli/commands/captures/backfill.py` |

Two more cases are attribute access, not imports:

- `tui/apps/monitor.py` sets `controller._on_partial`. Sub-plan 6 renames it.
- `variants/selection.py` reads `profile._active`. Sub-plan 3 removes it.

The contributing-code skill tells agents to use the "alias-aware `_parse_query` in
`cli/commands/captures/query.py`" for every capture or analysis command that takes a QUERY. So
the skill makes a private name the shared helper.

Tests import underscore names in 64 files. This plan does not change that. A unit test of a
module may test its private functions.

## Design

For each name, choose one of two actions:

1. **Rename it** without the underscore, where it stays. This is right when the name is a real
   part of its module's job, and several packages use it: `_capture_key`, `_entry_to_dict`,
   `_render_hex_line`, `_order_states`.
2. **Move it** to the package whose job it is, when its users show that it is in the wrong
   place. Check each case. For example, `_tcp_open` in
   `device/wican/mode.py` is a generic TCP reachability probe, and nothing in `device/` calls
   it. `cli/commands/status.py` has its own copy with the same name. One public function in
   `core/` (it needs nothing else from canair) can replace both.

`_parse_query` gets a public name and a home in `cli/args/query.py`. It is shared by several
commands and needs the ECU registry, so it is CLI-layer plumbing beside the other shared flag
helpers. Update the contributing-code skill.

### The gate

Add a list baseline `cross_package_private_imports` to `baseline.json`, and a test in
`tests/contracts/test_structure.py` with the same scan. Like the other list baselines, it fails
when the tree gains an entry and when an entry stops applying. The baseline starts with the 17
names and ends empty.

## Steps

1. Add the scan, the baseline and the test.
2. **One defining module per commit:** `captures/entries.py`, `captures/` (the
   `_set_session_field` name, after sub-plan 7 moves it), `render/formatting.py`, `states/`,
   `query/steps.py`, `device/wican/mode.py`, `install/update_check.py`. Each commit renames or
   moves its names, rewrites the callers and their tests, and removes the entries from the
   baseline.
3. `_parse_query` to `cli/args/query.py`. Update the contributing-code skill.

## Acceptance

- `cross_package_private_imports` is empty.
- The test fails on a new cross-package import of an underscore name. Check this once with a
  throwaway change.

## Out of scope

- Private names in tests.
- Underscore *modules* (`live/_monitor_record.py`). Sub-plan 5 renames them inside the new
  sub-packages.

## As built

The scan found 15 names, not 17: `_HIGHLIGHT_STYLE` (`render/sniff.py`, same package) and
`_bytes_to_ascii` (`live/monitor/`, now inside one package) no longer cross a package.
The scan is `scripts/check/import_graph.py::cross_package_private_imports`; the baseline
`cross_package_private_imports` started with those 15 names and is empty.

- Renamed in place: `capture_key`, `dedupe_payloads`, `entry_to_dict`, `group_by_key`,
  `is_hex_payload`, `key_ordinals`, `prev_same_index` (`captures/entries.py`),
  `set_session_field` (`captures/session_edit.py`), `order_states` (`states/vocabulary.py`),
  `render_hex_line` (`render/formatting.py`), `query_selectors` (`query/steps.py`) and `is_newer`
  (`install/update_check.py`). The `_looks_like_pid` alias of `query/steps.py` fed only a
  re-export that nothing read, so it is gone.
- Moved: `_tcp_open` and `_tcp_probe` of `device/wican/mode.py`, with the copy in
  `cli/commands/status/probe.py`, to `core/tcp.py` (`tcp_open`, `tcp_probe`). Tests patch
  `canair.core.tcp.tcp_open`.
- Moved: `_parse_query` to `cli/args/query.py` as `parse_canonical_query`, because
  `canair.query.parse_query` is a different function (no alias resolution).
- The attribute-access cases need no change here: `_on_partial` was renamed by sub-plan 6, and
  the scan skips attribute access, so `profile._active` stays with sub-plan 3.
- Deferred imports stay at 1094. The throwaway check (an import of a made-up `_ghost` name from
  `states/selectors.py`) failed `test_no_package_imports_a_private_name_of_another`.
