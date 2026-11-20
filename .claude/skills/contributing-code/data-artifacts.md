# Data and generated artifacts

The plumbing you maintain: generated files, schemas, and their currency checks.

## Data & generated artifacts — the plumbing you maintain

This is about the *code* that reads/writes profile data; the authoring/scrubbing discipline for the
data itself is **`contributing-profiles`**.

- `profiles/*/ecus/` is the source of truth, edited only through the surgical/validated editors
  (`src/canair/yaml_edit/`: `pids/` and `ecu.py`). **Keep editor coverage complete** — a field
  reachable only by hand is a bug to close, not a reason to normalize hand-editing.
- **Every definition editor lives in `src/canair/yaml_edit/` and shares one write path.** A new one
  declares a `Target` beside its rules, lists it in `yaml_edit/targets.py` (a test fails if you
  forget), and writes through `edit_doc` (ruamel) or `edit_ecu_text` (text surgery). It raises
  `EditError` (a subclass per file kind) and never prints or exits — `cli/__init__.py` reports it. It never
  opens or writes the file itself, and never `del`s or rebuilds a ruamel mapping to remove or rename
  an entry: `ruamel_ops.remove_key`/`remove_item`/`rename_key` keep the comments around it. The
  command is `run_edit(lambda: editor(...), "confirmation")` from `cli/commands/_edit.py`.
- **Free-text fields render themselves** via one shared policy (`src/canair/core/yaml_rt.py`): short
  notes inline, longer ones a wrapped folded `>-` block (double-quoted when folding can't carry
  the text exactly), never reflowing the rest of the file. A new ruamel-written free-text field
  just needs adding to `src/canair/yaml_edit/ecu.py::FREE_TEXT_FIELDS`.
- `profiles/*/captures/*.json` are written by the `--save` path, never by hand, and are
  **append-only** logs unioned by canair's git merge driver. If you touch the on-disk format, keep
  `src/canair/captures/merge.py` aligned with `src/canair/captures/io.py`.
- `profiles/*/out/*.json` is generated — regenerate (`uv run python scripts/gen/gen_autopid.py` writes
  both the verified-only and the `-unverified` build of every bundled car), never hand-edit.
- Schemas are tool-owned in `src/canair/schema/`; validate with `canair validate`.
- **Agent tooling hides bulk data on purpose.** `opencode.json` (OpenCode) and
  `.claude/settings.json` (Claude Code) deny file reads of `profiles/*/captures/*.json`, the raw-CAN
  logs in `profiles/*/captures/can/` (not its `index.yaml`), `profiles/*/out/` and `uv.lock`, and
  `.ignore` hides the same paths from `rg`. So a `Read`, `Grep` or `Glob` that "finds nothing" or
  is refused there is not a missing file: use the commands below. Claude Code's `Read` deny also
  blocks its Edit and Write tools on those paths. A shell `rg --no-ignore` or `cat` is not
  blocked, so do not use it to dodge the rule. Keep the three files in step when a bulk path
  moves or a new one appears.

  | To see | Run (`uv run canair --profile NAME …`) |
  |---|---|
  | what the captures hold | `captures uds --summary`, then `captures uds --sessions --last-sessions 5` |
  | one PID's latest or recent payloads | `captures uds BMS 2101 --latest`, `… --limit 5` |
  | a byte pattern or string anywhere | `search PATTERN` |
  | a raw-CAN log | `captures can` (the index), `correlate can FILE --id IDS --top N` |
  | what one PID ships as to the device | `wican autopid show BMS:2101 --firmware 4` |
  | a dependency version | `uv tree --depth 1`, `uv pip show NAME` |

  `captures uds` lists and `--sessions` keep the latest 50 (`--limit`), and `research` the top
  20, and each says what it hid. Start `research` and `coverage` from `--summary` or `--ecu`.
