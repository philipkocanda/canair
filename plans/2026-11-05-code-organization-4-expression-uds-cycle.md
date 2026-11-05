# Code organization, part 4: the `expression`/`uds` cycle

Status: **DONE** (2026-11-05). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 1.
Continues left-over item 2 of `plans/2026-11-04-package-layout-and-layering.md` (the
`expression : uds` half), and follows the recipe written there.

## Goal

Put `expression/` in the layer above `uds/`, so that `uds/` imports only `core/` and the
standard library, as its charter says.

## Findings (at `0ce4f742`)

- `uds/` imports `expression/` in two modules, both for one function:
  - `uds/notation.py` (401 lines: `ByteRef`, `ByteDisplay`, the `--notation` views) calls
    `expression.signed_range_is_exact` in `ByteRef`.
  - `uds/inspect_bytes.py` (214 lines: the byte sweeps that build candidate expressions) calls
    it too.
- `expression/` imports `uds/` in `decode_value.py` and `decoding.py`. That is the right
  direction.
- `signed_range_is_exact` lives in `expression/nodes.py`, beside the sign-extension ladder
  (`SIGNED_RANGE_WIDTHS`) it describes. The layout plan decided to keep it there.
- Inside `uds/`, only `uds/support_map.py` imports `notation`, and only for
  `subfunction_bytes_for_pid`.
- Outside `uds/`, 42 files import `uds.notation` and 13 import `uds.inspect_bytes`.
  `AGENTS.md` names `src/canair/uds/notation.py::ByteRef`.

## Why `notation` and `inspect_bytes` move up

Both modules build WiCAN expressions. Building an expression is the job of `expression/`, so
the two modules belong there by their charter, not only to break the cycle. Moving the guard
down into `uds/` would separate it from the ladder that it must agree with.

## Steps

One commit for each step.

1. **Move `subfunction_bytes_for_pid` to `uds/layout.py`.** It is a fact about the response
   layout (two bytes for a service-22 DID, else one). Rewrite its 17 importers. After this,
   `uds/support_map.py` does not import `notation`.
2. **Move `uds/notation.py` to `expression/notation.py`.** Use `git mv` and a codemod for the
   42 importers and the `monkeypatch` strings. Move its tests from `tests/uds/` to
   `tests/expression/`. Rename the key in `baseline.json` if the module has a ceiling.
3. **Move `uds/inspect_bytes.py` to `expression/inspect_bytes.py`**, in the same way.
4. **Split the layer.** In `pyproject.toml`, replace `canair.expression : canair.uds` with two
   layers, `expression` above `uds`. Update both charters. The `uds/` charter says "the
   standard library and `core` only", and that is now true. Regenerate the architecture page.
5. **Update the references.** `AGENTS.md` and the reverse-engineer-signal skill name
   `uds/notation.py`. The path check of sub-plan 1 lists every place.

## Acceptance

- `lint-imports` passes with `expression` and `uds` in separate layers.
- `scripts/check/import_graph.py` reports no cycle, if sub-plan 3 has landed.
- `--help` is unchanged. `--notation` output is unchanged (the golden tests cover it).

## Risks

- **Ceilings on import lines.** The layout plan notes that several importers are at their size
  ceiling (`cli/commands/hunt.py`, `live/_monitor_render.py`). A changed import that lands in a
  new isort group adds a line. Keep the new import beside the existing `canair.expression`
  imports.

## As built

All five steps landed as four commits (steps 4 and 5 share one).

- Step 1 moved `subfunction_bytes_for_pid` to `uds/layout.py`: 19 files changed, its test moved
  to `tests/uds/test_uds_layout.py`. Two importers deferred the import inside a function, so a
  second deferred line would have been added. Both now import `ByteDisplay` and the function at
  the top, beside their existing `notation` import (the deferred count fell from 1131 to 1129).
  `hunt.py` and `_monitor_render.py` sat at their ceiling: the unused shebang of `hunt.py` and a
  stale docstring line ("the ~150-line renderer") of `_monitor_render.py` paid for the new line.
- Step 2 moved `uds/notation.py` to `expression/notation.py`: 35 importer files, not 42 (the
  count included `subfunction_bytes_for_pid` importers that step 1 had already rewritten).
  `baseline.json` held no entry for it.
- Step 3 moved `uds/inspect_bytes.py` to `expression/inspect_bytes.py`: 13 files. Its test
  already lived in `tests/expression/`.
- Step 4: the contract has `canair.expression` above `canair.uds`; the architecture page lists
  them as layers 14 and 15. `scripts/check/import_graph.py` reports one cycle left
  (`ecus, profile, variants`).
- Step 5: only `AGENTS.md` named the old path. No skill or doc did.
- Gates: `ruff`, `ty`, `make structure`, the full suite (11055 passed), and both `--check`
  generators are green. `--help` is unchanged.
