# Code organization, part 9: thin command modules

Status: **PROPOSED** (2026-11-05). Part of
[`2026-11-05-code-organization.md`](2026-11-05-code-organization.md). Depends on sub-plan 8 for
the shared query helper only. Follows the command-package rule of
`plans/2026-08-06-command-packages-and-live-split.md` and the non-goals of
`plans/2026-11-03-yaml-edit-router.md`.

## Goal

1. Every command module in `cli/` is under 500 lines.
2. Code that a test could call without the CLI leaves `cli/`, as the `cli/` charter says:
   "Not here: anything a test could call without the CLI".

## Findings (at `0ce4f742`)

- `cli/` holds **31,900 lines in 142 files, 29% of the package**. Its charter says it holds
  "flags, output and glue only".
- **12 command modules are over 500 lines:**

  | Module | Lines | Notes |
  |---|---:|---|
  | `cli/commands/pids.py` | 1,557 | 42 functions; `add_parser` alone is 689 lines (about 31 subparsers) |
  | `cli/commands/bix.py` | 1,049 | `_annotate_payload` is 209 lines and computes the byte roles |
  | `cli/commands/ecu.py` | 1,047 | `cmd_detail` 109 lines, `cmd_pids` 81; a flat sibling `ecu_revisions.py` |
  | `cli/commands/wican.py` | 873 | the `mode` and `autopid` groups in one file |
  | `cli/commands/hunt.py` | 750 | `run` is 228 lines; three kinds (`uds`, `can`, physical) in one file |
  | `cli/commands/contribute.py` | 721 | flat siblings `_contribute_gates.py` and `_contribute_report.py` |
  | `cli/commands/profile.py` | 691 | |
  | `cli/commands/states.py` | 629 | |
  | `cli/commands/scan.py` | 624 | |
  | `cli/commands/config.py` | 604 | |
  | `cli/commands/captures/uds.py` | 524 | |
  | `cli/commands/decode/one.py` | 501 | |

- The command-packages plan says: "a command is one module until it needs two, then it becomes
  a package", and "do not add a flat `_<name>_*.py` sibling". The contributing-code skill says
  that the existing flat siblings are grandfathered. `contribute` and `ecu` are the two
  commands that still have them.
- The yaml-edit-router plan decided: "No table-driven argparse. The `pids` parser is 700 lines
  of declarations. Generating it risks `--help` drift for little gain." This plan keeps the
  declarations as they are, and only moves them into several files.
- `pids`, `ecu`, `wican`, `config` and `states` use their module docstring as `--help` text.
  When a module becomes a package, its docstring moves to the package `__init__.py`, together
  with the charter. The layout handoff notes that a charter goes before the `Examples:` text,
  where the help epilog is cut.

## Design

Each large command becomes a package with this shape:

```text
cli/commands/<cmd>/
  __init__.py    NAME, add_parser (it calls each group's add_parsers), run; the docstring is --help
  <group>.py     the declarations of one subcommand group, and its handlers
  render.py      the command's output, if it prints more than a line or two
```

Compute moves down, to the package that owns its data, as a function that returns data.

| Command | Groups | Compute that moves down |
|---|---|---|
| `pids` | `params` (upsert, rm, rename), `pid` (defaults, hoist, revisions, sensitive, snapshot-only, support map), `ecu_facts` (identity, CAN bus, wake, addressing, IOControl ranges, set-iocontrol), `research`, `migrate` | none expected. The editors are already in `yaml_edit/`. `_echo_capture_range` (61 lines) is a candidate for `captures/` or `analysis/`. |
| `bix` | `table`, `annotate`, `overview` | the byte annotation of `_annotate_payload`: one function that returns a row per byte (role, WiCAN, ISO-TP, Torque and bix indices, the signal that maps it). It goes beside `ByteRef` (in `expression/` after sub-plan 4). The printing stays in `render.py`. |
| `ecu` | `list`, `detail`, `pids`, `add`, `rename`, `edit`, `revisions` (today `ecu_revisions.py`), `set_variants` | the PID listing with the latest decoded state, if it is more than glue |
| `wican` | `mode`, `autopid` (`write`, `upload`, `download`, `diff`, `stats`) | the AutoPID diff (`show_diff`, 80 lines), to `interop/autopid/` |
| `hunt` | `uds`, `can`, `physical`, `promote` | each kind's search, if `run` holds more than flag handling. `analysis/cross_signal.py` (sub-plan 1) already holds the engine. |
| `contribute` | `pipeline`, `gates` (today `_contribute_gates.py`), `report` (today `_contribute_report.py`) | none. The orchestration is already in `contribute/`. |

For each "compute that moves down" entry, read the code first. Move only what has no flags and
no printing, and that a test could call directly. If a function mixes the two, separate the
check from the report first, as the `validation/` work of the layout plan did.

## Steps

One command per commit. "Split and package in one commit", as the skill says, so the rename is
booked once.

1. `contribute` (the flat siblings)
2. `ecu` (the flat sibling)
3. `pids`
4. `bix`, after sub-plan 4
5. `wican`
6. `hunt`
7. The second wave: `profile`, `states`, `scan`, `config`, `captures/uds.py`, `decode/one.py`.
   Each one is under 750 lines. Split each by concern inside its own package.

In each commit:

- Move the declarations without editing them. `scripts/gen/gen_cli_reference.py --check` must
  report no change.
- Move the command's tests to `tests/cli/commands/<cmd>/`.
- Check that `tests/cli/commands/test_live_operations.py` still passes, for `scan` and the
  other live commands.
- Remove the module from `oversized_modules`, or rename its key, before
  `test_structure --write`.
- Regenerate the architecture page if a charter changed.

## Acceptance

- No module under `cli/` is over 500 lines.
- No flat `_<cmd>_*.py` sibling is left in `cli/commands/`. The skill's "grandfathered"
  sentence can go.
- `--help`, the CLI reference, the golden outputs and the screenshots are unchanged.
- The share of lines in `cli/` falls. This is not a target. It shows that compute moved down.

## Risks

- **Help text.** A docstring that moves to a package `__init__.py` must keep its exact text.
  `gen_cli_reference.py --check` finds any change.
- **`monkeypatch` strings.** Command tests patch names such as `canair.cli.commands.pids.x`.
  After a split, the name can live in a different module from the one the test patches. A
  string that names a module that is gone fails loudly. A string that names the package
  `__init__.py`, where the name no longer is used, can fail or patch the wrong module. Patch
  the module that looks the name up, and run each moved test file alone once.
